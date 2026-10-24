"""Run an NFR-driven k6 load test against an application and report on it.

    python run_perf.py --db-url <non-prod URL> [--repo .] [--profile pre-merge]
                       [--llm mock|real] [--target-url URL] [--save-baseline]

Reads <repo>/perf/k6.config.json (how to start/seed/drive the app) and
<repo>/perf/nfr.json (the NFRs), then:
  1. validates the NFRs and guards the DB URL (refuses prod / the app's real data)
  2. seeds the load-test database
  3. starts the mock LLM (llm=mock) and the app with the test DB + mock env
  4. builds k6 scenarios + thresholds from the NFRs and the profile
  5. runs k6, sampling app CPU/memory if psutil is available
  6. stops everything and writes report.html / report.md / results.json

Results go to <repo>/<results_dir>/<timestamp>-<profile>/.
Exit codes: 0 = every NFR met and no regression, 1 = NFR or regression
failure, 2 = setup error (bad input, guard refusal, app didn't start).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_guard import GuardError, check_db_url, check_target_url, file_url_path  # noqa: E402
from report import build_report  # noqa: E402

PROFILES = {
    # Fast enough to run before every merge.
    "smoke": ["smoke"],
    "pre-merge": ["smoke", "load", "projected", "concurrency"],
    # Adds the stress ramp (finds the breaking point) and an optional soak.
    "full": ["smoke", "load", "projected", "concurrency", "stress", "soak"],
}
DEFAULT_DURATIONS = {
    "pre-merge": {"smoke": "20s", "load": "2m", "projected": "2m", "concurrency": "1m"},
    "full": {"smoke": "30s", "load": "5m", "projected": "5m", "concurrency": "3m", "stress": "5m", "soak": None},
    "smoke": {"smoke": "30s"},
}
REQUIRED_NFR = ("expected_tps", "projected_tps", "concurrent_users", "latency_ms", "max_error_rate")


class SetupError(Exception):
    pass


# --- inputs -------------------------------------------------------------------


def load_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise SetupError(f"{what} not found at {path}.")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate a BOM from Windows editors
    except json.JSONDecodeError as exc:
        raise SetupError(f"{what} at {path} isn't valid JSON: {exc}") from exc


def validate_nfr(nfr: dict) -> None:
    missing = [k for k in REQUIRED_NFR if nfr.get(k) in (None, "")]
    if missing:
        raise SetupError(f"NFR file is missing: {', '.join(missing)}. Ask the user for these values.")
    for key in ("expected_tps", "projected_tps", "concurrent_users"):
        if not isinstance(nfr[key], (int, float)) or nfr[key] <= 0:
            raise SetupError(f"NFR '{key}' must be a positive number, got {nfr[key]!r}.")
    if nfr["projected_tps"] < nfr["expected_tps"]:
        raise SetupError("NFR 'projected_tps' (future) is lower than 'expected_tps' (today); confirm with the user.")
    lat = nfr["latency_ms"]
    if not isinstance(lat, dict) or not any(k in lat for k in ("p50", "p90", "p95", "p99", "max")):
        raise SetupError("NFR 'latency_ms' needs at least one of p50/p90/p95/p99/max, in milliseconds.")
    if not 0 <= nfr["max_error_rate"] < 1:
        raise SetupError("NFR 'max_error_rate' is a fraction, e.g. 0.01 for 1%.")


def parse_duration(value: str) -> float:
    units = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    for suffix in ("ms", "h", "m", "s"):
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * units[suffix]
    return float(value)


def fmt_duration(seconds: float) -> str:
    return f"{int(math.ceil(seconds))}s"


# --- plan -----------------------------------------------------------------------


def _rate(tps: float) -> dict:
    """k6 needs an integer rate; express fractional TPS per minute instead."""
    if float(tps).is_integer():
        return {"rate": int(tps), "timeUnit": "1s"}
    return {"rate": max(1, round(tps * 60)), "timeUnit": "1m"}


def build_plan(app: dict, nfr: dict, profile: str, base_url: str, llm_mode: str, seed: int) -> dict:
    lat = nfr["latency_ms"]
    worst_ms = max(v for k, v in lat.items() if k in ("p50", "p90", "p95", "p99", "max"))
    expected, projected = float(nfr["expected_tps"]), float(nfr["projected_tps"])
    users = int(nfr["concurrent_users"])
    notes = []

    if llm_mode == "real":
        cap = float(app.get("llm", {}).get("real", {}).get("max_tps", 0.2))
        max_users = int(app.get("llm", {}).get("real", {}).get("max_vus", 2))
        if projected > cap or users > max_users:
            notes.append(
                f"Real-LLM mode caps load at {cap} TPS and {max_users} concurrent users to limit provider cost; "
                f"NFR targets ({expected}/{projected} TPS, {users} users) were NOT tested at full rate."
            )
        expected, projected, users = min(expected, cap), min(projected, cap), min(users, max_users)

    durations = {**DEFAULT_DURATIONS.get(profile, {}), **(nfr.get("durations") or {})}
    stress_factor = float(nfr.get("stress_factor", 1.5))
    grace = fmt_duration(max(10, worst_ms * 2 / 1000))

    def vus_for(tps: float) -> tuple[int, int]:
        pre = max(2, math.ceil(tps * worst_ms / 1000 * 1.5) + 1)
        return pre, max(pre * 4, users)

    scenarios, thresholds, order = {}, {}, []
    start = 0.0

    def add(name: str, scenario: dict, seconds: float, gated: bool, target_tps: float | None) -> None:
        nonlocal start
        scenario.update({"startTime": fmt_duration(start), "gracefulStop": grace, "exec": "run", "tags": {"phase": name}})
        scenarios[name] = scenario
        order.append({"name": name, "start_s": start, "duration_s": seconds, "gated": gated, "target_tps": target_tps})
        start += seconds + parse_duration(grace) + 5
        if not gated:
            return
        sel = f"{{scenario:{name}}}"
        thresholds[f"http_req_duration{sel}"] = [f"p({k[1:]})<{v}" if k != "max" else f"max<{v}" for k, v in lat.items()
                                                 if k in ("p50", "p90", "p95", "p99", "max")]
        thresholds[f"http_req_failed{sel}"] = [f"rate<{nfr['max_error_rate']}"]
        thresholds[f"checks{sel}"] = [f"rate>{1 - nfr['max_error_rate']}"]
        if target_tps:
            allowed = max(1, math.floor(target_tps * seconds * 0.02))  # >= 98% of arrivals must start
            thresholds[f"dropped_iterations{sel}"] = [f"count<={allowed}"]
        for endpoint, limits in (nfr.get("endpoints") or {}).items():
            thresholds[f"http_req_duration{{scenario:{name},name:{endpoint}}}"] = [
                f"p({k[1:]})<{v}" if k != "max" else f"max<{v}" for k, v in limits.items()
            ]

    for phase in PROFILES[profile]:
        dur = durations.get(phase)
        if dur is None:
            continue
        seconds = parse_duration(dur)
        if phase == "smoke":
            add(phase, {"executor": "constant-vus", "vus": 1, "duration": dur}, seconds, False, None)
            # A broken build fails fast instead of burning the whole run.
            thresholds["http_req_failed{scenario:smoke}"] = [{"threshold": "rate<0.5", "abortOnFail": True, "delayAbortEval": "10s"}]
        elif phase in ("load", "projected", "soak"):
            tps = projected if phase == "projected" else expected
            pre, max_vus = vus_for(tps)
            add(phase, {"executor": "constant-arrival-rate", **_rate(tps), "duration": dur,
                        "preAllocatedVUs": pre, "maxVUs": max_vus}, seconds, True, tps)
        elif phase == "concurrency":
            add(phase, {"executor": "constant-vus", "vus": users, "duration": dur}, seconds, True, None)
        elif phase == "stress":
            peak = projected * stress_factor
            pre, max_vus = vus_for(peak)
            r = _rate(peak)
            stages = [{"target": max(1, round(r["rate"] * f)), "duration": fmt_duration(seconds / 4)} for f in (0.25, 0.5, 0.75, 1.0)]
            add(phase, {"executor": "ramping-arrival-rate", "startRate": 0, "timeUnit": r["timeUnit"], "stages": stages,
                        "preAllocatedVUs": pre, "maxVUs": max_vus}, seconds, False, peak)

    return {
        "app_name": app.get("app_name", "application"),
        "base_url": base_url,
        "headers": app.get("headers", {}),
        "request_timeout": app.get("request_timeout", "60s"),
        "data_pools": app.get("data_pools", {}),
        "flows": app["flows"],
        "think_time_s": nfr.get("think_time_s", 1),
        "seed": seed,
        "profile": profile,
        "llm_mode": llm_mode,
        "scenarios": scenarios,
        "thresholds": thresholds,
        "phases": order,
        "notes": notes,
        "estimated_duration_s": round(start),
    }


# --- processes ------------------------------------------------------------------


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


def start_process(cmd: list[str], cwd: Path, env: dict, log_path: Path) -> subprocess.Popen:
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    local = cwd / cmd[0]
    exe = str(local.resolve()) if local.exists() else (shutil.which(cmd[0]) or cmd[0])
    return subprocess.Popen([exe, *cmd[1:]], cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, **kwargs)


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_healthy(url: str, proc: subprocess.Popen | None, timeout_s: float, log_path: Path | None) -> None:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:] if log_path else ""
            raise SetupError(f"Process exited with code {proc.returncode} before {url} was healthy.\n{tail}")
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status < 500:
                    return
        except Exception as exc:  # not up yet
            last = exc
        time.sleep(0.5)
    raise SetupError(f"{url} wasn't healthy within {timeout_s}s (last error: {last}).")


class ResourceSampler(threading.Thread):
    """Samples app CPU% / RSS once a second into resources.csv (needs psutil)."""

    def __init__(self, pid: int, out: Path):
        super().__init__(daemon=True)
        self.pid, self.out, self.stop_event = pid, out, threading.Event()

    def run(self) -> None:
        try:
            import psutil
        except ImportError:
            return
        try:
            root = psutil.Process(self.pid)
        except psutil.Error:
            return
        with open(self.out, "w", encoding="utf-8") as f:
            f.write("timestamp,cpu_percent,rss_mb,threads\n")
            procs: dict[int, "psutil.Process"] = {}
            while not self.stop_event.is_set():
                try:
                    for p in [root, *root.children(recursive=True)]:
                        procs.setdefault(p.pid, p)
                    cpu = rss = threads = 0.0
                    for p in list(procs.values()):
                        try:
                            cpu += p.cpu_percent(None)
                            rss += p.memory_info().rss
                            threads += p.num_threads()
                        except psutil.Error:
                            procs.pop(p.pid, None)
                    f.write(f"{time.time():.1f},{cpu:.1f},{rss / 1e6:.1f},{int(threads)}\n")
                    f.flush()
                except psutil.Error:
                    break
                self.stop_event.wait(1.0)


# --- seeding --------------------------------------------------------------------


def seed_db(app: dict, db_url: str, repo: Path, run_dir: Path, env: dict) -> list[str]:
    seed = app.get("db", {}).get("seed", {})
    actions = []
    if db_url.startswith("file:"):
        target = file_url_path(db_url)
        target.mkdir(parents=True, exist_ok=True)
        for item in seed.get("copy", []):
            shutil.copyfile(repo / item["from"], target / item["to"])
            actions.append(f"copied {item['from']} -> {target / item['to']}")
        for name, content in seed.get("reset", {}).items():
            (target / name).write_text(content, encoding="utf-8")
            actions.append(f"reset {target / name}")
    if seed.get("command"):
        cmd = fill(seed["command"], {"db_url": db_url})
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, shell=isinstance(cmd, str))
        (run_dir / "seed.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise SetupError(f"Seed command failed (exit {proc.returncode}); see seed.log.")
        actions.append("ran seed command")
    return actions


# --- meta -----------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, help="default: <repo>/perf/k6.config.json")
    parser.add_argument("--nfr", type=Path, help="default: <repo>/perf/nfr.json")
    parser.add_argument("--db-url", required=True, help="non-production load-test database URL (mandatory)")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="pre-merge")
    parser.add_argument("--llm", choices=["mock", "real"], default="mock")
    parser.add_argument("--confirm-real-llm", action="store_true", help="required with --llm real (provider cost)")
    parser.add_argument("--target-url", help="test an already-running non-prod app instead of starting one")
    parser.add_argument("--baseline", type=Path, help="default: <repo>/<results_dir>/baseline.json")
    parser.add_argument("--save-baseline", action="store_true", help="make this run the baseline if it passes")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="validate inputs and write the plan, don't run k6")
    args = parser.parse_args()

    repo = args.repo.resolve()
    app_proc = mock_proc = sampler = None
    try:
        app = load_json(args.config or repo / "perf" / "k6.config.json", "App perf config")
        nfr = load_json(args.nfr or repo / "perf" / "nfr.json", "NFR file")
        validate_nfr(nfr)
        if args.llm == "real" and not args.confirm_real_llm:
            raise SetupError("--llm real calls the real model provider and costs money; pass --confirm-real-llm after the user agrees.")
        k6 = shutil.which("k6")
        if not k6 and not args.dry_run:
            raise SetupError("k6 isn't on PATH. Install it (winget install GrafanaLabs.k6 / brew install k6 / see k6.io).")
        redacted_db = check_db_url(args.db_url, app, repo)

        results_root = repo / app.get("results_dir", "perf-results")
        run_dir = results_root / f"{datetime.now():%Y%m%d-%H%M%S}-{args.profile}"
        run_dir.mkdir(parents=True, exist_ok=True)
        print(f"Run directory: {run_dir}")

        port = app.get("port") or free_port()
        mock_port = free_port()
        base_url = args.target_url or fill(app.get("base_url", "http://127.0.0.1:{port}"), {"port": port})
        if args.target_url:
            check_target_url(args.target_url, app)

        plan = build_plan(app, nfr, args.profile, base_url, args.llm, args.seed)
        for name, content in (("plan.json", plan), ("nfr.json", nfr), ("app_config.json", app)):
            (run_dir / name).write_text(json.dumps(content, indent=2), encoding="utf-8")
        print(f"Plan: phases {[p['name'] for p in plan['phases']]}, about {plan['estimated_duration_s']}s.")
        for note in plan["notes"]:
            print(f"NOTE: {note}")

        env = dict(os.environ)
        env[app.get("db", {}).get("env_var", "DATABASE_URL")] = args.db_url
        mock_cfg = app.get("llm", {}).get("mock")
        if args.llm == "mock" and mock_cfg:
            env.update(fill(mock_cfg.get("env", {}), {"mock_port": mock_port}))

        meta = {
            "started": datetime.now().isoformat(timespec="seconds"),
            "profile": args.profile, "llm_mode": args.llm, "db_url": redacted_db, "base_url": base_url,
            "git": {"commit": git(repo, "rev-parse", "HEAD"), "branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
                    "dirty": bool(git(repo, "status", "--porcelain"))},
            "k6_version": subprocess.run([k6, "version"], capture_output=True, text=True).stdout.strip() if k6 else None,
            "host": {"os": platform.platform(), "cpus": os.cpu_count(), "python": sys.version.split()[0]},
            "mock_llm": ({k: mock_cfg.get(k) for k in ("chat_latency_ms", "chat_jitter_ms", "embed_latency_ms")}
                         if args.llm == "mock" and mock_cfg else None),
            "seed": args.seed,
        }

        if args.dry_run:
            (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            print("Dry run: inputs valid, plan written. Not starting anything.")
            return 0

        meta["seed_actions"] = seed_db(app, args.db_url, repo, run_dir, env)

        if args.llm == "mock" and mock_cfg:
            script = mock_cfg.get("script")
            cmd = [sys.executable, str(SKILL_DIR / "scripts" / "mock_llm_server.py"), "--port", str(mock_port),
                   "--chat-latency-ms", str(mock_cfg.get("chat_latency_ms", 300)),
                   "--chat-jitter-ms", str(mock_cfg.get("chat_jitter_ms", 100)),
                   "--embed-latency-ms", str(mock_cfg.get("embed_latency_ms", 30)), "--seed", str(args.seed)]
            if script:
                cmd += ["--script", str(repo / script)]
            mock_proc = start_process(cmd, repo, dict(os.environ), run_dir / "mock_llm.log")
            wait_healthy(f"http://127.0.0.1:{mock_port}/health", mock_proc, 20, run_dir / "mock_llm.log")

        if not args.target_url:
            start = app["start"]
            env.update({k: str(v) for k, v in fill(start.get("env", {}), {"port": port}).items() if not k.startswith("_")})
            cmd = fill(start["command"], {"port": port})
            app_proc = start_process(cmd, repo / start.get("cwd", "."), env, run_dir / "app.log")
            wait_healthy(base_url.rstrip("/") + start.get("health_path", "/health"), app_proc,
                         start.get("startup_timeout_s", 60), run_dir / "app.log")
            sampler = ResourceSampler(app_proc.pid, run_dir / "resources.csv")
            sampler.start()

        print(f"Running k6 ({args.profile}) against {base_url} ...", flush=True)
        k6_cmd = [k6, "run", "--quiet", "--out", f"csv={run_dir / 'metrics.csv'}", "-e", f"PLAN={run_dir / 'plan.json'}",
                  "-e", f"OUT_DIR={run_dir}", str(SKILL_DIR / "k6" / "load_test.js")]
        for var in app.get("pass_env", []):  # e.g. auth tokens referenced as {{env:NAME}}
            if var in os.environ:
                k6_cmd[2:2] = ["-e", f"{var}={os.environ[var]}"]
        with open(run_dir / "k6.log", "w", encoding="utf-8", errors="replace") as log:
            proc = subprocess.Popen(k6_cmd, cwd=run_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout:
                log.write(line)
                if not line.startswith("time=") or "level=error" in line:
                    print(line, end="", flush=True)
            proc.wait()
        meta["k6_exit_code"] = proc.returncode
        meta["finished"] = datetime.now().isoformat(timespec="seconds")
    except (SetupError, GuardError) as exc:
        print(f"SETUP ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if sampler:
            sampler.stop_event.set()
        stop_process(app_proc)
        stop_process(mock_proc)

    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    baseline = args.baseline or results_root / "baseline.json"
    results = build_report(run_dir, baseline if baseline.exists() else None)
    print(f"\n{results['verdict']}: report at {run_dir / 'report.html'}")
    if args.save_baseline:
        if results["verdict"] == "PASS":
            shutil.copyfile(run_dir / "results.json", baseline)
            print(f"Saved as baseline: {baseline}")
        else:
            print("Not saved as baseline: the run didn't pass.")
    return 0 if results["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
