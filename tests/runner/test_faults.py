"""A missing fault trigger cannot produce a false pass (plan §14, §23)."""

from __future__ import annotations

import asyncio

from drivers.base import DriverContext, ProtocolDriver, StepOutcome
from lab.assertions.engine import evaluate
from lab.runner.manifests import Scenario, load_profile
from lab.runner.results import Result


class _AlwaysOneDriver(ProtocolDriver):
    protocol = "acp"

    async def metric(self, name, ctx):  # noqa: ARG002
        return 1


def _scenario() -> Scenario:
    return Scenario(
        id="fault-never-fired",
        title="Unfired fault must not pass",
        purpose="A scenario that armed a proxy fault which never fired cannot claim recovery was tested.",
        classification="application_robustness",
        profile="acp-checkout-2026-04-17",
        role="agent",
        invariant="one-order-and-one-charge-per-operation",
        steps=[
            {
                "id": "complete",
                "action": "complete_checkout",
                "fault": {
                    "type": "drop_response",
                    "trigger_point": "after_backend_commit_before_response",
                },
            }
        ],
        assertions=[
            {"kind": "metric", "metric": "merchant_order_count_for_operation", "equals": 1},
            {"kind": "metric", "metric": "settled_charge_count_for_operation", "equals": 1},
        ],
        evidence=["fault_log"],
    )


def test_unfired_fault_is_inconclusive_not_pass(env_ns) -> None:
    sc = _scenario()
    ctx = DriverContext(
        env=env_ns,
        scenario=sc,
        profile=load_profile("acp-checkout-2026-04-17"),
        capability_snapshot={"capabilities": ["checkout"]},
    )
    ctx.record(StepOutcome(step_id="complete", action="complete_checkout", fault_id="flt-armed-but-silent"))
    final, outcomes, reason = asyncio.run(evaluate(ctx, _AlwaysOneDriver(ctx.profile)))
    assert final == Result.INCONCLUSIVE
    assert any(o.result == Result.INCONCLUSIVE and "never fired" in str(o.observed) for o in outcomes)
    assert "never fired" in reason or "not injected" in reason
    assert not any(o.result == Result.FAIL for o in outcomes)


def test_fired_fault_allows_pass_when_metrics_hold(env_ns, journal) -> None:
    sc = _scenario()
    ctx = DriverContext(
        env=env_ns,
        scenario=sc,
        profile=load_profile("acp-checkout-2026-04-17"),
        capability_snapshot={"capabilities": ["checkout"]},
    )
    ctx.record(StepOutcome(step_id="complete", action="complete_checkout", fault_id="flt-1"))
    journal.record_fault(
        fault_id="flt-1",
        fault_type="drop_response",
        trigger_point="after_backend_commit_before_response",
        affected_operation="POST /acp/checkout_sessions/chk/complete",
        confirmation_evidence={"upstream_status": 200},
        case_id=sc.id,
    )
    final, outcomes, _reason = asyncio.run(evaluate(ctx, _AlwaysOneDriver(ctx.profile)))
    assert final == Result.PASS
    assert all(o.result == Result.PASS for o in outcomes)
