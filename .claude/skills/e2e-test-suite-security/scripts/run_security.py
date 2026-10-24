"""Run the security test suite against an application and report on it.

    python run_security.py [--repo .] [--profile pre-merge|quick] [--static-only]
                           [--db-url URL] [--target-url URL] [--fail-on high]
    python run_security.py --rerender <run_dir> --manual-findings FILE

Reads <repo>/security/config.json. Checks:
  secrets       gitleaks over git history and the working tree
  sast          bandit (Python) + semgrep (OWASP Top 10, secrets, language packs)
  dependencies  pip-audit / npm audit
  config        debug flags, CORS, TLS verification, .env hygiene, pinning, Dockerfile
  dast          non-destructive live checks against a local or non-prod instance
Manual review findings (written by Claude after reading the code) are merged
with --rerender. Accepted risks come from security/accepted-risks.json.

Results: <repo>/<results_dir>/<timestamp>-<profile>/ with report.html,
report.md, findings.json, results.sarif and raw tool output.
Exit codes: 0 = no unaccepted finding at/above --fail-on, 1 = blocking
findings, 2 = setup error (bad config, guard refusal, app didn't start).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config_review  # noqa: E402
import dast  # noqa: E402
import scanners  # noqa: E402
from db_guard import GuardError, check_db_url, check_target_url, file_url_path  # noqa: E402
from findings import Finding, apply_acceptances, compare_with_previous, dedupe, from_dict, gate, to_dict  # noqa: E402
from report import write_all  # noqa: E402

PROFILES = {
    "quick": ["secrets-tree", "bandit", "dependencies", "config", "dast"],
    "pre-merge": ["secrets-history", "secrets-tree", "bandit", "semgrep", "dependencies", "config", "dast"],
}


class SetupError(Exception):
    pass


def load_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise SetupError(f"{what} not found at {path}.")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SetupError(f"{what} at {path} isn't valid JSON: {exc}") from exc


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fill(value, mapping: dict):
    if isinstance(value, str):
        for k, v in mapping.items():
            value = value.replace("{" + k + "}", str(v))
        return value
    if isinstance(value, list):
        return [fill(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, mapping) for k, v in value.items()}
    return value


def start(cmd: list[str], cwd: Path, env: dict, log: Path) -> subprocess.Popen:
    local = cwd / cmd[0]
    exe = str(local.resolve()) if local.exists() else (shutil.which(cmd[0]) or cmd[0])
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    return subprocess.Popen([exe, *cmd[1:]], cwd=cwd, env=env, stdout=open(log, "w", encoding="utf-8", errors="replace"),
                            stderr=subprocess.STDOUT, **kwargs)


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        import signal
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_healthy(url: str, proc: subprocess.Popen, timeout_s: float, log: Path) -> None:
    deadline, last = time.time() + timeout_s, None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SetupError(f"Process exited ({proc.returncode}) before {url} was healthy:\n{log.read_text(errors='replace')[-1500:]}")
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status < 500:
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.5)
    raise SetupError(f"{url} wasn't healthy within {timeout_s}s ({last}).")


def seed(cfg: dict, db_url: str, repo: Path) -> None:
    s = cfg.get("db", {}).get("seed", {})
    if db_url.startswith("file:"):
        target = file_url_path(db_url)
        target.mkdir(parents=True, exist_ok=True)
        for item in s.get("copy", []):
            shutil.copyfile(repo / item["from"], target / item["to"])
        for name, content in s.get("reset", {}).items():
            (target / name).write_text(content, encoding="utf-8")
    if s.get("command"):
        proc = subprocess.run(fill(s["command"], {"db_url": db_url}), cwd=repo, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SetupError(f"Seed command failed: {proc.stderr[-500:]}")


def finalize(run_dir: Path, findings: list[Finding], summary: dict, repo: Path, cfg: dict, results_root: Path) -> int:
    findings = dedupe(findings)
    accepted = repo / cfg.get("accepted_risks", "security/accepted-risks.json")
    summary["notes"] = summary.get("notes", []) + apply_acceptances(findings, accepted)
    previous = summary.get("previous_path")
    if previous is None:  # the latest earlier run with the same profile (a quick run checks less)
        runs = sorted(p for p in results_root.glob(f"*-{summary['profile']}/findings.json") if p.parent.name < run_dir.name)
        previous = runs[-1] if runs else None
    kinds = {"secrets-tree": "secrets", "bandit": "sast", "semgrep": "sast", "dependencies": "dependencies",
             "config": "config", "dast": "dast", "manual review": "manual"}
    present = {kinds[c] for c in summary["checks_run"] if c in kinds}
    summary["fixed"] = compare_with_previous(findings, Path(previous) if previous else None, present)
    summary["previous"] = Path(previous).parent.name if previous else None
    summary.pop("previous_path", None)
    ok, blocking = gate(findings, summary["fail_on"])
    summary["verdict"] = "PASS" if ok else "FAIL"
    summary["blocking"] = [to_dict(f) for f in blocking]
    summary["counts"] = {s: sum(1 for f in findings if f.severity == s and f.status != "accepted")
                         for s in ("critical", "high", "medium", "low", "info")}
    write_all(run_dir, findings, summary)
    c = summary["counts"]
    print(f"\n{summary['verdict']}: {c['critical']} critical, {c['high']} high, {c['medium']} medium, {c['low']} low, "
          f"{c['info']} info (unaccepted). Report: {run_dir / 'report.html'}")
    return 0 if ok else 1


def rerender(run_dir: Path, manual: Path | None) -> int:
    data = json.loads((run_dir / "findings.json").read_text(encoding="utf-8"))
    summary = {k: v for k, v in data.items() if k != "findings"}
    findings = [from_dict(d) for d in data["findings"] if d.get("check") != "manual"]
    if manual:
        entries = json.loads(manual.read_text(encoding="utf-8-sig"))
        for e in entries.get("findings", entries if isinstance(entries, list) else []):
            findings.append(Finding(check="manual", tool="manual review", **{k: v for k, v in e.items() if k in Finding.__dataclass_fields__
                                                                             and k not in ("check", "tool")}))
        shutil.copyfile(manual, run_dir / "manual_findings.json")
        if "manual review" not in summary["checks_run"]:
            summary["checks_run"].append("manual review")
    repo = Path(summary["repo"])
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    results_root = repo / cfg.get("results_dir", "security-results")
    # Compare against the same previous run as the original render ("" = there was none).
    summary["previous_path"] = str(results_root / summary["previous"] / "findings.json") if summary.get("previous") else ""
    for f in findings:
        f.status, f.acceptance, f.new = "open", None, None
    summary["notes"] = [n for n in summary.get("notes", []) if not n.startswith(("Acceptance", "Accepted risk"))]
    return finalize(run_dir, findings, summary, repo, cfg, results_root)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--config", type=Path, help="default: <repo>/security/config.json")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="pre-merge")
    ap.add_argument("--static-only", action="store_true", help="skip live checks")
    ap.add_argument("--db-url", help="non-production test data store for the live checks (required when the app is started)")
    ap.add_argument("--target-url", help="check an already-running local/non-prod instance instead of starting one")
    ap.add_argument("--fail-on", choices=["critical", "high", "medium", "low", "info"])
    ap.add_argument("--rerender", type=Path, help="re-render an existing run folder (e.g. after adding manual findings)")
    ap.add_argument("--manual-findings", type=Path, help="JSON of manual-review findings to merge (with --rerender)")
    args = ap.parse_args()

    if args.rerender:
        return rerender(args.rerender.resolve(), args.manual_findings)

    repo = args.repo.resolve()
    app_proc = mock_proc = None
    try:
        cfg = load_json(args.config or repo / "security" / "config.json", "Security config")
        checks = list(PROFILES[args.profile])
        live = "dast" in checks and not args.static_only and cfg.get("dast", {}).get("endpoints")
        if not live and "dast" in checks:
            checks.remove("dast")
        redacted_db = None
        if live and not args.target_url:
            if cfg.get("db") and not args.db_url:
                raise SetupError("Live checks start the app, which writes data: pass --db-url with a non-production test data store "
                                 "(or use --static-only).")
            if args.db_url:
                redacted_db = check_db_url(args.db_url, cfg, repo)
        if args.target_url:
            check_target_url(args.target_url, cfg)

        results_root = repo / cfg.get("results_dir", "security-results")
        run_dir = results_root / f"{datetime.now():%Y%m%d-%H%M%S}-{args.profile}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"Run directory: {run_dir}\nChecks: {', '.join(checks)}")

        findings: list[Finding] = []
        notes: list[str] = []

        def collect(label: str, result) -> None:
            f, n = result[0], result[1]
            findings.extend(f)
            notes.extend(n)
            print(f"  {label}: {len(f)} finding(s)" + (f"; {'; '.join(n)}" if n else ""), flush=True)

        if "secrets-tree" in checks:
            collect("secrets", scanners.run_gitleaks(repo, cfg, run_dir, history="secrets-history" in checks))
        if "bandit" in checks:
            collect("bandit", scanners.run_bandit(repo, cfg, run_dir))
        if "semgrep" in checks:
            collect("semgrep", scanners.run_semgrep(repo, cfg, run_dir))
        if "dependencies" in checks:
            collect("pip-audit", scanners.run_pip_audit(repo, cfg, run_dir))
            collect("npm audit", scanners.run_npm_audit(repo, cfg, run_dir))
        if "config" in checks:
            collect("config review", config_review.review(repo, cfg))

        target = None
        if live:
            if args.target_url:
                target = args.target_url
            else:
                env = dict(os.environ)
                if args.db_url:
                    env[cfg["db"]["env_var"]] = args.db_url
                    seed(cfg, args.db_url, repo)
                mock = cfg.get("llm", {}).get("mock")
                if mock:
                    mport = free_port()
                    cmd = [sys.executable, str(HERE / "mock_llm_server.py"), "--port", str(mport), "--chat-latency-ms", "20",
                           "--chat-jitter-ms", "0", "--embed-latency-ms", "5"] + (["--script", str(repo / mock["script"])] if mock.get("script") else [])
                    mock_proc = start(cmd, repo, dict(os.environ), run_dir / "mock_llm.log")
                    wait_healthy(f"http://127.0.0.1:{mport}/health", mock_proc, 20, run_dir / "mock_llm.log")
                    env.update(fill(mock.get("env", {}), {"mock_port": mport}))
                port = free_port()
                st = cfg["start"]
                env.update({k: str(v) for k, v in fill(st.get("env", {}), {"port": port}).items() if not k.startswith("_")})
                app_proc = start(fill(st["command"], {"port": port}), repo / st.get("cwd", "."), env, run_dir / "app.log")
                target = fill(cfg.get("base_url", "http://127.0.0.1:{port}"), {"port": port})
                wait_healthy(target.rstrip("/") + st.get("health_path", "/health"), app_proc, st.get("startup_timeout_s", 60), run_dir / "app.log")
            print(f"  live checks against {target} ...", flush=True)
            f, n, log = dast.run(target, cfg)
            (run_dir / "dast-requests.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
            collect("dast", (f, n))
    except (SetupError, GuardError) as exc:
        print(f"SETUP ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        stop(app_proc)
        stop(mock_proc)

    summary = {
        "app_name": cfg.get("app_name", repo.name), "repo": str(repo), "profile": args.profile, "target": target,
        "db_url": redacted_db, "started": datetime.now().isoformat(timespec="seconds"),
        "git": {"commit": git(repo, "rev-parse", "HEAD"), "branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
                "dirty": bool(git(repo, "status", "--porcelain"))},
        "fail_on": args.fail_on or cfg.get("fail_on", "high"), "checks_run": checks, "notes": notes,
        "tools": {t: bool(scanners.tool(t)) for t in ("gitleaks", "bandit", "semgrep", "pip-audit")},
    }
    return finalize(run_dir, findings, summary, repo, cfg, results_root)


if __name__ == "__main__":
    sys.exit(main())
