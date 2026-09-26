"""Report exporters: JSON bundle (via the store), JUnit XML, browser HTML, and comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from . import compare as compare_mod
from . import html as html_mod
from . import junit as junit_mod


def write_reports(bundle_dict: Dict[str, Any], run_dir: Path, *, formats=("json", "junit", "html"),
                  comparison: Optional[Dict[str, Any]] = None) -> Dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Path] = {}
    if "json" in formats:
        p = run_dir / "report.json"
        summary = {k: bundle_dict[k] for k in ("run", "summary", "capability_snapshots", "schema_integrity")}
        summary["cases"] = [{k: c[k] for k in ("case_id", "classification", "requirement_id", "profile", "result", "reason", "duration_ms",
                                                "evidence_references", "requirement_source")} for c in bundle_dict["cases"]]
        p.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        out["json"] = p
    if "junit" in formats:
        p = run_dir / "junit.xml"
        p.write_text(junit_mod.render(bundle_dict), encoding="utf-8")
        out["junit"] = p
    if "html" in formats:
        p = run_dir / "report.html"
        p.write_text(html_mod.render(bundle_dict, comparison), encoding="utf-8")
        out["html"] = p
    return out


__all__ = ["write_reports", "compare_mod", "html_mod", "junit_mod"]
