---
name: moe-agent-functional-testing
description: Runs every functional test of the MOE certificate equivalency agent one by one (each in its own pytest process) and saves each test's result plus a summary under the repo's testing/ sub-directory. Use when asked to run, re-run, or report on the agent's functional tests or test suite.
---

# MOE agent functional testing

Runs the full pytest suite of the certificate equivalency agent **one test at a
time**, saving each test's output and a run summary to
`testing/run-<YYYYMMDD-HHMMSS>/`. Every test uses fake model clients and
patched mock stores, so no API credentials are needed and `data/*.json` is
never written.

## Steps

1. **Run the script** with the repo's virtualenv Python, from the repo root
   (`D:\projects\MOE`, or whichever directory holds this repo's `agent/` and
   `tests/`):

   ```powershell
   & .\.venv\Scripts\python.exe .\.claude\skills\moe-agent-functional-testing\scripts\run_functional_tests.py
   ```

   - It prints one progress line per test. Each test runs in its own
     process (about a second apiece), so a full run takes a few minutes;
     allow a timeout of up to 10 minutes.
   - If the user named a subset (e.g. "just the APP-003 tests"), pass
     `-k "<pytest -k expression>"`, e.g. `-k app003`.
   - Exit code 1 only means at least one test failed. The results are still
     written, so continue to step 2.
   - If `.venv` is missing, say so and stop; don't install packages unasked.

2. **Read** the new `testing/run-*/summary.md` (the newest `run-*` folder).
   For each failure, open its file under `results/` to see the full
   traceback.

3. **Report** to the user:
   - overall PASS/FAIL and the totals (passed / failed / errors / skipped)
   - each failing test, with the assertion that failed in one line and the
     likely cause if the traceback makes it clear
   - the absolute path of the run folder and of `summary.md`

   Don't fix failing tests or code as part of this skill. Report them, and
   offer to investigate.

## Output layout

```
testing/run-<timestamp>/
  results/<NNN>_<file>__<test>.txt   status, duration, exit code, full pytest output
  summary.md                         totals, per-file table, failures (linked), all tests
  summary.json                       the same data, machine-readable
```

Each run gets its own timestamped folder, so earlier runs are never
overwritten.
