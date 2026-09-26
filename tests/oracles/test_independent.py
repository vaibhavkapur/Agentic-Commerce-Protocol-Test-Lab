"""Independent oracles do not compare an implementation with itself (plan §15)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from drivers.base import StepOutcome
from lab.assertions.oracles import ORACLES, OracleInconclusive, acp_totals_consistent, ucp_totals_consistent
from lab.assertions.oracles.sdjwt_independent import presentation_hash


def test_acp_totals_oracle_accepts_consistent_discount() -> None:
    step = StepOutcome(
        step_id="s",
        action="get",
        response={
            "status": 200,
            "body": {
                "totals": [
                    {"type": "subtotal", "amount": 2500},
                    {"type": "discount", "amount": 250},
                    {"type": "tax", "amount": 180},
                    {"type": "fulfillment", "amount": 500},
                    {"type": "total", "amount": 2930},
                ]
            },
        },
    )
    ok, _detail = acp_totals_consistent(None, step)
    assert ok is True


def test_ucp_totals_oracle_uses_signed_discount() -> None:
    step = StepOutcome(
        step_id="s",
        action="get",
        response={
            "status": 200,
            "body": {
                "totals": [
                    {"type": "subtotal", "amount": 2500},
                    {"type": "discount", "amount": -250},
                    {"type": "tax", "amount": 180},
                    {"type": "fulfillment", "amount": 500},
                    {"type": "total", "amount": 2930},
                ]
            },
        },
    )
    ok, _detail = ucp_totals_consistent(None, step)
    assert ok is True


def test_totals_oracle_inconclusive_without_required_types() -> None:
    step = StepOutcome(step_id="s", action="get", response={"status": 200, "body": {"totals": [{"type": "tax", "amount": 1}]}})
    with pytest.raises(OracleInconclusive):
        acp_totals_consistent(None, step)


def test_named_oracles_are_registered() -> None:
    assert "tap_signature_independently_valid" in ORACLES
    assert "ap2_checkout_binding_independent" in ORACLES
    assert "ucp_totals_exactly_one_subtotal_and_total" in ORACLES


def test_presentation_hash_is_stable() -> None:
    assert presentation_hash("abc.def.ghi") == presentation_hash("abc.def.ghi")
    assert presentation_hash("abc.def.ghi") != presentation_hash("abc.def.xyz")


def test_tap_oracle_inconclusive_without_signing_record() -> None:
    step = StepOutcome(step_id="s", action="send")
    with pytest.raises(OracleInconclusive):
        ORACLES["tap_signature_independently_valid"](SimpleNamespace(), step)
