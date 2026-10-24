"""Render a security run: findings.json, report.md, report.html, results.sarif."""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

from findings import SEVERITIES, Finding, to_dict

_SARIF_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}


def write_all(run_dir: Path, findings: list[Finding], summary: dict) -> None:
    (run_dir / "findings.json").write_text(json.dumps({**summary, "findings": [to_dict(f) for f in findings]}, indent=2), encoding="utf-8")
    md = render_md(findings, summary)
    (run_dir / "report.md").write_text(md, encoding="utf-8")
    (run_dir / "report.html").write_text(render_html(findings, summary), encoding="utf-8")
    (run_dir / "results.sarif").write_text(json.dumps(sarif(findings), indent=2), encoding="utf-8")


def _counts(findings: list[Finding]) -> Counter:
    return Counter(f.severity for f in findings if f.status != "accepted")


def render_md(findings: list[Finding], s: dict) -> str:
    c = _counts(findings)
    L = [f"# Security report: {s['app_name']}", "",
         f"**Verdict: {s['verdict']}** (fails on unaccepted findings of severity `{s['fail_on']}` or higher) | profile `{s['profile']}` | "
         f"commit `{(s.get('git') or {}).get('commit', '')[:10]}`{' (dirty)' if (s.get('git') or {}).get('dirty') else ''} | {s['started']}", "",
         "| " + " | ".join(x.capitalize() for x in SEVERITIES) + " | Accepted |", "|" + "---|" * (len(SEVERITIES) + 1),
         "| " + " | ".join(str(c[x]) for x in SEVERITIES) + f" | {sum(1 for f in findings if f.status == 'accepted')} |", ""]
    L += ["## Scope", "", f"- Repository: `{s['repo']}`", f"- Live target: `{s.get('target') or 'not tested'}`",
          f"- Checks run: {', '.join(s['checks_run'])}"]
    if s.get("db_url"):
        L.append(f"- Test data store: `{s['db_url']}`")
    for n in s.get("notes", []):
        L.append(f"- Note: {n}")
    if s.get("previous"):
        new = sum(1 for f in findings if f.new)
        L += ["", f"## Change since previous run ({s['previous']})", "", f"- New findings: **{new}**",
              f"- Fixed (no longer found): **{len(s.get('fixed', []))}**"]
        L += [f"  - [{x['severity']}] {x['title']} ({x['location']})" for x in s.get("fixed", [])]
    if s.get("blocking"):
        L += ["", "## Blocking findings", ""] + [f"- **{f['severity'].upper()}** {f['title']} ({f['location']})" for f in s["blocking"]]
    L += ["", "## Findings", ""]
    if not findings:
        L.append("No findings.")
    for i, f in enumerate(findings, 1):
        tags = [f.severity.upper(), f.check, f.tool] + (["NEW"] if f.new else []) + ([f.status.upper()] if f.status != "open" else [])
        L += [f"### {i}. {f.title}", "", f"`{'` `'.join(t for t in tags if t)}`" + (f" | {f.category}" if f.category else "")
              + (f" | {f.cwe}" if f.cwe else "") + (f" | confidence {f.confidence}" if f.confidence != "high" else ""), "",
              f"- **Where:** `{f.location}`" if f.location else "", f"- **Issue:** {f.description}",
              f"- **Recommended fix:** {f.recommendation}"]
        if f.evidence:
            L += ["- **Evidence:**", "", "  ```", *("  " + line for line in f.evidence.splitlines()[:12]), "  ```"]
        if f.references:
            L.append("- **References:** " + ", ".join(r for r in f.references if r))
        if f.acceptance:
            L.append(f"- **Accepted by** {f.acceptance.get('owner')} until {f.acceptance.get('expires')}: {f.acceptance.get('reason')}")
        L.append(f"- Fingerprint: `{f.fingerprint}`")
        L.append("")
    L += ["## Artifacts", "", "Raw tool output in this folder: `gitleaks-*.json`, `bandit.json`, `semgrep.json`, `pip-audit.json`, "
          "`dast-requests.json`, `app.log`. `results.sarif` can be uploaded to GitHub code scanning."]
    return "\n".join(x for x in L if x is not None) + "\n"


