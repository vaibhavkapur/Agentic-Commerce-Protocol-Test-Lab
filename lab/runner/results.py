"""Result classification and record types (plan §16 and §17)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


class Result(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_RUN = "NOT_RUN"
    INCONCLUSIVE = "INCONCLUSIVE"
    HARNESS_ERROR = "HARNESS_ERROR"


class Classification(str, enum.Enum):
    PROTOCOL_CONFORMANCE = "protocol_conformance"
    APPLICATION_ROBUSTNESS = "application_robustness"


@dataclass
class AssertionOutcome:
    """One evaluated assertion. ``result`` is PASS/FAIL/INCONCLUSIVE."""

    description: str
    result: Result
    expected: Any = None
    observed: Any = None
    detail: str = ""
    evidence_references: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["result"] = self.result.value
        return d


@dataclass
class CaseResult:
    run_id: str
    case_id: str
    classification: str
    requirement_id: Optional[str]
    result: Result
    expected: Any = None
    observed: Any = None
    duration_ms: int = 0
    reason: str = ""
    profile: str = ""
    purpose: str = ""
    capability_snapshot: Dict[str, Any] = field(default_factory=dict)
    assertions: List[AssertionOutcome] = field(default_factory=list)
    evidence_references: List[str] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    fault_ids: List[str] = field(default_factory=list)
    requirement_source: Optional[Dict[str, Any]] = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "classification": self.classification,
            "requirement_id": self.requirement_id,
            "requirement_source": self.requirement_source,
            "result": self.result.value,
            "expected": self.expected,
            "observed": self.observed,
            "duration_ms": self.duration_ms,
            "reason": self.reason,
            "profile": self.profile,
            "purpose": self.purpose,
            "capability_snapshot": self.capability_snapshot,
            "assertions": [a.to_dict() for a in self.assertions],
            "evidence_references": self.evidence_references,
            "steps": self.steps,
            "fault_ids": self.fault_ids,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class RunRecord:
    id: str
    suite_version: str
    suite_id: str
    target_id: str
    target_revision: str
    spec_manifest_digest: str
    fixture_seed: int
    environment: Dict[str, Any]
    started_at: str
    finished_at: str = ""
    run_status: str = "running"
    profiles: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def summarize(results: List[CaseResult]) -> Dict[str, Any]:
    """Counts by result plus the pass rate over executed, applicable cases only (plan §20)."""
    counts = {r.value: 0 for r in Result}
    for c in results:
        counts[c.result.value] += 1
    executed = counts["PASS"] + counts["FAIL"]
    by_class: Dict[str, Dict[str, int]] = {}
    for c in results:
        by_class.setdefault(c.classification, {r.value: 0 for r in Result})
        by_class[c.classification][c.result.value] += 1
    return {
        "counts": counts,
        "executed_applicable": executed,
        "pass_rate": (counts["PASS"] / executed) if executed else None,
        "pass_rate_denominator": executed,
        "by_classification": by_class,
        "total": len(results),
    }
