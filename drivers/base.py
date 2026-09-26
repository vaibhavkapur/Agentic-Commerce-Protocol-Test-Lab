"""Protocol driver interface (plan §10) and shared HTTP/metric plumbing.

A driver formats native protocol messages and executes lab-abstract actions.
It never decides pass/fail; the assertion engine does that from step outcomes,
fault events, and independent observations.

Drivers validate their *outgoing* messages against the pinned request schemas
so that a malformed harness request cannot be misdiagnosed as a target failure.
Steps marked ``intentionally_invalid`` skip that check and are labelled as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from fault_proxy import FaultInjected, TRIGGER_FOR_TYPE
from lab.assertions.schema import validate
from lab.runner.environment import Environment, HarnessError
from lab.runner.manifests import Profile, Scenario
from lab.runner.redaction import redact_headers, redact_json
from lab.runner.results import Result


class MetricUnavailable(Exception):
    """The metric cannot be established from the available evidence (→ INCONCLUSIVE)."""


class OutgoingMessageInvalid(HarnessError):
    """The harness built a request that violates the pinned schema and did not mark it intentional."""


@dataclass
class StepOutcome:
    step_id: str
    action: str
    request: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None
    transport_error: Optional[str] = None
    observations: Dict[str, Any] = field(default_factory=dict)
    intentionally_invalid: bool = False
    fault_id: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def status(self) -> Optional[int]:
        return self.response.get("status") if self.response else None

    @property
    def body(self) -> Any:
        return self.response.get("body") if self.response else None

    def header(self, name: str) -> Optional[str]:
        if not self.response:
            return None
        for k, v in self.response.get("headers", {}).items():
            if k.lower() == name.lower():
                return v
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "request": redact_json(self.request) if self.request else None,
            "response": redact_json(self.response) if self.response else None,
            "transport_error": self.transport_error,
            "observations": redact_json(self.observations),
            "intentionally_invalid": self.intentionally_invalid,
            "fault_id": self.fault_id,
            "notes": self.notes,
        }


@dataclass
class DriverContext:
    env: Environment
    scenario: Scenario
    profile: Profile
    capability_snapshot: Dict[str, Any]
    steps: Dict[str, StepOutcome] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)
    # Durable client journal: survives restart_client_worker. Keyed by operation key.
    durable: Dict[str, Any] = field(default_factory=dict)
    evidence_refs: List[str] = field(default_factory=list)

    @property
    def journal(self):
        return self.env.journal

    @property
    def clock(self):
        return self.env.clock

    @property
    def rng(self):
        return self.env.rng

    def last_step(self) -> Optional[StepOutcome]:
        return self.steps[self.order[-1]] if self.order else None

    def record(self, outcome: StepOutcome) -> StepOutcome:
        self.steps[outcome.step_id] = outcome
        self.order.append(outcome.step_id)
        return outcome


class ProtocolDriver:
    protocol: str = ""
    request_schemas: Dict[str, str] = {}

    def __init__(self, profile: Profile):
        self.profile = profile

    # ------------------------------------------------------------ interface
    async def inspect_target(self, env: Environment) -> Dict[str, Any]:
        raise NotImplementedError

    def check_applicability(self, scenario: Scenario, snapshot: Dict[str, Any]) -> Tuple[Optional[Result], str]:
        """Return (result, reason) when the case must not execute, else (None, "")."""
        advertised = set(snapshot.get("capabilities", []))
        for cap in scenario.requires_capabilities:
            if cap in advertised:
                continue
            if self.profile.is_required(cap):
                return Result.FAIL, (f"profile {self.profile.id} requires capability {cap!r} but the target did not advertise it "
                                     f"(advertised: {sorted(advertised)})")
            if self.profile.is_optional(cap):
                return Result.NOT_APPLICABLE, f"optional capability {cap!r} is not advertised by the target"
            return Result.HARNESS_ERROR, f"capability {cap!r} is not defined by profile {self.profile.id}"
        return None, ""

    async def prepare_fixture(self, ctx: DriverContext) -> None:
        for pre in ctx.scenario.preconditions:
            if pre == "fixture_reset":
                await ctx.env.reset_fixture()
            elif pre == "result_lookup_supported":
                if not ctx.env.can_observe:
                    raise HarnessError("scenario needs result lookup (observation) but the target exposes none")

    async def execute_step(self, step: Dict[str, Any], ctx: DriverContext) -> StepOutcome:
        action = step["action"]
        handler = getattr(self, f"action_{action}", None)
        if handler is None:
            raise HarnessError(f"driver {self.protocol} has no action {action!r}")
        outcome = await handler(step, ctx)
        return ctx.record(outcome)

    async def collect_evidence(self, ctx: DriverContext) -> Dict[str, str]:
        refs: Dict[str, str] = {}
        ev = ctx.env.journal.evidence
        cid = ctx.scenario.id
        wanted = set(ctx.scenario.evidence)
        checkout_id = (ctx.state.get("operation") or {}).get("checkout_id")
        if ctx.env.can_observe:
            if "merchant_order_snapshot" in wanted:
                data = await ctx.env.observe("/_lab/observe/orders", **({"checkout_id": checkout_id} if checkout_id else {}))
                refs["merchant_order_snapshot"] = ev.put("merchant_order_snapshot", data, case_id=cid)
            if "payment_event_snapshot" in wanted:
                data = await ctx.env.observe("/_lab/observe/payments", **({"checkout_id": checkout_id} if checkout_id else {}))
                refs["payment_event_snapshot"] = ev.put("payment_event_snapshot", data, case_id=cid)
            if "checkout_snapshot" in wanted and checkout_id:
                data = await ctx.env.observe(f"/_lab/observe/checkouts/{checkout_id}")
                refs["checkout_snapshot"] = ev.put("checkout_snapshot", data, case_id=cid)
            if "idempotency_snapshot" in wanted:
                data = await ctx.env.observe("/_lab/observe/idempotency")
                refs["idempotency_snapshot"] = ev.put("idempotency_snapshot", data, case_id=cid)
            if "verifier_decision" in wanted:
                data = await ctx.env.observe("/_lab/observe/verifiers")
                refs["verifier_decision"] = ev.put("verifier_decision", data, case_id=cid)
            if "registry_snapshot" in wanted:
                data = await ctx.env.observe("/_lab/observe/registry")
                refs["registry_snapshot"] = ev.put("registry_snapshot", data, case_id=cid)
        if "capability_snapshot" in wanted:
            refs["capability_snapshot"] = ev.put("capability_snapshot", ctx.capability_snapshot, case_id=cid)
        if "fault_log" in wanted:
            refs["fault_log"] = ev.put("fault_log", [f.to_dict() for f in ctx.env.journal.faults_for_case(cid)], case_id=cid)
        if "redacted_protocol_trace" in wanted:
            trace = [e.to_dict() for e in ctx.env.journal.messages_for_case(cid)]
            refs["redacted_protocol_trace"] = ev.put("redacted_protocol_trace", trace, case_id=cid)
        return refs

    # --------------------------------------------------------------- metrics
    async def metric(self, name: str, ctx: DriverContext) -> Any:
        handler = getattr(self, f"metric_{name}", None)
        if handler is None:
            raise MetricUnavailable(f"metric {name!r} is not provided by driver {self.protocol}")
        return await handler(ctx)

    def _operation_checkout_id(self, ctx: DriverContext) -> str:
        op = ctx.state.get("operation") or {}
        cid = op.get("checkout_id")
        if not cid:
            raise MetricUnavailable("no checkout operation was started in this case")
        return cid

    async def _observe_or_unavailable(self, ctx: DriverContext, path: str, **params) -> Any:
        if not ctx.env.can_observe:
            raise MetricUnavailable("target exposes no observation endpoint; order/charge counts cannot be established")
        return await ctx.env.observe(path, **params)

    async def metric_merchant_order_count_for_operation(self, ctx: DriverContext) -> int:
        cid = self._operation_checkout_id(ctx)
        data = await self._observe_or_unavailable(ctx, "/_lab/observe/orders", checkout_id=cid)
        return len(data["orders"])

    async def metric_settled_charge_count_for_operation(self, ctx: DriverContext) -> int:
        cid = self._operation_checkout_id(ctx)
        data = await self._observe_or_unavailable(ctx, "/_lab/observe/payments", checkout_id=cid)
        return sum(1 for p in data["payments"] if p["state"] == "settled")

    async def metric_payment_attempt_count_for_operation(self, ctx: DriverContext) -> int:
        cid = self._operation_checkout_id(ctx)
        data = await self._observe_or_unavailable(ctx, "/_lab/observe/payments", checkout_id=cid)
        return len(data["payments"])

    async def metric_unknown_payment_count_for_operation(self, ctx: DriverContext) -> int:
        cid = self._operation_checkout_id(ctx)
        data = await self._observe_or_unavailable(ctx, "/_lab/observe/payments", checkout_id=cid)
        return sum(1 for p in data["payments"] if p["state"] == "unknown")

    async def metric_merchant_checkout_status(self, ctx: DriverContext) -> str:
        cid = self._operation_checkout_id(ctx)
        data = await self._observe_or_unavailable(ctx, f"/_lab/observe/checkouts/{cid}")
        return data["status"]

    async def metric_client_resolved_order_matches_merchant(self, ctx: DriverContext) -> bool:
        cid = self._operation_checkout_id(ctx)
        resolved = ctx.state.get("client_resolved_order_id")
        if resolved is None:
            raise MetricUnavailable("client never resolved an order for the operation")
        data = await self._observe_or_unavailable(ctx, "/_lab/observe/orders", checkout_id=cid)
        ids = [o["id"] for o in data["orders"]]
        if len(ids) != 1:
            return False
        return ids[0] == resolved

    async def metric_fault_fired_count(self, ctx: DriverContext) -> int:
        return len(ctx.env.journal.faults_for_case(ctx.scenario.id))

    async def metric_merchant_stock_for_sku(self, ctx: DriverContext) -> Any:
        raise MetricUnavailable("use field assertions on the inventory admin response")

    # --------------------------------------------------------------- sending
    def _validate_outgoing(self, schema_name: Optional[str], body: Any, step: Dict[str, Any]) -> Optional[str]:
        if schema_name is None or body is None or step.get("intentionally_invalid"):
            return None
        ok, errors = validate(schema_name, body)
        if not ok:
            raise OutgoingMessageInvalid(
                f"harness request for step {step['id']!r} violates {schema_name}: {errors[:3]} "
                "(mark the step intentionally_invalid if this is a negative fixture)")
        return schema_name

    async def send(self, ctx: DriverContext, step: Dict[str, Any], *, method: str, path: str, headers: Dict[str, str],
                   json_body: Any = None, raw_body: Optional[bytes] = None, request_schema: Optional[str] = None,
                   query: Optional[Dict[str, str]] = None) -> StepOutcome:
        """Send one protocol request through the fault proxy and capture the outcome."""
        self._validate_outgoing(request_schema, json_body, step)
        outcome = StepOutcome(step_id=step["id"], action=step["action"], intentionally_invalid=bool(step.get("intentionally_invalid")))
        outcome.request = {"method": method, "path": path, "headers": redact_headers(headers), "body": json_body if raw_body is None
                           else {"_raw": raw_body[:512].decode("utf-8", "replace")}, "validated_against": request_schema
                           if not step.get("intentionally_invalid") else None}
        fault = step.get("fault")
        if fault and fault["type"] in TRIGGER_FOR_TYPE:
            spec = {"fault_type": fault["type"], "trigger_point": fault["trigger_point"], "method": method, "path_regex": "^" + _re_escape(path) + "$",
                    "delay_ms": fault.get("delay_ms", 0), "copies": fault.get("copies", 2), "case_id": ctx.scenario.id, "step_id": step["id"]}
            if headers.get("Idempotency-Key"):
                spec["header_equals"] = {"idempotency-key": headers["Idempotency-Key"]}
            armed = await ctx.env.faults.arm(spec)
            outcome.fault_id = armed["id"]
        ctx.journal.current_step_id = step["id"]
        try:
            content = raw_body if raw_body is not None else (json.dumps(json_body).encode("utf-8") if json_body is not None else None)
            if content is not None and "Content-Type" not in headers and "content-type" not in {k.lower() for k in headers}:
                headers = dict(headers)
                headers["Content-Type"] = "application/json"
            resp = await ctx.env.protocol_client.request(method, path, headers=headers, content=content, params=query)
            try:
                body = resp.json() if resp.content else None
            except ValueError:
                body = {"_raw": resp.text[:1024]}
            outcome.response = {"status": resp.status_code, "headers": dict(resp.headers), "body": body}
        except FaultInjected as exc:
            outcome.transport_error = f"response lost (lab fault {exc.fault_id}: {exc.fault_type})"
        except httpx.TransportError as exc:
            outcome.transport_error = f"transport error: {type(exc).__name__}: {exc}"
        finally:
            ctx.journal.current_step_id = None
        if fault and not outcome.transport_error and outcome.fault_id:
            fired = [f for f in ctx.env.journal.faults_for_case(ctx.scenario.id) if f.fault_id == outcome.fault_id]
            if not fired:
                outcome.notes.append(f"armed fault {outcome.fault_id} did not fire during this step")
        return outcome


def _re_escape(path: str) -> str:
    import re

    return re.escape(path)


DRIVER_REGISTRY: Dict[str, str] = {
    "acp": "drivers.acp.driver:AcpDriver",
    "ucp": "drivers.ucp.driver:UcpDriver",
    "ap2": "drivers.ap2.driver:Ap2Driver",
    "tap": "drivers.tap.driver:TapDriver",
}


def load_driver(profile: Profile) -> ProtocolDriver:
    import importlib

    spec = profile.driver if ":" in profile.driver else DRIVER_REGISTRY.get(profile.driver, profile.driver)
    module_name, _, cls_name = spec.rpartition(":")
    try:
        cls = getattr(importlib.import_module(module_name), cls_name)
    except (ImportError, AttributeError) as exc:
        raise HarnessError(f"cannot load driver {spec!r}: {exc}")
    return cls(profile)
