"""Single-file browser report: the dashboard template with the run bundle embedded."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .. import paths


def _safe_json(value: Any) -> str:
    # Prevent </script> breakouts inside the embedded JSON.
    return json.dumps(value, default=str).replace("</", "<\\/")


def render(bundle: Dict[str, Any], comparison: Optional[Dict[str, Any]] = None, *, embed_evidence: bool = True) -> str:
    template = (paths.DASHBOARD / "template.html").read_text(encoding="utf-8")
    data = dict(bundle)
    if not embed_evidence:
        data["evidence"] = []
    html = template.replace("/*__BUNDLE__*/", _safe_json(data))
    html = html.replace("/*__COMPARE__*/", _safe_json(comparison) if comparison else "null")
    return html
