"""Unit tests for the UI skill's scripts (no browser or app needed).

    python -m pytest <skill>/scripts/tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mock_llm_server import Mock  # noqa: E402
from run_ui import fingerprint_paths  # noqa: E402
from ui_report import build_summary  # noqa: E402


def _spec(title: str, status: str, error: str | None = None, attempts: int = 1, tags=None) -> dict:
    results = [{"status": "failed" if status != "expected" else "passed", "duration": 100,
                "error": {"message": error} if error else None, "attachments": []} for _ in range(attempts)]
    if status == "flaky":
        results[-1]["status"], results[-1]["error"] = "passed", None
    return {"title": title, "file": "a.spec.ts", "line": 3, "tags": tags or [],
            "tests": [{"projectName": "chromium", "status": status, "results": results}]}


def _write_run(root: Path, name: str, specs: list[dict], errors=None) -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "results.json").write_text(json.dumps({
        "suites": [{"title": "a.spec.ts", "file": "a.spec.ts", "specs": [], "suites": [{"title": "area", "specs": specs}]}],
        "errors": errors or []}), encoding="utf-8")
    return run


META = {"app_name": "x", "protected_changed": [], "playwright_exit_code": 0}


def test_classifies_passed_failed_flaky_skipped(tmp_path):
    run = _write_run(tmp_path, "20260101-000000", [
        _spec("ok", "expected"), _spec("broken", "unexpected", "\x1b[31mError: expected 3\x1b[0m\nmore", attempts=2),
        _spec("wobbly", "flaky", "timeout", attempts=2), _spec("later", "skipped")])
    s = build_summary(run, {**META, "playwright_exit_code": 1}, tmp_path)
    assert s["counts"] == {"passed": 1, "failed": 1, "flaky": 1, "skipped": 1, "total": 4}
    broken = next(t for t in s["tests"] if t["status"] == "failed")
    assert broken["error"] == "Error: expected 3" and broken["title"] == "area > broken"  # ANSI stripped
    assert s["verdict"] == "FAIL" and "1 test(s) failed" in s["reasons"]
    assert (run / "summary.md").exists()


def test_flaky_passes_unless_fail_on_flaky(tmp_path):
    run = _write_run(tmp_path, "20260101-000000", [_spec("ok", "expected"), _spec("wobbly", "flaky", "x", attempts=2)])
    assert build_summary(run, META, tmp_path)["verdict"] == "PASS"
    assert build_summary(run, META, tmp_path, fail_on_flaky=True)["verdict"] == "FAIL"


def test_protected_data_change_fails_the_run_even_if_tests_pass(tmp_path):
    run = _write_run(tmp_path, "20260101-000000", [_spec("ok", "expected")])
    s = build_summary(run, {**META, "protected_changed": ["data/orders.json"]}, tmp_path)
    assert s["verdict"] == "FAIL" and "protected data modified" in s["reasons"][0]
    assert "MODIFIED" in (run / "summary.md").read_text(encoding="utf-8")


def test_no_results_or_global_errors_fail(tmp_path):
    empty = tmp_path / "20260101-000000"
    empty.mkdir()
    assert build_summary(empty, META, tmp_path)["verdict"] == "FAIL"
    run = _write_run(tmp_path, "20260101-000001", [_spec("ok", "expected")], errors=[{"message": "config error"}])
    assert build_summary(run, META, tmp_path)["verdict"] == "FAIL"


def test_comparison_with_previous_run(tmp_path):
    first = _write_run(tmp_path, "20260101-000000", [_spec("a", "expected"), _spec("b", "unexpected", "x"), _spec("c", "unexpected", "x"),
                                                     _spec("gone", "expected")])
    build_summary(first, META, tmp_path)
    second = _write_run(tmp_path, "20260101-000100", [_spec("a", "unexpected", "x"), _spec("b", "expected"), _spec("c", "unexpected", "x"),
                                                      _spec("new", "expected")])
    comp = build_summary(second, META, tmp_path)["comparison"]
    name = lambda t: f"a.spec.ts > area > {t} [chromium]"  # noqa: E731
    assert comp["previous_run"] == "20260101-000000"
    assert comp["newly_failing"] == [name("a")] and comp["fixed"] == [name("b")] and comp["still_failing"] == [name("c")]
    assert comp["new_tests"] == [name("new")] and comp["removed_tests"] == [name("gone")]


def test_filtered_run_doesnt_report_unselected_tests_as_removed(tmp_path):
    first = _write_run(tmp_path, "20260101-000000", [_spec("a", "expected"), _spec("b", "expected")])
    build_summary(first, META, tmp_path)
    second = _write_run(tmp_path, "20260101-000100", [_spec("a", "expected")])
    assert build_summary(second, {**META, "grep": "@smoke"}, tmp_path)["comparison"]["removed_tests"] == []


def test_fingerprint_detects_changes_and_ignores_lock_files(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "q.json").write_text("[]")
    before = fingerprint_paths(tmp_path, ["data"])
    (tmp_path / "data" / "q.json.lock").write_text("")
    assert fingerprint_paths(tmp_path, ["data"]) == before
    (tmp_path / "data" / "q.json").write_text('[{"x": 1}]')
    assert fingerprint_paths(tmp_path, ["data"]) != before


def test_mock_scenarios_pick_their_own_script():
    script = {
        "tool_steps": [{"tool": "lookup", "args": {"id": "{{user:APP-\\d+}}"}}],
        "final_answer": "default for {{user:APP-\\d+}}",
        "scenarios": [{"match": "APP-003", "final_answer": "missing doc"},
                      {"match": "^(?i:hi)\\b(?!.*APP-\\d+)", "tool_steps": [], "final_answer": "hello"}],
    }
    mock = Mock(script, 0, 0, 0, 1)

    def answer(text: str) -> str:
        msgs = [{"role": "user", "content": text}]
        while True:
            m = mock.chat({"messages": msgs, "tools": [{}]})["choices"][0]["message"]
            if not m.get("tool_calls"):
                return m["content"]
            msgs += [m, {"role": "tool", "tool_call_id": m["tool_calls"][0]["id"], "content": "{}"}]

    assert answer("My ID is APP-001") == "default for APP-001"
    assert answer("My ID is APP-003") == "missing doc"  # inherits tool_steps, own answer
    assert answer("hi there") == "hello"
    assert answer("Hi, my ID is APP-001") == "default for APP-001"  # greeting with an id isn't the greeting scenario