def render_html(findings: list[Finding], s: dict) -> str:
    c = _counts(findings)
    sev_colors = {"critical": "#8e0000", "high": "#c62828", "medium": "#d9822b", "low": "#2f6fd6", "info": "#6b6b70"}
    tiles = "".join(f"<div class='tile'><span class='n' style='color:{sev_colors[x]}'>{c[x]}</span>{x}</div>" for x in SEVERITIES)
    rows = []
    for i, f in enumerate(findings, 1):
        badge = f"<span class='sev' style='background:{sev_colors[f.severity]}'>{f.severity}</span>"
        flags = (" <span class='flag'>new</span>" if f.new else "") + (f" <span class='flag'>{f.status}</span>" if f.status != "open" else "")
        refs = " ".join(f"<a href='{html.escape(r)}'>{html.escape(r[:60])}</a>" for r in f.references if r and r.startswith("http"))
        rows.append(f"""<details class='finding' {'open' if f.severity in ('critical', 'high') and f.status != 'accepted' else ''}>
<summary>{badge} <strong>{i}. {html.escape(f.title)}</strong>{flags}<span class='where'>{html.escape(f.location)}</span></summary>
<dl><dt>Issue</dt><dd>{html.escape(f.description)}</dd>
<dt>Recommended fix</dt><dd class='fix'>{html.escape(f.recommendation)}</dd>
{f"<dt>Evidence</dt><dd><pre>{html.escape(f.evidence)}</pre></dd>" if f.evidence else ''}
<dt>Classification</dt><dd>{html.escape(' | '.join(x for x in (f.check, f.tool, f.category, f.cwe, 'confidence ' + f.confidence) if x))}</dd>
{f"<dt>References</dt><dd>{refs}</dd>" if refs else ''}
{f"<dt>Accepted</dt><dd>{html.escape(str(f.acceptance))}</dd>" if f.acceptance else ''}
<dt>Fingerprint</dt><dd><code>{f.fingerprint}</code></dd></dl></details>""")
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in s.get("notes", []))
    verdict_bg = "#1a7f37" if s["verdict"] == "PASS" else "#c62828"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Security report: {html.escape(s['app_name'])}</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e8;--card:#f6f7f9;color-scheme:light}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16171a;--fg:#ececf0;--muted:#9a9aa3;--line:#2c2d33;--card:#1e1f23;color-scheme:dark}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.55 system-ui,sans-serif;margin:0;padding:24px 16px}} main{{max-width:1000px;margin:auto}}
h1{{font-size:22px;margin:0 0 6px}} h2{{font-size:17px;margin:26px 0 8px}} .muted{{color:var(--muted)}}
.verdict{{color:#fff;background:{verdict_bg};padding:2px 10px;border-radius:4px}}
.tiles{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}} .tile{{background:var(--card);border-radius:6px;padding:8px 14px;min-width:80px;text-transform:capitalize}}
.tile .n{{display:block;font-size:22px;font-weight:700}}
.finding{{border:1px solid var(--line);border-radius:6px;margin:8px 0;padding:8px 12px}} summary{{cursor:pointer}}
.sev{{color:#fff;border-radius:3px;padding:0 6px;font-size:12px;text-transform:uppercase;margin-right:4px}}
.flag{{border:1px solid var(--line);border-radius:3px;padding:0 5px;font-size:11px;margin-left:6px}}
.where{{display:block;color:var(--muted);font-size:12px;margin-left:2px;overflow-wrap:anywhere}}
dt{{font-weight:600;margin-top:8px}} dd{{margin:2px 0 0 0;overflow-wrap:anywhere}} .fix{{border-left:3px solid #1a7f37;padding-left:8px}}
pre{{white-space:pre-wrap;background:var(--card);padding:8px;border-radius:4px;font-size:12px;overflow-x:auto}}
</style></head><body><main>
<h1>Security report: {html.escape(s['app_name'])}</h1>
<p><span class="verdict">{s['verdict']}</span> <span class="muted">fails on {html.escape(s['fail_on'])}+ unaccepted findings | profile {html.escape(s['profile'])} |
commit {html.escape(((s.get('git') or {}).get('commit') or '')[:10])} | {html.escape(s['started'])}</span></p>
<div class="tiles">{tiles}</div>
<p class="muted">Repo {html.escape(s['repo'])}; live target {html.escape(s.get('target') or 'not tested')}; checks: {html.escape(', '.join(s['checks_run']))}.</p>
{f"<ul class='muted'>{notes}</ul>" if notes else ''}
<h2>Findings ({len(findings)})</h2>{''.join(rows) or '<p>No findings.</p>'}
</main></body></html>"""


def sarif(findings: list[Finding]) -> dict:
    rules, results = {}, []
    for f in findings:
        rules.setdefault(f.rule_id, {"id": f.rule_id, "name": f.rule_id, "shortDescription": {"text": f.title[:120]},
                                     "help": {"text": f.recommendation}, "properties": {"tags": [t for t in (f.check, f.cwe) if t]}})
        loc = {}
        file, _, line = f.location.rpartition(":")
        if file and line.isdigit():  # only file:line findings get a code location
            loc = {"physicalLocation": {"artifactLocation": {"uri": file}, "region": {"startLine": int(line)}}}
        results.append({"ruleId": f.rule_id, "level": _SARIF_LEVEL[f.severity], "message": {"text": f"{f.description} Fix: {f.recommendation}"},
                        "partialFingerprints": {"primary": f.fingerprint}, **({"locations": [loc]} if loc else {}),
                        **({"suppressions": [{"kind": "external", "justification": (f.acceptance or {}).get("reason", "")}]} if f.status == "accepted" else {})})
    return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": "e2e-test-suite-security", "rules": list(rules.values())}}, "results": results}]}
