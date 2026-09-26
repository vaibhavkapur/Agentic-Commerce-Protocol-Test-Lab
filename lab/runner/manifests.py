"""Loading and strict validation of profiles, targets, requirements, suites, and scenarios."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from jsonschema import Draft202012Validator

from .. import paths


class ManifestError(ValueError):
    """Raised when a manifest or scenario file is invalid."""


def _load_yaml(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        raise ManifestError(f"file not found: {path}")
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML in {path}: {exc}")


def _validate(instance: Any, schema_name: str, path: Path) -> None:
    schema = json.loads((paths.SCHEMAS / schema_name).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        lines = []
        for e in errors[:10]:
            loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
            lines.append(f"  at {loc}: {e.message}")
        raise ManifestError(f"{path} does not satisfy {schema_name}:\n" + "\n".join(lines))


@dataclass
class Capability:
    id: str
    discovery: str
    description: str = ""


@dataclass
class Profile:
    id: str
    protocol: str
    release: str
    status: str
    specification: Dict[str, Any]
    schemas: List[Dict[str, Any]]
    roles: Dict[str, str]
    transport: str
    required_capabilities: List[Capability]
    optional_capabilities: List[Capability]
    driver: str
    description: str = ""
    lab_decisions: List[str] = field(default_factory=list)
    reference_implementation: Dict[str, Any] = field(default_factory=dict)
    sdk: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def capability_ids(self) -> List[str]:
        return [c.id for c in self.required_capabilities + self.optional_capabilities]

    def is_required(self, cap_id: str) -> bool:
        return any(c.id == cap_id for c in self.required_capabilities)

    def is_optional(self, cap_id: str) -> bool:
        return any(c.id == cap_id for c in self.optional_capabilities)

    def manifest_digest(self) -> str:
        blob = json.dumps(self.raw, sort_keys=True).encode("utf-8")
        return "sha256:" + hashlib.sha256(blob).hexdigest()


def load_profile(profile_id: str) -> Profile:
    path = paths.PROFILES / f"{profile_id}.yaml"
    data = _load_yaml(path)
    _validate(data, "profile.schema.json", path)
    if data["id"] != profile_id:
        raise ManifestError(f"profile id {data['id']!r} does not match filename {path.name}")
    caps = data["capabilities"]
    return Profile(
        id=data["id"],
        protocol=data["protocol"],
        release=data["release"],
        status=data["status"],
        specification=data["specification"],
        schemas=data.get("schemas", []),
        roles=data["roles"],
        transport=data["transport"],
        required_capabilities=[Capability(**c) for c in caps["required"]],
        optional_capabilities=[Capability(**c) for c in caps["optional"]],
        driver=data["driver"],
        description=data.get("description", ""),
        lab_decisions=data.get("lab_decisions", []),
        reference_implementation=data.get("reference_implementation", {}),
        sdk=data.get("sdk", {}),
        raw=data,
    )


def list_profiles() -> List[Profile]:
    return [load_profile(p.stem) for p in sorted(paths.PROFILES.glob("*.yaml"))]


def verify_pinned_schemas(profile: Profile) -> List[Dict[str, Any]]:
    """Check that every pinned schema file still matches its recorded checksum."""
    report = []
    for entry in profile.schemas:
        p = paths.ROOT / entry["path"]
        if not p.exists():
            report.append({"path": entry["path"], "ok": False, "reason": "missing"})
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        report.append({"path": entry["path"], "ok": actual == entry["sha256"], "expected": entry["sha256"], "actual": actual})
    return report


@dataclass
class Target:
    id: str
    kind: str
    revision: str
    profiles: List[str]
    observation: Dict[str, Any]
    factory: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    base_url: Optional[str] = None
    proxy: Dict[str, Any] = field(default_factory=dict)
    declared_guarantees: List[str] = field(default_factory=list)
    description: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def load_target(target_id: str) -> Target:
    path = paths.TARGETS / f"{target_id}.yaml"
    data = _load_yaml(path)
    _validate(data, "target.schema.json", path)
    if data["id"] != target_id:
        raise ManifestError(f"target id {data['id']!r} does not match filename {path.name}")
    if data["kind"] == "local_reference" and not data.get("factory"):
        raise ManifestError(f"{path}: local_reference targets need a factory")
    if data["kind"] == "network" and not data.get("base_url"):
        raise ManifestError(f"{path}: network targets need a base_url")
    return Target(
        id=data["id"],
        kind=data["kind"],
        revision=data["revision"],
        profiles=data["profiles"],
        observation=data["observation"],
        factory=data.get("factory"),
        options=data.get("options", {}),
        base_url=data.get("base_url"),
        proxy=data.get("proxy", {}),
        declared_guarantees=data.get("declared_guarantees", []),
        description=data.get("description", ""),
        raw=data,
    )


def list_targets() -> List[Target]:
    return [load_target(p.stem) for p in sorted(paths.TARGETS.glob("*.yaml"))]


@dataclass
class Requirement:
    id: str
    kind: str
    source: Dict[str, Any]
    statement: str
    expected_behavior: str
    applicability: str
    profile: str
    keyword: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "keyword": self.keyword,
            "source": self.source,
            "statement": self.statement,
            "expected_behavior": self.expected_behavior,
            "applicability": self.applicability,
            "profile": self.profile,
        }


def load_requirements() -> Dict[str, Requirement]:
    reqs: Dict[str, Requirement] = {}
    for path in sorted(paths.REQUIREMENTS.glob("*.yaml")):
        data = _load_yaml(path)
        _validate(data, "requirement.schema.json", path)
        for r in data["requirements"]:
            if r["id"] in reqs:
                raise ManifestError(f"duplicate requirement id {r['id']} in {path}")
            reqs[r["id"]] = Requirement(profile=data["profile"], **r)
    return reqs


@dataclass
class Scenario:
    id: str
    title: str
    purpose: str
    classification: str
    profile: str
    role: str
    steps: List[Dict[str, Any]]
    assertions: List[Dict[str, Any]]
    evidence: List[str]
    requirement: Optional[str] = None
    invariant: Optional[str] = None
    requires_capabilities: List[str] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    fixtures: Dict[str, str] = field(default_factory=dict)
    cleanup: str = "reset_fixture"
    tags: List[str] = field(default_factory=list)
    notes: str = ""
    source_path: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def step(self, step_id: str) -> Optional[Dict[str, Any]]:
        for s in self.steps:
            if s["id"] == step_id:
                return s
        return None


def load_scenario(path: Path, *, profiles: Optional[Dict[str, Profile]] = None,
                  requirements: Optional[Dict[str, Requirement]] = None) -> Scenario:
    data = _load_yaml(path)
    _validate(data, "scenario.schema.json", path)
    step_ids = [s["id"] for s in data["steps"]]
    if len(step_ids) != len(set(step_ids)):
        raise ManifestError(f"{path}: duplicate step ids")
    for a in data["assertions"]:
        for key in ("step", "fault_step"):
            if key in a and a[key] not in step_ids:
                raise ManifestError(f"{path}: assertion references unknown step {a[key]!r}")
    if profiles is not None:
        prof = profiles.get(data["profile"])
        if prof is None:
            raise ManifestError(f"{path}: unknown profile {data['profile']!r}")
        known = set(prof.capability_ids())
        for cap in data.get("requires_capabilities", []):
            if cap not in known:
                raise ManifestError(
                    f"{path}: capability {cap!r} is not defined by profile {prof.id}; "
                    "unknown capabilities are never assumed"
                )
    if requirements is not None and data.get("requirement"):
        if data["requirement"] not in requirements:
            raise ManifestError(f"{path}: unknown requirement id {data['requirement']!r}")
    fields = {k: v for k, v in data.items()}
    return Scenario(source_path=str(path), raw=data, **fields)


@dataclass
class Suite:
    id: str
    version: str
    description: str
    scenarios: List[Scenario]
    source_path: str


def load_suite(suite_id: str, *, profiles: Optional[Dict[str, Profile]] = None,
               requirements: Optional[Dict[str, Requirement]] = None) -> Suite:
    path = paths.SUITES / f"{suite_id}.yaml"
    data = _load_yaml(path)
    _validate(data, "suite.schema.json", path)
    scenarios = []
    seen = set()
    for rel in data["cases"]:
        sc = load_scenario(paths.SUITES / rel, profiles=profiles, requirements=requirements)
        if sc.id in seen:
            raise ManifestError(f"{path}: duplicate case id {sc.id}")
        seen.add(sc.id)
        scenarios.append(sc)
    return Suite(id=data["id"], version=data["version"], description=data["description"], scenarios=scenarios,
                 source_path=str(path))


def list_suites() -> List[str]:
    return [p.stem for p in sorted(paths.SUITES.glob("*.yaml"))]


def find_scenario(case_id: str, *, profiles=None, requirements=None) -> Scenario:
    for path in sorted(paths.SUITES.rglob("*.yaml")):
        if path.parent == paths.SUITES:
            continue
        data = _load_yaml(path)
        if isinstance(data, dict) and data.get("id") == case_id:
            return load_scenario(path, profiles=profiles, requirements=requirements)
    raise ManifestError(f"no scenario with id {case_id!r} under {paths.SUITES}")


def all_scenarios(*, profiles=None, requirements=None) -> List[Scenario]:
    out = []
    for path in sorted(paths.SUITES.rglob("*.yaml")):
        if path.parent == paths.SUITES:
            continue
        out.append(load_scenario(path, profiles=profiles, requirements=requirements))
    return out
