# Triage: confirming findings and spotting false positives

Before reporting a high or critical finding, confirm it.

| Finding | Confirm by | Common false positive |
|---|---|---|
| gitleaks secret | Open the file/commit: is it a real credential (not a placeholder, test fixture or hash)? | Example keys in docs, test fixtures, `.env.example` placeholders. A secret in a git-ignored local file is reported as info. |
| bandit B105/B106 hardcoded password | Is it actually a secret? | Variable names like `password_field = "password"` |
| bandit B104 bind 0.0.0.0 | Is it the default, or only inside a container whose port is published deliberately? | Dockerfile CMD behind a proxy |
| bandit B603/B607 subprocess | Is any argument user-controlled? | Fixed tool invocations |
| semgrep injection rules | Trace the input: does user data really reach the sink unsanitized? | Already-parameterized ORM calls |
| pip-audit / npm audit CVE | Is the vulnerable function reachable, and is it a runtime dependency? | Dev-only tools; affected feature unused (still recommend upgrading) |
| DAST missing auth | Is the endpoint meant to be public? If yes, set `"public": true` in the config | Health/status endpoints |
| DAST no rate limit | Is there a gateway/proxy in front in real deployments that the local run skips? | Report it, but note it may be enforced upstream |
| DAST missing headers | Are they added by the reverse proxy in deployed environments? | Local run without the proxy |
| DAST exposed docs | Are docs intentionally public? Then add the path to `allowed_public_paths` | Public developer APIs |

Record a confirmed false positive or accepted risk in `security/accepted-risks.json`
only with the user's agreement (reason, owner, expiry); don't delete it from the report.
