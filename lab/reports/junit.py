"""JUnit XML export.

Mapping of lab results onto JUnit's vocabulary (documented in docs/reports.md):

* PASS → passed testcase
* FAIL → <failure type="FAIL">
* INCONCLUSIVE → <failure type="INCONCLUSIVE"> (CI must not go green on evidence the lab could not establish)
* HARNESS_ERROR → <error>
* NOT_APPLICABLE, NOT_RUN → <skipped> with the reason
"""

from __future__ import annotations

from typing import Any, Dict
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


def _fmt_assertions(case: Dict[str, Any]) -> str:
    lines = [f"[{case['result']}] {case['case_id']} — {case.get('purpose', '').strip()}"]
    if case.get("requirement_source"):
        src = case["requirement_source"]
        lines.append(f"requirement: {src.get('id')} ({src.get('kind')}) — {src.get('source', {}).get('document', '')} › {src.get('source', {}).get('section', '')}")
    if case.get("reason"):
        lines.append(f"reason: {case['reason']}")
    for a in case.get("assertions", []):
        lines.append(f"  - [{a['result']}] {a['description']}: expected={a.get('expected')!r} observed={a.get('observed')!r} {a.get('detail') or ''}")
    if case.get("evidence_references"):
        lines.append("evidence: " + ", ".join(case["evidence_references"]))
    return "\n".join(lines)


def render(bundle: Dict[str, Any]) -> str:
    run = bundle["run"]
    root = ET.Element("testsuites", name=f"commerce-lab {run['suite_id']} @ {run['target_id']}")
    by_class: Dict[str, list] = {}
    for c in bundle["cases"]:
        by_class.setdefault(c["classification"], []).append(c)
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for classification, cases in by_class.items():
        suite = ET.SubElement(root, "testsuite", name=f"{run['suite_id']}.{classification}", timestamp=run["started_at"])
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        for c in cases:
            counts["tests"] += 1
            tc = ET.SubElement(suite, "testcase", name=c["case_id"], classname=f"{c['profile']}.{classification}",
                               time=f"{c['duration_ms'] / 1000:.3f}")
            props = ET.SubElement(tc, "properties")
            for k, v in (("lab_result", c["result"]), ("profile", c["profile"]), ("requirement_id", c.get("requirement_id") or ""),
                         ("target_id", run["target_id"]), ("seed", str(run["fixture_seed"]))):
                ET.SubElement(props, "property", name=k, value=str(v))
            text = _fmt_assertions(c)
            if c["result"] == "FAIL":
                counts["failures"] += 1
                el = ET.SubElement(tc, "failure", type="FAIL", message=(c.get("reason") or "assertion failed")[:512])
                el.text = text
            elif c["result"] == "INCONCLUSIVE":
                counts["failures"] += 1
                el = ET.SubElement(tc, "failure", type="INCONCLUSIVE", message=(c.get("reason") or "inconclusive")[:512])
                el.text = text
            elif c["result"] == "HARNESS_ERROR":
                counts["errors"] += 1
                el = ET.SubElement(tc, "error", type="HARNESS_ERROR", message=(c.get("reason") or "harness error")[:512])
                el.text = text
            elif c["result"] in ("NOT_APPLICABLE", "NOT_RUN"):
                counts["skipped"] += 1
                el = ET.SubElement(tc, "skipped", message=f"{c['result']}: {c.get('reason') or ''}"[:512])
                el.text = text
            else:
                ET.SubElement(tc, "system-out").text = text
        for k, v in counts.items():
            suite.set(k, str(v))
            total[k] += v
    for k, v in total.items():
        root.set(k, str(v))
    ET.indent(root) if hasattr(ET, "indent") else None
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
