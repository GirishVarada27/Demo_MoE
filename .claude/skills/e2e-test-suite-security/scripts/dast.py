"""Non-destructive live checks against a running local / non-prod instance.

Every probe is a normal HTTP request with harmless marker values. Nothing
here tries to break in, brute-force, or change data beyond what a normal
request does. Checks:
  headers, info disclosure, CORS, HTTP methods, exposed docs/files,
  authentication on protected endpoints, session ownership, error handling
  and input validation, oversized input, rate limiting.
Endpoints come from the app's security config (`dast.endpoints`).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4

from findings import Finding

TRACE_MARKERS = re.compile(r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+|at [\w.$]+\([\w]+\.java:\d+\)|"
                           r"System\.\w+Exception|<b>Fatal error</b>|ORA-\d{5}|SQLSTATE\[|psycopg2\.|sqlite3\.", re.I)
API = "OWASP API Security Top 10 2023"


class Response:
    def __init__(self, status: int, headers: dict, body: str, elapsed_ms: float, error: str | None = None):
        self.status, self.headers, self.body, self.elapsed_ms, self.error = status, headers, body, elapsed_ms, error


class Client:
    def __init__(self, base_url: str, timeout: float, default_headers: dict, log: list):
        self.base = base_url.rstrip("/")
        self.timeout, self.default_headers, self.log = timeout, default_headers, log

    def request(self, method: str, path: str, body=None, headers: dict | None = None, raw: bytes | None = None, auth: bool = True) -> Response:
        hdrs = dict(self.default_headers if auth else {})
        hdrs.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(200_000).decode("utf-8", "replace"),
                                (time.perf_counter() - started) * 1000)
        except urllib.error.HTTPError as e:
            resp = Response(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(200_000).decode("utf-8", "replace"),
                            (time.perf_counter() - started) * 1000)
        except Exception as e:  # connection refused, timeout
            resp = Response(0, {}, "", (time.perf_counter() - started) * 1000, error=str(e))
        self.log.append({"method": method, "path": path, "status": resp.status, "ms": round(resp.elapsed_ms), "error": resp.error})
        return resp


def _render(value, ctx: dict):
    if isinstance(value, dict):
        return {k: _render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, ctx) for v in value]
    if isinstance(value, str):
        for k, v in ctx.items():
            value = value.replace("{{" + k + "}}", str(v))
    return value


def _evidence(method: str, path: str, r: Response, extra: str = "") -> str:
    hdrs = {k: v for k, v in r.headers.items() if k in ("server", "x-powered-by", "access-control-allow-origin",
                                                       "access-control-allow-credentials", "content-type", "allow")}
    return f"{method} {path} -> {r.status} {json.dumps(hdrs)} {extra}".strip()[:500]


def run(base_url: str, cfg: dict, timeout: float = 30) -> tuple[list[Finding], list[str], list[dict]]:
    dast = cfg.get("dast", {})
    endpoints = dast.get("endpoints", [])
    log: list[dict] = []
    client = Client(base_url, timeout, dast.get("auth_headers", {}), log)
    findings: list[Finding] = []
    notes: list[str] = []
    is_https = base_url.startswith("https://")

    def call(ep: dict, ctx: dict | None = None, auth: bool = True, headers: dict | None = None) -> tuple[str, Response]:
        ctx = {"session": f"sec-{uuid4().hex[:12]}", "input": ep.get("sample_input", "hello"), **(ctx or {})}
        path = _render(ep["path"], ctx)
        body = _render(ep.get("body"), ctx) if ep.get("body") is not None else None
        return path, client.request(ep.get("method", "GET"), path, body, headers=headers, auth=auth)

    if not endpoints:
        return [], ["No dast.endpoints configured; live checks skipped."], log

    # 1. Security headers, on the first endpoint of each kind.
    first = endpoints[0]
    path, r = call(first)
    if r.status == 0:
        return [], [f"Target {base_url} unreachable: {r.error}"], log
    ctype = r.headers.get("content-type", "")
    header_rules = [
        ("x-content-type-options", "low", "Missing X-Content-Type-Options header",
         "Send `X-Content-Type-Options: nosniff` on every response (middleware or reverse proxy).", "CWE-693"),
        ("cache-control", "low", "API responses can be cached",
         "Send `Cache-Control: no-store` on responses carrying user or case data.", "CWE-524"),
    ]
    if "html" in ctype:
        header_rules += [
            ("content-security-policy", "medium", "Missing Content-Security-Policy",
             "Add a restrictive CSP (default-src 'self'; frame-ancestors 'none'); start in report-only mode.", "CWE-1021"),
            ("x-frame-options", "low", "Missing clickjacking protection",
             "Send `X-Frame-Options: DENY` or CSP `frame-ancestors 'none'`.", "CWE-1021"),
        ]
    if is_https:
        header_rules.append(("strict-transport-security", "medium", "Missing HSTS",
                             "Send `Strict-Transport-Security: max-age=31536000; includeSubDomains`.", "CWE-319"))
    for header, sev, title, rec, cwe in header_rules:
        if header not in r.headers:
            findings.append(Finding(check="dast", rule_id=f"DAST-HDR-{header.upper()}", title=title, severity=sev,
                                    description=f"The response from {first.get('method', 'GET')} {first['path']} has no `{header}` header.",
                                    recommendation=rec, location=f"{first.get('method', 'GET')} {first['path']}",
                                    evidence=_evidence(first.get("method", "GET"), path, r), category="A05:2021 Security Misconfiguration",
                                    cwe=cwe, tool="dast"))
    server = r.headers.get("server", "") + " " + r.headers.get("x-powered-by", "")
    if re.search(r"\d+\.\d+", server):
        findings.append(Finding(check="dast", rule_id="DAST-INFO-VERSION", title="Server software version disclosed", severity="low",
                                description=f"Response headers reveal `{server.strip()}`, which helps attackers match known CVEs.",
                                recommendation="Strip or genericize Server / X-Powered-By headers (e.g. `server_header=False` in uvicorn).",
                                location="response headers", evidence=_evidence("GET", path, r), category="A05:2021 Security Misconfiguration",
                                cwe="CWE-200", tool="dast"))

    # 2. CORS: does the app trust an arbitrary origin?
    evil = "https://cors-probe.invalid"
    for ep in endpoints[:3]:
        path, r = call(ep, headers={"Origin": evil})
        acao, acac = r.headers.get("access-control-allow-origin"), r.headers.get("access-control-allow-credentials", "").lower()
        if acao in (evil, "*"):
            creds = acac == "true"
            findings.append(Finding(
                check="dast", rule_id="DAST-CORS", title="CORS trusts any origin" + (" with credentials" if creds else ""),
                severity="high" if (creds or acao == evil) else "low",
                description=f"A request with `Origin: {evil}` got `Access-Control-Allow-Origin: {acao}`"
                            + (" and `Access-Control-Allow-Credentials: true`, so any website can make authenticated calls as the user." if creds else "."),
                recommendation="Allow-list the real front-end origins; never reflect the request Origin or use `*` with credentials.",
                location=f"{ep.get('method', 'GET')} {ep['path']}", evidence=_evidence(ep.get("method", "GET"), path, r),
                category="A05:2021 Security Misconfiguration", cwe="CWE-942", tool="dast"))
            break

    # 3. TRACE enabled?
    r = client.request("TRACE", "/")
    if r.status == 200:
        findings.append(Finding(check="dast", rule_id="DAST-TRACE", title="HTTP TRACE enabled", severity="low",
                                description="TRACE echoes requests back, which can expose headers to cross-site tracing.",
                                recommendation="Disable TRACE at the server or proxy.", location="TRACE /",
                                evidence=_evidence("TRACE", "/", r), category="A05:2021 Security Misconfiguration", cwe="CWE-693", tool="dast"))

    # 4. Exposed docs and sensitive files.
    exposed = [("/docs", "low", "Interactive API docs exposed"), ("/redoc", "low", "API docs exposed"),
               ("/openapi.json", "low", "OpenAPI schema exposed"), ("/swagger-ui.html", "low", "Swagger UI exposed"),
               ("/swagger.json", "low", "Swagger schema exposed"), ("/.env", "critical", "Environment file served over HTTP"),
               ("/.git/HEAD", "high", "Git metadata served over HTTP"), ("/server-status", "medium", "Server status page exposed"),
               ("/actuator/env", "high", "Spring actuator env endpoint exposed")]
    for p, sev, title in exposed + [(x, "medium", f"Sensitive path exposed: {x}") for x in dast.get("extra_sensitive_paths", [])]:
        r = client.request("GET", p, auth=False)
        looks_real = r.status == 200 and not (p.startswith("/.") and "<html" in r.body.lower()[:200])
        if p == "/.git/HEAD":
            looks_real = looks_real and r.body.startswith("ref:")
        if looks_real and p not in dast.get("allowed_public_paths", []):
            docs = p in ("/docs", "/redoc", "/openapi.json", "/swagger-ui.html", "/swagger.json")
            findings.append(Finding(
                check="dast", rule_id=f"DAST-EXPOSED{p.replace('/', '-').upper()}", title=title, severity=sev,
                description=f"GET {p} returns 200 without authentication." + (" It maps every endpoint and schema for an attacker." if docs else ""),
                recommendation=("Disable docs in non-dev environments (FastAPI: docs_url=None, redoc_url=None, openapi_url=None "
                                "unless an env flag enables them) or put them behind auth." if docs else
                                "Block the path at the web server, keep the file out of the deploy artifact, and rotate anything it exposed."),
                location=f"GET {p}", evidence=_evidence("GET", p, r), category="A05:2021 Security Misconfiguration",
                cwe="CWE-200" if docs else "CWE-538", tool="dast"))

    # 5. Authentication: protected endpoints must refuse anonymous callers.
    for ep in endpoints:
        if ep.get("public"):
            continue
        path, r = call(ep, auth=False)
        if 200 <= r.status < 300:
            findings.append(Finding(
                check="dast", rule_id="DAST-AUTH-MISSING", title=f"No authentication on {ep.get('method', 'GET')} {ep['path']}",
                severity=ep.get("auth_missing_severity", "high"),
                description=f"An anonymous request (no credentials) to {ep.get('method', 'GET')} {ep['path']} succeeded with "
                            f"{r.status}. Anyone who can reach the service can use it" + (f" -- {ep['impact']}" if ep.get("impact") else "") + ".",
                recommendation=ep.get("auth_fix") or "Require authentication (e.g. OAuth2/OIDC bearer tokens or an API gateway) and "
                               "authorize each request against the caller's identity before touching data.",
                location=f"{ep.get('method', 'GET')} {ep['path']}", evidence=_evidence(ep.get("method", "GET"), path, r, r.body[:200]),
                category=f"{API} API2: Broken Authentication", cwe="CWE-306", tool="dast"))
        elif r.status in (401, 403) and dast.get("auth_headers"):
            bad = {k: "Bearer invalid-token-probe" for k in dast["auth_headers"]}
            path, r2 = call(ep, auth=False, headers=bad)
            if 200 <= r2.status < 300:
                findings.append(Finding(check="dast", rule_id="DAST-AUTH-INVALID-TOKEN", title=f"Invalid token accepted on {ep['path']}",
                                        severity="critical", description="A request with a made-up token succeeded.",
                                        recommendation="Validate token signature, issuer, audience and expiry on every request.",
                                        location=f"{ep.get('method', 'GET')} {ep['path']}", evidence=_evidence(ep.get("method", "GET"), path, r2),
                                        category=f"{API} API2: Broken Authentication", cwe="CWE-287", tool="dast"))

    # 6. Session ownership.
    for ep in endpoints:
        if not ep.get("session_scoped"):
            continue
        if ep.get("session_response_field"):
            findings += _server_issued_session_checks(client, ep, dast)
            continue
        shared = f"sec-owner-{uuid4().hex[:8]}"
        _, r1 = call(ep, {"session": shared})
        path, r2 = call(ep, {"session": shared}, headers={"User-Agent": "second-client-probe"})
        if 200 <= r1.status < 300 and 200 <= r2.status < 300:
            findings.append(Finding(
                check="dast", rule_id="DAST-SESSION-UNBOUND", title=f"Sessions not bound to the caller on {ep['path']}", severity="high",
                description="Two unrelated clients used the same caller-chosen session id and both were served from that session. "
                            "Anyone who learns or guesses an id can continue, read or delete another user's conversation.",
                recommendation="Issue session ids server-side (random, 128-bit+), bind each to the authenticated user, reject "
                               "client-chosen ids, and check ownership on every read/write/delete.",
                location=f"{ep.get('method', 'GET')} {ep['path']}", evidence=_evidence(ep.get("method", "GET"), path, r2),
                category=f"{API} API1: Broken Object Level Authorization", cwe="CWE-639", tool="dast"))
            break

    # 7. Error handling and input validation on JSON endpoints.
    for ep in [e for e in endpoints if e.get("method", "GET") in ("POST", "PUT", "PATCH")]:
        probes = [("malformed JSON", b'{"broken": ', None), ("wrong type", None, _wrong_types(ep.get("body")))]
        field = ep.get("input_field")
        if field:
            marker = f"probe{uuid4().hex[:6]}"
            probes += [
                ("markup marker", None, _with_field(ep, field, f"<i>{marker}</i>\"'")),
                ("template marker", None, _with_field(ep, field, "calc {{7*191}} ${7*191}")),
                ("oversized input", None, _with_field(ep, field, "A" * int(ep.get("max_input_chars", 4000) + 1000))),
            ]
        for label, raw, body in probes:
            path = _render(ep["path"], {"session": f"sec-{uuid4().hex[:8]}"})
            r = client.request(ep["method"], path, body, raw=raw)
            loc = f"{ep['method']} {ep['path']}"
            if TRACE_MARKERS.search(r.body):
                findings.append(Finding(check="dast", rule_id="DAST-ERR-VERBOSE", title=f"Stack trace / internals in error response ({label})",
                                        severity="medium", description="The response exposes a stack trace or database error.",
                                        recommendation="Return a generic error body with a correlation id; log details server-side only.",
                                        location=loc, evidence=_evidence(ep["method"], path, r, r.body[:300]),
                                        category="A05:2021 Security Misconfiguration", cwe="CWE-209", tool="dast"))
            if r.status >= 500 and label in ("malformed JSON", "wrong type"):
                findings.append(Finding(check="dast", rule_id="DAST-ERR-5XX", title=f"Unhandled server error on {label}", severity="medium",
                                        description=f"Invalid input caused {r.status} instead of a 4xx validation error.",
                                        recommendation="Validate request bodies with a schema and return 400/422 for invalid input.",
                                        location=loc, evidence=_evidence(ep["method"], path, r, r.body[:200]),
                                        category="A04:2021 Insecure Design", cwe="CWE-20", tool="dast"))
            if label == "markup marker" and "html" in r.headers.get("content-type", "") and f"<i>{marker}</i>" in r.body:
                findings.append(Finding(check="dast", rule_id="DAST-XSS-REFLECT", title="Input reflected unescaped in HTML", severity="high",
                                        description="Markup sent in the request came back unescaped in an HTML response.",
                                        recommendation="HTML-escape all output by default (template autoescaping); add a CSP.",
                                        location=loc, evidence=_evidence(ep["method"], path, r), category="A03:2021 Injection", cwe="CWE-79", tool="dast"))
            if label == "template marker" and "1337" in r.body:
                findings.append(Finding(check="dast", rule_id="DAST-SSTI", title="Template expression evaluated", severity="high",
                                        description="An arithmetic template expression in the input was evaluated (1337 in the response).",
                                        recommendation="Never render user input as a template; pass it as data to a fixed template.",
                                        location=loc, evidence=_evidence(ep["method"], path, r, r.body[:200]), category="A03:2021 Injection",
                                        cwe="CWE-1336", tool="dast", confidence="medium"))
            if label == "oversized input" and 200 <= r.status < 300:
                findings.append(Finding(check="dast", rule_id="DAST-INPUT-SIZE", title=f"No input size limit on `{field}`", severity="medium",
                                        description=f"A {ep.get('max_input_chars', 4000) + 1000}-character `{field}` was accepted.",
                                        recommendation="Enforce a maximum length/body size (schema max_length, server body limit).",
                                        location=loc, evidence=_evidence(ep["method"], path, r), category=f"{API} API4: Unrestricted Resource Consumption",
                                        cwe="CWE-770", tool="dast"))

    # 8. Rate limiting: a short burst on one endpoint should eventually be throttled.
    probe = dast.get("rate_limit_probe")
    if probe:
        ep = next((e for e in endpoints if e["name"] == probe["endpoint"]), None)
        if ep:
            # A dedicated test user (probe.auth_headers) keeps the earlier probes' requests out of its budget.
            probe_auth = probe.get("auth_headers")
            statuses = [call(ep, auth=not probe_auth, headers=probe_auth)[1].status for _ in range(int(probe.get("requests", 30)))]
            if 429 not in statuses:
                findings.append(Finding(
                    check="dast", rule_id="DAST-NO-RATE-LIMIT", title=f"No rate limiting on {ep.get('method', 'GET')} {ep['path']}",
                    severity=probe.get("severity", "medium"),
                    description=f"{len(statuses)} back-to-back requests were all served (statuses: {sorted(set(statuses))}); none was throttled."
                                + (f" {probe['impact']}" if probe.get("impact") else ""),
                    recommendation="Add per-client rate limits (API gateway, reverse proxy, or middleware such as slowapi) returning 429, "
                                   "plus quotas on expensive operations.",
                    location=f"{ep.get('method', 'GET')} {ep['path']}", evidence=f"statuses: {statuses}",
                    category=f"{API} API4: Unrestricted Resource Consumption", cwe="CWE-770", tool="dast"))
    return findings, notes, log


def _server_issued_session_checks(client: Client, ep: dict, dast: dict) -> list[Finding]:
    """For endpoints that issue a session id in the response: a second test
    user (dast.alt_auth_headers) must not be able to continue the first
    user's session, and a caller-chosen id must not be accepted."""
    out: list[Finding] = []
    method, loc = ep.get("method", "POST"), f"{ep.get('method', 'POST')} {ep['path']}"
    field, resp_field = ep.get("session_field", "session_id"), ep["session_response_field"]
    base = _render(json.loads(json.dumps(ep.get("body") or {})), {"input": ep.get("sample_input", "hello")})
    r1 = client.request(method, ep["path"], base)
    try:
        sid = json.loads(r1.body).get(resp_field)
    except (ValueError, AttributeError):
        sid = None
    if not (200 <= r1.status < 300 and sid):
        return out
    alt = dast.get("alt_auth_headers")
    if alt:
        r2 = client.request(method, ep["path"], {**base, field: sid}, headers=alt, auth=False)
        if 200 <= r2.status < 300:
            out.append(Finding(
                check="dast", rule_id="DAST-SESSION-CROSS-USER", title=f"Another user can continue a session on {ep['path']}", severity="high",
                description="A session created by test user A was accepted when test user B sent its id, so B can read or act within A's conversation.",
                recommendation="Store the owner with each session and return 404 unless the authenticated caller is that owner.",
                location=loc, evidence=_evidence(method, ep["path"], r2), category=f"{API} API1: Broken Object Level Authorization",
                cwe="CWE-639", tool="dast"))
    else:
        out.append(Finding(check="dast", rule_id="DAST-SESSION-NO-ALT-USER", title="Cross-user session check not run", severity="info",
                           description="No dast.alt_auth_headers (a second non-prod test user) is configured, so session ownership "
                                       "between users couldn't be tested.",
                           recommendation="Add a second test user's credentials as dast.alt_auth_headers.", location=loc, tool="dast"))
    chosen = f"sec-chosen-{uuid4().hex[:8]}"
    r3 = client.request(method, ep["path"], {**base, field: chosen})
    if 200 <= r3.status < 300:
        out.append(Finding(
            check="dast", rule_id="DAST-SESSION-FIXATION", title=f"Caller-chosen session ids accepted on {ep['path']}", severity="medium",
            description=f"A made-up session id ({chosen}) was accepted, so ids aren't exclusively server-issued (session fixation risk).",
            recommendation="Reject unknown session ids (404) and only use ids the server generated.",
            location=loc, evidence=_evidence(method, ep["path"], r3), category=f"{API} API2: Broken Authentication", cwe="CWE-384", tool="dast"))
    return out


def _with_field(ep: dict, field: str, value: str) -> dict:
    body = json.loads(json.dumps(ep.get("body") or {}))
    body[field] = value
    return _render(body, {"session": f"sec-{uuid4().hex[:8]}", "input": value})


def _wrong_types(body):
    if not isinstance(body, dict):
        return {"unexpected": ["value"]}
    return {k: {"nested": [1, 2]} for k in body}
