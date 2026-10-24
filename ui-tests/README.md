# UI regression tests

Playwright (`@playwright/test` 1.64.0, Chromium by default) tests for the
Streamlit UI, run through the `e2e-test-suite-ui-playwright` Claude Code
skill. Ask Claude to "run the UI tests", or run them directly:

```
.venv\Scripts\python.exe %USERPROFILE%\.claude\skills\e2e-test-suite-ui-playwright\scripts\run_ui.py --repo . --db-url file:///D:/ui-data/moe
```

The runner starts `streamlit run ui/app.py` with `MOE_DATA_URL` pointed at
that test data dir (seeded fresh from `data/applicants.json` every run) and
Azure OpenAI pointed at a local mock that replies from
`mock_llm_script.json`, so every run gets the same answers. It fails the run
if anything under `data/` changes.

| Spec | Covers |
|---|---|
| `smoke.spec.ts` | app shell, applicant list and details |
| `applicant-turn.spec.ts` | load an applicant: sectioned answer, tool trace order, trace details, raw response, case state; APP-003 asks for the missing Emirates ID |
| `conversation.spec.ts` | typed message, follow-up turn keeps context, Reset conversation |
| `failure-injection.spec.ts` | "Inject tool failures": retry, then escalation; ticket written to the test data dir |
| `visual.spec.ts` | `@visual` baselines for the sidebar and an answer card (`tests/__screenshots__/`, Windows/Chromium) |

Setup on a new machine: `cd ui-tests && npm ci && npx playwright install chromium`.
Re-record visual baselines only after an approved UI change:
`run_ui.py ... --grep @visual --update-snapshots`.
