"""Scenario runner (plan §6): suite loader → applicability → steps + faults → assertions → records."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from drivers.base import DriverContext, ProtocolDriver, load_driver
from lab.assertions.engine import evaluate

from .clock import LabClock, LabRng
from .environment import Environment, HarnessError
from .events import EventJournal
from .manifests import (
    ManifestError,
    Profile,
    Requirement,
    Scenario,
    Target,
    find_scenario,
    list_profiles,
    load_requirements,
    load_suite,
    load_target,
    verify_pinned_schemas,
)
from .results import CaseResult, Result, RunRecord, summarize

DEFAULT_FROZEN_CLOCK = 1790294400  # 2026-09-25T00:00:00Z, the planning baseline date
SUITE_VERSION = "0.1.0"


@dataclass
class RunOptions:
    target_id: str
    suite_id: Optional[str] = None
    case_ids: List[str] = field(default_factory=list)
    seed: int = 42
    run_id: Optional[str] = None
    frozen_clock: Optional[int] = DEFAULT_FROZEN_CLOCK
    stop_on_harness_error: bool = False


@dataclass
class RunBundle:
    run: RunRecord
    cases: List[CaseResult]
    journal: EventJournal
    profiles: Dict[str, Profile]
    requirements: Dict[str, Requirement]
    target: Target
    capability_snapshots: Dict[str, Dict[str, Any]]
    schema_integrity: Dict[str, Any]

    def summary(self) -> Dict[str, Any]:
        return summarize(self.cases)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run": self.run.to_dict(),
            "summary": self.summary(),
            "target": self.target.raw,
            "profiles": {pid: p.raw for pid, p in self.profiles.items() if pid in self.run.profiles},
            "capability_snapshots": self.capability_snapshots,
            "schema_integrity": self.schema_integrity,
            "cases": [c.to_dict() for c in self.cases],
            "protocol_events": [e.to_dict() for e in self.journal.protocol_events],
            "fault_events": [f.to_dict() for f in self.journal.fault_events],
            "evidence": self.journal.evidence.all(),
        }


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _make_run_id(seed: int) -> str:
    return "run-" + datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-s{seed}"


def _manifest_digest(profiles: List[Profile]) -> str:
    h = hashlib.sha256()
    for p in sorted(profiles, key=lambda p: p.id):
        h.update(p.manifest_digest().encode())
    return "sha256:" + h.hexdigest()


class Runner:
    def __init__(self, options: RunOptions):
        self.options = options
        self.profiles: Dict[str, Profile] = {p.id: p for p in list_profiles()}
        self.requirements = load_requirements()
        self.target = load_target(options.target_id)
        self.scenarios: List[Scenario] = []
        self.suite_id = options.suite_id or "adhoc"
        if options.suite_id:
            suite = load_suite(options.suite_id, profiles=self.profiles, requirements=self.requirements)
            self.scenarios = suite.scenarios
            self.suite_version = suite.version
        else:
            self.suite_version = SUITE_VERSION
        for cid in options.case_ids:
            if not any(s.id == cid for s in self.scenarios):
                self.scenarios.append(find_scenario(cid, profiles=self.profiles, requirements=self.requirements))
        if options.case_ids and options.suite_id:
            self.scenarios = [s for s in self.scenarios if s.id in options.case_ids]
        if not self.scenarios:
            raise ManifestError("nothing to run: provide --suite and/or --case")
        self.run_id = options.run_id or _make_run_id(options.seed)
        self.clock = LabClock(frozen_at=float(options.frozen_clock) if options.frozen_clock is not None else None)
        self.rng = LabRng(options.seed)
        self.journal = EventJournal(self.run_id, self.clock)
        self.drivers: Dict[str, ProtocolDriver] = {}
        self.snapshots: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ run
    async def run(self) -> RunBundle:
        used_profiles = sorted({s.profile for s in self.scenarios})
        run = RunRecord(
            id=self.run_id, suite_version=self.suite_version, suite_id=self.suite_id, target_id=self.target.id,
            target_revision=self.target.revision, spec_manifest_digest=_manifest_digest([self.profiles[p] for p in used_profiles]),
            fixture_seed=self.options.seed, environment={}, started_at=_now_iso(), profiles=used_profiles,
        )
        schema_integrity = {pid: verify_pinned_schemas(self.profiles[pid]) for pid in used_profiles}
        env = Environment(target=self.target, clock=self.clock, rng=self.rng, journal=self.journal)
        cases: List[CaseResult] = []
        try:
            await env.start()
        except HarnessError as exc:
            for sc in self.scenarios:
                cases.append(self._harness_error_case(sc, f"environment failed to start: {exc}"))
            run.finished_at = _now_iso()
            run.run_status = "harness_error"
            run.counts = summarize(cases)["counts"]
            return RunBundle(run, cases, self.journal, self.profiles, self.requirements, self.target, {}, schema_integrity)
        run.environment = {**env.description, "frozen_clock": self.options.frozen_clock, "python": _python_version(), "mode": env.mode}
        try:
            for pid in used_profiles:
                if pid not in self.target.profiles:
                    continue
                driver = load_driver(self.profiles[pid])
                self.drivers[pid] = driver
                self.snapshots[pid] = await driver.inspect_target(env)
            for sc in self.scenarios:
                result = await self._run_case(sc, env)
                cases.append(result)
                if result.result == Result.HARNESS_ERROR and self.options.stop_on_harness_error:
                    break
        finally:
            await env.stop()
        run.finished_at = _now_iso()
        run.run_status = "completed"
        run.counts = summarize(cases)["counts"]
        return RunBundle(run, cases, self.journal, self.profiles, self.requirements, self.target, self.snapshots, schema_integrity)

    def _harness_error_case(self, sc: Scenario, reason: str) -> CaseResult:
        return CaseResult(run_id=self.run_id, case_id=sc.id, classification=sc.classification, requirement_id=sc.requirement,
                          result=Result.HARNESS_ERROR, reason=reason, profile=sc.profile, purpose=sc.purpose,
                          requirement_source=self._req_source(sc), started_at=_now_iso(), finished_at=_now_iso())

    def _req_source(self, sc: Scenario) -> Optional[Dict[str, Any]]:
        if sc.requirement and sc.requirement in self.requirements:
            return self.requirements[sc.requirement].to_dict()
        if sc.invariant:
            return {"id": sc.invariant, "kind": "application_invariant", "statement": f"application invariant {sc.invariant}"}
        return None

    async def _run_case(self, sc: Scenario, env: Environment) -> CaseResult:
        started = time.monotonic()
        self.journal.current_case_id = sc.id
        result = CaseResult(run_id=self.run_id, case_id=sc.id, classification=sc.classification, requirement_id=sc.requirement,
                            result=Result.NOT_RUN, profile=sc.profile, purpose=sc.purpose, requirement_source=self._req_source(sc),
                            started_at=_now_iso())
        result.expected = self._expected_summary(sc)
        if sc.profile not in self.target.profiles:
            result.result = Result.NOT_RUN
            result.reason = f"target {self.target.id} does not claim profile {sc.profile}"
            return self._finish(result, started)
        driver = self.drivers[sc.profile]
        snapshot = self.snapshots.get(sc.profile, {})
        result.capability_snapshot = {k: v for k, v in snapshot.items() if k != "raw"}
        if snapshot.get("error"):
            result.result = Result.HARNESS_ERROR if "unreachable" in snapshot["error"] else Result.FAIL
            result.reason = f"capability discovery failed: {snapshot['error']}"
            return self._finish(result, started)
        verdict, reason = driver.check_applicability(sc, snapshot)
        if verdict is not None:
            result.result = verdict
            result.reason = reason
            result.observed = {"advertised_capabilities": snapshot.get("capabilities", [])}
            return self._finish(result, started)
        if sc.classification == "application_robustness" and self.target.declared_guarantees and sc.invariant not in self.target.declared_guarantees:
            result.result = Result.NOT_APPLICABLE
            result.reason = f"target does not declare invariant {sc.invariant!r}; declared: {self.target.declared_guarantees}"
            return self._finish(result, started)

        ctx = DriverContext(env=env, scenario=sc, profile=self.profiles[sc.profile], capability_snapshot=snapshot)
        try:
            await driver.prepare_fixture(ctx)
            for step in sc.steps:
                await driver.execute_step(step, ctx)
            final, outcomes, reason = await evaluate(ctx, driver)
            result.result = final
            result.reason = reason
            result.assertions = outcomes
            result.observed = {o.description: o.observed for o in outcomes if o.result != Result.PASS} or "all assertions satisfied"
        except HarnessError as exc:
            result.result = Result.HARNESS_ERROR
            result.reason = f"harness error: {exc}"
        except Exception as exc:  # noqa: BLE001 — anything unexpected is a harness failure, never a target verdict
            result.result = Result.HARNESS_ERROR
            result.reason = f"unexpected harness exception: {type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
        finally:
            try:
                refs = await driver.collect_evidence(ctx)
                result.evidence_references = sorted(refs.values())
            except Exception as exc:  # noqa: BLE001
                result.evidence_references = []
                result.reason += f" (evidence collection failed: {exc})"
            leftover = await env.faults.disarm_all()
            if leftover:
                result.steps.append({"note": "unfired faults disarmed at case end", "faults": leftover})
            await env.faults.sync_remote_events()
            if sc.cleanup == "reset_fixture":
                try:
                    await env.reset_fixture()
                except HarnessError as exc:
                    result.reason += f" (cleanup failed: {exc})"
        result.steps = [ctx.steps[s].to_dict() for s in ctx.order] + result.steps
        result.fault_ids = [f.fault_id for f in self.journal.faults_for_case(sc.id)]
        return self._finish(result, started)

    @staticmethod
    def _expected_summary(sc: Scenario) -> Any:
        return [{k: v for k, v in a.items() if k != "description"} for a in sc.assertions]

    def _finish(self, result: CaseResult, started: float) -> CaseResult:
        result.duration_ms = int((time.monotonic() - started) * 1000)
        result.finished_at = _now_iso()
        self.journal.current_case_id = ""
        return result


def _python_version() -> str:
    import sys

    return sys.version.split()[0]


def run_sync(options: RunOptions) -> RunBundle:
    runner = Runner(options)
    return asyncio.run(runner.run())
