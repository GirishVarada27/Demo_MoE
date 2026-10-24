# App config: `ui-tests/ui-tests.config.json`

```jsonc
{
  "app_name": "Orders portal",
  "project_dir": "ui-tests",              // the Playwright project (package.json, playwright.config.ts)
  "results_dir": "ui-results",            // run folders (keep out of git)
  "default_browsers": ["chromium"],
  "workers": 2,
  "retries": 1,                           // a test passing only on retry is reported flaky
  "base_url": "http://127.0.0.1:{port}",
  "start": {
    "command": ["npm", "run", "start", "--", "--port", "{port}"],
    "cwd": ".",
    "health_path": "/health",             // any URL that answers < 500 once the UI is ready
    "startup_timeout_s": 90,
    "env": {"FEATURE_FLAGS": "test"}      // extra env for the app ("_"-prefixed keys ignored)
  },
  "db": {
    "env_var": "DATABASE_URL",            // how the app is told which data store to use
    "seed": {
      "command": ["npm", "run", "db:seed:test", "--", "{db_url}"],
      "copy": [{"from": "fixtures/users.json", "to": "users.json"}],   // file:// stores only
      "reset": {"orders.json": "[]"}                                    // file:// stores only
    }
  },
  "db_guard": {
    "allowed_schemes": ["postgres"], "protected_paths": ["data"],
    "prod_env_vars": ["DATABASE_URL"], "prod_db_urls": [], "prod_base_urls": []
  },
  "protected_paths": ["data"],            // fingerprinted before and after; any change fails the run
  "llm": {
    "mock": {
      "script": "ui-tests/mock_llm_script.json",
      "chat_latency_ms": 30, "embed_latency_ms": 5,
      "env": {"OPENAI_BASE_URL": "http://127.0.0.1:{mock_port}/v1", "OPENAI_API_KEY": "mock"}
    }
  }
}
```

Environment passed to Playwright: `BASE_URL` (used as `baseURL`),
`UI_RESULTS_DIR` (where reports go), `UI_DATA_DIR` (the isolated data
dir, for file:// stores) and `CI=1`.

## Files in `ui-tests/`

| File | Purpose |
|---|---|
| `package.json` + `package-lock.json` | `@playwright/test` pinned to an exact version; commit the lockfile |
| `playwright.config.ts` | reporters (HTML/JSON/JUnit into `UI_RESULTS_DIR`), traces and screenshots on failure, projects per browser, screenshot tolerances |
| `tests/fixtures.ts` | the page object + `test` fixture |
| `tests/*.spec.ts` | one file per area; tests tagged `@smoke` / `@regression` / `@visual` |
| `tests/__screenshots__/` | approved visual baselines (commit them) |
| `mock_llm_script.json` | deterministic model replies (if the app uses an LLM) |
