"""Run an app's Playwright UI regression suite against an isolated, seeded instance.

    python run_ui.py --db-url <non-prod URL> [--repo .] [--browsers chromium]
                     [--grep @smoke] [--update-snapshots] [--target-url URL]
    python run_ui.py --db-url <URL> --serve [--serve-seconds 600]

Reads <repo>/ui-tests/ui-tests.config.json (how to start, seed and isolate
the app; where the Playwright project lives), then:
  1. guards the test data URL (refuses prod / the app's real data) and seeds it
  2. fingerprints the app's protected data paths (e.g. data/)
  3. starts the mock LLM (if configured) and the app, waits for health
  4. runs `npx playwright test` in the project dir (BASE_URL etc. in env)
  5. stops everything, checks the protected data wasn't touched, and writes
     summary.md / summary.json next to Playwright's HTML/JSON/JUnit reports
--serve does 1-3 and keeps the app up (for writing tests / `playwright codegen`).

Results: <repo>/<results_dir>/<timestamp>/. Exit codes: 0 = all passed (flaky
tests are reported; --fail-on-flaky makes them fail), 1 = test failures or
protected data modified, 2 = setup error.
"""

from __future__ import annotations

import argparse
import hashlib
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

from db_guard import GuardError, check_db_url, check_target_url, file_url_path  # noqa: E402
from ui_report import build_summary  # noqa: E402


class SetupError(Exception):
    pass


def load_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise SetupError(f"{what} not found at {path}.")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SetupError(f"{what} at {path} isn't valid JSON: {exc}") from exc


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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


