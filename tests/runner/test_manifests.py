"""Invalid scenario definitions are rejected (plan §23)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lab.runner.manifests import (
    ManifestError,
    all_scenarios,
    list_profiles,
    list_suites,
    load_profile,
    load_requirements,
    load_scenario,
    load_suite,
    load_target,
    verify_pinned_schemas,
)


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _base_scenario(**overrides) -> dict:
    data = {
        "id": "bad-scenario-example",
        "title": "A deliberately invalid scenario",
        "purpose": "Prove the loader rejects definitions that do not satisfy the schema.",
        "classification": "protocol_conformance",
        "profile": "acp-checkout-2026-04-17",
        "role": "agent",
        "requirement": "LAB-ACP-SESS-001",
        "requires_capabilities": ["checkout"],
        "preconditions": ["fixture_reset"],
        "steps": [{"id": "create", "action": "create_checkout_session"}],
        "assertions": [{"kind": "status", "step": "create", "equals": 201}],
        "evidence": ["redacted_protocol_trace"],
    }
    data.update(overrides)
    return data


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    data = _base_scenario()
    del data["purpose"]
    with pytest.raises(ManifestError, match="does not satisfy"):
        load_scenario(_write(tmp_path, "missing.yaml", data))


def test_unknown_assertion_kind_is_rejected(tmp_path: Path) -> None:
    data = _base_scenario(assertions=[{"kind": "vibes", "step": "create"}])
    with pytest.raises(ManifestError, match="does not satisfy"):
        load_scenario(_write(tmp_path, "kind.yaml", data))


def test_conformance_case_without_requirement_is_rejected(tmp_path: Path) -> None:
    data = _base_scenario()
    del data["requirement"]
    with pytest.raises(ManifestError, match="does not satisfy"):
        load_scenario(_write(tmp_path, "noreq.yaml", data))


def test_robustness_case_without_invariant_is_rejected(tmp_path: Path) -> None:
    data = _base_scenario(
        classification="application_robustness",
        assertions=[{"kind": "metric", "metric": "merchant_order_count_for_operation", "equals": 1}],
    )
    data.pop("requirement", None)
    with pytest.raises(ManifestError, match="does not satisfy"):
        load_scenario(_write(tmp_path, "noinv.yaml", data))


def test_duplicate_step_ids_are_rejected(tmp_path: Path) -> None:
    data = _base_scenario(
        steps=[
            {"id": "create", "action": "create_checkout_session"},
            {"id": "create", "action": "complete_checkout"},
        ]
    )
    with pytest.raises(ManifestError, match="duplicate step ids"):
        load_scenario(_write(tmp_path, "dup.yaml", data))


def test_assertion_referencing_unknown_step_is_rejected(tmp_path: Path) -> None:
    data = _base_scenario(assertions=[{"kind": "status", "step": "no-such-step", "equals": 200}])
    with pytest.raises(ManifestError, match="unknown step"):
        load_scenario(_write(tmp_path, "badstep.yaml", data))


def test_unknown_capability_is_never_assumed(tmp_path: Path) -> None:
    profiles = {p.id: p for p in list_profiles()}
    data = _base_scenario(requires_capabilities=["telepathy"])
    with pytest.raises(ManifestError, match="not defined by profile"):
        load_scenario(_write(tmp_path, "telepathy.yaml", data), profiles=profiles)


def test_unknown_requirement_id_is_rejected(tmp_path: Path) -> None:
    reqs = load_requirements()
    data = _base_scenario(requirement="LAB-ACP-FAKE-999")
    with pytest.raises(ManifestError, match="unknown requirement"):
        load_scenario(_write(tmp_path, "fakereq.yaml", data), requirements=reqs)


def test_published_scenarios_and_suites_load() -> None:
    profiles = {p.id: p for p in list_profiles()}
    reqs = load_requirements()
    scenarios = all_scenarios(profiles=profiles, requirements=reqs)
    assert len(scenarios) == 54
    for sid in list_suites():
        suite = load_suite(sid, profiles=profiles, requirements=reqs)
        assert suite.scenarios


def test_pinned_schema_checksums_match() -> None:
    for profile in list_profiles():
        for row in verify_pinned_schemas(profile):
            assert row["ok"], f"{profile.id} {row}"


def test_targets_claim_known_profiles() -> None:
    profiles = {p.id for p in list_profiles()}
    for target in (
        "local-merchant-broken",
        "local-merchant-corrected",
        "local-merchant-minimal",
        "local-merchant-misdeclared",
    ):
        t = load_target(target)
        assert set(t.profiles) <= profiles


def test_profile_id_matches_filename() -> None:
    p = load_profile("acp-checkout-2026-04-17")
    assert p.protocol == "acp"
    assert p.status == "released_snapshot"
