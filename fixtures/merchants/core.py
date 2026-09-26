"""Protocol-neutral merchant business fixture (plan §11).

The same core backs the ACP and UCP adapters. It owns inventory, versioned
checkout records, the order store, an idempotency store, and the simulated
payment processor. Behavioural variants are switched by ``options`` so the same
code can act as the deliberately broken and the corrected reference targets.

Variant options (defaults are the corrected behaviour):

* ``idempotency``: ``"strict"`` | ``"ignored"``
* ``complete_order``: ``"check_then_charge"`` | ``"charge_then_check"``
* ``psp_idempotency``: bool — pass a processor-side idempotency key
* ``error_body``: ``"conformant"`` | ``"legacy"``
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from fixtures.payments.simulator import PaymentSimulator

TAX_RATE_BPS = 800  # 8.00 %
SHIPPING_FLAT = 500

DEFAULT_INVENTORY = {
    "sku_tee_blue_m": {"title": "Blue T-Shirt (M)", "unit_amount": 2500, "stock": 20, "digital": False},
    "sku_socks_best": {"title": "The Best Socks", "unit_amount": 900, "stock": 50, "digital": False},
    "sku_shoe_gold_9": {"title": "Limited Gold Sneaker (W9)", "unit_amount": 19900, "stock": 1, "digital": False},
    "sku_ebook_guide": {"title": "Agentic Commerce Field Guide (ebook)", "unit_amount": 1200, "stock": 9999, "digital": True},
}

DEFAULT_DISCOUNTS = {
    "SAVE10": {"title": "10% off", "percent_off": 10},
    "FLAT500": {"title": "500 off", "amount_off": 500},
}


class DomainError(Exception):
    """Business-level failure. ``code`` is protocol-neutral; adapters map it to native shapes."""

    def __init__(self, code: str, message: str, path: Optional[str] = None, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.http_status = http_status


@dataclass
class LineItem:
    id: str
    sku: str
    quantity: int
    unit_amount: int
    title: str
    digital: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Checkout:
    id: str
    protocol: str
    currency: str
    line_items: List[LineItem]
    buyer: Dict[str, Any]
    status: str  # open | completed | canceled | expired
    version: int
    created_at: str
    updated_at: str
    expires_at: str
    discount_codes: List[str] = field(default_factory=list)
    order_id: Optional[str] = None
    completion_operation_key: Optional[str] = None
    payment_ids: List[str] = field(default_factory=list)
    fulfillment_option_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["line_items"] = [li.to_dict() for li in self.line_items]
        return d


@dataclass
class Order:
    id: str
    checkout_id: str
    operation_key: Optional[str]
    total: int
    currency: str
    payment_id: str
    created_at: str
    line_items: List[Dict[str, Any]]
    status: str = "confirmed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IdempotencyRecord:
    scope: str
    key: str
    body_digest: str
    state: str  # in_flight | done
    status_code: Optional[int] = None
    response_body: Any = None
    response_headers: Dict[str, str] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def body_digest(body: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class MerchantCore:
    def __init__(self, clock, rng, payments: PaymentSimulator, options: Optional[Dict[str, Any]] = None):
        self.clock = clock
        self.rng = rng
        self.payments = payments
        self.options = {
            "idempotency": "strict",
            "complete_order": "check_then_charge",
            "psp_idempotency": True,
            "error_body": "conformant",
        }
        self.options.update(options or {})
        self.inventory: Dict[str, Dict[str, Any]] = {}
        self.discounts: Dict[str, Dict[str, Any]] = {}
        self.checkouts: Dict[str, Checkout] = {}
        self.orders: Dict[str, Order] = {}
        self.idempotency: Dict[Tuple[str, str], IdempotencyRecord] = {}
        self.journal: List[Dict[str, Any]] = []
        self.merchant_id = "merchant_lab_1"
        self.merchant_name = "Lab Demo Merchant"
        self.merchant_website = "https://merchant.lab.test"
        self.reset()

    # ------------------------------------------------------------------ admin
    def reset(self) -> None:
        self.inventory = {k: dict(v) for k, v in DEFAULT_INVENTORY.items()}
        self.discounts = {k: dict(v) for k, v in DEFAULT_DISCOUNTS.items()}
        self.checkouts.clear()
        self.orders.clear()
        self.idempotency.clear()
        self.journal.clear()
        self.payments.reset()
        self._log("fixture_reset")

    def set_inventory(self, sku: str, *, unit_amount: Optional[int] = None, stock: Optional[int] = None) -> Dict[str, Any]:
        if sku not in self.inventory:
            raise DomainError("not_found", f"unknown sku {sku}", http_status=404)
        if unit_amount is not None:
            self.inventory[sku]["unit_amount"] = unit_amount
        if stock is not None:
            self.inventory[sku]["stock"] = stock
        self._log("inventory_changed", sku=sku, unit_amount=unit_amount, stock=stock)
        return dict(self.inventory[sku])

    def _log(self, kind: str, **extra) -> None:
        ev = {"type": kind, "at": self.clock.iso(), "seq": len(self.journal) + 1}
        ev.update(extra)
        self.journal.append(ev)

    # ------------------------------------------------------------- checkouts
    def _build_line_items(self, items: List[Dict[str, Any]], existing: Optional[List[LineItem]] = None) -> List[LineItem]:
        out: List[LineItem] = []
        for idx, it in enumerate(items):
            sku = it.get("id")
            qty = it.get("quantity", 1)
            if not isinstance(sku, str) or sku not in self.inventory:
                raise DomainError("not_found", f"unknown item {sku!r}", path=f"$.line_items[{idx}].id", http_status=404)
            if not isinstance(qty, int) or qty < 1:
                raise DomainError("invalid", "quantity must be a positive integer", path=f"$.line_items[{idx}].quantity")
            inv = self.inventory[sku]
            if qty > inv["stock"]:
                raise DomainError("out_of_stock", f"only {inv['stock']} of {sku} available", path=f"$.line_items[{idx}]", http_status=200)
            li_id = None
            if existing:
                for old in existing:
                    if old.sku == sku:
                        li_id = old.id
            out.append(LineItem(id=li_id or self.rng.token("li", 4), sku=sku, quantity=qty,
                                unit_amount=inv["unit_amount"], title=inv["title"], digital=inv["digital"]))
        return out

    def create_checkout(self, *, protocol: str, items: List[Dict[str, Any]], buyer: Optional[Dict[str, Any]] = None,
                        currency: str = "USD", discount_codes: Optional[List[str]] = None) -> Checkout:
        if not items:
            raise DomainError("invalid", "at least one item is required", path="$.line_items")
        line_items = self._build_line_items(items)
        now = self.clock.iso()
        chk = Checkout(
            id=self.rng.token("chk", 6),
            protocol=protocol,
            currency=currency.upper(),
            line_items=line_items,
            buyer=dict(buyer or {}),
            status="open",
            version=1,
            created_at=now,
            updated_at=now,
            expires_at=self._iso_plus(6 * 3600),
            discount_codes=list(discount_codes or []),
        )
        self.checkouts[chk.id] = chk
        self._log("checkout_created", checkout_id=chk.id, protocol=protocol)
        return chk

    def _iso_plus(self, seconds: int) -> str:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(self.clock.now() + seconds, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def get_checkout(self, checkout_id: str) -> Checkout:
        chk = self.checkouts.get(checkout_id)
        if chk is None:
            raise DomainError("not_found", f"unknown checkout session {checkout_id}", http_status=404)
        return chk

    def update_checkout(self, checkout_id: str, *, items: Optional[List[Dict[str, Any]]] = None,
                        buyer: Optional[Dict[str, Any]] = None, discount_codes: Optional[List[str]] = None,
                        fulfillment_option_id: Optional[str] = None) -> Checkout:
        chk = self.get_checkout(checkout_id)
        if chk.status != "open":
            raise DomainError("conflict", f"checkout session is {chk.status}", http_status=409)
        if items is not None:
            chk.line_items = self._build_line_items(items, existing=chk.line_items)
        if buyer is not None:
            chk.buyer.update(buyer)
        if discount_codes is not None:
            chk.discount_codes = list(discount_codes)
        if fulfillment_option_id is not None:
            chk.fulfillment_option_id = fulfillment_option_id
        chk.version += 1
        chk.updated_at = self.clock.iso()
        self._log("checkout_updated", checkout_id=chk.id, version=chk.version)
        return chk

    def cancel_checkout(self, checkout_id: str) -> Checkout:
        chk = self.get_checkout(checkout_id)
        if chk.status in ("completed", "canceled"):
            raise DomainError("conflict", f"checkout session already {chk.status}", http_status=409)
        chk.status = "canceled"
        chk.version += 1
        chk.updated_at = self.clock.iso()
        self._log("checkout_canceled", checkout_id=chk.id)
        return chk

    # ---------------------------------------------------------------- totals
    def totals(self, chk: Checkout) -> Dict[str, Any]:
        subtotal = sum(li.unit_amount * li.quantity for li in chk.line_items)
        applied: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        discount = 0
        for code in chk.discount_codes:
            spec = self.discounts.get(code.upper())
            if spec is None:
                rejected.append({"code": code, "reason": "discount_code_invalid"})
                continue
            amt = spec.get("amount_off") or (subtotal * spec["percent_off"]) // 100
            amt = min(amt, subtotal - discount)
            discount += amt
            applied.append({"code": code.upper(), "title": spec["title"], "amount": amt})
        physical = any(not li.digital for li in chk.line_items)
        fulfillment = SHIPPING_FLAT if physical else 0
        taxable = subtotal - discount
        tax = (taxable * TAX_RATE_BPS) // 10000
        total = taxable + tax + fulfillment
        return {
            "subtotal": subtotal,
            "discount": discount,
            "tax": tax,
            "fulfillment": fulfillment,
            "total": total,
            "applied_discounts": applied,
            "rejected_discounts": rejected,
            "physical": physical,
        }

    # ------------------------------------------------------------ completion
    def complete_checkout(self, checkout_id: str, *, payment_token: str, operation_key: Optional[str]) -> Tuple[Checkout, Order]:
        chk = self.get_checkout(checkout_id)
        mode = self.options["complete_order"]
        if mode == "check_then_charge":
            if chk.status == "completed" and chk.order_id:
                return chk, self.orders[chk.order_id]
            if chk.status != "open":
                raise DomainError("conflict", f"checkout session is {chk.status}", http_status=409)
            self._check_stock(chk)
            payment = self._charge(chk, payment_token, operation_key)
            return self._finalize(chk, payment, operation_key)
        # Deliberately broken ordering: charge first, then notice the session was already completed.
        if chk.status == "canceled":
            raise DomainError("conflict", "checkout session is canceled", http_status=409)
        payment = self._charge(chk, payment_token, operation_key)
        if chk.status == "completed" and chk.order_id:
            self._log("duplicate_charge_after_completion", checkout_id=chk.id, payment_id=payment.id)
            return chk, self.orders[chk.order_id]
        self._check_stock(chk)
        return self._finalize(chk, payment, operation_key)

    def _check_stock(self, chk: Checkout) -> None:
        for li in chk.line_items:
            inv = self.inventory[li.sku]
            if li.quantity > inv["stock"]:
                raise DomainError("out_of_stock", f"{li.sku} is out of stock", path="$.line_items", http_status=200)
            if inv["unit_amount"] != li.unit_amount:
                raise DomainError("price_changed", f"price of {li.sku} changed from {li.unit_amount} to {inv['unit_amount']}",
                                  path="$.line_items", http_status=200)

    def _charge(self, chk: Checkout, token: str, operation_key: Optional[str]):
        t = self.totals(chk)
        psp_key = f"{chk.id}:complete" if self.options["psp_idempotency"] else None
        payment = self.payments.submit_charge(amount=t["total"], currency=chk.currency, token=token, checkout_id=chk.id,
                                              operation_key=operation_key, processor_idempotency_key=psp_key)
        if payment.id not in chk.payment_ids:
            chk.payment_ids.append(payment.id)
        self._log("charge_submitted", checkout_id=chk.id, payment_id=payment.id, state=payment.state)
        if payment.state == "failed":
            raise DomainError("payment_declined", "the payment was declined", path="$.payment_data", http_status=200)
        if payment.state == "unknown":
            raise DomainError("payment_unknown", "payment outcome unknown; do not retry blindly", path="$.payment_data", http_status=503)
        return payment

    def _finalize(self, chk: Checkout, payment, operation_key: Optional[str]) -> Tuple[Checkout, Order]:
        t = self.totals(chk)
        for li in chk.line_items:
            self.inventory[li.sku]["stock"] -= li.quantity
        order = Order(
            id=self.rng.token("ord", 6),
            checkout_id=chk.id,
            operation_key=operation_key,
            total=t["total"],
            currency=chk.currency,
            payment_id=payment.id,
            created_at=self.clock.iso(),
            line_items=[li.to_dict() for li in chk.line_items],
        )
        self.orders[order.id] = order
        chk.status = "completed"
        chk.order_id = order.id
        chk.completion_operation_key = operation_key
        chk.version += 1
        chk.updated_at = self.clock.iso()
        self._log("order_created", checkout_id=chk.id, order_id=order.id, payment_id=payment.id)
        return chk, order

    # ----------------------------------------------------------- idempotency
    def idem_begin(self, scope: str, key: str, body: Any) -> Tuple[str, IdempotencyRecord]:
        digest = body_digest(body)
        rec = self.idempotency.get((scope, key))
        if rec is None:
            rec = IdempotencyRecord(scope=scope, key=key, body_digest=digest, state="in_flight", created_at=self.clock.iso())
            self.idempotency[(scope, key)] = rec
            return "new", rec
        if rec.body_digest != digest:
            return "conflict", rec
        if rec.state == "in_flight":
            return "in_flight", rec
        return "replay", rec

    def idem_finish(self, rec: IdempotencyRecord, status_code: int, body: Any, headers: Optional[Dict[str, str]] = None) -> None:
        rec.state = "done"
        rec.status_code = status_code
        rec.response_body = body
        rec.response_headers = dict(headers or {})

    def idem_abandon(self, rec: IdempotencyRecord) -> None:
        self.idempotency.pop((rec.scope, rec.key), None)

    # ----------------------------------------------------------- observation
    def orders_for_checkout(self, checkout_id: str) -> List[Order]:
        return [o for o in self.orders.values() if o.checkout_id == checkout_id]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "inventory": self.inventory,
            "checkouts": {k: v.to_dict() for k, v in self.checkouts.items()},
            "orders": {k: v.to_dict() for k, v in self.orders.items()},
            "idempotency": [r.to_dict() for r in self.idempotency.values()],
            "journal": list(self.journal),
            "options": dict(self.options),
        }
