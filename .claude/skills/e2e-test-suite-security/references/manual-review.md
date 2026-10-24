# Manual review checklist

Read the code. Report only what you confirmed, with the location you read
and the code you're quoting. For pre-merge runs, start from the changed files
and follow their callers and callees.

## What to look for

**Authentication & authorization**
- Is every non-public route behind authentication? Is it enforced in middleware or a dependency, not just remembered per route?
- Object-level checks: does any handler, service or tool take an id (user, order, applicant, file) from the request without checking that the caller owns it? (API1 BOLA)
- Function-level checks: can an ordinary user reach admin-only operations? (API5)
- Mass assignment: are request models bound straight onto DB models, so callers can set fields like `role` or `owner_id`? (API3)

**Sessions & tokens**
- Are session ids generated server-side, random, bound to a user, expired and invalidated on logout?
- JWTs: is the signature verified with a fixed algorithm, and are `exp`/`aud`/`iss` checked?

**Data handling & integrity**
- Is every query parameterized? Does any input reach a shell, `eval`, a template, a file path or a deserializer?
- Read-modify-write on files or rows without locks or transactions under concurrent requests (races, lost updates)
- Is sensitive data written to logs, error responses or third parties?

**Resource limits** (API4)
- Unbounded in-memory stores, caches or lists keyed by client input; no pagination or size limits; expensive calls (LLM, email, payment) without quotas.

**Errors & config**
- Do exceptions get turned into responses that leak internals? Is debug or docs behaviour enabled by default rather than by explicit opt-in?

**LLM apps** (OWASP LLM Top 10 2025)
- LLM01 Prompt injection: can user text (or retrieved/tool text) override instructions? Is any security decision left to the prompt instead of code?
- LLM06 Excessive agency: which tools can the model call, with what arguments, and on whose behalf? Are side-effecting tools (writes, emails, tickets) and object ids authorized in code?
- LLM02/LLM07 Sensitive information disclosure / system prompt leakage: do secrets, other users' data or internal policy text sit in the context?
- LLM05 Improper output handling: is model output rendered as HTML, executed, or passed to other systems unchecked?
- LLM10 Unbounded consumption: per-user limits on turns, tokens and tool calls?

## manual_review.json format

```json
{
  "reviewer": "Claude (manual code review)",
  "findings": [
    {
      "rule_id": "MANUAL-<SHORT-ID>",
      "title": "One-line statement of the issue",
      "severity": "critical|high|medium|low|info",
      "description": "What's wrong, how it could be abused, what's affected.",
      "recommendation": "The specific fix: what to change, where, and how.",
      "location": "path/to/file.py:function_or_line",
      "evidence": "the code you read that shows it",
      "category": "OWASP mapping, e.g. API1:2023 BOLA",
      "cwe": "CWE-639",
      "confidence": "high|medium|low"
    }
  ]
}
```

Keep `rule_id` + `location` + `evidence` stable between runs for an unchanged
issue, so its fingerprint stays the same and accepted risks keep matching.

## Severity guide

| Severity | Typical meaning |
|---|---|
| critical | Exploitable remotely without credentials with a severe impact: committed live secret, auth bypass, RCE, SQL injection |
| high | Serious and plausible: missing auth on sensitive data, BOLA, CORS with credentials, sessions not bound to users |
| medium | Needs extra conditions or has limited impact: no rate limiting, verbose errors, races on non-critical data, unbounded memory |
| low | Hardening: missing headers, version disclosure, docs exposed, unpinned dependencies |
| info | Worth knowing, no direct risk |
