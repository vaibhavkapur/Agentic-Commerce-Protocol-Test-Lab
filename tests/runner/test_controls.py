"""Negative controls fail and corrected controls pass (plan §23, Demo A–C)."""

from __future__ import annotations

from tests.conftest import run_case, run_suite

from lab.runner.results import Result


def test_broken_target_double_charges_after_lost_response() -> None:
    bundle = run_case(
        "local-merchant-broken",
        "acp-complete-response-lost-replay",
        run_id="ctrl-broken",
    )
    case = bundle.cases[0]
    assert case.result == Result.FAIL
    assert "settled_charge_count_for_operation" in case.reason
    observed = next(
        a.observed for a in case.assertions if a.description == "metric settled_charge_count_for_operation"
    )
    assert observed == 2


def test_corrected_target_recovers_with_one_order_and_one_charge() -> None:
    bundle = run_case(
        "local-merchant-corrected",
        "acp-complete-response-lost-replay",
        run_id="ctrl-corrected",
    )
    case = bundle.cases[0]
    assert case.result == Result.PASS
    by_desc = {a.description: a.observed for a in case.assertions}
    assert by_desc["metric merchant_order_count_for_operation"] == 1
    assert by_desc["metric settled_charge_count_for_operation"] == 1
    assert by_desc["metric client_resolved_order_matches_merchant"] is True


def test_identity_authenticates_but_wrong_checkout_is_rejected() -> None:
    bundle = run_suite("local-merchant-corrected", "demo-identity-vs-authority", run_id="ctrl-id")
    by_id = {c.case_id: c for c in bundle.cases}
    assert by_id["tap-valid-request-accepted"].result == Result.PASS
    assert by_id["ap2-mandate-bound-to-other-checkout-rejected"].result == Result.PASS
    assert all(c.result == Result.PASS for c in bundle.cases)


def test_broken_target_trusts_spoofed_forwarded_host() -> None:
    bundle = run_case(
        "local-merchant-broken",
        "tap-forwarded-host-spoof-rejected",
        run_id="ctrl-tap-spoof",
    )
    assert bundle.cases[0].result == Result.FAIL