def seed(cfg: dict, db_url: str, repo: Path) -> list[str]:
    s = cfg.get("db", {}).get("seed", {})
    actions = []
    if db_url.startswith("file:"):
        target = file_url_path(db_url)
        target.mkdir(parents=True, exist_ok=True)
        for item in s.get("copy", []):
            shutil.copyfile(repo / item["from"], target / item["to"])
            actions.append(f"copied {item['from']}")
        for name, content in s.get("reset", {}).items():
            (target / name).write_text(content, encoding="utf-8")
            actions.append(f"reset {name}")
    if s.get("command"):
        proc = subprocess.run(fill(s["command"], {"db_url": db_url}), cwd=repo, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SetupError(f"Seed command failed: {proc.stderr[-500:]}")
        actions.append("ran seed command")
    return actions


def fingerprint_paths(repo: Path, paths: list[str]) -> dict[str, str]:
    """sha256 of every file under the protected paths, to prove the run didn't touch them."""
    out = {}
    for p in paths:
        root = repo / p
        files = [root] if root.is_file() else sorted(f for f in root.rglob("*") if f.is_file()) if root.exists() else []
        for f in files:
            if f.name.endswith(".lock"):
                continue
            out[str(f.relative_to(repo)).replace("\\", "/")] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--config", type=Path, help="default: <repo>/ui-tests/ui-tests.config.json")
    ap.add_argument("--db-url", help="non-production test data store (required when the app is started)")
    ap.add_argument("--target-url", help="test an already-running local/non-prod instance instead of starting one")
    ap.add_argument("--browsers", help="comma list: chromium,firefox,webkit (default from config)")
    ap.add_argument("--grep", help="only tests whose title/tags match, e.g. @smoke or 'applicant flow'")
    ap.add_argument("--grep-invert", help="skip tests whose title/tags match, e.g. @visual")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--retries", type=int, help="retries per failing test; a test that passes on retry is reported flaky")
    ap.add_argument("--repeat-each", type=int, help="run every test N times (flakiness hunting)")
    ap.add_argument("--update-snapshots", action="store_true", help="re-record visual baselines (only after the user approves)")
    ap.add_argument("--fail-on-flaky", action="store_true")
    ap.add_argument("--serve", action="store_true", help="start the seeded app and keep it running; don't run tests")
    ap.add_argument("--serve-seconds", type=int, default=0, help="with --serve: stop after N seconds (0 = until Ctrl+C)")
    args = ap.parse_args()

    repo = args.repo.resolve()
    procs: list[subprocess.Popen] = []
    try:
        cfg = load_json(args.config or repo / "ui-tests" / "ui-tests.config.json", "UI test config")
        project = repo / cfg.get("project_dir", "ui-tests")
        if not (project / "node_modules" / "@playwright" / "test").exists():
            raise SetupError(f"Playwright isn't installed in {project}: run `npm ci` and `npx playwright install chromium` there.")
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not npx:
            raise SetupError("npx (Node.js) isn't on PATH.")

        redacted_db, db_path = None, None
        if args.target_url:
            check_target_url(args.target_url, cfg)
        elif cfg.get("db"):
            if not args.db_url:
                raise SetupError("Starting the app writes data: pass --db-url with a non-production test data store.")
            redacted_db = check_db_url(args.db_url, cfg, repo)
            db_path = file_url_path(args.db_url) if args.db_url.startswith("file:") else None

        results_root = repo / cfg.get("results_dir", "ui-results")
        run_dir = results_root / datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        protected = cfg.get("protected_paths", [])
        before = fingerprint_paths(repo, protected)

        seed_actions = []
        if args.target_url:
            base_url = args.target_url
        else:
            env = dict(os.environ)
            if args.db_url:
                env[cfg["db"]["env_var"]] = args.db_url
                seed_actions = seed(cfg, args.db_url, repo)
            mock = cfg.get("llm", {}).get("mock")
            if mock:
                mport = free_port()
                cmd = [sys.executable, str(HERE / "mock_llm_server.py"), "--port", str(mport),
                       "--chat-latency-ms", str(mock.get("chat_latency_ms", 30)), "--chat-jitter-ms", "0",
                       "--embed-latency-ms", str(mock.get("embed_latency_ms", 5))] + (["--script", str(repo / mock["script"])] if mock.get("script") else [])
                procs.append(start(cmd, repo, dict(os.environ), run_dir / "mock_llm.log"))
                wait_healthy(f"http://127.0.0.1:{mport}/health", procs[-1], 20, run_dir / "mock_llm.log")
                env.update(fill(mock.get("env", {}), {"mock_port": mport}))
            port = free_port()
            st = cfg["start"]
            env.update({k: str(v) for k, v in fill(st.get("env", {}), {"port": port}).items() if not k.startswith("_")})
            procs.append(start(fill(st["command"], {"port": port}), repo / st.get("cwd", "."), env, run_dir / "app.log"))
            base_url = fill(cfg.get("base_url", "http://127.0.0.1:{port}"), {"port": port})
            wait_healthy(base_url.rstrip("/") + st.get("health_path", "/"), procs[-1], st.get("startup_timeout_s", 90), run_dir / "app.log")

        if args.serve:
            print(f"Serving {base_url} (test data: {redacted_db or 'n/a'}). Logs: {run_dir}", flush=True)
            print(f"  e.g. cd {project} && npx playwright codegen {base_url}", flush=True)
            deadline = time.time() + args.serve_seconds if args.serve_seconds else None
            while deadline is None or time.time() < deadline:
                if any(p.poll() is not None for p in procs):
                    raise SetupError("A served process exited; see its log in the run folder.")
                time.sleep(1)
            return 0

        browsers = (args.browsers or ",".join(cfg.get("default_browsers", ["chromium"]))).split(",")
        cmd = [npx, "playwright", "test"] + [f"--project={b.strip()}" for b in browsers if b.strip()]
        if args.grep:
            cmd += ["--grep", args.grep]
        if args.grep_invert:
            cmd += ["--grep-invert", args.grep_invert]
        workers = args.workers or cfg.get("workers")
        if workers:
            cmd += [f"--workers={workers}"]
        retries = args.retries if args.retries is not None else cfg.get("retries", 1)
        cmd += [f"--retries={retries}"]
        if args.repeat_each:
            cmd += [f"--repeat-each={args.repeat_each}"]
        if args.update_snapshots:
            cmd += ["--update-snapshots"]
        test_env = dict(os.environ, BASE_URL=base_url, UI_RESULTS_DIR=str(run_dir), CI="1")
        if db_path:
            test_env["UI_DATA_DIR"] = str(db_path)
        print(f"Run directory: {run_dir}\nRunning: {' '.join(cmd[1:])} against {base_url}", flush=True)
        with open(run_dir / "playwright.log", "w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen(cmd, cwd=project, env=test_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout:
                log.write(line)
                print(line, end="", flush=True)
            proc.wait()
        exit_code = proc.returncode
    except (SetupError, GuardError) as exc:
        print(f"SETUP ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    finally:
        for p in reversed(procs):
            stop(p)

    after = fingerprint_paths(repo, protected)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    meta = {
        "app_name": cfg.get("app_name", repo.name), "repo": str(repo), "base_url": base_url, "db_url": redacted_db,
        "browsers": browsers, "grep": " / not ".join(x for x in (args.grep, args.grep_invert) if x) or None, "retries": retries, "updated_snapshots": args.update_snapshots,
        "started": run_dir.name, "playwright_exit_code": exit_code, "seed_actions": seed_actions,
        "protected_paths": protected, "protected_changed": changed,
        "git": {"commit": git(repo, "rev-parse", "HEAD"), "branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
                "dirty": bool(git(repo, "status", "--porcelain"))},
    }
    summary = build_summary(run_dir, meta, results_root, fail_on_flaky=args.fail_on_flaky)
    print(f"\n{summary['verdict']}: {summary['counts']['passed']} passed, {summary['counts']['failed']} failed, "
          f"{summary['counts']['flaky']} flaky, {summary['counts']['skipped']} skipped. Report: {run_dir / 'summary.md'}")
    if changed:
        print(f"PROTECTED DATA MODIFIED during the run: {', '.join(changed)}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
