"""Run every functional test of the certificate equivalency agent, one at a time.

Collects every pytest test id in the repo, runs each in its own pytest
process (so one test's state or crash can't affect another), and writes:

    testing/run-<YYYYMMDD-HHMMSS>/
        results/<NNN>_<test name>.txt   full output + status for each test
        summary.md                      totals, per-file table, failures
        summary.json                    same data, machine-readable

Tests use fake model clients and patched mock stores, so no API
credentials are needed and data/*.json is never written.

Usage (from anywhere):
    <repo>/.venv/Scripts/python.exe run_functional_tests.py [--repo PATH] [--out PATH] [-k EXPR]
Exit code: 0 if every test passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# scripts/ -> moe-agent-functional-testing/ -> skills/ -> .claude/ -> repo root
DEFAULT_REPO = Path(__file__).resolve().parents[4]


def _pytest(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def collect(repo: Path, keyword: str | None) -> list[str]:
    args = ["--collect-only", "-q"] + (["-k", keyword] if keyword else [])
    proc = _pytest(repo, *args)
    ids = [line.strip() for line in proc.stdout.splitlines() if "::" in line and not line.startswith(" ")]
    if not ids and proc.returncode not in (0, 5):
        sys.exit(f"Test collection failed:\n{proc.stdout}\n{proc.stderr}")
    return ids


def _status(proc: subprocess.CompletedProcess) -> str:
    tail = proc.stdout.strip().splitlines()[-1:] or [""]
    if proc.returncode == 0:
        return "SKIPPED" if re.search(r"\bskipped\b", tail[0]) and "passed" not in tail[0] else "PASSED"
    if proc.returncode == 1:
        return "FAILED"
    return "ERROR"


def _safe_name(index: int, test_id: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", test_id.replace("tests/", "").replace("::", "__")).strip("_")
    return f"{index:03d}_{name[:120]}.txt"


def _failure_line(output: str) -> str:
    """The first assertion/exception line, for the summary's failure list."""
    for line in output.splitlines():
        if line.startswith("E "):
            return line[1:].strip()[:300]
    return "(see result file)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO, help="repo root (default: inferred from this script's location)")
    parser.add_argument("--out", type=Path, help="testing directory (default: <repo>/testing)")
    parser.add_argument("-k", dest="keyword", help="only run tests matching this pytest -k expression")
    args = parser.parse_args()

    repo = args.repo.resolve()
    started = datetime.now()
    run_dir = (args.out or repo / "testing").resolve() / f"run-{started:%Y%m%d-%H%M%S}"
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    test_ids = collect(repo, args.keyword)
    if not test_ids:
        print("No tests collected.")
        return 1
    print(f"Running {len(test_ids)} tests one by one -> {run_dir}")

    records = []
    for i, test_id in enumerate(test_ids, 1):
        t0 = time.perf_counter()
        proc = _pytest(repo, test_id, "-q", "--tb=long", "-rA", "--no-header")
        duration = time.perf_counter() - t0
        status = _status(proc)
        output = proc.stdout + (f"\n--- stderr ---\n{proc.stderr}" if proc.stderr.strip() else "")
        result_file = results_dir / _safe_name(i, test_id)
        result_file.write_text(
            f"test:     {test_id}\nstatus:   {status}\nduration: {duration:.2f}s\n"
            f"exit:     {proc.returncode}\n{'=' * 72}\n{output}",
            encoding="utf-8",
        )
        records.append(
            {
                "index": i,
                "test": test_id,
                "file": test_id.split("::", 1)[0],
                "status": status,
                "duration_s": round(duration, 2),
                "result_file": f"results/{result_file.name}",
                "failure": _failure_line(output) if status in ("FAILED", "ERROR") else None,
            }
        )
        print(f"[{i:>3}/{len(test_ids)}] {status:<7} {duration:6.2f}s  {test_id}", flush=True)

    finished = datetime.now()
    counts = defaultdict(int)
    for r in records:
        counts[r["status"]] += 1
    by_file: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        by_file[r["file"]][r["status"]] += 1
    failures = [r for r in records if r["status"] in ("FAILED", "ERROR")]
    overall = "PASS" if not failures else "FAIL"

    summary = {
        "overall": overall,
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "duration_s": round((finished - started).total_seconds(), 1),
        "python": sys.executable,
        "filter": args.keyword,
        "totals": {"total": len(records), **{k: counts[k] for k in ("PASSED", "FAILED", "ERROR", "SKIPPED")}},
        "tests": records,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Certificate equivalency agent: functional test run",
        "",
        f"- **Overall:** {overall}",
        f"- **Started:** {summary['started']}  **Finished:** {summary['finished']}  ({summary['duration_s']}s)",
        f"- **Totals:** {len(records)} tests: {counts['PASSED']} passed, {counts['FAILED']} failed, "
        f"{counts['ERROR']} errors, {counts['SKIPPED']} skipped",
        f"- **Filter:** `{args.keyword}`" if args.keyword else "- **Filter:** none (all tests)",
        "",
        "## By test file",
        "",
        "| File | Passed | Failed | Errors | Skipped |",
        "|---|---|---|---|---|",
    ]
    for file, c in sorted(by_file.items()):
        lines.append(f"| `{file}` | {c['PASSED']} | {c['FAILED']} | {c['ERROR']} | {c['SKIPPED']} |")
    lines += ["", "## Failures", ""]
    if failures:
        for r in failures:
            lines.append(f"- **{r['status']}** `{r['test']}`: {r['failure']} ([output]({r['result_file']}))")
    else:
        lines.append("None.")
    lines += ["", "## All tests", "", "| # | Status | Time | Test |", "|---|---|---|---|"]
    for r in records:
        lines.append(f"| {r['index']} | {r['status']} | {r['duration_s']}s | [`{r['test']}`]({r['result_file']}) |")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n{overall}: {counts['PASSED']}/{len(records)} passed. Summary: {run_dir / 'summary.md'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
