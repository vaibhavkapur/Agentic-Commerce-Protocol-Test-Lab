"""UCP v2026-08-25 driver: the lab acts as the Platform against a Business target.

Shares the client-side recovery machinery with the ACP driver (durable journal,
restart, replay) but speaks the native UCP REST binding and body shapes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from drivers.acp.driver import AcpDriver
from drivers.base import DriverContext, StepOutcome
from lab.runner.environment import Environment, HarnessError

API_KEY = "lab_ucp_api_key_synthetic"
PLATFORM_PROFILE = "https://platform.lab.test/.well-known/ucp"
HANDLER_ID = "lab_sim_card_1"
DEFAULT_BUYER = {"first_name": "Lab", "last_name": "Buyer", "email": "lab.buyer@example.test"}
CHECKOUT_CAPABILITY = "dev.ucp.shopping.checkout"


class UcpDriver(AcpDriver):
    protocol = "ucp"
    complete_schema: Optional[str] = None  # UCP request shapes are derived from the checkout schema; validated structurally below

    async def inspect_target(self, env: Environment) -> Dict[str, Any]:
        try:
            r = await env.protocol_client.get("/.well-known/ucp")
        except httpx.HTTPError as exc:
            return {"capabilities": [], "error": f"profile unreachable: {exc}", "raw": None}
        if r.status_code != 200:
            return {"capabilities": [], "error": f"profile returned {r.status_code}", "raw": None}
        doc = r.json()
        ucp = doc.get("ucp", {})
        caps = sorted(ucp.get("capabilities", {}).keys())
        services = ucp.get("services", {}).get("dev.ucp.shopping", [])
        endpoint = next((s.get("endpoint") for s in services if s.get("transport") == "rest"), None)
        if endpoint:
            caps.append("service:dev.ucp.shopping:rest")
        return {"capabilities": caps, "protocol_version": ucp.get("version"), "rest_endpoint": endpoint,
                "payment_handlers": sorted(ucp.get("payment_handlers", {}).keys()), "raw": doc}

    def _base(self, ctx: DriverContext) -> str:
        endpoint = (ctx.capability_snapshot or {}).get("rest_endpoint")
        if endpoint:
            from urllib.parse import urlparse

            return urlparse(endpoint).path.rstrip("/") or "/ucp"
        return "/ucp"

    def _complete_path(self, ctx: DriverContext, cid: str) -> str:
        return f"{self._base(ctx)}/checkout-sessions/{cid}/complete"

    def _get_path(self, ctx: DriverContext, cid: str) -> str:
        return f"{self._base(ctx)}/checkout-sessions/{cid}"

    def _headers(self, ctx: DriverContext, step: Dict[str, Any], *, idempotency_key: Optional[str]) -> Dict[str, str]:
        params = step.get("with", {})
        h = {
            "X-API-Key": API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "UCP-Agent": f'profile="{PLATFORM_PROFILE}"',
            "Request-Id": ctx.rng.uuid(),
            "User-Agent": "commerce-lab/0.1 (ucp driver)",
        }
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        for name in params.get("omit_headers", []):
            h = {k: v for k, v in h.items() if k.lower() != name.lower()}
        h.update(params.get("headers", {}))
        return h

    @staticmethod
    def _line_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{"item": {"id": it["id"]}, "quantity": int(it.get("quantity", 1))} for it in items]

    async def action_fetch_discovery(self, step, ctx: DriverContext) -> StepOutcome:
        return await self.send(ctx, step, method="GET", path="/.well-known/ucp", headers={})

    async def action_create_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        body: Dict[str, Any] = p.get("body_override") or {"line_items": self._line_items(p.get("items", [{"id": "sku_tee_blue_m", "quantity": 1}]))}
        if not p.get("body_override"):
            buyer = p.get("buyer", DEFAULT_BUYER)
            if buyer:
                body["buyer"] = buyer
            if p.get("discount_codes") is not None:
                body["discounts"] = {"codes": p["discount_codes"]}
        key = self._idem_key(ctx, step)
        outcome = await self.send(ctx, step, method="POST", path=f"{self._base(ctx)}/checkout-sessions",
                                  headers=self._headers(ctx, step, idempotency_key=key), json_body=body)
        outcome.observations["idempotency_key"] = key
        self._after_session_response(ctx, outcome)
        return outcome

    async def action_update_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        cid = self._checkout_id(ctx, step)
        body: Dict[str, Any] = p.get("body_override") or {}
        if not p.get("body_override"):
            if "items" in p:
                body["line_items"] = self._line_items(p["items"])
            if "buyer" in p:
                body["buyer"] = p["buyer"]
            if p.get("discount_codes") is not None:
                body["discounts"] = {"codes": p["discount_codes"]}
        key = self._idem_key(ctx, step)
        outcome = await self.send(ctx, step, method="PUT", path=self._get_path(ctx, cid),
                                  headers=self._headers(ctx, step, idempotency_key=key), json_body=body)
        outcome.observations["idempotency_key"] = key
        self._after_session_response(ctx, outcome)
        return outcome

    def _complete_body(self, p: Dict[str, Any]) -> Dict[str, Any]:
        if p.get("body_override"):
            return p["body_override"]
        return {
            "payment": {
                "instruments": [{
                    "id": "pi_lab_card_1",
                    "handler_id": p.get("handler_id", HANDLER_ID),
                    "type": "card",
                    "selected": True,
                    "credential": {"type": "lab_simulated_token", "token": p.get("payment_token", "tok_ok")},
                }]
            }
        }

    async def action_cancel_checkout_session(self, step, ctx: DriverContext) -> StepOutcome:
        cid = self._checkout_id(ctx, step)
        key = self._idem_key(ctx, step)
        outcome = await self.send(ctx, step, method="POST", path=f"{self._base(ctx)}/checkout-sessions/{cid}/cancel",
                                  headers=self._headers(ctx, step, idempotency_key=key), json_body=step.get("with", {}).get("body_override", {}))
        outcome.observations["idempotency_key"] = key
        return outcome

    async def action_send_raw_request(self, step, ctx: DriverContext) -> StepOutcome:
        p = step.get("with", {})
        path = p["path"].replace("{checkout_id}", ctx.state.get("checkout_id", "missing"))
        headers = self._headers(ctx, step, idempotency_key=self._idem_key(ctx, step)) if p.get("default_headers", True) else {}
        headers.update(p.get("headers", {}))
        raw = p.get("raw_body")
        full = f"{self._base(ctx)}{path}" if path.startswith("/checkout") else path
        return await self.send(ctx, step, method=p.get("method", "POST"), path=full, headers=headers,
                               json_body=p.get("body") if raw is None else None,
                               raw_body=raw.encode("utf-8") if raw is not None else None)
