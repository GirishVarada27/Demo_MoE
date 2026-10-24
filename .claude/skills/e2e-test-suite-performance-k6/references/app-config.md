# App config: `perf/k6.config.json`

Describes how to start, seed and drive one application. The k6 script and
orchestrator are generic; everything app-specific lives here. Start from
`templates/k6.config.example.json`.

```jsonc
{
  "app_name": "Orders service",
  "base_url": "http://127.0.0.1:{port}",          // {port} = "port" below, or a free port
  "port": null,
  "start": {                                       // omit if you always use --target-url
    "command": ["npm", "run", "start:perf", "--", "--port", "{port}"],
    "cwd": ".",
    "health_path": "/health",                      // must answer < 500 once ready
    "startup_timeout_s": 90,
    "env": {"API_TOKENS": "<test-only token config>"}  // extra env for the started app ("_"-prefixed keys ignored)
  },
  "db": {
    "env_var": "DATABASE_URL",                     // how the app is told which DB to use
    "seed": {
      "command": ["npm", "run", "db:seed:perf", "--", "{db_url}"],   // any seed/reset command
      "copy": [{"from": "fixtures/users.json", "to": "users.json"}], // file:// DBs only
      "reset": {"orders.json": "[]"}                                 // file:// DBs only
    }
  },
  "db_guard": {
    "allowed_schemes": ["postgres", "postgresql"],
    "protected_paths": ["data"],                    // file:// URLs may not overlap these
    "prod_env_vars": ["DATABASE_URL"],              // values in env/.env = real DB, refused
    "prod_db_urls": ["postgres://orders-db.internal:5432/orders"],
    "prod_base_urls": ["https://api.example.com"],
    "allowed_url_pattern": "loadtest|perf"          // optional allow-list regex
  },
  "llm": {                                          // only for apps that call an LLM
    "mock": {
      "script": "perf/mock_llm_script.json",        // see scripts/mock_llm_server.py docstring
      "chat_latency_ms": 300, "chat_jitter_ms": 100, "embed_latency_ms": 30,
      "env": {"OPENAI_BASE_URL": "http://127.0.0.1:{mock_port}/v1", "OPENAI_API_KEY": "mock"}
    },
    "real": {"max_tps": 0.2, "max_vus": 2}          // caps for --llm real
  },
  "headers": {"Authorization": "Bearer {{env:PERF_API_TOKEN}}"},
  "pass_env": ["PERF_API_TOKEN"],                   // env vars forwarded to k6
  "request_timeout": "60s",
  "data_pools": {"skus": ["A-1", "B-2", "C-3"]},
  "flows": [
    {
      "name": "checkout",
      "weight": 3,                                  // relative share of transactions
      "session": "per_iteration",                   // or "per_vu"
      "steps": [
        {"name": "POST /api/cart", "method": "POST", "path": "/api/cart",
         "body": {"sku": "{{pool:skus}}", "qty": 1, "session": "{{session}}"},
         "expect_status": [200, 201], "json_not_empty": ["cart_id"],
         "extract": {"cart": "cart_id"}},
        {"name": "POST /api/checkout", "method": "POST", "path": "/api/checkout/{{var:cart}}",
         "expect_status": [200], "json_equals": {"status": "confirmed"}, "think_time_s": 0}
      ]
    }
  ],
  "results_dir": "perf-results"
}
```

## Templates in flows

| Template | Value |
|---|---|
| `{{pool:NAME}}` | a value from `data_pools.NAME`, picked by the seeded RNG (the same pick for the whole transaction) |
| `{{var:NAME}}` | a value extracted by an earlier step's `extract` (`{"NAME": "json.path"}`) |
| `{{session}}` | a unique session id per transaction (or per VU with `"session": "per_vu"`) |
| `{{uuid}}`, `{{vu}}`, `{{iter}}` | a seeded uuid / VU number / iteration number |
| `{{env:NAME}}` | an env var forwarded via `pass_env` (secrets: never write them in the config) |

## Checks per step

`expect_status` (default `[200]`), `json_not_empty` (dot paths), `json_equals`
(`{path: value}`), `body_contains` (strings). Failed checks count toward the
functional-check NFR, and their response bodies are sampled into the report.

## Onboarding a new app

1. Find the routes a real user journey hits (router files, OpenAPI spec, controllers). Model the 1–3 most important journeys as flows, weighted by real traffic share if the user knows it.
2. Find the start command and a health endpoint. If there's no health endpoint, use a cheap GET that returns 200.
3. Find the DB env var and a seed path that works on an empty non-prod DB.
4. List paid or rate-limited externals (LLMs, payment, SMS). Mock them, or ask the user how to stub them.
5. Fill `db_guard` with every known production URL, so the guard can refuse it.
6. `--dry-run`, then a `--profile smoke` run, before trusting any numbers.
