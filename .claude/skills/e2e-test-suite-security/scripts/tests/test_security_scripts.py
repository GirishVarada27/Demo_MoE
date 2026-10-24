"""Unit tests for the security skill's scripts.

    python -m pytest <skill>/scripts/tests -q
Positive-control tests (bandit, gitleaks) plant a known issue and check the
scanner finds it; they're skipped if that scanner isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config_review  # noqa: E402
import dast  # noqa: E402
import scanners  # noqa: E402
from findings import Finding, apply_acceptances, compare_with_previous, dedupe, fingerprint, gate, to_dict  # noqa: E402
from report import sarif, write_all  # noqa: E402


def _f(**kw) -> Finding:
    base = dict(check="dast", rule_id="R1", title="t", severity="high", description="d", recommendation="r", location="a.py:10", evidence="x = 1")
    return Finding(**{**base, **kw})


# --- findings -----------------------------------------------------------------


def test_fingerprint_survives_line_moves():
    assert fingerprint("R", "a.py:10", "x = 1") == fingerprint("R", "a.py:42", "x = 1")
    assert fingerprint("R", "a.py:10", "x = 1") != fingerprint("R", "b.py:10", "x = 1")


def test_gate_blocks_at_threshold_and_respects_acceptance(tmp_path):
    high, low = _f(), _f(rule_id="R2", severity="low")
    assert gate([high, low], "high")[0] is False
    assert gate([low], "high")[0] is True
    accepted = tmp_path / "accepted.json"
    accepted.write_text(json.dumps({"accepted": [{"fingerprint": high.fingerprint, "reason": "internal only", "owner": "sec",
                                                  "expires": "2099-01-01"}]}))
    apply_acceptances([high, low], accepted)
    assert high.status == "accepted" and gate([high, low], "high")[0] is True


def test_expired_or_incomplete_acceptance_stops_suppressing(tmp_path):
    f, g = _f(), _f(rule_id="R9")
    accepted = tmp_path / "accepted.json"
    accepted.write_text(json.dumps({"accepted": [
        {"fingerprint": f.fingerprint, "reason": "x", "owner": "y", "expires": "2020-01-01"},
        {"fingerprint": g.fingerprint, "reason": "no owner or expiry"},
    ]}))
    warnings = apply_acceptances([f, g], accepted, today=date(2026, 1, 1))
    assert f.status == "expired-acceptance" and g.status == "open"
    assert gate([f, g], "high")[0] is False and len(warnings) == 2


def test_compare_marks_new_and_fixed(tmp_path):
    old, kept = _f(rule_id="OLD"), _f(rule_id="KEEP")
    prev = tmp_path / "findings.json"
    prev.write_text(json.dumps({"findings": [to_dict(old), to_dict(kept)]}))
    new, kept2 = _f(rule_id="NEW"), _f(rule_id="KEEP")
    fixed = compare_with_previous([new, kept2], prev)
    assert new.new is True and kept2.new is False and [x["fingerprint"] for x in fixed] == [old.fingerprint]


def test_dedupe_keeps_highest_severity():
    a, b = _f(severity="low"), _f(severity="critical")
    assert [x.severity for x in dedupe([a, b])] == ["critical"]


# --- config review --------------------------------------------------------------


def test_config_review_flags_debug_cors_tls_and_unpinned(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        'app.run(debug=True)\nallow_origins=["*"]\nrequests.get(u, verify=False)\nSECRET_KEY = "changeme"\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("flask>=2\nrequests==2.31.0\n")
    cfg = {"source_paths": ["app"], "python": {"requirements": ["requirements.txt"]}}
    ids = {f.rule_id for f in config_review.review(tmp_path, cfg)[0]}
    assert {"CFG-DEBUG", "CFG-CORS-WILDCARD", "CFG-TLS-VERIFY-OFF", "CFG-SECRET-DEFAULT", "CFG-UNPINNED-DEPS"} <= ids


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_config_review_detects_tracked_env(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".env").write_text("API_KEY=x\n")
    subprocess.run(["git", "add", ".env"], cwd=tmp_path, check=True)
    ids = {f.rule_id for f in config_review.review(tmp_path, {"source_paths": []})[0]}
    assert "CFG-ENV-TRACKED" in ids and "CFG-ENV-NOT-IGNORED" in ids


# --- positive controls for the external scanners ---------------------------------


@pytest.mark.skipif(not scanners.tool("bandit"), reason="bandit not installed")
def test_bandit_finds_planted_issue(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bad.py").write_text("import subprocess\n\ndef run(cmd):\n    return subprocess.call(cmd, shell=True)\n")
    found, _ = scanners.run_bandit(tmp_path, {"languages": ["python"], "source_paths": ["src"]}, tmp_path)
    assert any(f.rule_id == "bandit:B602" for f in found)
    assert all(f.recommendation for f in found)


@pytest.mark.skipif(not scanners.tool("gitleaks"), reason="gitleaks not installed")
def test_gitleaks_finds_planted_secret(tmp_path):
    (tmp_path / "src").mkdir()
    # A made-up key in a format gitleaks recognizes (not a real credential).
    fake = "ghp_" + "Z9yX8wV7uT6sR5qP4oN3mL2kJ1iH0gF9eD8c"
    (tmp_path / "src" / "settings.py").write_text(f'GITHUB_TOKEN = "{fake}"\n')
    found, notes = scanners.run_gitleaks(tmp_path, {"source_paths": ["src"]}, tmp_path, history=False)
    assert found, notes
    assert found[0].severity in ("high", "critical") and "rotate" in found[0].recommendation.lower()
    assert fake not in found[0].evidence  # redacted


# --- DAST against a tiny local server ---------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body, headers=None):
        data = body.encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/docs":
            self._send(200, "<html>docs</html>", {"Content-Type": "text/html"})
        elif self.path == "/api/items":
            origin = self.headers.get("Origin")
            hdrs = {"Content-Type": "application/json", "Server": "TestServer/1.2"}
            if origin:
                hdrs.update({"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true"})
            self._send(200, '{"items": []}', hdrs)
        else:
            self._send(404, "not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            json.loads(raw)
        except ValueError:
            self._send(500, 'Traceback (most recent call last):\n  File "app.py", line 3, in handler', {"Content-Type": "text/plain"})
            return
        self._send(200, '{"ok": true}', {"Content-Type": "application/json"})


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_dast_finds_planted_weaknesses(server):
    cfg = {"dast": {"endpoints": [
        {"name": "items", "method": "GET", "path": "/api/items"},
        {"name": "create", "method": "POST", "path": "/api/create", "body": {"text": "{{input}}"}, "input_field": "text", "max_input_chars": 100},
    ], "rate_limit_probe": {"endpoint": "items", "requests": 5}}}
    found, notes, log = dast.run(server, cfg, timeout=5)
    ids = {f.rule_id for f in found}
    assert {"DAST-CORS", "DAST-AUTH-MISSING", "DAST-ERR-VERBOSE", "DAST-ERR-5XX", "DAST-NO-RATE-LIMIT",
            "DAST-INFO-VERSION", "DAST-EXPOSED-DOCS", "DAST-INPUT-SIZE"} <= ids
    cors = next(f for f in found if f.rule_id == "DAST-CORS")
    assert cors.severity == "high" and "Allow-list" in cors.recommendation
    assert all(f.recommendation and f.evidence for f in found)
    assert len(log) < 60  # a bounded number of ordinary requests


def test_dast_reports_unreachable_target():
    found, notes, _ = dast.run("http://127.0.0.1:9", {"dast": {"endpoints": [{"name": "x", "path": "/"}]}}, timeout=2)
    assert not found and "unreachable" in notes[0]


# --- report ---------------------------------------------------------------------------


def test_reports_and_sarif(tmp_path):
    fs = [_f(), _f(rule_id="R2", severity="low", location="GET /docs")]
    summary = {"app_name": "x", "verdict": "FAIL", "fail_on": "high", "profile": "quick", "started": "now", "repo": "r",
               "checks_run": ["dast"], "notes": [], "blocking": [to_dict(fs[0])]}
    write_all(tmp_path, fs, summary)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Recommended fix" in md and "Blocking findings" in md
    s = sarif(fs)
    results = s["runs"][0]["results"]
    assert results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "a.py"
    assert "locations" not in results[1]  # an endpoint isn't a file location
