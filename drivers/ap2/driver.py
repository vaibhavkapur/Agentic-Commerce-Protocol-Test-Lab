"""AP2 v0.2.0 driver: the lab acts as the Shopping Agent presenting mandate chains.

The driver owns the *agent-side* fixture material (issuer, user, agent keys) and
constructs positive and negative mandate chains with ``Ap2FixtureBuilder``.
The target plays merchant and credential provider and returns signed receipts.
Key material derives from the run seed, so an in-process target trusts the
lab's test issuer without any out-of-band exchange; a network target must be
started with the same ``--seed``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from drivers.base import DriverContext, MetricUnavailable, ProtocolDriver, StepOutcome
from drivers.ucp.driver import API_KEY as UCP_API_KEY, PLATFORM_PROFILE
from fixtures.issuers.ap2_fixtures import Ap2FixtureBuilder, checkout_hash
from fixtures.issuers.jws import verify_compact
from fixtures.issuers.sdjwt import sd_hash
from fixtures.issuers.trust_material import TrustMaterial
from lab.assertions.schema import validate
from lab.runner.environment import Environment, HarnessError


class Ap2Driver(ProtocolDriver):
    protocol = "ap2"

    async def inspect_target(self, env: Environment) -> Dict[str, Any]:
        try:
            r = await env.protocol_client.get("/ap2/capabilities")
        except httpx.HTTPError as exc:
            return {"capabilities": [], "error": str(exc), "raw": None}
        if r.status_code != 200:
            return {"capabilities": [], "error": f"capabilities returned {r.status_code}", "raw": None}
        doc = r.json()
        caps = [f"role:{role}" for role in doc.get("roles", [])] + [f"mandate:{m}" for m in doc.get("mandate_types", [])]
        return {"capabilities": caps, "version": doc.get("version"), "merchant": doc.get("merchant"),
                "merchant_public_jwk": doc.get("merchant_public_jwk"), "raw": doc}

    # ---------------------------------------------------------------- helpers
    def _builder(self, ctx: DriverContext) -> Ap2FixtureBuilder:
        if "ap2_builder" not in ctx.state:
            material = TrustMaterial.generate(ctx.rng)
            ctx.state["ap2_material"] = material
            ctx.state["ap2_builder"] = Ap2FixtureBuilder(ctx.clock, ctx.rng, material)
        return ctx.state["ap2_builder"]

    def _merchant(self, ctx: DriverContext) -> Dict[str, Any]:
        m = (ctx.capability_snapshot or {}).get("merchant")
        if not m:
            raise HarnessError("target did not expose its AP2 merchant identity")
        return m

    async def _create_checkout(self, ctx: DriverContext, step: Dict[str, Any], items, label: str) -> StepOutcome:
        headers = {"X-API-Key": UCP_API_KEY, "Content-Type": "application/json", "UCP-Agent": f'profile="{PLATFORM_PROFILE}"',
                   "Request-Id": ctx.rng.uuid(), "Idempotency-Key": ctx.rng.uuid()}
        body = {"line_items": [{"item": {"id": it["id"]}, "quantity": int(it.get("quantity", 1))} for it in items],
                "buyer": {"first_name": "Lab", "last_name": "Buyer", "email": "lab.buyer@example.test"}}
        outcome = await self.send(ctx, step, method="POST", path="/ucp/checkout-sessions", headers=headers, json_body=body)
        if outcome.status == 201 and isinstance(outcome.body, dict):
            ctx.state[label] = outcome.body["id"]
            if label == "checkout_id":
                ctx.state["operation"] = {"checkout_id": outcome.body["id"], "protocol": "ap2"}
            total = next((t["amount"] for t in outcome.body.get("totals", []) if t.get("type") == "total"), None)
            ctx.state[f"{label}_total"] = total
            ctx.state[f"{label}_currency"] = outcome.body.get("currency")
        return outcome

    # ---------------------------------------------------------------- actions
    async def action_create_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        return await self._create_checkout(ctx, step, p.get("items", [{"id": "sku_shoe_gold_9", "quantity": 1}]), "checkout_id")

    async def action_create_other_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        return await self._create_checkout(ctx, step, p.get("items", [{"id": "sku_socks_best", "quantity": 1}]), "other_checkout_id")

    async def action_obtain_checkout_jwt(self, step, ctx: DriverContext) -> StepOutcome:
        which = step.get("with", {}).get("for", "current")
        label = "checkout_id" if which == "current" else "other_checkout_id"
        cid = ctx.state.get(label)
        if not cid:
            raise HarnessError(f"obtain_checkout_jwt: no {label} in state")
        outcome = await self.send(ctx, step, method="POST", path="/ap2/checkouts/sign", headers={"Content-Type": "application/json"},
                                  json_body={"checkout_id": cid})
        if outcome.status == 200 and isinstance(outcome.body, dict):
            key = "checkout_jwt" if which == "current" else "other_checkout_jwt"
            ctx.state[key] = outcome.body["checkout_jwt"]
            snapshot_jwk = (ctx.capability_snapshot or {}).get("merchant_public_jwk")
            try:
                verify_compact(outcome.body["checkout_jwt"], snapshot_jwk, expected_alg="ES256")
                outcome.observations["checkout_jwt_merchant_signature"] = "valid"
            except (ValueError, TypeError) as exc:
                outcome.observations["checkout_jwt_merchant_signature"] = f"invalid: {exc}"
            outcome.observations["checkout_hash"] = checkout_hash(outcome.body["checkout_jwt"])
        return outcome

    async def action_build_checkout_mandate(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        variant = p.get("variant", "valid_direct")
        jwt = ctx.state.get("checkout_jwt")
        if not jwt:
            raise HarnessError("build_checkout_mandate: obtain_checkout_jwt must run first")
        bundle = self._builder(ctx).build_checkout_mandate(variant, jwt, merchant=self._merchant(ctx),
                                                           other_checkout_jwt=ctx.state.get("other_checkout_jwt"),
                                                           allowed_skus=p.get("allowed_skus"))
        ctx.state["checkout_mandate"] = bundle
        out = StepOutcome(step_id=step["id"], action=step["action"], intentionally_invalid=bool(step.get("intentionally_invalid")))
        out.observations.update(bundle.to_dict())
        out.observations["presentation_reference"] = sd_hash(bundle.presentation)
        ok, errors = validate("ap2:checkout_mandate", {"vct": "mandate.checkout.1", "checkout_jwt": jwt, "checkout_hash": checkout_hash(jwt)})
        out.observations["closed_content_schema_valid"] = ok
        if not ok:
            out.notes.extend(errors)
        return out

    async def action_present_checkout_mandate(self, step, ctx: DriverContext) -> StepOutcome:
        bundle = ctx.state.get("checkout_mandate")
        if bundle is None:
            raise HarnessError("present_checkout_mandate: build_checkout_mandate must run first")
        cid = step.get("with", {}).get("checkout_id") or ctx.state.get("checkout_id")
        op_key = ctx.rng.uuid()
        outcome = await self.send(ctx, step, method="POST", path=f"/ap2/checkouts/{cid}/complete-with-mandate",
                                  headers={"Content-Type": "application/json"},
                                  json_body={"checkout_mandate": bundle.presentation, "payment_token": "tok_ok", "operation_key": op_key})
        self._record_receipt(ctx, outcome, bundle.presentation, signer_jwk=(ctx.capability_snapshot or {}).get("merchant_public_jwk"))
        return outcome

    async def action_build_payment_mandate(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        variant = p.get("variant", "valid_direct")
        jwt = ctx.state.get("checkout_jwt")
        if not jwt:
            raise HarnessError("build_payment_mandate: obtain_checkout_jwt must run first")
        amount = p.get("amount", ctx.state.get("checkout_id_total"))
        currency = p.get("currency", ctx.state.get("checkout_id_currency", "USD"))
        bundle = self._builder(ctx).build_payment_mandate(variant, checkout_jwt=jwt, amount=amount, currency=currency,
                                                          payee=self._merchant(ctx), other_checkout_jwt=ctx.state.get("other_checkout_jwt"))
        ctx.state["payment_mandate"] = bundle
        out = StepOutcome(step_id=step["id"], action=step["action"], intentionally_invalid=bool(step.get("intentionally_invalid")))
        out.observations.update(bundle.to_dict())
        out.observations["presentation_reference"] = sd_hash(bundle.presentation)
        out.observations["amount"] = amount
        return out

    async def action_present_payment_mandate(self, step, ctx: DriverContext) -> StepOutcome:
        bundle = ctx.state.get("payment_mandate")
        if bundle is None:
            raise HarnessError("present_payment_mandate: build_payment_mandate must run first")
        outcome = await self.send(ctx, step, method="POST", path="/ap2/credential-provider/authorize-payment",
                                  headers={"Content-Type": "application/json"},
                                  json_body={"payment_mandate": bundle.presentation, "checkout_jwt": ctx.state.get("checkout_jwt")})
        material: TrustMaterial = ctx.state["ap2_material"]
        self._record_receipt(ctx, outcome, bundle.presentation, signer_jwk=material.credential_provider.public_jwk)
        return outcome

    def _record_receipt(self, ctx: DriverContext, outcome: StepOutcome, presentation: str, *, signer_jwk: Optional[Dict[str, Any]]) -> None:
        body = outcome.body if isinstance(outcome.body, dict) else {}
        receipt = body.get("receipt") or {}
        outcome.observations["verifier"] = {"result": receipt.get("status"), "error": receipt.get("error"),
                                            "error_description": receipt.get("error_description"), "failed_step": receipt.get("failed_step")}
        outcome.observations["receipt"] = receipt
        outcome.observations["presentation_reference"] = sd_hash(presentation)
        outcome.observations["receipt_reference_matches"] = receipt.get("reference") == sd_hash(presentation)
        jwt = body.get("receipt_jwt")
        if jwt and signer_jwk:
            try:
                verify_compact(jwt, signer_jwk, expected_alg="ES256")
                outcome.observations["receipt_signature_valid"] = True
            except ValueError as exc:
                outcome.observations["receipt_signature_valid"] = False
                outcome.notes.append(f"receipt signature: {exc}")
        schema = "ap2:checkout_receipt" if "checkout" in outcome.request.get("path", "") else "ap2:payment_receipt"
        ok, errors = validate(schema, {k: v for k, v in receipt.items() if k not in ("failed_step", "steps")}) if receipt else (False, ["no receipt"])
        outcome.observations["receipt_schema_valid"] = ok
        if not ok:
            outcome.notes.extend(errors)
        if receipt.get("order_id") and not str(receipt["order_id"]).startswith("pending:"):
            ctx.state["client_resolved_order_id"] = receipt["order_id"]

    # ---------------------------------------------------------------- metrics
    async def metric_merchant_receipt_count(self, ctx: DriverContext) -> int:
        data = await self._observe_or_unavailable(ctx, "/_lab/observe/verifiers")
        return len(data.get("ap2_decisions", []))
