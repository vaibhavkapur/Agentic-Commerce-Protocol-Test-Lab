"""Simulated payment processor with durable event records (plan §11).

States are explicit: ``authorized``, ``submitted``, ``settled``, ``failed``,
``unknown``. The simulator never exposes a single boolean success flag; the
assertion layer counts settled charges by inspecting the journal, which is an
oracle independent of anything the merchant adapter returns to the client.

Deterministic behaviour is keyed on the synthetic credential token:

* ``tok_ok``       → submitted then settled
* ``tok_decline``  → failed (issuer decline)
* ``tok_unknown``  → submitted, provider response lost, state stays ``unknown``
* ``tok_slow``     → authorized only; settles when ``settle_pending()`` is called
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class PaymentRecord:
    id: str
    provider_reference: str
    amount: int
    currency: str
    state: str
    checkout_id: str
    operation_key: Optional[str]
    instrument_token_digest: str
    created_at: str
    updated_at: str
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PaymentSimulator:
    def __init__(self, clock, rng):
        self.clock = clock
        self.rng = rng
        self.payments: Dict[str, PaymentRecord] = {}
        self.journal: List[Dict[str, Any]] = []
        self.idempotent_submissions: Dict[str, str] = {}

    def reset(self) -> None:
        self.payments.clear()
        self.journal.clear()
        self.idempotent_submissions.clear()

    def _event(self, payment: PaymentRecord, kind: str, **extra) -> None:
        ev = {"payment_id": payment.id, "type": kind, "at": self.clock.iso(), "state": payment.state,
              "provider_reference": payment.provider_reference}
        ev.update(extra)
        payment.events.append(ev)
        payment.updated_at = self.clock.iso()
        self.journal.append(ev)

    def submit_charge(self, *, amount: int, currency: str, token: str, checkout_id: str,
                      operation_key: Optional[str] = None, processor_idempotency_key: Optional[str] = None) -> PaymentRecord:
        """Submit a charge. ``processor_idempotency_key`` models a PSP-side idempotency key;
        when the merchant supplies one, a repeated submission returns the existing payment."""
        import hashlib

        if processor_idempotency_key and processor_idempotency_key in self.idempotent_submissions:
            return self.payments[self.idempotent_submissions[processor_idempotency_key]]
        pid = self.rng.token("pay", 6)
        rec = PaymentRecord(
            id=pid,
            provider_reference=self.rng.token("psp", 8),
            amount=amount,
            currency=currency,
            state="authorized",
            checkout_id=checkout_id,
            operation_key=operation_key,
            instrument_token_digest="sha256:" + hashlib.sha256(token.encode()).hexdigest()[:16],
            created_at=self.clock.iso(),
            updated_at=self.clock.iso(),
        )
        self.payments[pid] = rec
        if processor_idempotency_key:
            self.idempotent_submissions[processor_idempotency_key] = pid
        self._event(rec, "authorized")
        if token == "tok_decline":
            rec.state = "failed"
            self._event(rec, "failed", reason="issuer_declined")
            return rec
        if token == "tok_slow":
            return rec
        rec.state = "submitted"
        self._event(rec, "submitted")
        if token == "tok_unknown":
            rec.state = "unknown"
            self._event(rec, "provider_response_lost")
            return rec
        rec.state = "settled"
        self._event(rec, "settled")
        return rec

    def settle_pending(self) -> List[str]:
        settled = []
        for rec in self.payments.values():
            if rec.state in ("authorized", "submitted"):
                rec.state = "settled"
                self._event(rec, "settled")
                settled.append(rec.id)
        return settled

    def resolve_unknown(self, payment_id: str, outcome: str) -> None:
        rec = self.payments[payment_id]
        if rec.state != "unknown":
            raise ValueError("payment is not in unknown state")
        rec.state = outcome
        self._event(rec, "reconciled", outcome=outcome)

    def for_checkout(self, checkout_id: str) -> List[PaymentRecord]:
        return [p for p in self.payments.values() if p.checkout_id == checkout_id]

    def snapshot(self) -> Dict[str, Any]:
        return {"payments": [p.to_dict() for p in self.payments.values()], "journal": list(self.journal)}
