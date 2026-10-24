"""Finding model shared by every check, plus fingerprinting, accepted-risk
suppression, comparison with the previous run, and the pass/fail gate."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

SEVERITIES = ["critical", "high", "medium", "low", "info"]
_RANK = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass
class Finding:
    check: str                 # secrets | sast | dependencies | config | dast | manual
    rule_id: str               # e.g. bandit B105, DAST-CORS-001, a CVE/GHSA id
    title: str
    severity: str              # one of SEVERITIES
    description: str           # what is wrong and why it matters
    recommendation: str        # the specific fix
    location: str = ""         # file:line, endpoint, or package==version
    evidence: str = ""         # the request/response, code snippet or tool output that proves it
    category: str = ""         # OWASP Top 10 / API Top 10 / LLM Top 10 entry
    cwe: str = ""              # e.g. CWE-798
    references: list[str] = field(default_factory=list)
    tool: str = ""
    confidence: str = "high"   # high | medium | low
    fingerprint: str = ""
    status: str = "open"       # open | accepted | expired-acceptance
    acceptance: dict | None = None
    new: bool | None = None    # vs the previous run; None when there is no previous run

    def __post_init__(self) -> None:
        if self.severity not in _RANK:
            self.severity = "info"
        if not self.fingerprint:
            self.fingerprint = fingerprint(self.rule_id, self.location, self.evidence)


def fingerprint(rule_id: str, location: str, evidence: str) -> str:
    """Stable across line shifts: the file path without its line number, plus
    the evidence with whitespace and digits collapsed."""
    loc = re.sub(r":\d+(?::\d+)?$", "", location or "")
    ev = re.sub(r"\s+", " ", re.sub(r"\d+", "#", evidence or ""))[:200]
    return hashlib.sha1(f"{rule_id}|{loc}|{ev}".encode()).hexdigest()[:16]


def at_least(severity: str, threshold: str) -> bool:
    return _RANK.get(severity, 99) <= _RANK.get(threshold, 1)


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen: dict[str, Finding] = {}
    for f in findings:
        if f.fingerprint not in seen or at_least(f.severity, seen[f.fingerprint].severity):
            seen[f.fingerprint] = f
    return sorted(seen.values(), key=lambda f: (_RANK[f.severity], f.check, f.location))


def apply_acceptances(findings: list[Finding], accepted_path: Path, today: date | None = None) -> list[str]:
    """Mark findings listed in accepted-risks.json. Returns warnings (e.g.
    expired or stale entries). An acceptance needs a reason, an owner and
    an expiry date; expired ones stop suppressing."""
    warnings: list[str] = []
    if not accepted_path.exists():
        return warnings
    today = today or date.today()
    entries = json.loads(accepted_path.read_text(encoding="utf-8-sig")).get("accepted", [])
    by_fp = {e.get("fingerprint"): e for e in entries}
    for f in findings:
        entry = by_fp.get(f.fingerprint)
        if not entry:
            continue
        missing = [k for k in ("reason", "owner", "expires") if not entry.get(k)]
        if missing:
            warnings.append(f"Acceptance for {f.fingerprint} ignored: missing {', '.join(missing)}.")
            continue
        f.acceptance = entry
        f.status = "expired-acceptance" if date.fromisoformat(entry["expires"]) < today else "accepted"
        if f.status == "expired-acceptance":
            warnings.append(f"Acceptance for '{f.title}' ({f.fingerprint}) expired on {entry['expires']}.")
    current = {f.fingerprint for f in findings}
    for fp, entry in by_fp.items():
        if fp not in current:
            warnings.append(f"Accepted risk {fp} ('{entry.get('title', '')}') no longer occurs; remove it from accepted-risks.json.")
    return warnings


def compare_with_previous(findings: list[Finding], previous_path: Path | None, checks_present: set[str] | None = None) -> list[dict]:
    """Flag each finding as new or not, and return the previous run's findings
    that are gone (fixed). Only findings from checks that ran this time count:
    a check that was skipped (e.g. --static-only, no manual review yet) didn't
    fix anything."""
    if not previous_path or not previous_path.exists():
        return []
    previous = json.loads(previous_path.read_text(encoding="utf-8")).get("findings", [])
    if checks_present is not None:
        previous = [p for p in previous if p.get("check") in checks_present]
    old = {p["fingerprint"] for p in previous}
    for f in findings:
        f.new = f.fingerprint not in old
    current = {f.fingerprint for f in findings}
    return [{"title": p["title"], "severity": p["severity"], "location": p["location"], "fingerprint": p["fingerprint"]}
            for p in previous if p["fingerprint"] not in current]


def gate(findings: list[Finding], fail_on: str) -> tuple[bool, list[Finding]]:
    blocking = [f for f in findings if f.status != "accepted" and at_least(f.severity, fail_on)]
    return not blocking, blocking


def to_dict(f: Finding) -> dict:
    return asdict(f)


def from_dict(d: dict) -> Finding:
    known = {k: v for k, v in d.items() if k in Finding.__dataclass_fields__}
    return Finding(**known)
