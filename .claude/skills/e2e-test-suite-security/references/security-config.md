# App config: `security/config.json`

```jsonc
{
  "app_name": "Orders service",
  "languages": ["python"],                 // python | javascript | typescript (others: semgrep only)
  "source_paths": ["src", "api"],          // what the scanners read
  "config_paths": ["deploy", "Dockerfile"],// extra config files for config review
  "exclude_paths": [".venv", "node_modules", "tests", "security-results"],
  "python": {"venv_python": ".venv/Scripts/python.exe", "requirements": ["requirements.txt"]},
  "node_paths": ["frontend"],              // folders with package-lock.json for npm audit
  "semgrep_configs": null,                 // default: p/owasp-top-ten, p/secrets + language packs
  "fail_on": "high",                       // gate level
  "accepted_risks": "security/accepted-risks.json",
  "results_dir": "security-results",

  // Live checks: how to start the app locally (same shape as the k6 skill's config)
  "base_url": "http://127.0.0.1:{port}",
  "start": {"command": ["npm", "start", "--", "--port", "{port}"], "health_path": "/health", "startup_timeout_s": 60,
            "env": {"API_TOKENS": "<config that makes the test users' tokens valid>"}},  // "_"-prefixed keys ignored
  "db": {"env_var": "DATABASE_URL", "seed": {"command": ["npm", "run", "db:seed", "--", "{db_url}"]}},
  "db_guard": {"allowed_schemes": ["postgres"], "prod_env_vars": ["DATABASE_URL"], "prod_db_urls": [], "prod_base_urls": []},
  "llm": {"mock": {"script": "perf/mock_llm_script.json", "env": {"OPENAI_BASE_URL": "http://127.0.0.1:{mock_port}/v1"}}},

  "dast": {
    "auth_headers": {"Authorization": "Bearer <token for a NON-PROD test user A>"},  // omit if the app has no auth
    "alt_auth_headers": {"Authorization": "Bearer <token for test user B>"},      // enables cross-user session checks
    "allowed_public_paths": [],            // e.g. ["/docs"] if public docs are intended
    "extra_sensitive_paths": ["/admin"],
    "endpoints": [
      {"name": "create_order", "method": "POST", "path": "/api/orders",
       "body": {"sku": "A-1", "note": "{{input}}", "session": "{{session}}"},
       "input_field": "note", "max_input_chars": 1000, "session_scoped": false,
       "impact": "anyone can place orders"},
      {"name": "health", "method": "GET", "path": "/health", "public": true}
    ],
    "rate_limit_probe": {"endpoint": "create_order", "requests": 20, "severity": "medium",
                         "auth_headers": {"Authorization": "Bearer <test user C>"}}  // optional: its own user, so other probes don't use its budget
  }
}
```

## Endpoint fields

| Field | Meaning |
|---|---|
| `public` | true = anonymous access is intended (skips the auth check) |
| `body` | JSON body; `{{input}}` = sample/probe input, `{{session}}` = a fresh session id |
| `input_field` | the free-text field to probe for error handling, reflection and size limits |
| `max_input_chars` | the documented limit; the probe sends this + 1000 chars |
| `session_scoped` | true = the endpoint uses a session id; checks that it's bound to the caller |
| `session_response_field`, `session_field` | for server-issued sessions: the response field holding the new id and the request field that sends it back. The check then (a) continues user A's session as user B (`alt_auth_headers`), which must fail, and (b) sends a made-up id, which must be refused. Without these, the check sends one caller-chosen id from two clients. |
| `impact` | one line on what anonymous access would allow (goes into the finding) |
| `auth_missing_severity`, `auth_fix` | override the default severity/fix for a missing-auth finding |

Use a dedicated non-prod test account for `auth_headers`, never a real user's
token, and keep the token itself in an env var or local file rather than the
committed config if it's sensitive.

# Accepted risks: `security/accepted-risks.json`

```json
{"accepted": [
  {"fingerprint": "3f2a9c...", "title": "API docs exposed", "reason": "Internal-only service behind VPN",
   "owner": "platform-team", "expires": "2027-03-31"}
]}
```

`fingerprint` comes from the report. All four fields are required, and an
entry stops suppressing once `expires` passes. Entries for findings that no
longer occur are flagged for removal.
