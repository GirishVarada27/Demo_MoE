---
name: e2e-test-suite-security
description: Runs a repeatable security test suite against a full-stack application the user owns, covering secrets in code and git history (gitleaks), static analysis (bandit, semgrep), vulnerable dependencies (pip-audit, npm audit), configuration review, non-destructive live checks against a local or non-prod instance, and a manual code review by Claude. Produces a report with each issue, its evidence and a recommended fix, plus a pass/fail gate for pre-merge or pre-deploy use. Use when the user asks to security test, scan for vulnerabilities, check security posture, or audit an app before merging or deploying.
---

# e2e-test-suite-security

Finds security issues in the user's own application and says how to fix each
one. Defensive and non-destructive: it reads code, audits dependencies, and
sends ordinary requests with harmless marker values to a **local or non-prod**
instance. It never targets production or third-party systems.

Skill files (paths relative to this SKILL.md):
- `scripts/run_security.py`: orchestrator (scanners, live checks, merge, gate, report)
- `scripts/scanners.py`: gitleaks, bandit, semgrep, pip-audit and npm audit wrappers, with fix guidance
- `scripts/config_review.py`: debug, CORS, TLS-verification, default-secret, pinning, `.env` and Dockerfile checks
- `scripts/dast.py`: live checks (headers, CORS, exposed docs/files, auth, session ownership, error handling, input limits, rate limiting)
- `scripts/findings.py`: finding model, fingerprints, accepted risks, new/fixed comparison, gate
- `scripts/report.py`: report.md / report.html / findings.json / results.sarif
- `scripts/setup_tools.py`: installs bandit, pip-audit and semgrep into an isolated venv; checks gitleaks
- `references/security-config.md`, `references/manual-review.md`, `references/triage.md`
- `templates/`: starting points for a new app's `security/` files

## Rules

1. **Only test what the user owns or is authorized to test**: their repo, and a local instance or a non-prod URL they confirm for this run. The guard refuses production-looking targets and data stores; don't work around it.
2. **The live checks start the app against a non-production test data store.** Ask for it every run (`--db-url`), the same as for load testing. Never use the app's real data. If the user only wants static checks, use `--static-only`.
3. **Stay non-destructive.** Use the built-in probes only. Don't add exploit payloads, brute forcing, or anything that modifies or deletes real data. If something looks exploitable, report it with the evidence you have; don't prove it further.
4. **Every finding needs evidence and a concrete fix.** Don't pad the report with speculation. Mark uncertain findings with `confidence: low`.
5. **Never mark a finding accepted, or change severities or thresholds, to make the gate pass.** Only the user accepts risk, with a reason, owner and expiry.
6. **Don't print secrets.** gitleaks output is redacted; keep it that way in your messages.

## Workflow

### 1. Prepare

- Run `python <skill>/scripts/setup_tools.py --check`. If tools are missing, ask before installing (`setup_tools.py` without `--check`; gitleaks via winget/brew).
- In the target repo, read `security/config.json`. If it's missing, onboard the app with `references/security-config.md`: languages, source paths, how to start it locally, its endpoints (mark which are public), the data-store env var, and paid externals to mock. Write the config from `templates/security.config.example.json`, and confirm the endpoint list with the user.

### 2. Confirm scope (every run)

Ask, in one message:
- the live target: start the app locally (default), or a named **non-prod** URL (`--target-url`)
- the non-prod test data store URL (`--db-url`; e.g. `file:///D:/sec-data/<app>` for file stores), or `--static-only`
- the profile: `pre-merge` (default: full git-history secrets scan plus semgrep) or `quick` (working tree only, no semgrep)
- the gate: fail on `high` and above (default, from the config's `fail_on`), or another level

### 3. Run the automated suite

```
python <skill>/scripts/run_security.py --repo . --db-url "<URL>" [--profile pre-merge] [--target-url URL] [--fail-on high]
```

Exit codes: `0` no blocking findings, `1` blocking findings, `2` setup error (the message says why).

Then check coverage: a tool that was missing or failed appears as a note in the report. Say so; never present a skipped check as clean. Sanity-check that the scanners saw the code (e.g. bandit's file count in `bandit.json`).

### 4. Manual review (Claude)

Scanners miss logic flaws. Follow `references/manual-review.md`: review authentication and authorization, object-level access, session handling, data writes and concurrency, resource limits, error handling, and for LLM apps prompt injection, tool permissions (excessive agency) and output handling. For `pre-merge`, focus on the files changed vs the target branch (`git diff --name-only <base>...HEAD`) and the code they touch.

Write each confirmed issue to `<run_dir>/manual_review.json` (format in `references/manual-review.md`). Each needs a `location` you actually read, `evidence` quoted from the code, and a specific `recommendation`. Then merge:

```
python <skill>/scripts/run_security.py --rerender <run_dir> --manual-findings <run_dir>/manual_review.json
```

### 5. Triage and report back

Use `references/triage.md` to weed out false positives. Verify each high or critical finding against the code or response before reporting it. If a scanner finding is a false positive, say so with the reason; the user can record it in `security/accepted-risks.json`. Tell the user:
- the verdict and counts by severity, and the blocking findings
- the top issues, each with location, issue and recommended fix (the report has all of them)
- what changed since the previous run (new and fixed findings), and any skipped or failed checks
- the absolute paths to `report.html` and `results.sarif` (the latter can be uploaded to GitHub code scanning)

Offer to fix specific findings, but don't change the app's code unless the user asks.

## Repeatability

Commit `security/config.json` and `security/accepted-risks.json`. Each run writes `<results_dir>/<timestamp>-<profile>/` (keep run folders out of git), compares with the previous run, and fingerprints findings so they stay stable when code moves. Accepted risks expire, so they get re-reviewed.
