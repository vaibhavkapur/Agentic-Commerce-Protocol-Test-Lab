"""Seeded runs reproduce the same local behaviour (plan §19, §23)."""

from __future__ import annotations

from tests.conftest import run_case


def _session_id(bundle) -> str:
    case = bundle.cases[0]
    for step in case.steps:
        body = (step.get("response") or {}).get("body") or {}
        if isinstance(body, dict) and body.get("id"):
            return body["id"]
    raise AssertionError("no checkout id in steps")


def test_same_seed_reproduces_checkout_ids() -> None:
    a = run_case("local-merchant-corrected", "acp-create-session-201", seed=42, run_id="seed-a")
    b = run_case("local-merchant-corrected", "acp-create-session-201", seed=42, run_id="seed-b")
    assert a.cases[0].result.value == "PASS"
    assert b.cases[0].result.value == "PASS"
    assert _session_id(a) == _session_id(b)


def test_different_seed_changes_generated_ids() -> None:
    a = run_case("local-merchant-corrected", "acp-create-session-201", seed=42, run_id="seed-x")
    b = run_case("local-merchant-corrected", "acp-create-session-201", seed=99, run_id="seed-y")
    assert _session_id(a) != _session_id(b)
