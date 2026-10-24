"""Configuration and hygiene checks the scanners don't cover well, plus
git hygiene (is .env ignored / tracked). Language-agnostic regex rules over
the app's source and config files."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from findings import Finding

TEXT_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rb", ".php", ".cs", ".json", ".yaml",
                 ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".properties", ".xml"}
TEXT_NAMES = {"Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Procfile", "requirements.txt"}

# (rule id, regex, severity, title, recommendation, category, cwe)
RULES = [
    ("CFG-DEBUG", r"(?i)\b(debug\s*[:=]\s*(true|1)\b|app\.run\([^)]*debug\s*=\s*True|DEBUG\s*=\s*True)", "medium",
     "Debug mode enabled in code/config",
     "Default debug off and enable it only from an env var in local development; debug pages leak stack traces and can allow code execution.",
     "A05:2021 Security Misconfiguration", "CWE-489"),
    ("CFG-CORS-WILDCARD", r"""(?i)(allow_origins\s*=\s*\[\s*["']\*["']|Access-Control-Allow-Origin["']?\s*[:,]\s*["']\*|cors\(\s*\)|origin\s*:\s*["']\*["'])""", "medium",
     "CORS allows any origin",
     "List the exact front-end origins allowed; never combine a wildcard or reflected origin with credentials.",
     "A05:2021 Security Misconfiguration", "CWE-942"),
    ("CFG-TLS-VERIFY-OFF", r"(?i)(verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0)", "high",
     "TLS certificate verification disabled",
     "Keep certificate verification on; trust the internal CA via a CA bundle instead.", "A02:2021 Cryptographic Failures", "CWE-295"),
    ("CFG-BIND-ALL", r"""(?i)(--host["',\s]+0\.0\.0\.0|host\s*=\s*["']0\.0\.0\.0["'])""", "low",
     "Server binds to all interfaces",
     "Bind to 127.0.0.1 by default and expose the service through a reverse proxy/load balancer that enforces TLS and auth.",
     "A05:2021 Security Misconfiguration", "CWE-1327"),
    ("CFG-DOCKER-ROOT", r"(?im)^FROM\s+\S+", "low", "Container may run as root",
     "Add a non-root USER to the Dockerfile and drop capabilities.", "A05:2021 Security Misconfiguration", "CWE-250"),
    ("CFG-SECRET-DEFAULT", r"""(?i)(secret_key|jwt_secret|api_key|password)\s*[:=]\s*["'](changeme|secret|password|admin|default|test123|dev)["']""", "high",
     "Hardcoded default secret",
     "Remove the default; read the value from the environment and fail at startup if it's missing.",
     "A07:2021 Identification and Authentication Failures", "CWE-1188"),
]


def _files(repo: Path, cfg: dict) -> list[Path]:
    excludes = [repo / p for p in cfg.get("exclude_paths", [])]
    roots = [repo / p for p in cfg.get("source_paths", ["."])] + [repo / p for p in cfg.get("config_paths", [])]
    roots += [repo / n for n in TEXT_NAMES if (repo / n).exists()]
    out = []
    for root in roots:
        candidates = [root] if root.is_file() else (root.rglob("*") if root.exists() else [])
        for f in candidates:
            if not f.is_file() or any(e == f or e in f.parents for e in excludes) or ".git" in f.parts:
                continue
            if f.suffix in TEXT_SUFFIXES or f.name in TEXT_NAMES:
                out.append(f)
    return sorted(set(out))


def review(repo: Path, cfg: dict) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    for f in _files(repo, cfg):
        rel = str(f.relative_to(repo)).replace("\\", "/")
        if rel == ".env" or f.name.startswith(".env"):
            continue  # local secrets are handled by the gitleaks / git hygiene checks
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for rule_id, pattern, sev, title, rec, cat, cwe in RULES:
            if rule_id == "CFG-DOCKER-ROOT":
                if f.name == "Dockerfile" and re.search(pattern, text) and not re.search(r"(?im)^USER\s+(?!root\b)\S+", text):
                    findings.append(Finding(check="config", rule_id=rule_id, title=title, severity=sev, description="No non-root USER instruction.",
                                            recommendation=rec, location=rel, category=cat, cwe=cwe, tool="config-review"))
                continue
            for m in re.finditer(pattern, text):
                line = text.count("\n", 0, m.start()) + 1
                snippet = text.splitlines()[line - 1].strip()[:200]
                findings.append(Finding(check="config", rule_id=rule_id, title=title, severity=sev,
                                        description=f"Matched `{m.group(0)[:80]}`.", recommendation=rec, location=f"{rel}:{line}",
                                        evidence=snippet, category=cat, cwe=cwe, tool="config-review", confidence="medium"))
    findings += _dependency_pinning(repo, cfg)
    findings += _git_hygiene(repo)
    return findings, []


def _dependency_pinning(repo: Path, cfg: dict) -> list[Finding]:
    out = []
    for req in cfg.get("python", {}).get("requirements", []):
        path = repo / req
        if not path.exists():
            continue
        loose = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.startswith(("#", "-")) and "==" not in l and "@" not in l]
        if loose:
            out.append(Finding(
                check="config", rule_id="CFG-UNPINNED-DEPS", title=f"Unpinned dependencies in {req}", severity="low",
                description=f"{len(loose)} requirement(s) use ranges ({', '.join(loose[:5])}{'...' if len(loose) > 5 else ''}), so every "
                            "install can pull a different, unreviewed version, including a compromised release.",
                recommendation="Pin exact versions with hashes for deployable builds (pip-compile --generate-hashes / uv lock) and "
                               "update them deliberately, keeping ranges only in library metadata.",
                location=req, evidence=", ".join(loose), category="A08:2021 Software and Data Integrity Failures", cwe="CWE-1104",
                tool="config-review"))
    return out


def _git_hygiene(repo: Path) -> list[Finding]:
    if not (repo / ".git").exists() or not shutil.which("git"):
        return []
    out = []
    tracked = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout.splitlines()
    env_files = [t for t in tracked if re.search(r"(^|/)\.env(\.|$)", t) and not t.endswith((".example", ".sample", ".template"))]
    for t in env_files:
        out.append(Finding(check="config", rule_id="CFG-ENV-TRACKED", title=f"Environment file committed: {t}", severity="critical",
                           description="An env file with real values is tracked in git, so every clone has the secrets.",
                           recommendation="Rotate every secret in it, `git rm --cached` the file, add it to .gitignore, and purge it "
                                          "from history; commit a values-free .env.example instead.",
                           location=t, category="A02:2021 Cryptographic Failures", cwe="CWE-538", tool="config-review"))
    if (repo / ".env").exists() and subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=repo).returncode != 0:
        out.append(Finding(check="config", rule_id="CFG-ENV-NOT-IGNORED", title=".env is not git-ignored", severity="high",
                           description="A local .env exists and isn't covered by .gitignore; one `git add .` commits it.",
                           recommendation="Add `.env` (and `.env.*`, except examples) to .gitignore.", location=".gitignore",
                           category="A05:2021 Security Misconfiguration", cwe="CWE-538", tool="config-review"))
    return out
