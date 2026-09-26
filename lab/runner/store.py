"""SQLite persistence for local runs (plan §17 data model) plus the JSON run bundle on disk.

Layout under ``runs/``:

* ``lab.sqlite`` — tables test_runs, case_results, protocol_events, fault_events
* ``<run_id>/bundle.json`` — the complete machine-readable run (self-contained, redacted)
* ``<run_id>/evidence/<ref>.json`` — one file per evidence blob
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS test_runs (
  id TEXT PRIMARY KEY, suite_id TEXT, suite_version TEXT, target_id TEXT, target_revision TEXT,
  spec_manifest_digest TEXT, fixture_seed INTEGER, environment TEXT, started_at TEXT, finished_at TEXT,
  run_status TEXT, profiles TEXT, counts TEXT
);
CREATE TABLE IF NOT EXISTS case_results (
  run_id TEXT, case_id TEXT, classification TEXT, requirement_id TEXT, result TEXT, expected TEXT, observed TEXT,
  duration_ms INTEGER, reason TEXT, profile TEXT, capability_snapshot TEXT, evidence_references TEXT, assertions TEXT,
  PRIMARY KEY (run_id, case_id)
);
CREATE TABLE IF NOT EXISTS protocol_events (
  run_id TEXT, case_id TEXT, event_id TEXT, direction TEXT, protocol TEXT, profile_version TEXT, operation TEXT,
  timestamp TEXT, sequence INTEGER, redacted_payload_reference TEXT, payload_digest TEXT, step_id TEXT, meta TEXT,
  PRIMARY KEY (run_id, event_id)
);
CREATE TABLE IF NOT EXISTS fault_events (
  run_id TEXT, case_id TEXT, fault_id TEXT, fault_type TEXT, trigger_point TEXT, fired_at TEXT, sequence INTEGER,
  affected_operation TEXT, confirmation_evidence TEXT, step_id TEXT,
  PRIMARY KEY (run_id, fault_id, sequence)
);
"""


def _j(v: Any) -> str:
    return json.dumps(v, default=str, sort_keys=True)


class RunStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else paths.RUNS
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "lab.sqlite"
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ---------------------------------------------------------------- write
    def save(self, bundle) -> Path:
        run = bundle.run
        with self._conn() as conn:
            conn.execute("DELETE FROM case_results WHERE run_id=?", (run.id,))
            conn.execute("DELETE FROM protocol_events WHERE run_id=?", (run.id,))
            conn.execute("DELETE FROM fault_events WHERE run_id=?", (run.id,))
            conn.execute(
                "INSERT OR REPLACE INTO test_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run.id, run.suite_id, run.suite_version, run.target_id, run.target_revision, run.spec_manifest_digest,
                 run.fixture_seed, _j(run.environment), run.started_at, run.finished_at, run.run_status, _j(run.profiles), _j(run.counts)),
            )
            for c in bundle.cases:
                conn.execute(
                    "INSERT INTO case_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run.id, c.case_id, c.classification, c.requirement_id, c.result.value, _j(c.expected), _j(c.observed), c.duration_ms,
                     c.reason, c.profile, _j(c.capability_snapshot), _j(c.evidence_references), _j([a.to_dict() for a in c.assertions])),
                )
            for e in bundle.journal.protocol_events:
                conn.execute(
                    "INSERT OR REPLACE INTO protocol_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run.id, e.case_id, e.event_id, e.direction, e.protocol, e.profile_version, e.operation, e.timestamp, e.sequence,
                     e.redacted_payload_reference, e.payload_digest, e.step_id, _j(e.meta)),
                )
            for f in bundle.journal.fault_events:
                conn.execute(
                    "INSERT OR REPLACE INTO fault_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run.id, f.case_id, f.fault_id, f.fault_type, f.trigger_point, f.fired_at, f.sequence, f.affected_operation,
                     _j(f.confirmation_evidence), f.step_id),
                )
        run_dir = self.root / run.id
        (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
        bundle_path = run_dir / "bundle.json"
        bundle_path.write_text(json.dumps(bundle.to_dict(), indent=2, default=str, sort_keys=False), encoding="utf-8")
        for blob in bundle.journal.evidence.all():
            (run_dir / "evidence" / f"{blob['reference']}.json").write_text(json.dumps(blob, indent=2, default=str), encoding="utf-8")
        return bundle_path

    # ----------------------------------------------------------------- read
    def load_bundle(self, run_id: str) -> Dict[str, Any]:
        p = self.root / run_id / "bundle.json"
        if not p.exists():
            raise FileNotFoundError(f"no bundle for run {run_id!r} under {self.root}")
        return json.loads(p.read_text(encoding="utf-8"))

    def list_runs(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id, suite_id, target_id, fixture_seed, started_at, finished_at, run_status, counts FROM test_runs ORDER BY started_at").fetchall()
        return [dict(r, counts=json.loads(r["counts"] or "{}")) for r in rows]

    def latest_run_id(self) -> Optional[str]:
        runs = self.list_runs()
        return runs[-1]["id"] if runs else None
