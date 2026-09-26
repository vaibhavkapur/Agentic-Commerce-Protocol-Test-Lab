"""HTTP surface for the AP2 roles the local target plays: merchant and credential provider.

These endpoints are lab application endpoints (AP2 v0.2.0 does not define a REST
binding for mandate presentation); the *mandate structures and verification
rules* are the pinned protocol content.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fixtures.issuers.ap2_fixtures import Ap2FixtureBuilder, checkout_hash
from fixtures.issuers.jws import verify_compact
from fixtures.merchants.core import DomainError, MerchantCore

from .ap2_verifier import Ap2Verifier


class Ap2Endpoints:
    def __init__(self, core: MerchantCore, verifier: Ap2Verifier, builder: Ap2FixtureBuilder, merchant: Dict[str, Any]):
        self.core = core
        self.verifier = verifier
        self.builder = builder
        self.merchant = merchant
        self.issued_checkout_jwts: Dict[str, str] = {}

    def checkout_object(self, checkout_id: str) -> Dict[str, Any]:
        chk = self.core.get_checkout(checkout_id)
        t = self.core.totals(chk)
        return {
            "id": chk.id,
            "merchant": self.merchant,
            "status": {"open": "ready_for_complete", "completed": "completed", "canceled": "canceled"}.get(chk.status, chk.status),
            "currency": chk.currency,
            "line_items": [{"id": li.id, "item": {"id": li.sku, "title": li.title, "price": li.unit_amount}, "quantity": li.quantity}
                           for li in chk.line_items],
            "totals": [{"type": "subtotal", "amount": t["subtotal"]}, {"type": "total", "amount": t["total"]}],
        }

    async def issue_checkout_jwt(self, request: Request):
        body = json.loads(await request.body() or b"{}")
        try:
            obj = self.checkout_object(body.get("checkout_id", ""))
        except DomainError as exc:
            return JSONResponse({"error": exc.code, "message": exc.message}, status_code=404)
        jwt = self.builder.merchant_checkout_jwt(obj)
        self.issued_checkout_jwts[obj["id"]] = jwt
        return JSONResponse({"checkout_id": obj["id"], "checkout_jwt": jwt, "checkout_hash": checkout_hash(jwt)})

    async def complete_with_mandate(self, request: Request):
        checkout_id = request.path_params["checkout_id"]
        body = json.loads(await request.body() or b"{}")
        presentation = body.get("checkout_mandate")
        if not isinstance(presentation, str):
            return JSONResponse({"error": "invalid_request", "message": "checkout_mandate presentation required"}, status_code=400)
        receipt, receipt_jwt = self.verifier.verify_checkout_mandate(presentation, expected_checkout_id=checkout_id)
        result: Dict[str, Any] = {"receipt": receipt, "receipt_jwt": receipt_jwt}
        if receipt["status"] == "Success":
            try:
                chk, order = self.core.complete_checkout(checkout_id, payment_token=body.get("payment_token", "tok_ok"),
                                                         operation_key=body.get("operation_key"))
                receipt["order_id"] = order.id
                result["order"] = order.to_dict()
            except DomainError as exc:
                receipt.update({"status": "Error", "error": "invalid_mandate", "error_description": f"completion failed: {exc.message}"})
                receipt.pop("order_id", None)
            result["receipt_jwt"] = self.builder.merchant_checkout_jwt(receipt, signer=self.verifier.merchant_key)
        return JSONResponse(result, status_code=200 if receipt["status"] == "Success" else 403)

    async def authorize_payment(self, request: Request):
        body = json.loads(await request.body() or b"{}")
        pm = body.get("payment_mandate")
        checkout_jwt = body.get("checkout_jwt")
        if not isinstance(pm, str) or not isinstance(checkout_jwt, str):
            return JSONResponse({"error": "invalid_request", "message": "payment_mandate and checkout_jwt required"}, status_code=400)
        try:
            checkout = verify_compact(checkout_jwt, self.verifier.merchant_key.public_jwk, expected_alg="ES256")
        except ValueError as exc:
            return JSONResponse({"error": "invalid_request", "message": f"checkout_jwt not merchant-signed: {exc}"}, status_code=400)
        total = next((t["amount"] for t in checkout.get("totals", []) if t.get("type") == "total"), None)
        receipt, receipt_jwt = self.verifier.verify_payment_mandate(
            pm, expected_transaction_id=checkout_hash(checkout_jwt), expected_amount=total, expected_currency=checkout.get("currency"))
        return JSONResponse({"receipt": receipt, "receipt_jwt": receipt_jwt}, status_code=200 if receipt["status"] == "Success" else 403)

    async def capabilities(self, request: Request):
        return JSONResponse({
            "protocol": "ap2",
            "version": "v0.2.0",
            "roles": ["merchant", "credential_provider"],
            "mandate_types": ["mandate.checkout.1", "mandate.checkout.open.1", "mandate.payment.1", "mandate.payment.open.1"],
            "constraint_types": ["checkout.allowed_merchants", "checkout.line_items", "payment.amount_range", "payment.allowed_payees"],
            "merchant": self.merchant,
            "merchant_public_jwk": self.verifier.merchant_key.public_jwk,
        })

    def routes(self, prefix: str = "/ap2") -> List[Route]:
        return [
            Route(f"{prefix}/capabilities", self.capabilities, methods=["GET"]),
            Route(f"{prefix}/checkouts/sign", self.issue_checkout_jwt, methods=["POST"]),
            Route(f"{prefix}/checkouts/{{checkout_id}}/complete-with-mandate", self.complete_with_mandate, methods=["POST"]),
            Route(f"{prefix}/credential-provider/authorize-payment", self.authorize_payment, methods=["POST"]),
        ]
