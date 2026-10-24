"""Summarize a Playwright run: summary.json + summary.md in the run folder.

Reads Playwright's JSON reporter output (results.json), classifies every
test (passed / failed / flaky / skipped), pulls the first error line and the
failure artifacts (trace, screenshot, video, visual diff), compares with the
previous run (newly failing, fixed, still failing), and decides the verdict.

    python ui_report.py <run_dir>     # re-summarize an existing run
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_STATUS = {"expected": "passed", "unexpected": "failed", "flaky": "flaky", "skipped": "skipped"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _walk(suite: dict, titles: list[str], out: list[dict], run_dir: Path) -> None:
    here = titles + ([suite["title"]] if suite.get("title") and not suite["title"].endswith((".ts", ".js")) else [])
    for spec in suite.get("specs", []):
        for test in spec.get("tests", []):
            results = test.get("results", [])
            last = results[-1] if results else {}
            error = next((r.get("error") for r in reversed(results) if r.get("error")), None)
            message = _ANSI.sub("", (error or {}).get("message", "")).strip()
            artifacts = []
            for r in results:
                for a in r.get("attachments", []):
                    if a.get("path"):
                        p = Path(a["path"])
                        try:
                            rel = str(p.resolve().relative_to(run_dir.resolve())).replace("\\", "/")
                        except ValueError:
                            rel = str(p)
                        artifacts.append({"name": a.get("name"), "path": rel})
            out.append({
                "id": f"{spec.get('file')} > {' > '.join(here + [spec['title']])} [{test.get('projectName')}]",
                "file": spec.get("file"), "line": spec.get("line"), "title": " > ".join(here + [spec["title"]]),
                "project": test.get("projectName"), "tags": spec.get("tags", []),
                "status": _STATUS.get(test.get("status"), test.get("status")), "attempts": len(results),
                "duration_ms": sum(r.get("duration", 0) for r in results),
                "error": message.splitlines()[0][:300] if message else None, "error_full": message[:3000] if message else None,
                "artifacts": artifacts, "last_status": last.get("status"),
            })
    for child in suite.get("suites", []):
        _walk(child, here, out, run_dir)


def parse_results(run_dir: Path) -> tuple[list[dict], list[str]]:
    path = run_dir / "results.json"
    if not path.exists():
        return [], ["Playwright wrote no results.json (it failed before running tests); see playwright.log."]
    data = json.loads(path.read_text(encoding="utf-8"))
    tests: list[dict] = []
    for suite in data.get("suites", []):
        _walk(suite, [], tests, run_dir)
    errors = [_ANSI.sub("", e.get("message", "")).strip()[:500] for e in data.get("errors", [])]
    return tests, errors


def _previous(results_root: Path, run_dir: Path) -> Path | None:
    runs = sorted(p for p in results_root.glob("*/summary.json") if p.parent.name < run_dir.name)
    return runs[-1] if runs else None


def build_summary(run_dir: Path, meta: dict, results_root: Path, fail_on_flaky: bool = False) -> dict:
    tests, run_errors = parse_results(run_dir)
    counts = {s: sum(1 for t in tests if t["status"] == s) for s in ("passed", "failed", "flaky", "skipped")}
    counts["total"] = len(tests)

    comparison = None
    prev_path = _previous(results_root, run_dir)
    if prev_path:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
        old = {t["id"]: t["status"] for t in prev.get("tests", [])}
        cur = {t["id"]: t["status"] for t in tests}
        bad = ("failed",)
        comparison = {
            "previous_run": prev_path.parent.name,
            "newly_failing": sorted(i for i, s in cur.items() if s in bad and old.get(i) not in bad),
            "fixed": sorted(i for i, s in cur.items() if s not in bad and old.get(i) in bad),
            "still_failing": sorted(i for i, s in cur.items() if s in bad and old.get(i) in bad),
            "new_tests": sorted(set(cur) - set(old)),
            "removed_tests": sorted(set(old) - set(cur)) if not (meta.get("grep") or prev.get("meta", {}).get("grep")) else [],
        }

    reasons = []
    if counts["failed"]:
        reasons.append(f"{counts['failed']} test(s) failed")
    if fail_on_flaky and counts["flaky"]:
        reasons.append(f"{counts['flaky']} flaky test(s) (--fail-on-flaky)")
    if meta.get("protected_changed"):
        reasons.append(f"protected data modified: {', '.join(meta['protected_changed'])}")
    if not tests:
        reasons.append("no tests ran")
    if run_errors:
        reasons.append("Playwright reported errors outside tests")
    if meta.get("playwright_exit_code") not in (0, None) and not counts["failed"] and not run_errors and tests:
        reasons.append(f"Playwright exited with code {meta['playwright_exit_code']}")
    verdict = "FAIL" if reasons else "PASS"

    summary = {"verdict": verdict, "reasons": reasons, "counts": counts, "comparison": comparison, "run_errors": run_errors,
               "meta": meta, "tests": tests}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (run_dir / "summary.md").write_text(render_md(summary, run_dir), encoding="utf-8")
    return summary


def render_md(s: dict, run_dir: Path) -> str:
    m, c = s["meta"], s["counts"]
    git = m.get("git") or {}
    L = [f"# UI regression run: {m.get('app_name')}", "",
         f"**Verdict: {s['verdict']}**" + (f" ({'; '.join(s['reasons'])})" if s["reasons"] else ""), "",
         f"- {c['total']} tests: **{c['passed']} passed, {c['failed']} failed, {c['flaky']} flaky, {c['skipped']} skipped**",
         f"- Browsers: {', '.join(m.get('browsers') or [])}; filter: `{m.get('grep') or 'all tests'}`; retries: {m.get('retries')}",
         f"- Target: `{m.get('base_url')}`; test data: `{m.get('db_url') or 'n/a'}`",
         f"- Commit `{(git.get('commit') or '')[:10]}` on `{git.get('branch')}`{' (uncommitted changes)' if git.get('dirty') else ''}",
         f"- Protected data ({', '.join(m.get('protected_paths') or []) or 'none configured'}): "
         + ("**MODIFIED: " + ", ".join(m["protected_changed"]) + "**" if m.get("protected_changed") else "untouched")]
    if m.get("updated_snapshots"):
        L.append("- **Visual baselines were re-recorded in this run** (--update-snapshots): review the changed images before committing.")
    for e in s["run_errors"]:
        L += ["", "## Error outside tests", "", "```", e, "```"]
    comp = s.get("comparison")
    if comp:
        L += ["", f"## Compared with previous run ({comp['previous_run']})", ""]
        for key, label in (("newly_failing", "Newly failing (likely regressions)"), ("fixed", "Fixed"), ("still_failing", "Still failing"),
                           ("new_tests", "New tests"), ("removed_tests", "Removed tests")):
            if comp[key]:
                L += [f"**{label}:**"] + [f"- {i}" for i in comp[key]] + [""]
        if not any(comp[k] for k in ("newly_failing", "fixed", "still_failing", "new_tests", "removed_tests")):
            L.append("No change in results.")
    bad = [t for t in s["tests"] if t["status"] in ("failed", "flaky")]
    if bad:
        L += ["", "## Failures and flaky tests", ""]
        for t in bad:
            L += [f"### {t['status'].upper()}: {t['title']} [{t['project']}]", "",
                  f"- File: `{t['file']}:{t['line']}`; attempts: {t['attempts']}; tags: {', '.join(t['tags']) or 'none'}",
                  f"- Error: {t['error'] or 'n/a'}"]
            for a in t["artifacts"]:
                L.append(f"- {a['name']}: `{a['path']}`")
            trace = next((a for a in t["artifacts"] if a["name"] == "trace"), None)
            if trace:
                L.append(f"- Open the trace: `npx playwright show-trace \"{run_dir / trace['path']}\"`")
            if t["error_full"]:
                L += ["", "```", *t["error_full"].splitlines()[:25], "```"]
            L.append("")
    L += ["", "## All tests", "", "| Status | Test | Browser | Time |", "|---|---|---|---|"]
    for t in s["tests"]:
        L.append(f"| {t['status']} | {t['title']} | {t['project']} | {t['duration_ms'] / 1000:.1f}s |")
    L += ["", f"Full HTML report: `npx playwright show-report \"{run_dir / 'html'}\"`. JUnit: `junit.xml`. "
          "Logs: `playwright.log`, `app.log`, `mock_llm.log`."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    rd = Path(sys.argv[1]).resolve()
    existing = json.loads((rd / "summary.json").read_text(encoding="utf-8")) if (rd / "summary.json").exists() else {}
    res = build_summary(rd, existing.get("meta", {}), rd.parent)
    print(f"{res['verdict']}: {rd / 'summary.md'}")
