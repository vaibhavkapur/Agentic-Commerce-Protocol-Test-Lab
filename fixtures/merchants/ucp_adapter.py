"""UCP (Universal Commerce Protocol) business adapter over the shared merchant core.

Pinned to tag ``v2026-08-25`` of Universal-Commerce-Protocol/ucp
(``source/schemas/shopping/checkout.json`` and ``source/services/shopping/rest.openapi.json``).
Native UCP statuses (``incomplete``, ``ready_for_complete``, ``completed``,
``canceled``) are emitted directly.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .core import Checkout, DomainError, MerchantCore

UCP_VERSION = "2026-08-25"
API_KEY = "lab_ucp_api_key_synthetic"
CHECKOUT_CAPABILITY = "dev.ucp.shopping.checkout"
DISCOUNT_CAPABILITY = "dev.ucp.shopping.discount"
HANDLER_NAME = "dev.commerce-lab.simulated_card"
HANDLER_ID = "lab_sim_card_1"


class UcpAdapter:
    def __init__(self, core: MerchantCore, *, discount_capability: bool = True, advertise_checkout: bool = True,
                 base_path: str = "/ucp"):
        self.core = core
        self.discount_capability = discount_capability
        self.advertise_checkout = advertise_checkout
        self.base_path = base_path

    # ------------------------------------------------------------- discovery
    def profile_document(self, request: Request) -> Dict[str, Any]:
        host = request.headers.get("host", "target.lab")
        scheme = request.url.scheme or "http"
        capabilities: Dict[str, Any] = {}
        if self.advertise_checkout:
            capabilities[CHECKOUT_CAPABILITY] = [{
                "version": UCP_VERSION,
                "spec": f"https://ucp.dev/{UCP_VERSION}/specification/shopping/checkout",
                "schema": f"https://ucp.dev/{UCP_VERSION}/schemas/shopping/checkout.json",
            }]
        if self.discount_capability:
            capabilities[DISCOUNT_CAPABILITY] = [{
                "version": UCP_VERSION,
                "spec": f"https://ucp.dev/{UCP_VERSION}/specification/shopping/extensions/discount",
                "schema": f"https://ucp.dev/{UCP_VERSION}/schemas/shopping/discount.json",
                "extends": CHECKOUT_CAPABILITY,
            }]
        return {
            "ucp": {
                "version": UCP_VERSION,
                "services": {
                    "dev.ucp.shopping": [{
                        "version": UCP_VERSION,
                        "spec": f"https://ucp.dev/{UCP_VERSION}/specification/overview",
                        "transport": "rest",
                        "schema": f"https://ucp.dev/{UCP_VERSION}/services/shopping/rest.openapi.json",
                        "endpoint": f"{scheme}://{host}{self.base_path}",
                    }]
                },
                "capabilities": capabilities,
                "payment_handlers": {
                    HANDLER_NAME: [{
                        "id": HANDLER_ID,
                        "version": UCP_VERSION,
                        "spec": "https://commerce-lab.local/handlers/simulated-card",
                        "schema": "https://commerce-lab.local/handlers/simulated-card/schema.json",
                        "available_instruments": [{"type": "card"}],
                        "config": {"mode": "simulated"},
                    }]
                },
            }
        }

    # ---------------------------------------------------------------- errors
    def error(self, status: int, code: str, content: str, *, severity: str = "unrecoverable", path: Optional[str] = None) -> JSONResponse:
        if self.core.options.get("error_body") == "legacy":
            return JSONResponse({"error": content, "reason": code}, status_code=status)
        msg: Dict[str, Any] = {"type": "error", "code": code, "content": content, "severity": severity}
        if path:
            msg["path"] = path
        return JSONResponse({"ucp": {"version": UCP_VERSION, "status": "error"}, "messages": [msg]}, status_code=status)

    def _authenticate(self, request: Request) -> Optional[JSONResponse]:
        if request.headers.get("x-api-key") != API_KEY:
            return self.error(401, "identity_required", "X-API-Key missing or invalid")
        ua = request.headers.get("ucp-agent", "")
        if not ua.startswith('profile="') or not ua.endswith('"'):
            return self.error(400, "invalid_request", 'UCP-Agent header must be profile="<uri>" (RFC 8941 dictionary)')
        if not request.headers.get("request-id"):
            return self.error(400, "invalid_request", "Request-Id header is required")
        return None

    async def _json_body(self, request: Request, required: bool = True) -> Tuple[Any, Optional[JSONResponse]]:
        raw = await request.body()
        if not raw:
            return ({} if not required else None), (None if not required else self.error(400, "invalid_request", "body required"))
        try:
            return json.loads(raw), None
        except json.JSONDecodeError:
            return None, self.error(400, "invalid_request", "body is not valid JSON")

    def _view(self, chk: Checkout, messages: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        t = self.core.totals(chk)
        line_items = []
        for li in chk.line_items:
            base = li.unit_amount * li.quantity
            line_items.append({
                "id": li.id,
                "item": {"id": li.sku, "title": li.title, "price": li.unit_amount},
                "quantity": li.quantity,
                "totals": [{"type": "subtotal", "amount": base}, {"type": "total", "amount": base}],
            })
        totals: List[Dict[str, Any]] = [{"type": "subtotal", "display_text": "Subtotal", "amount": t["subtotal"]}]
        if t["discount"]:
            totals.append({"type": "discount", "display_text": "Discount", "amount": -t["discount"]})
        if t["fulfillment"]:
            totals.append({"type": "fulfillment", "display_text": "Shipping", "amount": t["fulfillment"]})
        totals.append({"type": "tax", "display_text": "Tax", "amount": t["tax"]})
        totals.append({"type": "total", "display_text": "Total", "amount": t["total"]})
        msgs = list(messages or [])
        if chk.status == "open":
            if not chk.buyer.get("email"):
                status = "incomplete"
                msgs.append({"type": "error", "code": "missing", "path": "$.buyer.email", "content": "Buyer email is required",
                             "severity": "recoverable"})
            else:
                status = "ready_for_complete"
        elif chk.status == "completed":
            status = "completed"
        else:
            status = "canceled"
        caps: Dict[str, Any] = {CHECKOUT_CAPABILITY: [{"version": UCP_VERSION}]}
        if self.discount_capability:
            caps[DISCOUNT_CAPABILITY] = [{"version": UCP_VERSION}]
        view: Dict[str, Any] = {
            "ucp": {
                "version": UCP_VERSION,
                "capabilities": caps,
                "payment_handlers": {HANDLER_NAME: [{"id": HANDLER_ID, "version": UCP_VERSION, "available_instruments": [{"type": "card"}]}]},
            },
            "id": chk.id,
            "status": status,
            "currency": chk.currency.upper(),
            "line_items": line_items,
            "totals": totals,
            "messages": msgs,
            "links": [
                {"type": "terms_of_service", "url": "https://merchant.lab.test/terms"},
                {"type": "privacy_policy", "url": "https://merchant.lab.test/privacy"},
            ],
            "expires_at": chk.expires_at,
        }
        if chk.buyer:
            view["buyer"] = {k: v for k, v in chk.buyer.items() if k in ("first_name", "last_name", "email", "phone_number")}
        if self.discount_capability and (chk.discount_codes or t["applied_discounts"]):
            view["discounts"] = {
                "codes": list(chk.discount_codes),
                "applied": [{"code": a["code"], "title": a["title"], "amount": a["amount"]} for a in t["applied_discounts"]],
                "rejected": [{"code": r["code"], "reason": r["reason"]} for r in t["rejected_discounts"]],
            }
        if chk.status == "completed" and chk.order_id:
            order = self.core.orders[chk.order_id]
            view["order"] = {"id": order.id, "permalink_url": f"https://merchant.lab.test/orders/{order.id}", "label": order.id.upper()}
        return view

    def _domain_error_response(self, exc: DomainError, chk: Optional[Checkout]) -> JSONResponse:
        if chk is not None and exc.code in ("out_of_stock", "payment_declined", "price_changed", "invalid"):
            code = {"payment_declined": "payment_failed", "price_changed": "item_unavailable"}.get(exc.code, exc.code)
            msg = {"type": "error", "code": code, "path": exc.path or "$", "content": exc.message, "severity": "recoverable"}
            return JSONResponse(self._view(chk, messages=[msg]), status_code=200)
        if exc.code == "conflict":
            return self.error(409, "conflict", exc.message)
        if exc.code == "not_found":
            return self.error(404, "not_found", exc.message, path=exc.path)
        if exc.code == "payment_unknown":
            return self.error(503, "payment_failed", exc.message)
        return self.error(400, exc.code, exc.message, path=exc.path)

    async def _with_idempotency(self, request: Request, body: Any, handler) -> Response:
        key = request.headers.get("idempotency-key")
        if self.core.options.get("idempotency") == "ignored":
            return await handler()
        if not key:
            return self.error(400, "invalid_request", "Idempotency-Key header is required")
        scope = f"{request.headers.get('x-api-key', '')}|{request.method} {request.url.path}"
        state, rec = self.core.idem_begin(scope, key, body)
        if state == "conflict":
            return self.error(409, "conflict", "Idempotency-Key reused with a different request body")
        if state == "in_flight":
            return self.error(409, "conflict", "request with this Idempotency-Key is in flight")
        if state == "replay":
            hdrs = dict(rec.response_headers)
            hdrs["Idempotent-Replayed"] = "true"
            return JSONResponse(rec.response_body, status_code=rec.status_code or 200, headers=hdrs)
        try:
            resp = await handler()
        except Exception:
            self.core.idem_abandon(rec)
            raise
        if isinstance(resp, JSONResponse):
            if resp.status_code >= 500:
                self.core.idem_abandon(rec)
            else:
                self.core.idem_finish(rec, resp.status_code, json.loads(resp.body), {})
        return resp

    # -------------------------------------------------------------- handlers
    async def create(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        body, err = await self._json_body(request)
        if err:
            return err

        async def handler() -> Response:
            if not isinstance(body, dict) or not isinstance(body.get("line_items"), list) or not body["line_items"]:
                return self.error(400, "invalid_request", "line_items must be a non-empty array", path="$.line_items")
            items = []
            for idx, li in enumerate(body["line_items"]):
                item = li.get("item") if isinstance(li, dict) else None
                if not isinstance(item, dict) or "id" not in item:
                    return self.error(400, "invalid_request", "line_items[].item.id is required", path=f"$.line_items[{idx}].item.id")
                items.append({"id": item["id"], "quantity": li.get("quantity", 1)})
            codes = None
            if self.discount_capability and isinstance(body.get("discounts"), dict):
                codes = body["discounts"].get("codes") or []
            try:
                chk = self.core.create_checkout(protocol="ucp", items=items, buyer=body.get("buyer"), currency="USD",
                                                discount_codes=codes)
            except DomainError as exc:
                if exc.code == "out_of_stock":
                    return JSONResponse({"ucp": {"version": UCP_VERSION, "status": "error"},
                                         "messages": [{"type": "error", "code": "out_of_stock", "content": exc.message,
                                                       "severity": "unrecoverable", "path": exc.path}]}, status_code=200)
                return self._domain_error_response(exc, None)
            return JSONResponse(self._view(chk), status_code=201)

        return await self._with_idempotency(request, body, handler)

    async def get(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        try:
            chk = self.core.get_checkout(request.path_params["id"])
        except DomainError as exc:
            return self._domain_error_response(exc, None)
        return JSONResponse(self._view(chk))

    async def update(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        body, err = await self._json_body(request)
        if err:
            return err

        async def handler() -> Response:
            try:
                chk = self.core.get_checkout(request.path_params["id"])
            except DomainError as exc:
                return self._domain_error_response(exc, None)
            items = None
            if isinstance(body.get("line_items"), list):
                items = [{"id": li["item"]["id"], "quantity": li.get("quantity", 1)} for li in body["line_items"]]
            codes = None
            if self.discount_capability and isinstance(body.get("discounts"), dict):
                codes = body["discounts"].get("codes") or []
            try:
                chk = self.core.update_checkout(chk.id, items=items, buyer=body.get("buyer"), discount_codes=codes)
            except DomainError as exc:
                return self._domain_error_response(exc, chk)
            return JSONResponse(self._view(chk))

        return await self._with_idempotency(request, body, handler)

    async def complete(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        body, err = await self._json_body(request)
        if err:
            return err

        async def handler() -> Response:
            try:
                chk = self.core.get_checkout(request.path_params["id"])
            except DomainError as exc:
                return self._domain_error_response(exc, None)
            payment = body.get("payment") if isinstance(body, dict) else None
            instruments = payment.get("instruments") if isinstance(payment, dict) else None
            if not instruments:
                return self.error(400, "invalid_request", "payment.instruments is required", path="$.payment.instruments")
            selected = [i for i in instruments if i.get("selected")] or instruments
            inst = selected[0]
            cred = inst.get("credential") or {}
            token = cred.get("token")
            if inst.get("handler_id") != HANDLER_ID or not token:
                return self.error(400, "invalid_request", "instrument must reference the advertised handler and carry a token",
                                  path="$.payment.instruments[0]")
            if chk.status == "open" and not chk.buyer.get("email"):
                return JSONResponse(self._view(chk), status_code=200)
            try:
                chk, _order = self.core.complete_checkout(chk.id, payment_token=token,
                                                          operation_key=request.headers.get("idempotency-key"))
            except DomainError as exc:
                return self._domain_error_response(exc, chk)
            return JSONResponse(self._view(chk), status_code=200)

        return await self._with_idempotency(request, body, handler)

    async def cancel(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        body, err = await self._json_body(request, required=False)
        if err:
            return err

        async def handler() -> Response:
            try:
                chk = self.core.get_checkout(request.path_params["id"])
                chk = self.core.cancel_checkout(chk.id)
            except DomainError as exc:
                return self._domain_error_response(exc, None)
            return JSONResponse(self._view(chk))

        return await self._with_idempotency(request, body, handler)

    async def profile(self, request: Request) -> Response:
        return JSONResponse(self.profile_document(request))

    def routes(self) -> List[Route]:
        p = self.base_path
        return [
            Route("/.well-known/ucp", self.profile, methods=["GET"]),
            Route(f"{p}/checkout-sessions", self.create, methods=["POST"]),
            Route(f"{p}/checkout-sessions/{{id}}", self.get, methods=["GET"]),
            Route(f"{p}/checkout-sessions/{{id}}", self.update, methods=["PUT"]),
            Route(f"{p}/checkout-sessions/{{id}}/complete", self.complete, methods=["POST"]),
            Route(f"{p}/checkout-sessions/{{id}}/cancel", self.cancel, methods=["POST"]),
        ]
