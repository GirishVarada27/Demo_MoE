---
name: e2e-test-suite-ui-playwright
description: Builds, maintains and runs repeatable Playwright UI regression suites (Node @playwright/test) for full-stack web apps. Each run starts the app against an isolated, seeded non-production data store with a deterministic mock model, alarms if the app's real data was modified, and reports pass/fail/flaky per test with traces and screenshots plus new failures vs the previous run. Use when asked to UI test, end-to-end/browser test, write or run UI regression tests, check the UI before merging, or add tests for a new UI feature.
---

# e2e-test-suite-ui-playwright

Regression tests for an app's UI that run the same way every time. The
**tests live in the app's repo** (`ui-tests/`: pinned Playwright, lockfile,
specs, approved screenshots), so they're reviewed and versioned with the
code. **This skill supplies the harness**: start and isolate the app, run,
report, compare, gate.

Skill files (paths relative to this SKILL.md):
- `scripts/run_ui.py`: orchestrator (guard + seed the test data, mock LLM, start app, `npx playwright test`, protected-data alarm, summary). `--serve` keeps the seeded app up for writing tests or `playwright codegen`.
- `scripts/ui_report.py`: summary.md / summary.json from Playwright's JSON report, with comparison against the previous run
- `scripts/mock_llm_server.py`: OpenAI/Azure-compatible mock with per-scenario scripts (deterministic answers)
- `scripts/db_guard.py`: refuses production / the app's real data
- `templates/`: `package.json`, `playwright.config.ts`, `fixtures.ts`, `example.spec.ts`, `ui-tests.config.json`, `gitignore`
- `references/app-config.md`, `references/writing-tests.md`, `references/visual.md`, `references/triage.md`

## Rules

1. **Never point UI tests at production** (app, data or third-party accounts). The app is started locally against a test data store the user names, or a confirmed non-prod URL.
2. **Ask for the non-production test data URL every run** (`--db-url`). The run seeds and resets it. Don't take it from `.env`, and don't reuse an old one without asking.
3. **Never weaken a test to make it pass**: no deleted assertions, no added `test.skip`/`.fixme`, no looser selectors or longer timeouts just to get green, and no re-recorded visual baselines. A failure is either an app bug (report it) or a test bug (fix the test's mistake, and say so).
4. **Re-record visual baselines only with the user's explicit approval** of the change (show the diff images first).
5. **Deterministic by construction**: use mocks for paid or nondeterministic services (LLMs), fixed seed data and fresh browser contexts. Never use `waitForTimeout`/sleeps; wait for UI state.
6. **Don't modify the app's code** unless the user asks. If a test needs a hook (e.g. a `data-testid`), propose it.

## Workflow

### 1. Find or create the suite

In the target repo, look for `ui-tests/ui-tests.config.json` and `ui-tests/playwright.config.ts`.

- **Found:** read both, plus the spec list (`ui-tests/tests/*.spec.ts`), and summarize the covered journeys in a line.
- **Missing:** onboard the app:
  1. Read `references/app-config.md`. Find the UI start command, a health URL, the data-store env var, a seed path, and paid externals to mock.
  2. Copy the templates into `ui-tests/`. Pin `@playwright/test` to an exact release at least 2 weeks old, then run `npm install` and `npx playwright install chromium`.
  3. Read the UI code and list the user journeys worth protecting. Confirm them with the user, then write specs following `references/writing-tests.md`, with a page object in `fixtures.ts`.
  4. Use `--serve` and inspect the DOM (roles, `data-testid`s) before writing selectors.

### 2. Confirm scope (every run)

Ask in one message:
- the non-prod test data URL (`--db-url`, e.g. `file:///D:/ui-data/<app>`), or a non-prod `--target-url` if the app is already deployed somewhere
- browsers: `chromium` (default) or `chromium,firefox,webkit`
- scope: everything (default before merging), `@smoke`, or `--grep` for the area being changed

### 3. Run

From the target repo, with Python 3.10+:

```
python <skill>/scripts/run_ui.py --repo . --db-url "<URL>" [--browsers chromium] [--grep @smoke] [--grep-invert @visual]
```

- Exit codes: `0` pass (flaky tests are listed; `--fail-on-flaky` makes them fail), `1` a test failed or protected data was modified, `2` setup error.
- `--repeat-each 3 --retries 0` hunts flakiness. Use it on new or changed specs before trusting them.
- New specs must be shown to **fail on a broken UI** before you rely on them: temporarily break the behaviour under test, run, confirm the failure, then restore the file (`git checkout -- <file>`, after checking it was clean).

### 4. Report back

Read `<results_dir>/<run>/summary.md`, and `references/triage.md` for failures. Tell the user:
- the verdict, the counts (passed/failed/flaky/skipped), and whether protected data stayed untouched
- **newly failing** tests first (likely regressions from the change), then still-failing and flaky ones
- for each failure: what the user would see broken, the assertion that failed, whether it's an app bug or a test bug, and the trace path (`npx playwright show-trace <trace.zip>`)
- the paths to `summary.md` and the HTML report (`npx playwright show-report <run>/html`)

### 5. Keep the suite current

When a UI feature is added or changed, add or update the spec for that journey in the same change. For pre-merge runs, check `git diff --name-only <base>...HEAD` for UI files whose journeys have no test, and offer to add one.

## Repeatability

Commit `ui-tests/` (config, specs, `package-lock.json`, `tests/__screenshots__/`) and the mock script. Keep `ui-results/` run folders out of git. Every run seeds the test data fresh, uses the mock's fixed answers and new browser contexts, and compares results with the previous run.
