"""ACP (Agentic Commerce Protocol) seller adapter over the shared merchant core.

Pinned to the released ``2026-04-17`` snapshot of
``spec/2026-04-17/openapi/openapi.agentic_checkout.yaml`` and
``json-schema/schema.agentic_checkout.json``. Native statuses and shapes are
preserved; nothing here is mapped through a universal checkout status.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .core import Checkout, DomainError, MerchantCore

ACP_VERSION = "2026-04-17"
SUPPORTED_VERSIONS = ["2026-04-17"]
API_KEY = "lab_acp_api_key_synthetic"

DISCOUNT_EXTENSION = {
    "name": "discount",
    "extends": ["$.CheckoutSession.discounts", "$.CheckoutSessionCreateRequest.discounts", "$.CheckoutSessionUpdateRequest.discounts"],
    "spec": "https://github.com/agentic-commerce-protocol/agentic-commerce-protocol/blob/main/spec/2026-04-17/json-schema/schema.discount.json",
    "schema": "https://raw.githubusercontent.com/agentic-commerce-protocol/agentic-commerce-protocol/main/spec/2026-04-17/json-schema/schema.discount.json",
}

PAYMENT_HANDLER = {
    "id": "handler_lab_sim_01",
    "name": "dev.commerce-lab.simulated.card",
    "display_name": "Lab Simulated Card",
    "version": "2026-04-17",
    "spec": "https://commerce-lab.local/handlers/simulated-card",
    "requires_delegate_payment": False,
    "requires_pci_compliance": False,
    "psp": "lab_payment_simulator",
    "config_schema": "https://commerce-lab.local/handlers/simulated-card/config.json",
    "instrument_schemas": ["https://commerce-lab.local/handlers/simulated-card/instrument.json"],
    "config": {"mode": "simulated"},
}


class AcpAdapter:
    def __init__(self, core: MerchantCore, *, discount_extension: bool = True, base_path: str = "/acp"):
        self.core = core
        self.discount_extension = discount_extension
        self.base_path = base_path

    # ------------------------------------------------------------- discovery
    def discovery_document(self, request: Request) -> Dict[str, Any]:
        host = request.headers.get("host", "target.lab")
        scheme = request.url.scheme or "http"
        doc: Dict[str, Any] = {
            "protocol": {"name": "acp", "version": ACP_VERSION, "supported_versions": SUPPORTED_VERSIONS,
                         "documentation_url": "https://developers.openai.com/commerce"},
            "api_base_url": f"{scheme}://{host}{self.base_path}",
            "transports": ["rest"],
            "capabilities": {"services": ["checkout"], "supported_currencies": ["usd"], "supported_locales": ["en-US"]},
        }
        if self.discount_extension:
            doc["capabilities"]["extensions"] = [{"name": "discount", "spec": DISCOUNT_EXTENSION["spec"], "schema": DISCOUNT_EXTENSION["schema"]}]
        return doc

    # ---------------------------------------------------------------- errors
    def error(self, status: int, type_: str, code: str, message: str, param: Optional[str] = None,
              extra: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> JSONResponse:
        if self.core.options.get("error_body") == "legacy":
            body: Dict[str, Any] = {"error": message, "reason": code}
        else:
            body = {"type": type_, "code": code, "message": message}
            if param:
                body["param"] = param
            if extra:
                body.update(extra)
        return JSONResponse(body, status_code=status, headers=headers)

    # --------------------------------------------------------------- helpers
    def _authenticate(self, request: Request) -> Optional[JSONResponse]:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth.split(" ", 1)[1].strip() != API_KEY:
            return self.error(401, "invalid_request", "unauthorized", "missing or invalid bearer token")
        version = request.headers.get("api-version")
        if not version:
            return self.error(400, "invalid_request", "api_version_required", "API-Version header is required",
                              extra={"supported_versions": SUPPORTED_VERSIONS})
        if version not in SUPPORTED_VERSIONS:
            return self.error(400, "invalid_request", "api_version_unsupported", f"API-Version {version} is not supported",
                              extra={"supported_versions": SUPPORTED_VERSIONS})
        return None

    async def _json_body(self, request: Request, required: bool = True) -> Tuple[Any, Optional[JSONResponse]]:
        raw = await request.body()
        if not raw:
            if required:
                return None, self.error(400, "invalid_request", "invalid_json", "request body is required")
            return {}, None
        try:
            return json.loads(raw), None
        except json.JSONDecodeError:
            return None, self.error(400, "invalid_request", "invalid_json", "request body is not valid JSON")

    def _session_view(self, chk: Checkout, *, with_order: bool = False, messages: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        t = self.core.totals(chk)
        line_items = []
        for li in chk.line_items:
            base = li.unit_amount * li.quantity
            line_items.append({
                "id": li.id,
                "item": {"id": li.sku, "name": li.title, "unit_amount": li.unit_amount},
                "quantity": li.quantity,
                "name": li.title,
                "unit_amount": li.unit_amount,
                "sku": li.sku,
                "availability_status": "in_stock",
                "totals": [
                    {"type": "items_base_amount", "display_text": "Items", "amount": base},
                    {"type": "subtotal", "display_text": "Subtotal", "amount": base},
                    {"type": "total", "display_text": "Total", "amount": base},
                ],
            })
        totals = [
            {"type": "items_base_amount", "display_text": "Items", "amount": t["subtotal"]},
            {"type": "subtotal", "display_text": "Subtotal", "amount": t["subtotal"]},
        ]
        if t["discount"]:
            totals.append({"type": "discount", "display_text": "Discount", "amount": t["discount"]})
        totals.extend([
            {"type": "fulfillment", "display_text": "Shipping", "amount": t["fulfillment"]},
            {"type": "tax", "display_text": "Tax", "amount": t["tax"]},
            {"type": "total", "display_text": "Total", "amount": t["total"]},
        ])
        msgs = list(messages or [])
        if chk.status == "open":
            if not chk.buyer.get("email"):
                status = "not_ready_for_payment"
                msgs.append({"type": "error", "code": "missing", "param": "$.buyer.email", "content_type": "plain",
                             "content": "Buyer email is required", "resolution": "recoverable"})
            else:
                status = "ready_for_payment"
        elif chk.status == "completed":
            status = "completed"
        elif chk.status == "canceled":
            status = "canceled"
        else:
            status = "expired"
        fulfillment_options = []
        if t["physical"]:
            fulfillment_options.append({
                "type": "shipping", "id": "ship_standard", "title": "Standard Shipping", "carrier": "LabPost",
                "totals": [{"type": "fulfillment", "display_text": "Shipping", "amount": t["fulfillment"]},
                           {"type": "total", "display_text": "Total", "amount": t["fulfillment"]}],
            })
        else:
            fulfillment_options.append({"type": "digital", "id": "digital_instant", "title": "Instant download",
                                        "totals": [{"type": "total", "display_text": "Total", "amount": 0}]})
        capabilities: Dict[str, Any] = {
            "payment": {"handlers": [PAYMENT_HANDLER]},
            "interventions": {"supported": []},
        }
        if self.discount_extension:
            capabilities["extensions"] = [DISCOUNT_EXTENSION]
        view: Dict[str, Any] = {
            "id": chk.id,
            "protocol": {"version": ACP_VERSION},
            "capabilities": capabilities,
            "status": status,
            "currency": chk.currency.lower(),
            "line_items": line_items,
            "fulfillment_options": fulfillment_options,
            "selected_fulfillment_options": [{"type": fulfillment_options[0]["type"], "option_id": fulfillment_options[0]["id"],
                                              "item_ids": [li["id"] for li in line_items]}],
            "totals": totals,
            "messages": msgs,
            "links": [
                {"type": "terms_of_use", "url": "https://merchant.lab.test/terms"},
                {"type": "privacy_policy", "url": "https://merchant.lab.test/privacy"},
            ],
        }
        if chk.buyer:
            view["buyer"] = {k: v for k, v in chk.buyer.items() if k in ("first_name", "last_name", "email", "phone_number")}
        if self.discount_extension and (chk.discount_codes or t["applied_discounts"]):
            view["discounts"] = {
                "codes": list(chk.discount_codes),
                "applied": [{"id": f"disc_{a['code'].lower()}", "code": a["code"], "amount": a["amount"],
                             "coupon": {"id": a["code"], "name": a["title"]}} for a in t["applied_discounts"]],
                "rejected": [{"code": r["code"], "reason": r["reason"]} for r in t["rejected_discounts"]],
            }
        if with_order and chk.order_id:
            order = self.core.orders[chk.order_id]
            view["order"] = {
                "id": order.id,
                "checkout_session_id": chk.id,
                "permalink_url": f"https://merchant.lab.test/orders/{order.id}",
                "status": "confirmed",
                "totals": [{"type": "total", "display_text": "Total", "amount": order.total}],
            }
        return view

    def _domain_error_response(self, exc: DomainError, chk: Optional[Checkout]) -> JSONResponse:
        """Business-outcome errors return a valid session with MessageError; protocol errors return Error."""
        if chk is not None and exc.code in ("out_of_stock", "payment_declined", "price_changed", "invalid"):
            code = {"price_changed": "invalid", "invalid": "invalid"}.get(exc.code, exc.code)
            msg = {"type": "error", "code": code, "param": exc.path or "$", "content_type": "plain", "content": exc.message,
                   "resolution": "recoverable" if exc.code != "payment_declined" else "requires_buyer_input"}
            return JSONResponse(self._session_view(chk, messages=[msg]), status_code=200)
        status = exc.http_status if exc.http_status >= 400 else 400
        if exc.code == "conflict":
            return self.error(409, "invalid_request", "conflict", exc.message, exc.path)
        if exc.code == "not_found":
            return self.error(404, "invalid_request", "not_found", exc.message, exc.path)
        if exc.code == "payment_unknown":
            return self.error(503, "service_unavailable", "payment_unknown", exc.message, exc.path)
        return self.error(status, "invalid_request", exc.code, exc.message, exc.path)

    def _aggregate_items(self, raw_items: List[Any]) -> Tuple[List[Dict[str, Any]], Optional[JSONResponse]]:
        """ACP 2026-04-17 ``Item`` has no quantity field (``additionalProperties: false``).

        Lab decision (profile ``acp-checkout-2026-04-17``): one ``Item`` entry per unit;
        repeated ids aggregate into a single line item with that quantity.
        """
        counts: Dict[str, int] = {}
        order: List[str] = []
        for idx, it in enumerate(raw_items):
            if not isinstance(it, dict) or "id" not in it:
                return [], self.error(400, "invalid_request", "invalid", "line_items[] entries must be Item objects with an id",
                                      param=f"$.line_items[{idx}]")
            unknown = set(it.keys()) - {"id", "name", "unit_amount"}
            if unknown:
                return [], self.error(400, "invalid_request", "invalid", f"unknown Item field(s): {', '.join(sorted(unknown))}",
                                      param=f"$.line_items[{idx}].{sorted(unknown)[0]}")
            if it["id"] not in counts:
                order.append(it["id"])
            counts[it["id"]] = counts.get(it["id"], 0) + 1
        return [{"id": sku, "quantity": counts[sku]} for sku in order], None

    # ----------------------------------------------------------- idempotency
    async def _with_idempotency(self, request: Request, body: Any, handler) -> Response:
        """Wrap a state-changing handler with Idempotency-Key semantics (strict variant)."""
        key = request.headers.get("idempotency-key")
        if self.core.options.get("idempotency") == "ignored":
            return await handler()
        if not key:
            return self.error(400, "invalid_request", "idempotency_key_required", "Idempotency-Key header is required")
        if len(key) > 255:
            return self.error(400, "invalid_request", "idempotency_key_invalid", "Idempotency-Key exceeds 255 characters")
        scope = f"{request.headers.get('authorization', '')}|{request.method} {request.url.path}"
        state, rec = self.core.idem_begin(scope, key, body)
        if state == "conflict":
            return self.error(422, "invalid_request", "idempotency_conflict",
                              "Idempotency-Key has already been used with a different request body")
        if state == "in_flight":
            return self.error(409, "invalid_request", "idempotency_in_flight",
                              "A request with this Idempotency-Key is currently being processed", headers={"Retry-After": "1"})
        if state == "replay":
            hdrs = dict(rec.response_headers)
            hdrs["Idempotent-Replayed"] = "true"
            hdrs["Idempotency-Key"] = key
            return JSONResponse(rec.response_body, status_code=rec.status_code or 200, headers=hdrs)
        try:
            resp = await handler()
        except Exception:
            self.core.idem_abandon(rec)
            raise
        if isinstance(resp, JSONResponse):
            payload = json.loads(resp.body)
            if resp.status_code >= 500:
                self.core.idem_abandon(rec)
            else:
                self.core.idem_finish(rec, resp.status_code, payload, {"Request-Id": request.headers.get("request-id", "")})
            resp.headers["Idempotency-Key"] = key
        return resp

    # -------------------------------------------------------------- handlers
    async def create_session(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        body, err = await self._json_body(request)
        if err:
            return err

        async def handler() -> Response:
            if not isinstance(body, dict):
                return self.error(400, "invalid_request", "invalid_json", "body must be an object")
            missing = [k for k in ("line_items", "currency", "capabilities") if k not in body]
            if missing:
                return self.error(400, "invalid_request", "missing", f"missing required field(s): {', '.join(missing)}",
                                  param=f"$.{missing[0]}")
            if not isinstance(body["line_items"], list) or not body["line_items"]:
                return self.error(400, "invalid_request", "invalid", "line_items must be a non-empty array", param="$.line_items")
            if body["currency"].lower() != "usd":
                return self.error(400, "invalid_request", "unsupported", "only usd is supported", param="$.currency")
            items, item_err = self._aggregate_items(body["line_items"])
            if item_err:
                return item_err
            codes = None
            if self.discount_extension and isinstance(body.get("discounts"), dict):
                codes = body["discounts"].get("codes") or []
            try:
                chk = self.core.create_checkout(protocol="acp", items=items, buyer=body.get("buyer"),
                                                currency=body["currency"], discount_codes=codes)
            except DomainError as exc:
                return self._domain_error_response(exc, None)
            return JSONResponse(self._session_view(chk), status_code=201)

        return await self._with_idempotency(request, body, handler)

    async def get_session(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        try:
            chk = self.core.get_checkout(request.path_params["checkout_session_id"])
        except DomainError as exc:
            return self._domain_error_response(exc, None)
        return JSONResponse(self._session_view(chk, with_order=chk.status == "completed"))

    async def update_session(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        body, err = await self._json_body(request)
        if err:
            return err

        async def handler() -> Response:
            try:
                chk = self.core.get_checkout(request.path_params["checkout_session_id"])
            except DomainError as exc:
                return self._domain_error_response(exc, None)
            codes = None
            if self.discount_extension and isinstance(body.get("discounts"), dict):
                codes = body["discounts"].get("codes") or []
            fo = None
            sel = body.get("selected_fulfillment_options")
            if isinstance(sel, list) and sel:
                fo = sel[0].get("option_id")
            items = None
            if isinstance(body.get("line_items"), list):
                items, item_err = self._aggregate_items(body["line_items"])
                if item_err:
                    return item_err
            try:
                chk = self.core.update_checkout(chk.id, items=items, buyer=body.get("buyer"),
                                                discount_codes=codes, fulfillment_option_id=fo)
            except DomainError as exc:
                return self._domain_error_response(exc, chk)
            return JSONResponse(self._session_view(chk))

        return await self._with_idempotency(request, body, handler)

    async def complete_session(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        body, err = await self._json_body(request)
        if err:
            return err

        async def handler() -> Response:
            try:
                chk = self.core.get_checkout(request.path_params["checkout_session_id"])
            except DomainError as exc:
                return self._domain_error_response(exc, None)
            pd = body.get("payment_data") if isinstance(body, dict) else None
            if not isinstance(pd, dict):
                return self.error(400, "invalid_request", "missing", "payment_data is required", param="$.payment_data")
            instrument = pd.get("instrument") or {}
            credential = instrument.get("credential") or {}
            token = credential.get("token")
            if pd.get("handler_id") != PAYMENT_HANDLER["id"] or not token:
                return self.error(400, "invalid_request", "invalid", "payment_data.handler_id/instrument.credential.token required",
                                  param="$.payment_data")
            if body.get("buyer"):
                try:
                    self.core.update_checkout(chk.id, buyer=body["buyer"])
                except DomainError:
                    pass
            if chk.status == "open" and not chk.buyer.get("email"):
                return JSONResponse(self._session_view(chk), status_code=200)
            try:
                chk, order = self.core.complete_checkout(chk.id, payment_token=token,
                                                         operation_key=request.headers.get("idempotency-key"))
            except DomainError as exc:
                return self._domain_error_response(exc, chk)
            return JSONResponse(self._session_view(chk, with_order=True), status_code=200)

        return await self._with_idempotency(request, body, handler)

    async def cancel_session(self, request: Request) -> Response:
        err = self._authenticate(request)
        if err:
            return err
        body, err = await self._json_body(request, required=False)
        if err:
            return err

        async def handler() -> Response:
            try:
                chk = self.core.get_checkout(request.path_params["checkout_session_id"])
                chk = self.core.cancel_checkout(chk.id)
            except DomainError as exc:
                return self._domain_error_response(exc, None)
            return JSONResponse(self._session_view(chk))

        return await self._with_idempotency(request, body, handler)

    async def discovery(self, request: Request) -> Response:
        return JSONResponse(self.discovery_document(request))

    def routes(self) -> List[Route]:
        p = self.base_path
        return [
            Route("/.well-known/acp.json", self.discovery, methods=["GET"]),
            Route(f"{p}/checkout_sessions", self.create_session, methods=["POST"]),
            Route(f"{p}/checkout_sessions/{{checkout_session_id}}", self.get_session, methods=["GET"]),
            Route(f"{p}/checkout_sessions/{{checkout_session_id}}", self.update_session, methods=["POST"]),
            Route(f"{p}/checkout_sessions/{{checkout_session_id}}/complete", self.complete_session, methods=["POST"]),
            Route(f"{p}/checkout_sessions/{{checkout_session_id}}/cancel", self.cancel_session, methods=["POST"]),
        ]
