"""TAP driver: the lab acts as the trusted agent signing merchant requests (RFC 9421).

Negative fixtures are constructed deliberately: the driver signs the request it
*intends* the verifier to see, then (for mutation cases) changes what it sends.
Every step records the signature base and public key so the independent
RFC 9421 oracle can re-derive the expected verdict without trusting either the
driver's or the verifier's canonicalization code.
"""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional

import httpx

from drivers.base import DriverContext, ProtocolDriver, StepOutcome
from fixtures.issuers.http_signatures import HttpRequestView, SignatureParams, content_digest, sign_request, signature_base
from fixtures.issuers.keys import KeyHandle, public_only
from fixtures.issuers.trust_material import AGENT_DIRECTORY_URL, TrustMaterial
from lab.runner.environment import Environment, HarnessError

TARGET_AUTHORITY = "target.lab"
OTHER_AUTHORITY = "other-merchant.example"
PAYER_PATH = "/tap/checkout/authorize"
BROWSER_PATH = "/tap/catalog"


class TapDriver(ProtocolDriver):
    protocol = "tap"

    async def inspect_target(self, env: Environment) -> Dict[str, Any]:
        try:
            r = await env.protocol_client.get("/tap/capabilities")
        except httpx.HTTPError as exc:
            return {"capabilities": [], "error": str(exc), "raw": None}
        if r.status_code != 200:
            return {"capabilities": [], "error": f"capabilities returned {r.status_code}", "raw": None}
        doc = r.json()
        caps = [f"tag:{t}" for t in doc.get("tags", [])] + [f"alg:{a}" for a in doc.get("algorithms", [])]
        caps.append("signed_request_verification")
        if doc.get("policy", {}).get("replay_protection"):
            caps.append("policy:replay_protection")
        return {"capabilities": caps, "raw": doc}

    def _material(self, ctx: DriverContext) -> TrustMaterial:
        if "tap_material" not in ctx.state:
            ctx.state["tap_material"] = TrustMaterial.generate(ctx.rng)
        return ctx.state["tap_material"]

    def _key(self, ctx: DriverContext, name: str) -> KeyHandle:
        return getattr(self._material(ctx), name)

    async def action_send_signed_request(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        variant = p.get("variant", "valid")
        m = self._material(ctx)
        now = ctx.clock.now_int()
        key = m.agent
        alg_param: Optional[str] = None
        path = p.get("path", PAYER_PATH)
        send_path = path
        tag = p.get("tag", "agent-payer-auth" if path == PAYER_PATH else "agent-browser-auth")
        authority = TARGET_AUTHORITY
        method = "GET" if path == BROWSER_PATH else "POST"
        body = b"" if method == "GET" else b'{"checkout_id":"' + ctx.state.get("checkout_id", "chk_lab").encode() + b'","amount":19900}'
        send_body = body
        created, expires = now, now + 300
        extra_headers: Dict[str, str] = {}
        cover_digest = bool(body)
        notes = []

        if variant == "unknown_key":
            key = m.rogue_agent
            notes.append("signed with a key that is not in the registry")
        elif variant == "revoked_key":
            key = m.agent
            notes.append("signed with a key the registry has revoked")
        elif variant == "rotated_out_key":
            key = m.agent
            notes.append("signed with the pre-rotation key")
        elif variant == "secondary_key":
            key = m.agent_secondary
        elif variant == "expired":
            created, expires = now - 900, now - 600
        elif variant == "future_created":
            created, expires = now + 600, now + 900
        elif variant == "lifetime_too_long":
            created, expires = now, now + 3600
        elif variant == "mutated_path":
            send_path = path + "-mutated"
            notes.append("path mutated after signing")
        elif variant == "tampered_body":
            send_body = body.replace(b"19900", b"100")
            notes.append("body mutated after signing; Content-Digest left as signed")
        elif variant == "wrong_authority":
            authority = OTHER_AUTHORITY
            notes.append("signed for another merchant's authority")
        elif variant == "forwarded_host_spoof":
            authority = OTHER_AUTHORITY
            extra_headers["X-Forwarded-Host"] = OTHER_AUTHORITY
            notes.append("signed for another authority and asserted it via caller-supplied X-Forwarded-Host")
        elif variant == "wrong_tag":
            tag = "agent-browser-auth"
        elif variant == "unsupported_alg":
            alg_param = "hmac-sha256"
        elif variant == "missing_content_digest":
            cover_digest = False
        elif variant not in ("valid", "replay"):
            raise HarnessError(f"unknown TAP variant {variant!r}")

        headers: Dict[str, str] = {"Signature-Agent": f'"{AGENT_DIRECTORY_URL}"', "Host": TARGET_AUTHORITY}
        if body:
            headers["Content-Type"] = "application/json"
            headers["Content-Digest"] = content_digest(body)
        covered = ["@method", "@authority", "@path"] + (["content-digest"] if cover_digest and body else [])
        params = SignatureParams(covered=covered, created=created, expires=expires, keyid=key.kid,
                                 alg=alg_param or key.rfc9421_alg, nonce=ctx.rng.hex(16), tag=tag)
        view = HttpRequestView(method=method, authority=authority, path=path, headers=headers, body=body)
        sig_headers = sign_request(view, key, params)
        headers.update(sig_headers)
        headers.update(extra_headers)
        headers.update(p.get("headers", {}))

        outcome = await self.send(ctx, step, method=method, path=send_path, headers=headers, raw_body=send_body if method != "GET" else None)
        if variant == "replay":
            first = outcome
            ctx.record(StepOutcome(step_id=step["id"] + "_first", action=step["action"], request=first.request, response=first.response,
                                   transport_error=first.transport_error, observations={"verifier": self._verdict(first)}))
            outcome = await self.send(ctx, step, method=method, path=send_path, headers=headers, raw_body=send_body if method != "GET" else None)
            outcome.observations["first_attempt"] = self._verdict(first)
        outcome.observations["verifier"] = self._verdict(outcome)
        outcome.observations["variant"] = variant
        outcome.observations["signing"] = {
            "signature_base": signature_base(view, params),
            "covered": covered,
            "params": params.serialize(),
            "signed_authority": authority,
            "signed_path": path,
            "sent_path": send_path,
            "sent_authority": TARGET_AUTHORITY,
            "signed_body_digest": content_digest(body) if body else None,
            "sent_body_digest": content_digest(send_body) if send_body else None,
            "public_jwk": public_only(key.public_jwk),
            "alg": alg_param or key.rfc9421_alg,
            "signature_b64": headers["Signature"].split(":")[1],
            "signature_input": headers["Signature-Input"],
            "expected_verdict": self._expected_verdict(variant),
        }
        outcome.notes.extend(notes)
        return outcome

    @staticmethod
    def _verdict(outcome: StepOutcome) -> Dict[str, Any]:
        body = outcome.body if isinstance(outcome.body, dict) else {}
        if outcome.transport_error:
            return {"result": "no_response", "error": outcome.transport_error}
        return {"result": "verified" if body.get("verified") else "rejected", "error": None if body.get("verified") else body.get("reason"),
                "authority_used": body.get("authority_used"), "authority_source": body.get("authority_source"), "agent_id": body.get("agent_id")}

    @staticmethod
    def _expected_verdict(variant: str) -> str:
        return "verified" if variant in ("valid", "secondary_key", "rotated_out_key") else "rejected"

    async def action_rotate_agent_key(self, step, ctx: DriverContext) -> StepOutcome:
        result = await ctx.env.admin("/_lab/admin/registry/rotate")
        return StepOutcome(step_id=step["id"], action=step["action"], observations={"registry": result})

    async def action_revoke_agent_key(self, step, ctx: DriverContext) -> StepOutcome:
        kid = step.get("with", {}).get("key_id") or self._material(ctx).agent.kid
        result = await ctx.env.admin(f"/_lab/admin/registry/revoke/{kid}")
        return StepOutcome(step_id=step["id"], action=step["action"], observations={"registry": result, "key_id": kid})

    async def action_advance_clock(self, step, ctx: DriverContext) -> StepOutcome:
        seconds = int(step.get("with", {}).get("seconds", 0))
        ctx.clock.advance(seconds)
        return StepOutcome(step_id=step["id"], action=step["action"], observations={"advanced_seconds": seconds, "now": ctx.clock.now_int()})
