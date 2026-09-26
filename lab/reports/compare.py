"""Baseline vs candidate comparison (plan §18, §20)."""

from __future__ import annotations

from typing import Any, Dict, List

from lab.runner.results import Result

ORDER = [r.value for r in Result]


def compare(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    b_cases = {c["case_id"]: c for c in baseline["cases"]}
    c_cases = {c["case_id"]: c for c in candidate["cases"]}
    rows: List[Dict[str, Any]] = []
    transitions: Dict[str, int] = {}
    for cid in sorted(set(b_cases) | set(c_cases)):
        b = b_cases.get(cid)
        c = c_cases.get(cid)
        br = b["result"] if b else "MISSING"
        cr = c["result"] if c else "MISSING"
        change = "same" if br == cr else ("improved" if _rank(cr) < _rank(br) else "regressed")
        key = f"{br}->{cr}"
        transitions[key] = transitions.get(key, 0) + 1
        rows.append({
            "case_id": cid,
            "classification": (c or b).get("classification"),
            "profile": (c or b).get("profile"),
            "requirement_id": (c or b).get("requirement_id"),
            "baseline": br,
            "candidate": cr,
            "change": change,
            "baseline_reason": (b or {}).get("reason", ""),
            "candidate_reason": (c or {}).get("reason", ""),
        })
    same_manifest = baseline["run"]["spec_manifest_digest"] == candidate["run"]["spec_manifest_digest"]
    return {
        "baseline": {"run_id": baseline["run"]["id"], "target_id": baseline["run"]["target_id"], "target_revision": baseline["run"]["target_revision"],
                     "summary": baseline["summary"], "spec_manifest_digest": baseline["run"]["spec_manifest_digest"], "seed": baseline["run"]["fixture_seed"]},
        "candidate": {"run_id": candidate["run"]["id"], "target_id": candidate["run"]["target_id"], "target_revision": candidate["run"]["target_revision"],
                      "summary": candidate["summary"], "spec_manifest_digest": candidate["run"]["spec_manifest_digest"], "seed": candidate["run"]["fixture_seed"]},
        "comparable": same_manifest and baseline["run"]["suite_id"] == candidate["run"]["suite_id"],
        "notes": [] if same_manifest else ["spec manifest digests differ: the two runs used different pinned profiles and are not directly comparable"],
        "transitions": transitions,
        "regressions": [r for r in rows if r["change"] == "regressed"],
        "improvements": [r for r in rows if r["change"] == "improved"],
        "rows": rows,
    }


def _rank(result: str) -> int:
    # lower is better; MISSING worst
    order = {"PASS": 0, "NOT_APPLICABLE": 1, "NOT_RUN": 2, "INCONCLUSIVE": 3, "HARNESS_ERROR": 4, "FAIL": 5, "MISSING": 6}
    return order.get(result, 6)


def render_text(diff: Dict[str, Any]) -> str:
    b, c = diff["baseline"], diff["candidate"]
    lines = [
        f"baseline  {b['run_id']}  target={b['target_id']}@{b['target_revision']}  counts={b['summary']['counts']}",
        f"candidate {c['run_id']}  target={c['target_id']}@{c['target_revision']}  counts={c['summary']['counts']}",
        f"comparable: {diff['comparable']}  transitions: {diff['transitions']}",
    ]
    lines += [f"  note: {n}" for n in diff["notes"]]
    for label, rows in (("regressed", diff["regressions"]), ("improved", diff["improvements"])):
        if rows:
            lines.append(f"{label}:")
            for r in rows:
                lines.append(f"  {r['case_id']:52} {r['baseline']:>14} -> {r['candidate']:<14} {r['candidate_reason'][:100]}")
    return "\n".join(lines)
