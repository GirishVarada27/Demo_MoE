"""Wrappers for the external scanners: gitleaks (secrets), bandit and
semgrep (static analysis), pip-audit / npm audit (dependencies). Each
returns (findings, notes); a missing or failing tool becomes a note in the
report, never a silent pass."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from findings import Finding

TOOLS_VENV = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "e2e-test-suite-security" / "venv"
_BIN = TOOLS_VENV / ("Scripts" if os.name == "nt" else "bin")


def tool(name: str) -> str | None:
    for candidate in (_BIN / f"{name}.exe", _BIN / name):
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def _run(cmd: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", SEMGREP_SEND_METRICS="off")
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env)


def _rel(path: str, repo: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(repo.resolve())).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


# --- secrets ----------------------------------------------------------------------


def run_gitleaks(repo: Path, cfg: dict, out_dir: Path, history: bool = True) -> tuple[list[Finding], list[str]]:
    """history=True also walks every commit on every branch (slower; catches
    secrets that were committed and later deleted)."""
    exe = tool("gitleaks")
    if not exe:
        return [], ["gitleaks not installed; secret scanning skipped (winget install Gitleaks.Gitleaks / brew install gitleaks)."]
    findings, notes = [], []
    scans = []
    if history and (repo / ".git").exists():
        scans.append(("history", ["detect", "--source", str(repo), "--log-opts=--all"]))
    for path in cfg.get("source_paths", ["."]):
        scans.append((f"tree:{path}", ["detect", "--no-git", "--source", str(repo / path)]))
    seen = set()
    for label, args in scans:
        report = out_dir / f"gitleaks-{label.replace(':', '_').replace('/', '_')}.json"
        proc = _run([exe, *args, "--report-format", "json", "--report-path", str(report), "--redact", "--no-banner", "--exit-code", "0"], repo)
        if proc.returncode != 0 or not report.exists():
            notes.append(f"gitleaks {label} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
            continue
        for leak in json.loads(report.read_text(encoding="utf-8") or "[]"):
            file = _rel(leak.get("File", ""), repo if label == "history" else repo)
            key = (leak.get("RuleID"), file, leak.get("StartLine"), leak.get("Commit", ""))
            if key in seen:
                continue
            seen.add(key)
            ignored = _git_ignored(repo, file)
            in_history = bool(leak.get("Commit"))
            if ignored and not in_history:
                severity, title = "info", f"Secret in local, git-ignored file: {leak.get('Description')}"
                rec = ("This file isn't committed, which is right. Keep it out of git, out of images and builds, and "
                       "out of logs; prefer a secret manager for shared environments.")
            else:
                severity = "critical" if in_history else "high"
                title = f"Committed secret: {leak.get('Description')}" if in_history else f"Hardcoded secret: {leak.get('Description')}"
                rec = ("Treat the secret as compromised: revoke and rotate it now. Then move it to an environment "
                       "variable or secret manager, and remove it from git history (git filter-repo / BFG) if it was committed.")
            findings.append(Finding(
                check="secrets", rule_id=f"gitleaks:{leak.get('RuleID')}", title=title, severity=severity,
                description=f"gitleaks matched rule '{leak.get('RuleID')}'" + (f" in commit {leak.get('Commit', '')[:10]}." if in_history else "."),
                recommendation=rec, location=f"{file}:{leak.get('StartLine')}", evidence=leak.get("Match", "")[:200],
                category="A02:2021 Cryptographic Failures / A07:2021", cwe="CWE-798", tool="gitleaks",
            ))
    return findings, notes


def _git_ignored(repo: Path, file: str) -> bool:
    if not (repo / ".git").exists() or not shutil.which("git"):
        return False
    return subprocess.run(["git", "check-ignore", "-q", file], cwd=repo, capture_output=True).returncode == 0


# --- SAST -------------------------------------------------------------------------

# Concrete fixes for the bandit tests most often seen in web apps.
BANDIT_FIXES = {
    "B101": "Don't use assert for security or input checks (asserts are stripped with -O); raise an explicit exception.",
    "B104": "Bind to 127.0.0.1 (or a specific interface) by default; expose publicly only behind a reverse proxy, via config.",
    "B105": "Move the credential to an environment variable / secret manager; never hardcode it.",
    "B106": "Move the credential to an environment variable / secret manager; never hardcode it.",
    "B107": "Don't default a password argument to a literal; require it from config.",
    "B108": "Use tempfile.mkstemp()/TemporaryDirectory() instead of a fixed path under /tmp.",
    "B110": "Don't swallow exceptions silently; log them (without secrets) or handle the specific exception.",
    "B113": "Pass an explicit timeout= to every outbound HTTP request.",
    "B201": "Never run Flask with debug=True outside local development; drive it from an env var that defaults to False.",
    "B301": "Never unpickle untrusted data; use JSON or another data-only format.",
    "B303": "Use a modern hash (SHA-256+) and, for passwords, a KDF such as argon2/bcrypt.",
    "B307": "Replace eval() with ast.literal_eval() or explicit parsing.",
    "B311": "Use the secrets module (not random) for tokens, ids and anything security-relevant.",
    "B324": "Use hashlib.sha256 (or usedforsecurity=False for non-security checksums).",
    "B501": "Keep TLS verification on (verify=True); fix the CA bundle instead of disabling it.",
    "B506": "Use yaml.safe_load() instead of yaml.load().",
    "B602": "Pass the command as a list with shell=False; never interpolate user input into a shell string.",
    "B603": "Make sure the executable and arguments are not user-controlled; use absolute paths.",
    "B608": "Use parameterized queries / the ORM's bound parameters instead of string-built SQL.",
    "B701": "Enable Jinja2 autoescape (autoescape=select_autoescape()).",
}
_SEV = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low", "ERROR": "high", "WARNING": "medium", "INFO": "low"}


def run_bandit(repo: Path, cfg: dict, out_dir: Path) -> tuple[list[Finding], list[str]]:
    if "python" not in cfg.get("languages", []):
        return [], []
    exe = tool("bandit")
    if not exe:
        return [], ["bandit not installed; Python static analysis skipped (run scripts/setup_tools.py)."]
    targets = [str(repo / p) for p in cfg.get("source_paths", ["."]) if (repo / p).exists()]
    excludes = ",".join(str(repo / p) for p in cfg.get("exclude_paths", []))
    report = out_dir / "bandit.json"
    _run([exe, "-r", *targets, "-f", "json", "-o", str(report), "-q", *(["-x", excludes] if excludes else [])], repo)
    if not report.exists():
        return [], ["bandit produced no report."]
    data = json.loads(report.read_text(encoding="utf-8"))
    findings = []
    for r in data.get("results", []):
        test = r.get("test_id", "")
        findings.append(Finding(
            check="sast", rule_id=f"bandit:{test}", title=f"{r.get('test_name', test)}: {r.get('issue_text', '')}"[:160],
            severity=_SEV.get(r.get("issue_severity", "LOW"), "low"), confidence=r.get("issue_confidence", "MEDIUM").lower(),
            description=r.get("issue_text", ""), recommendation=BANDIT_FIXES.get(test, "Review and fix per the bandit documentation linked below."),
            location=f"{_rel(r.get('filename', ''), repo)}:{r.get('line_number')}", evidence=(r.get("code") or "").strip()[:300],
            cwe=f"CWE-{r['issue_cwe']['id']}" if r.get("issue_cwe") else "", references=[r.get("more_info", "")], tool="bandit",
        ))
    return findings, []


def run_semgrep(repo: Path, cfg: dict, out_dir: Path) -> tuple[list[Finding], list[str]]:
    exe = tool("semgrep") or tool("pysemgrep")
    if not exe:
        return [], ["semgrep not installed; multi-language static analysis skipped."]
    rulesets = cfg.get("semgrep_configs") or ["p/owasp-top-ten", "p/secrets"] + (["p/python"] if "python" in cfg.get("languages", []) else []) \
        + (["p/javascript", "p/typescript"] if {"javascript", "typescript"} & set(cfg.get("languages", [])) else [])
    report = out_dir / "semgrep.json"
    cmd = [exe, "scan", "--json", "--output", str(report), "--metrics", "off", "--quiet", "--disable-version-check"]
    for r in rulesets:
        cmd += ["--config", r]
    for p in cfg.get("exclude_paths", []):
        cmd += ["--exclude", p]
    cmd += [p for p in cfg.get("source_paths", ["."]) if (repo / p).exists()]
    try:
        proc = _run(cmd, repo, timeout=1200)
    except subprocess.TimeoutExpired:
        return [], ["semgrep timed out after 20 minutes."]
    if not report.exists():
        return [], [f"semgrep failed (rules are fetched from the semgrep registry; needs internet): {(proc.stderr or '').strip()[:300]}"]
    data = json.loads(report.read_text(encoding="utf-8"))
    notes = [f"semgrep: {e.get('message', '')[:200]}" for e in data.get("errors", [])[:3]]
    findings = []
    for r in data.get("results", []):
        extra = r.get("extra", {})
        meta = extra.get("metadata", {})
        cwe = meta.get("cwe")
        owasp = meta.get("owasp")
        fix = extra.get("fix")
        findings.append(Finding(
            check="sast", rule_id=f"semgrep:{r.get('check_id', '').split('.')[-1]}", title=(extra.get("message") or r.get("check_id", ""))[:160],
            severity=_SEV.get(extra.get("severity", "INFO"), "low"), confidence=str(meta.get("confidence", "medium")).lower(),
            description=extra.get("message", ""),
            recommendation=(f"Suggested change: {fix}. " if fix else "") + "Apply the fix described in the rule reference below.",
            location=f"{r.get('path', '').replace(chr(92), '/')}:{r.get('start', {}).get('line')}",
            evidence=(extra.get("lines") or "").strip()[:300],
            category=", ".join(owasp) if isinstance(owasp, list) else (owasp or ""),
            cwe=(cwe[0] if isinstance(cwe, list) else (cwe or "")).split(":")[0], references=list(meta.get("references", []))[:3], tool="semgrep",
        ))
    return findings, notes


# --- dependencies -----------------------------------------------------------------


def run_pip_audit(repo: Path, cfg: dict, out_dir: Path) -> tuple[list[Finding], list[str]]:
    if "python" not in cfg.get("languages", []):
        return [], []
    exe = tool("pip-audit")
    if not exe:
        return [], ["pip-audit not installed; Python dependency audit skipped."]
    py = cfg.get("python", {})
    venv_python = repo / py["venv_python"] if py.get("venv_python") else None
    with tempfile.TemporaryDirectory() as tmp:
        if venv_python and venv_python.exists():
            frozen = _run([str(venv_python), "-m", "pip", "freeze", "--exclude-editable"], repo).stdout
            req = Path(tmp) / "frozen.txt"
            req.write_text("\n".join(l for l in frozen.splitlines() if "==" in l), encoding="utf-8")
            args, source = ["-r", str(req), "--no-deps", "--disable-pip"], f"installed packages in {py['venv_python']}"
        else:
            reqs = [r for r in py.get("requirements", ["requirements.txt"]) if (repo / r).exists()]
            if not reqs:
                return [], ["No Python requirements or venv found; dependency audit skipped."]
            args, source = sum((["-r", str(repo / r)] for r in reqs), []), ", ".join(reqs)
        report = out_dir / "pip-audit.json"
        proc = _run([exe, *args, "-f", "json", "-o", str(report), "--progress-spinner", "off"], repo)
    if not report.exists():
        return [], [f"pip-audit failed (needs internet for the vulnerability database): {(proc.stderr or '').strip()[:300]}"]
    data = json.loads(report.read_text(encoding="utf-8"))
    findings = []
    for dep in data.get("dependencies", []):
        for v in dep.get("vulns", []):
            fixes = v.get("fix_versions") or []
            findings.append(Finding(
                check="dependencies", rule_id=v.get("id", "vuln"), title=f"{dep['name']} {dep['version']}: {v.get('id')}",
                severity=_dep_severity(v), description=(v.get("description") or "Known vulnerability.")[:600],
                recommendation=(f"Upgrade {dep['name']} to {fixes[0]} or later (fixed in: {', '.join(fixes)}), then re-run the tests."
                                if fixes else f"No fixed release yet: check whether the vulnerable code path is used, pin, or replace {dep['name']}."),
                location=f"{dep['name']}=={dep['version']}", evidence=f"source: {source}; aliases: {', '.join(v.get('aliases', []))}",
                category="A06:2021 Vulnerable and Outdated Components", cwe="CWE-1395",
                references=[f"https://osv.dev/vulnerability/{v.get('id')}"], tool="pip-audit",
            ))
    return findings, []


def _dep_severity(v: dict) -> str:
    """pip-audit doesn't report CVSS; treat known exploitable classes as high,
    everything else as medium, so the reviewer looks at each one."""
    text = (v.get("description") or "").lower()
    if any(k in text for k in ("remote code execution", "arbitrary code", "rce", "authentication bypass", "sql injection")):
        return "high"
    return "medium"


def run_npm_audit(repo: Path, cfg: dict, out_dir: Path) -> tuple[list[Finding], list[str]]:
    if not {"javascript", "typescript"} & set(cfg.get("languages", [])):
        return [], []
    npm = shutil.which("npm")
    findings, notes = [], []
    for pkg_dir in cfg.get("node_paths", ["."]):
        d = repo / pkg_dir
        if not (d / "package-lock.json").exists():
            notes.append(f"No package-lock.json in {pkg_dir}; npm audit skipped there.")
            continue
        if not npm:
            return [], ["npm not installed; Node dependency audit skipped."]
        proc = _run([npm, "audit", "--json", "--omit=dev"], d)
        (out_dir / f"npm-audit-{pkg_dir.replace('/', '_')}.json").write_text(proc.stdout, encoding="utf-8")
        try:
            vulns = json.loads(proc.stdout or "{}").get("vulnerabilities", {})
        except json.JSONDecodeError:
            notes.append(f"npm audit in {pkg_dir} returned no JSON.")
            continue
        for name, v in vulns.items():
            fix = v.get("fixAvailable")
            via = [x for x in v.get("via", []) if isinstance(x, dict)]
            findings.append(Finding(
                check="dependencies", rule_id=(via[0].get("url", "").rsplit("/", 1)[-1] if via else f"npm:{name}"),
                title=f"{name} {v.get('range', '')}: {via[0].get('title', 'vulnerable') if via else 'vulnerable dependency'}",
                severity={"critical": "critical", "high": "high", "moderate": "medium", "low": "low"}.get(v.get("severity"), "info"),
                description=via[0].get("title", "") if via else "",
                recommendation=(f"Upgrade to {fix['name']}@{fix['version']}" if isinstance(fix, dict) else
                                "Run `npm audit fix`" if fix else "No fix available: assess reachability, or replace the package."),
                location=f"{pkg_dir}/package-lock.json:{name}", evidence=", ".join(x.get("url", "") for x in via)[:300],
                category="A06:2021 Vulnerable and Outdated Components", cwe="CWE-1395", tool="npm audit",
            ))
    return findings, notes
