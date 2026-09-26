"""Common event format shared by proxy, drivers, fixtures, and reports (plan §5, §17).

``protocol_events`` record every message crossing the lab boundary with a
redacted payload reference and a digest of the *unredacted* payload so that a
trace can be matched against fixture-side records without exporting secrets.

``fault_events`` record when and where a fault actually fired. A scenario that
armed a fault which never fired cannot claim to have tested recovery from it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from .redaction import redact_headers, redact_json


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_json(value: Any) -> str:
    return digest_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


@dataclass
class ProtocolEvent:
    run_id: str
    case_id: str
    event_id: str
    direction: str  # "agent->target" | "target->agent" | "proxy->upstream" | "upstream->proxy" | "lab->fixture" | "fixture->lab"
    protocol: str
    profile_version: str
    operation: str
    timestamp: str
    sequence: int
    payload_digest: str
    redacted_payload_reference: str
    step_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FaultEvent:
    run_id: str
    case_id: str
    fault_id: str
    fault_type: str
    trigger_point: str
    fired_at: str
    sequence: int
    affected_operation: str
    confirmation_evidence: Dict[str, Any] = field(default_factory=dict)
    step_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EvidenceStore:
    """In-memory evidence blobs keyed by reference id; persisted by the report layer.

    Blobs are stored *after* redaction. The unredacted digest is kept alongside.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._blobs: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    def put(self, kind: str, payload: Any, *, case_id: str, unredacted_digest: Optional[str] = None,
            headers: Optional[Dict[str, str]] = None, label: str = "") -> str:
        self._counter += 1
        ref = f"ev-{self._counter:05d}-{kind}"
        record = {
            "reference": ref,
            "kind": kind,
            "case_id": case_id,
            "label": label,
            "payload": redact_json(payload),
            "unredacted_digest": unredacted_digest or digest_json(payload),
        }
        if headers is not None:
            record["headers"] = redact_headers(headers)
        self._blobs[ref] = record
        return ref

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        return self._blobs.get(ref)

    def all(self) -> List[Dict[str, Any]]:
        return list(self._blobs.values())

    def for_case(self, case_id: str) -> List[Dict[str, Any]]:
        return [b for b in self._blobs.values() if b["case_id"] == case_id]


class EventJournal:
    """Append-only journal of protocol and fault events for one run."""

    def __init__(self, run_id: str, clock):
        self.run_id = run_id
        self.clock = clock
        self.protocol_events: List[ProtocolEvent] = []
        self.fault_events: List[FaultEvent] = []
        self.evidence = EvidenceStore(run_id)
        self.current_case_id: str = ""
        self.current_step_id: Optional[str] = None

    def record_message(self, *, direction: str, protocol: str, profile_version: str, operation: str,
                       headers: Dict[str, str], body: Any, raw_body: Optional[bytes] = None,
                       meta: Optional[Dict[str, Any]] = None, case_id: Optional[str] = None) -> ProtocolEvent:
        cid = case_id or self.current_case_id
        seq = self.clock.tick()
        if raw_body is not None:
            digest = digest_bytes(raw_body)
        else:
            digest = digest_json(body)
        ref = self.evidence.put("protocol_message", body, case_id=cid, unredacted_digest=digest, headers=headers,
                                label=f"{direction} {operation}")
        ev = ProtocolEvent(
            run_id=self.run_id,
            case_id=cid,
            event_id=f"pe-{seq:05d}",
            direction=direction,
            protocol=protocol,
            profile_version=profile_version,
            operation=operation,
            timestamp=self.clock.iso(),
            sequence=seq,
            payload_digest=digest,
            redacted_payload_reference=ref,
            step_id=self.current_step_id,
            meta=meta or {},
        )
        self.protocol_events.append(ev)
        return ev

    def record_fault(self, *, fault_id: str, fault_type: str, trigger_point: str, affected_operation: str,
                     confirmation_evidence: Dict[str, Any], case_id: Optional[str] = None) -> FaultEvent:
        seq = self.clock.tick()
        ev = FaultEvent(
            run_id=self.run_id,
            case_id=case_id or self.current_case_id,
            fault_id=fault_id,
            fault_type=fault_type,
            trigger_point=trigger_point,
            fired_at=self.clock.iso(),
            sequence=seq,
            affected_operation=affected_operation,
            confirmation_evidence=redact_json(confirmation_evidence),
            step_id=self.current_step_id,
        )
        self.fault_events.append(ev)
        return ev

    def faults_for_case(self, case_id: str) -> List[FaultEvent]:
        return [f for f in self.fault_events if f.case_id == case_id]

    def messages_for_case(self, case_id: str) -> List[ProtocolEvent]:
        return [e for e in self.protocol_events if e.case_id == case_id]
