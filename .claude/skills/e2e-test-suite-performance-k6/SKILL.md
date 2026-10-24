---
name: e2e-test-suite-performance-k6
description: Runs repeatable, NFR-driven k6 load tests against a full-stack application's HTTP API, against a mandatory non-production load-test database, and produces a detailed troubleshooting report with a pass/fail verdict and regression check against a saved baseline. Use when the user asks to load test, performance test, stress test, check TPS/latency/concurrency, or verify performance before merging, for any app with an HTTP backend.
---

# e2e-test-suite-performance-k6

Load tests an application with k6. Every run is driven by **this app's NFRs**,
runs against a **non-production database the user names**, and ends in a
report that says PASS/FAIL, why, and where to look.

Skill files (paths relative to this SKILL.md):
- `scripts/run_perf.py`: orchestrator (guard, seed, start app + mock LLM, k6, report, teardown)
- `k6/load_test.js`: generic plan-driven k6 script (never edit per app; per-app detail lives in the app's config)
- `scripts/mock_llm_server.py`: OpenAI/Azure-compatible mock model with fixed, seeded latency
- `scripts/report.py`: report builder (also re-runs standalone on a results folder)
- `scripts/db_guard.py`: refuses production/real databases
- `references/app-config.md`, `references/nfr.md`, `references/reading-the-report.md`
- `templates/`: starting points for a new app's `perf/` files

## Non-negotiable rules

1. **Ask for the NFRs at the start of every run.** Never invent them, and never reuse saved values without showing them and getting confirmation.
2. **Ask for the non-production load-test DB URL at the start of every run.** It's mandatory: don't proceed without one, don't take it from the app's `.env`, and don't reuse a URL from an earlier run without asking. If the guard refuses it, explain why and ask for another. Never print credentials.
3. **Never point load at production**, whether that's the DB, the base URL or real third-party accounts. Local app instances and named non-prod environments only.
4. **Never weaken an NFR, a threshold or a check to make a run pass.** Report the failure.
5. **Real paid services (e.g. a real LLM) only with explicit consent.** Default to mocks. Real mode is capped by the app config, and the report says what wasn't tested at full rate.

## Workflow

### 1. Find the app's perf setup

Work in the target repo (the user's current project unless they name another).
Look for `perf/k6.config.json`.

- **Found:** read it and summarize the flows under test in one line.
- **Missing:** onboard the app (read `references/app-config.md`). Find the HTTP API (routes, auth), how to start it locally, its health endpoint, the env var that selects its database, how to seed test data, and any paid external calls to mock. Copy `templates/k6.config.example.json` to `perf/k6.config.json`, fill it in, and confirm the flows and endpoints with the user before running anything. If the app has no HTTP API k6 can drive, stop and discuss options with the user.

### 2. Capture the NFRs (every run)

Read `references/nfr.md`. If `perf/nfr.json` exists, show its values in a short table and ask the user to confirm or change each one. Otherwise ask for all of them. Required:

| NFR | Ask as |
|---|---|
| `expected_tps` | Expected transactions per second today (peak hour) |
| `projected_tps` | Projected future TPS (and by when), to verify headroom for future enhancements |
| `latency_ms` | Acceptable latency thresholds, at least p95 (ms); p99 recommended; optional per endpoint |
| `concurrent_users` | Peak concurrent users |
| `max_error_rate` | Max acceptable error rate (e.g. 1% = 0.01) |

Optional: `think_time_s`, `durations`, `stress_factor`, `regression_tolerance_pct`.
Explain that one transaction = one flow from the config (it may be several HTTP calls).
Write the confirmed values to `perf/nfr.json`, with `captured_at` (today's date) and `captured_from: "user"`.

### 3. Get the load-test DB URL (every run)

Ask: *"What's the non-production load-test database URL for this run?"* Point out that the run seeds and resets that database. For file-based apps it's a `file:///` directory URL (e.g. `file:///D:/perf-data/<app>`). Pass it only with `--db-url`, and don't write it into files.

### 4. Choose the profile and mode

- Profile: `pre-merge` (default, a few minutes, gating), `full` (adds a stress ramp to find the breaking point, plus a soak if `durations.soak` is set), or `smoke` (sanity only). Phases: smoke → load @ expected TPS → projected @ future TPS → concurrency @ concurrent users → (full: stress ramp to projected × stress_factor, soak).
- LLM mode: `mock` (default). `real` only if the user explicitly asks and accepts the provider cost; then add `--llm real --confirm-real-llm`.
- Before starting, do a `--dry-run` and tell the user the phases and the estimated duration.

### 5. Run

From the target repo, with a Python 3.10+ interpreter (the app's venv is fine):

```
python <skill>/scripts/run_perf.py --repo . --db-url "<URL>" --profile pre-merge [--llm mock] [--target-url URL] [--save-baseline]
```

- `--target-url` tests an already-running non-prod deployment instead of starting the app locally. The guard refuses production-looking hosts.
- If the estimate is over about 9 minutes, run it in the background and wait for completion.
- Exit codes: `0` all NFRs met and no regression, `1` NFR/regression failure (the report says which), `2` setup error (bad NFR, guard refusal, app didn't start; the message says why).

### 6. Report back

Read `<results_dir>/<run>/report.md`, and `references/reading-the-report.md` when diagnosing. Tell the user:
- the verdict, and each failing NFR check with its target and actual value
- the regression result vs the baseline (or that none exists or isn't comparable)
- the top diagnostics, i.e. likely causes and where to look, plus the stress breaking point if run
- the absolute path to `report.html` (open it in a browser) and the run folder

If the run passed and no comparable baseline exists, offer to save it with `--save-baseline` (re-run), or copy the run's `results.json` to `<results_dir>/baseline.json`. Don't overwrite an existing baseline without asking.

## Repeatability

Same config + NFRs + seed + mock latency gives the same load and comparable results: the scripts use seeded data/flow selection, seed the test DB fresh, and mock latency is fixed. Commit `perf/k6.config.json`, `perf/nfr.json`, any mock script and `perf-results/baseline.json` so pre-merge runs compare against the same reference. `perf-results/*/` run folders are artifacts; keep them out of git.
