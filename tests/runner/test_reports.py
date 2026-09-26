"""Assertion failures survive report export (plan §17, §23)."""

from __future__ import annotations

from lab.reports import write_reports
from lab.reports.compare import compare
from lab.runner.results import Result
from lab.runner.store import RunStore
from tests.conftest import run_case, run_suite


def test_fail_reason_and_assertions_are_in_json_junit_and_html(tmp_path) -> None:
    bundle = run_case(
        "local-merchant-broken",
        "acp-complete-response-lost-replay",
        run_id="report-fail",
    )
    assert bundle.cases[0].result == Result.FAIL
    store = RunStore(tmp_path)
    store.save(bundle)
    outputs = write_reports(bundle.to_dict(), tmp_path / bundle.run.id)
    json_text = outputs["json"].read_text(encoding="utf-8")
    junit_text = outputs["junit"].read_text(encoding="utf-8")
    html_text = outputs["html"].read_text(encoding="utf-8")
    assert "FAIL" in json_text
    assert "acp-complete-response-lost-replay" in json_text
    assert "settled_charge_count_for_operation" in json_text
    assert "<failure" in junit_text
    assert "type=\"FAIL\"" in junit_text
    assert "acp-complete-response-lost-replay" in html_text
    assert "FAIL" in html_text


def test_sqlite_persists_case_result(tmp_path) -> None:
    bundle = run_case("local-merchant-corrected", "acp-create-session-201", run_id="report-sql")
    store = RunStore(tmp_path)
    store.save(bundle)
    loaded = store.load_bundle(bundle.run.id)
    assert loaded["cases"][0]["result"] == "PASS"
    rows = store.list_runs()
    assert rows[-1]["id"] == bundle.run.id


def test_compare_marks_broken_to_corrected_as_improvement() -> None:
    broken = run_suite("local-merchant-broken", "demo-broken-vs-corrected", run_id="cmp-broken")
    corrected = run_suite("local-merchant-corrected", "demo-broken-vs-corrected", run_id="cmp-corrected")
    diff = compare(broken.to_dict(), corrected.to_dict())
    assert diff["comparable"] is True
    assert diff["improvements"]
    assert not diff["regressions"]
    assert all(row["baseline"] == "FAIL" and row["candidate"] == "PASS" for row in diff["improvements"])
