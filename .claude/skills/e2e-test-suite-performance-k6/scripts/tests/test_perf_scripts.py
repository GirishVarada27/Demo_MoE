"""Unit tests for the skill's scripts (no k6 or app needed).

    python -m pytest <skill>/scripts/tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_guard import GuardError, check_db_url, check_target_url, redact  # noqa: E402
from mock_llm_server import Mock  # noqa: E402
from report import build_report, pct  # noqa: E402
from run_perf import SetupError, build_plan, validate_nfr  # noqa: E402

APP = {
    "flows": [{"name": "f", "steps": [{"name": "GET /", "path": "/"}]}],
    "db_guard": {"protected_paths": ["data"], "prod_db_urls": ["postgres://db.internal:5432/app"], "prod_env_vars": ["DATABASE_URL"]},
    "llm": {"real": {"max_tps": 0.5, "max_vus": 2}},
}
NFR = {"expected_tps": 2, "projected_tps": 5, "concurrent_users": 20, "latency_ms": {"p95": 800, "p99": 1500}, "max_error_rate": 0.01}


# --- db_guard -----------------------------------------------------------------


@pytest.mark.parametrize("url", [None, "", "   ", "not-a-url"])
def test_guard_requires_a_url(url, tmp_path):
    with pytest.raises(GuardError):
        check_db_url(url, APP, tmp_path)


@pytest.mark.parametrize("url", [
    "postgres://user:pw@prod-db.example.com/app",
    "postgres://db.example.com/app_production",
    "mysql://live.db.example.com/app",
    "file:///D:/prod/data",
])
def test_guard_rejects_production_looking_urls(url, tmp_path):
    with pytest.raises(GuardError, match="production"):
        check_db_url(url, APP, tmp_path)


def test_guard_rejects_known_prod_url_even_with_other_credentials(tmp_path):
    with pytest.raises(GuardError, match="production/real"):
        check_db_url("postgres://loadtest:secret@db.internal:5432/app?sslmode=require", APP, tmp_path)


def test_guard_rejects_prod_url_from_dotenv(tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgres://main-db.corp/app\n", encoding="utf-8")
    with pytest.raises(GuardError):
        check_db_url("postgres://main-db.corp/app", APP, tmp_path)


def test_guard_rejects_app_data_dir_and_repo(tmp_path):
    (tmp_path / "data").mkdir()
    with pytest.raises(GuardError, match="real data"):
        check_db_url((tmp_path / "data").as_uri(), APP, tmp_path)
    with pytest.raises(GuardError):
        check_db_url(tmp_path.as_uri(), APP, tmp_path)


def test_guard_accepts_a_separate_loadtest_db(tmp_path):
    assert check_db_url("postgres://perf:pw@loadtest-db.internal/app_perf", APP, tmp_path) == "postgres://perf:***@loadtest-db.internal/app_perf"
    assert check_db_url((tmp_path.parent / "perf-data").as_uri(), APP, tmp_path)


def test_guard_allow_list_pattern(tmp_path):
    app = {**APP, "db_guard": {**APP["db_guard"], "allowed_url_pattern": r"loadtest"}}
    with pytest.raises(GuardError, match="pattern"):
        check_db_url("postgres://qa-db.internal/app", app, tmp_path)


def test_target_guard():
    check_target_url("http://127.0.0.1:8000", APP)
    with pytest.raises(GuardError):
        check_target_url("https://api.prod.example.com", APP)


def test_redact_hides_password():
    assert "secret" not in redact("postgres://u:secret@h:5432/db")


# --- NFR + plan ---------------------------------------------------------------


def test_validate_nfr_requires_every_field():
    with pytest.raises(SetupError, match="concurrent_users"):
        validate_nfr({k: v for k, v in NFR.items() if k != "concurrent_users"})
    with pytest.raises(SetupError, match="projected_tps"):
        validate_nfr({**NFR, "projected_tps": 1})
    validate_nfr(NFR)


def test_plan_reflects_the_nfrs():
    plan = build_plan(APP, NFR, "pre-merge", "http://x", "mock", 1)
    assert [p["name"] for p in plan["phases"]] == ["smoke", "load", "projected", "concurrency"]
    assert plan["scenarios"]["load"]["rate"] == 2 and plan["scenarios"]["projected"]["rate"] == 5
    assert plan["scenarios"]["concurrency"]["vus"] == 20
    assert plan["thresholds"]["http_req_duration{scenario:load}"] == ["p(95)<800", "p(99)<1500"]
    assert plan["thresholds"]["http_req_failed{scenario:projected}"] == ["rate<0.01"]
    # Phases run one after another, never overlapping.
    starts = [p["start_s"] for p in plan["phases"]]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_fractional_tps_uses_per_minute_rate():
    plan = build_plan(APP, {**NFR, "expected_tps": 0.5, "projected_tps": 1.5}, "pre-merge", "http://x", "mock", 1)
    assert plan["scenarios"]["load"]["rate"] == 30 and plan["scenarios"]["load"]["timeUnit"] == "1m"


def test_real_llm_mode_caps_load_and_says_so():
    plan = build_plan(APP, NFR, "pre-merge", "http://x", "real", 1)
    assert plan["scenarios"]["projected"]["timeUnit"] == "1m" and plan["scenarios"]["projected"]["rate"] == 30
    assert plan["scenarios"]["concurrency"]["vus"] == 2
    assert plan["notes"] and "NOT tested at full rate" in plan["notes"][0]


def test_endpoint_thresholds():
    plan = build_plan(APP, {**NFR, "endpoints": {"GET /": {"p95": 200}}}, "pre-merge", "http://x", "mock", 1)
    assert plan["thresholds"]["http_req_duration{scenario:load,name:GET /}"] == ["p(95)<200"]


# --- mock LLM -----------------------------------------------------------------


def test_mock_follows_tool_script_then_answers():
    script = {"tool_steps": [{"tool": "lookup", "args": {"id": "{{user:APP-\\d+}}"}},
                             {"tool": "eval", "args": {"c": "{{tool:lookup:applicant.curriculum}}"}}],
              "final_answer": "done for {{tool:lookup:applicant.curriculum}}"}
    mock = Mock(script, 0, 0, 0, 1)
    msgs = [{"role": "user", "content": "My ID is APP-007"}]
    r1 = mock.chat({"messages": msgs, "tools": [{}]})["choices"][0]["message"]
    assert json.loads(r1["tool_calls"][0]["function"]["arguments"]) == {"id": "APP-007"}
    msgs += [r1, {"role": "tool", "tool_call_id": r1["tool_calls"][0]["id"], "content": json.dumps({"applicant": {"curriculum": "IB"}})}]
    r2 = mock.chat({"messages": msgs, "tools": [{}]})["choices"][0]["message"]
    assert json.loads(r2["tool_calls"][0]["function"]["arguments"]) == {"c": "IB"}
    msgs += [r2, {"role": "tool", "tool_call_id": r2["tool_calls"][0]["id"], "content": "{}"}]
    assert mock.chat({"messages": msgs, "tools": [{}]})["choices"][0]["message"]["content"] == "done for IB"
    assert mock.chat({"messages": msgs})["choices"][0]["message"]["content"] == "PASS"  # no tools offered


def test_mock_embeddings_are_deterministic():
    mock = Mock({}, 0, 0, 0, 1)
    a = mock.embeddings({"input": ["attested transcript"]})["data"][0]["embedding"]
    b = mock.embeddings({"input": ["attested transcript"]})["data"][0]["embedding"]
    assert a == b and abs(sum(x * x for x in a) - 1) < 1e-9


# --- report -------------------------------------------------------------------


def _write_run(run_dir: Path, latencies: list[float], statuses: list[str], nfr=NFR):
    plan = build_plan(APP, nfr, "smoke", "http://x", "mock", 1)
    plan["phases"] = [{"name": "load", "start_s": 0, "duration_s": 10, "gated": True, "target_tps": 1}]
    (run_dir / "plan.json").write_text(json.dumps(plan))
    (run_dir / "nfr.json").write_text(json.dumps(nfr))
    (run_dir / "meta.json").write_text(json.dumps({"k6_exit_code": 0}))
    rows = ["metric_name,timestamp,metric_value,check,error,error_code,expected_response,group,method,name,proto,scenario,service,status,subproto,tls_version,url,extra_tags,metadata"]
    for i, (ms, st) in enumerate(zip(latencies, statuses)):
        t = 1000 + i
        failed = "1" if not st.startswith("2") else "0"
        rows += [f"http_reqs,{t},1,,,,true,,GET,GET /,,load,,{st},,,,,", f"http_req_duration,{t},{ms},,,,true,,GET,GET /,,load,,{st},,,,,",
                 f"http_req_failed,{t},{failed},,,,true,,GET,GET /,,load,,{st},,,,,", f"iterations,{t},1,,,,,,,,,load,,,,,,,"]
    (run_dir / "metrics.csv").write_text("\n".join(rows) + "\n")


def test_report_passes_within_nfr(tmp_path):
    _write_run(tmp_path, [100.0] * 10, ["200"] * 10)
    result = build_report(tmp_path, None)
    assert result["verdict"] == "PASS"
    assert (tmp_path / "report.html").exists() and (tmp_path / "report.md").exists()


def test_report_fails_and_explains_5xx(tmp_path):
    _write_run(tmp_path, [100.0] * 8 + [2000.0, 2500.0], ["200"] * 8 + ["500", "500"])
    result = build_report(tmp_path, None)
    assert result["verdict"] == "FAIL"
    failed = {c["criterion"] for c in result["failed_checks"]}
    assert {"latency p95", "error rate"} <= failed
    assert any("5xx" in d["title"] for d in result["diagnostics"])


def test_report_regression_only_gates_on_comparable_baseline(tmp_path):
    base_dir, cur_dir = tmp_path / "base", tmp_path / "cur"
    base_dir.mkdir(), cur_dir.mkdir()
    _write_run(base_dir, [100.0] * 10, ["200"] * 10)
    build_report(base_dir, None)
    _write_run(cur_dir, [300.0] * 10, ["200"] * 10)
    assert build_report(cur_dir, base_dir / "results.json")["regression"]["regressed"] is True

    _write_run(cur_dir, [300.0] * 10, ["200"] * 10, nfr={**NFR, "expected_tps": 9, "projected_tps": 9})
    reg = build_report(cur_dir, base_dir / "results.json")["regression"]
    assert reg["comparable"] is False and reg["regressed"] is False


def test_pct():
    assert pct([1, 2, 3, 4], 50) == 2.5 and pct([], 95) is None
