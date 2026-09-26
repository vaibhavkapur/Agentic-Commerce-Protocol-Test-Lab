"""An unavailable fixture becomes HARNESS_ERROR (plan §16, §23)."""

from __future__ import annotations

import asyncio

import pytest

from drivers.acp.driver import AcpDriver
from lab.runner.engine import RunOptions, run_sync
from lab.runner.environment import Environment, HarnessError
from lab.runner.manifests import Scenario, Target, load_profile
from lab.runner.results import Result


def _broken_factory_target() -> Target:
    return Target(
        id="unavailable-fixture",
        kind="local_reference",
        revision="missing@0",
        profiles=["acp-checkout-2026-04-17"],
        observation={"kind": "lab_fixture"},
        factory="does.not.exist:factory",
        declared_guarantees=["one-order-and-one-charge-per-operation"],
    )


def test_missing_factory_raises_harness_error(clock, rng, journal) -> None:
    env = Environment(target=_broken_factory_target(), clock=clock, rng=rng, journal=journal)

    async def go():
        with pytest.raises(HarnessError, match="cannot import target factory"):
            await env.start()

    asyncio.run(go())


def test_runner_records_harness_error_when_environment_cannot_start(monkeypatch) -> None:
    monkeypatch.setattr("lab.runner.engine.load_target", lambda _id: _broken_factory_target())
    bundle = run_sync(
        RunOptions(
            target_id="unavailable-fixture",
            case_ids=["acp-create-session-201"],
            seed=1,
            run_id="harness-unavailable",
            frozen_clock=1790294400,
        )
    )
    assert bundle.run.run_status == "harness_error"
    assert bundle.cases
    assert all(c.result == Result.HARNESS_ERROR for c in bundle.cases)
    assert "environment failed to start" in bundle.cases[0].reason


def test_unknown_driver_capability_is_harness_error_not_assumed() -> None:
    profile = load_profile("acp-checkout-2026-04-17")
    sc = Scenario(
        id="unknown-cap-runtime",
        title="Unknown capability",
        purpose="Runtime applicability must not invent capabilities the profile does not define.",
        classification="protocol_conformance",
        profile=profile.id,
        role="agent",
        requirement="LAB-ACP-SESS-001",
        requires_capabilities=["telepathy"],
        steps=[{"id": "x", "action": "fetch_discovery"}],
        assertions=[{"kind": "status", "step": "x", "equals": 200}],
        evidence=["capability_snapshot"],
    )
    verdict, reason = AcpDriver(profile).check_applicability(sc, {"capabilities": ["checkout"]})
    assert verdict == Result.HARNESS_ERROR
    assert "not defined by profile" in reason
