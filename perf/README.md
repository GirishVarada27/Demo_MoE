# Load testing

Load tests run through the `e2e-test-suite-performance-k6` Claude Code skill
(ask Claude to "load test the app"). It asks for the NFRs and a non-production
load-test data URL every run, then drives `POST /api/v1/chat` with k6.

| File | Purpose |
|---|---|
| `k6.config.json` | How to start (uvicorn `api.server:app`), seed and drive MOE, and which data dirs are refused |
| `mock_llm_script.json` | The mock model's tool-call sequence per turn: lookup → documents → search → eligibility → answer |
| `nfr.json` | The NFRs last confirmed by the user (created by the skill) |
| `../perf-results/baseline.json` | The accepted baseline that pre-merge runs are compared against |

- **Test data:** MOE's stores are JSON files, so the "DB URL" is a `file:///`
  directory (e.g. `file:///D:/perf-data/moe`), passed to the app as
  `MOE_DATA_URL`. Each run copies `data/applicants.json` there and resets the
  escalation and draft stores. `data/` itself is refused.
- **Model:** by default a local mock stands in for Azure OpenAI (fixed,
  seeded latency of 300 ± 100 ms per chat call), so runs are free and
  repeatable and measure MOE's own overhead. `--llm real` uses the real
  deployment, capped at 0.2 TPS and 2 users.
- **Applicant mix:** APP-001/002/004/005/007/008/009/010 take the normal
  answer path. APP-003 (missing document) and APP-006 (no rule, escalates)
  are left out, because their turns need different replies or write the
  shared escalation file.

Run without Claude:

```
.venv\Scripts\python.exe %USERPROFILE%\.claude\skills\e2e-test-suite-performance-k6\scripts\run_perf.py --repo . --db-url file:///D:/perf-data/moe --profile pre-merge
```
