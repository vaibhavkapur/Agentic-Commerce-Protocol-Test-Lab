"""Unknown and optional capabilities are classified, never silently assumed (plan §8, §16)."""

from __future__ import annotations

from tests.conftest import run_case, run_suite

from lab.runner.results import Result


def test_optional_capability_absent_is_not_applicable() -> None:
    bundle = run_case("local-merchant-minimal", "acp-discount-extension", run_id="app-opt")
    assert len(bundle.cases) == 1
    case = bundle.cases[0]
    assert case.result == Result.NOT_APPLICABLE
    assert "optional capability" in case.reason


def test_required_capability_absent_is_fail() -> None:
    bundle = run_case("local-merchant-misdeclared", "ucp-create-checkout-201", run_id="app-req")
    case = bundle.cases[0]
    assert case.result == Result.FAIL
    assert "requires capability" in case.reason
    assert "did not advertise" in case.reason


def test_applicability_demo_separates_optional_from_required() -> None:
    minimal = run_suite("local-merchant-minimal", "demo-applicability", run_id="app-min")
    by_id = {c.case_id: c for c in minimal.cases}
    assert by_id["acp-discount-extension"].result == Result.NOT_APPLICABLE
    assert by_id["ucp-discount-capability"].result == Result.NOT_APPLICABLE
    assert by_id["ucp-create-checkout-201"].result == Result.PASS

    mis = run_suite("local-merchant-misdeclared", "demo-applicability", run_id="app-mis")
    by_id = {c.case_id: c for c in mis.cases}
    assert by_id["acp-discount-extension"].result == Result.NOT_RUN
    assert by_id["ucp-create-checkout-201"].result == Result.FAIL
    assert by_id["ucp-profile-discovery"].result == Result.FAIL
