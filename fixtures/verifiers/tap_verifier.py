"""TAP signed-request verifier fixture (merchant gateway role).

Profile: RFC 9421 HTTP Message Signatures with the parameter set used by the
Visa Trusted Agent Protocol sample (``created``, ``expires``, ``nonce``,
``keyid``, ``alg``, ``tag``) and a ``Signature-Agent`` header naming the agent
directory. Unlike the sample's verifier, the signature base here is the RFC 9421
base *including* ``"@signature-params"`` (see profile ``lab_decisions``).

Canonicalization at the boundary: the lab fault proxy is the trusted edge. It
records the authority it actually observed in ``X-Lab-Boundary-Authority``. The
corrected verifier derives ``@authority`` from that header only; the broken
verifier (``tap_trust_forwarded_host=True``) trusts a caller-supplied
``X-Forwarded-Host`` and can be made to verify a signature over the wrong
merchant context.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fixtures.issuers.http_signatures import HttpRequestView, content_digest, parse_signature, parse_signature_input, signature_base
from fixtures.issuers.keys import jwk_to_public_key, verify_bytes
from fixtures.registries.agent_registry import AgentRegistry, TrustError

ALLOWED_ALGS = ("ed25519", "ecdsa-p256-sha256")
ALLOWED_TAGS = ("agent-browser-auth", "agent-payer-auth")
MAX_LIFETIME = 8 * 60
MAX_SKEW = 60
BOUNDARY_HEADER = "x-lab-boundary-authority"


@dataclass
class Verification:
    verified: bool = False
    reason: str = ""
    detail: str = ""
    agent_id: Optional[str] = None
    key_id: Optional[str] = None
    alg: Optional[str] = None
    tag: Optional[str] = None
    nonce: Optional[str] = None
    authority_used: Optional[str] = None
    authority_source: Optional[str] = None
    covered: List[str] = field(default_factory=list)
    checks: List[str] = field(default_factory=list)
    policy_checks: List[str] = field(default_factory=list)

    def fail(self, reason: str, detail: str) -> "Verification":
        self.verified = False
        self.reason = reason
        self.detail = detail
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TapVerifier:
    def __init__(self, clock, registry: AgentRegistry, options: Optional[Dict[str, Any]] = None):
        self.clock = clock
        self.registry = registry
        self.options = {"tap_trust_forwarded_host": False, "tap_replay_protection": True}
        self.options.update(options or {})
        self.replay_cache: Dict[str, int] = {}
        self.decisions: List[Dict[str, Any]] = []

    def _authority(self, request: Request) -> (str, str):
        if self.options.get("tap_trust_forwarded_host"):
            xfh = request.headers.get("x-forwarded-host")
            if xfh:
                return xfh.strip().lower(), "x-forwarded-host (caller-supplied; broken)"
        boundary = request.headers.get(BOUNDARY_HEADER)
        if boundary:
            return boundary.strip().lower(), BOUNDARY_HEADER
        return (request.headers.get("host") or "").lower(), "host"

    async def verify(self, request: Request, *, required_tag: str) -> Verification:
        v = Verification()
        body = await request.body()
        authority, source = self._authority(request)
        v.authority_used, v.authority_source = authority, source
        sig_input = request.headers.get("signature-input")
        sig = request.headers.get("signature")
        if not sig_input or not sig:
            return v.fail("signature_missing", "Signature-Input or Signature header missing")
        try:
            label, params, _raw = parse_signature_input(sig_input)
            sig_label, sig_bytes = parse_signature(sig)
        except ValueError as exc:
            return v.fail("signature_malformed", str(exc))
        if sig_label != label:
            return v.fail("signature_malformed", "Signature label does not match Signature-Input label")
        v.covered = params.covered
        v.key_id, v.alg, v.tag, v.nonce = params.keyid, params.alg, params.tag, params.nonce
        for name in ("created", "expires", "keyid", "alg", "nonce", "tag"):
            if getattr(params, name) is None:
                return v.fail("signature_malformed", f"missing signature parameter {name}")
        v.checks.append("headers parsed")

        if "@authority" not in params.covered or "@path" not in params.covered:
            return v.fail("coverage_insufficient", "@authority and @path must be covered")
        if body and "content-digest" not in params.covered:
            return v.fail("coverage_insufficient", "requests with a body must cover content-digest")
        v.checks.append("coverage ok")

        now = self.clock.now_int()
        if params.created > now + MAX_SKEW:
            return v.fail("not_yet_valid", "signature created in the future")
        if params.expires < now:
            return v.fail("expired", f"signature expired at {params.expires} (now {now})")
        if params.expires <= params.created or params.expires - params.created > MAX_LIFETIME:
            return v.fail("lifetime_invalid", "expires must be after created and within the maximum lifetime")
        v.checks.append("time window ok")

        if params.tag not in ALLOWED_TAGS:
            return v.fail("context_mismatch", f"unknown tag {params.tag!r}")
        if params.tag != required_tag:
            return v.fail("context_mismatch", f"tag {params.tag!r} not permitted for this operation (requires {required_tag!r})")
        if params.alg not in ALLOWED_ALGS:
            return v.fail("unsupported_algorithm", f"alg {params.alg!r} is not in the profile allowlist")
        v.checks.append("operation context ok")

        try:
            rk = self.registry.resolve(str(params.keyid), at=now)
        except TrustError as exc:
            v.policy_checks.append(f"registry: {exc.reason}")
            return v.fail(exc.reason, exc.detail)
        if rk.algorithm != params.alg:
            return v.fail("unsupported_algorithm", f"key {params.keyid} is registered for {rk.algorithm}, request says {params.alg}")
        v.agent_id = rk.agent_id
        agent = self.registry.agents[rk.agent_id]
        sig_agent = (request.headers.get("signature-agent") or "").strip().strip('"')
        if sig_agent and sig_agent != agent.directory_url:
            return v.fail("unknown_agent", "Signature-Agent does not match the registered directory for this key")
        v.checks.append("key resolved")

        headers = {k: v_ for k, v_ in request.headers.items()}
        view = HttpRequestView(method=request.method, authority=authority, path=request.url.path, headers=headers, body=body,
                               query=request.url.query or "")
        if "content-digest" in params.covered:
            cd = request.headers.get("content-digest")
            if not cd or cd.strip() != content_digest(body):
                return v.fail("content_digest_mismatch", "Content-Digest does not match the body")
            v.checks.append("content-digest ok")
        try:
            base = signature_base(view, params)
        except ValueError as exc:
            return v.fail("coverage_insufficient", str(exc))
        try:
            pub = jwk_to_public_key(rk.public_jwk)
        except ValueError as exc:
            return v.fail("unknown_key", f"unusable registry key: {exc}")
        if not verify_bytes(pub, params.alg, base.encode("utf-8"), sig_bytes):
            return v.fail("signature_invalid", "signature does not verify over the request as received at the boundary")
        v.checks.append("signature ok")

        if self.options.get("tap_replay_protection", True):
            replay_key = f"{params.keyid}:{params.nonce}"
            self._prune(now)
            if replay_key in self.replay_cache:
                v.policy_checks.append("replay: nonce already seen")
                return v.fail("replay", "nonce already used with this key within its validity window")
            self.replay_cache[replay_key] = params.expires
            v.policy_checks.append("replay: nonce recorded")
        v.verified = True
        v.reason = "verified"
        return v

    def _prune(self, now: int) -> None:
        for k in [k for k, exp in self.replay_cache.items() if exp < now]:
            self.replay_cache.pop(k, None)

    # ------------------------------------------------------------- endpoints
    async def authorize_checkout(self, request: Request):
        v = await self.verify(request, required_tag="agent-payer-auth")
        return self._respond(v, "authorize_checkout")

    async def browse_catalog(self, request: Request):
        v = await self.verify(request, required_tag="agent-browser-auth")
        return self._respond(v, "browse_catalog")

    def _respond(self, v: Verification, operation: str) -> JSONResponse:
        record = v.to_dict()
        record["operation"] = operation
        record["at"] = self.clock.iso()
        self.decisions.append(record)
        return JSONResponse(record, status_code=200 if v.verified else 401)

    async def capabilities(self, request: Request):
        return JSONResponse({
            "protocol": "tap",
            "profile": "rfc9421-visa-sample-parameters",
            "tags": list(ALLOWED_TAGS),
            "algorithms": list(ALLOWED_ALGS),
            "required_components": ["@authority", "@path"],
            "body_component": "content-digest",
            "max_lifetime_seconds": MAX_LIFETIME,
            "registry": "/registry/keys/{key_id}",
            "policy": {"replay_protection": bool(self.options.get("tap_replay_protection", True))},
        })

    def routes(self, prefix: str = "/tap") -> List[Route]:
        return [
            Route(f"{prefix}/capabilities", self.capabilities, methods=["GET"]),
            Route(f"{prefix}/checkout/authorize", self.authorize_checkout, methods=["POST"]),
            Route(f"{prefix}/catalog", self.browse_catalog, methods=["GET"]),
        ]

    def snapshot(self) -> Dict[str, Any]:
        return {"decisions": list(self.decisions), "replay_cache_size": len(self.replay_cache), "options": dict(self.options)}
