"""Repository-relative paths used by the runner."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("COMMERCE_LAB_ROOT", Path(__file__).resolve().parent.parent))
SCHEMAS = ROOT / "schemas"
PINNED = SCHEMAS / "pinned"
MANIFESTS = ROOT / "manifests"
PROFILES = MANIFESTS / "profiles"
TARGETS = MANIFESTS / "targets"
REQUIREMENTS = MANIFESTS / "requirements"
SUITES = ROOT / "suites"
RUNS = Path(os.environ.get("COMMERCE_LAB_RUNS", ROOT / "runs"))
DASHBOARD = ROOT / "dashboard"
