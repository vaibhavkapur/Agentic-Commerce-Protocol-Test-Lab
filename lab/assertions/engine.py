"""Assertion engine: turns step outcomes, fault events, and oracle observations into a result.

Aggregation rules (plan §16):

* any assertion FAIL → case FAIL;
* otherwise any INCONCLUSIVE → case INCONCLUSIVE;
* an armed proxy fault that never fired makes the case INCONCLUSIVE regardless of
  other assertions, because the scenario did not test what it claims to test;
* otherwise PASS.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from drivers.base import DriverContext, MetricUnavailable, ProtocolDriver, StepOutcome
from lab.runner.results import AssertionOutcome, Result

from . import jsonpath
from .oracles import ORACLES, OracleInconclusive
from .schema import SchemaError, validate


def _compare(observed: Any, spec: Dict[str, Any]) -> Tuple[bool, Any]:
    """Return (ok, expected_description)."""
    if "equals" in spec:
        return observed == spec["equals"], spec["equals"]
    if "not_equals" in spec:
        return observed != spec["not_equals"], {"not": spec["not_equals"]}
    if "in" in spec:
        return observed in spec["in"], {"in": spec["in"]}
    ok = True
    exp: Dict[str, Any] = {}
    if "gte" in spec:
        exp["gte"] = spec["gte"]
        ok = ok and isinstance(observed, (int, float)) and observed >= spec["gte"]
    if "lte" in spec:
        exp["lte"] = spec["lte"]
        ok = ok and isinstance(observed, (int, float)) and observed <= spec["lte"]
    if "present" in spec:
        exp["present"] = spec["present"]
        ok = ok and ((observed is not None) == spec["present"])
    if not exp:
        return True, "(no comparison)"
    return ok, exp


def _describe(a: Dict[str, Any]) -> str:
    if a.get("description"):
        return a["description"]
    kind = a["kind"]
    if kind == "metric":
        return f"metric {a['metric']}"
    if kind == "oracle":
        return f"oracle {a['oracle']} on step {a['step']}"
    if kind == "status":
        return f"step {a['step']} HTTP status"
    if kind == "schema":
        return f"step {a['step']} response matches {a['schema']}"
    if kind == "field":
        return f"step {a['step']} field {a['path']}"
    if kind == "header":
        return f"step {a['step']} header {a['header']}"
    if kind == "fault_fired":
        return f"fault armed on step {a['fault_step']} fired"
    if kind == "verifier":
        return f"step {a['step']} verifier decision"
    if kind == "transport_error":
        return f"step {a['step']} transport error"
    return kind


async def evaluate(ctx: DriverContext, driver: ProtocolDriver) -> Tuple[Result, List[AssertionOutcome], str]:
    scenario = ctx.scenario
    outcomes: List[AssertionOutcome] = []
    reasons: List[str] = []

    # 1. Fault preconditions: every proxy fault armed by a step must have fired.
    fired_ids = {f.fault_id for f in ctx.journal.faults_for_case(scenario.id)}
    unfired: List[str] = []
    for step in scenario.steps:
        so = ctx.steps.get(step["id"])
        if step.get("fault") and so is not None and so.fault_id and so.fault_id not in fired_ids:
            unfired.append(f"{so.fault_id} ({step['fault']['type']} at {step['fault']['trigger_point']}) on step {step['id']}")
    if unfired:
        outcomes.append(AssertionOutcome(description="all armed faults fired", result=Result.INCONCLUSIVE, expected="fired",
                                         observed="never fired: " + "; ".join(unfired),
                                         detail="a scenario cannot claim to have tested recovery from a fault that was not injected"))

    for a in scenario.assertions:
        desc = _describe(a)
        label = a.get("policy_label")
        if label == "application_policy":
            desc += " [application policy]"
        try:
            outcome = await _evaluate_one(a, ctx, driver, desc)
        except Exception as exc:  # defensive: an assertion bug must not become a false pass
            outcome = AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=f"assertion evaluation error: {type(exc).__name__}: {exc}")
        outcomes.append(outcome)

    if any(o.result == Result.FAIL for o in outcomes):
        final = Result.FAIL
        reasons = [f"{o.description}: expected {o.expected!r}, observed {o.observed!r}" for o in outcomes if o.result == Result.FAIL]
    elif any(o.result == Result.INCONCLUSIVE for o in outcomes):
        final = Result.INCONCLUSIVE
        reasons = [f"{o.description}: {o.detail or o.observed}" for o in outcomes if o.result == Result.INCONCLUSIVE]
    else:
        final = Result.PASS
        reasons = []
    return final, outcomes, "; ".join(reasons)


def _step(ctx: DriverContext, a: Dict[str, Any], key: str = "step") -> Optional[StepOutcome]:
    return ctx.steps.get(a[key])


async def _evaluate_one(a: Dict[str, Any], ctx: DriverContext, driver: ProtocolDriver, desc: str) -> AssertionOutcome:
    kind = a["kind"]

    if kind == "metric":
        try:
            observed = await driver.metric(a["metric"], ctx)
        except MetricUnavailable as exc:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=str(exc), expected=a.get("equals"))
        ok, expected = _compare(observed, a)
        return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected=expected, observed=observed)

    if kind == "oracle":
        step = _step(ctx, a)
        if step is None:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=f"step {a['step']} did not execute")
        fn = ORACLES.get(a["oracle"])
        if fn is None:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=f"unknown oracle {a['oracle']!r}")
        try:
            value, detail = fn(ctx, step)
        except OracleInconclusive as exc:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=str(exc))
        spec = dict(a)
        spec.setdefault("equals", True)
        ok, expected = _compare(value, spec)
        return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected=expected, observed=value, detail=detail)

    step = _step(ctx, a, "fault_step" if kind == "fault_fired" else "step")
    if step is None:
        return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=f"referenced step did not execute")

    if kind == "fault_fired":
        fired = [f for f in ctx.journal.faults_for_case(ctx.scenario.id) if f.fault_id == step.fault_id]
        if step.fault_id is None:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail="step armed no fault")
        if not fired:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, expected="fired", observed="not fired",
                                    detail="fault was armed but never fired")
        ev = fired[0]
        return AssertionOutcome(description=desc, result=Result.PASS, expected="fired", observed=f"fired at {ev.fired_at} on {ev.affected_operation}",
                                detail=str(ev.confirmation_evidence))

    if kind == "transport_error":
        observed = step.transport_error is not None
        ok = observed == bool(a["equals"])
        return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected=a["equals"], observed=step.transport_error or False)

    if step.response is None and kind in ("status", "schema", "field", "header"):
        return AssertionOutcome(description=desc, result=Result.FAIL, expected=a.get("equals", a.get("in")),
                                observed=f"no response ({step.transport_error or 'step produced no HTTP exchange'})")

    if kind == "status":
        ok, expected = _compare(step.status, a)
        return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected=expected, observed=step.status)

    if kind == "schema":
        try:
            ok, errors = validate(a["schema"], step.body)
        except SchemaError as exc:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=str(exc))
        return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected=f"valid {a['schema']}",
                                observed="valid" if ok else f"{len(errors)} schema violation(s)", detail="; ".join(errors[:5]))

    if kind == "field":
        try:
            hits = jsonpath.select(step.body, a["path"])
        except jsonpath.PathError as exc:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=str(exc))
        if a.get("present") is not None and not any(k in a for k in ("equals", "not_equals", "in", "gte", "lte")):
            ok = bool(hits) == a["present"]
            return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected={"present": a["present"]},
                                    observed=hits[0] if hits else None)
        observed = hits[0] if hits else None
        if a["path"].count("[*]") or a["path"].count("[?"):
            observed = hits if len(hits) != 1 else hits[0]
        ok, expected = _compare(observed, a)
        return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected=expected, observed=observed)

    if kind == "header":
        observed = step.header(a["header"])
        ok, expected = _compare(observed, a)
        return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected=expected, observed=observed)

    if kind == "verifier":
        v = step.observations.get("verifier")
        if not v:
            return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail="step recorded no verifier decision",
                                    observed=step.transport_error)
        ok = str(v.get("result")).lower() == str(a["result"]).lower()
        if ok and a.get("error") is not None:
            ok = v.get("error") == a["error"]
        expected = {"result": a["result"], **({"error": a["error"]} if a.get("error") else {})}
        return AssertionOutcome(description=desc, result=Result.PASS if ok else Result.FAIL, expected=expected,
                                observed={k: v.get(k) for k in ("result", "error")}, detail=str(v.get("error_description") or v.get("detail") or ""))

    return AssertionOutcome(description=desc, result=Result.INCONCLUSIVE, detail=f"unknown assertion kind {kind!r}")
