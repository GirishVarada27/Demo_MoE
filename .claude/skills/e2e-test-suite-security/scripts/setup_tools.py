"""Install or verify the scanners: bandit, pip-audit and semgrep into an
isolated venv (so they never conflict with the app's own dependencies),
and check that gitleaks is on PATH.

    python setup_tools.py [--check]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import venv

from scanners import TOOLS_VENV, tool

PACKAGES = ["bandit", "pip-audit", "semgrep"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only report what's installed")
    args = ap.parse_args()
    missing = [p for p in PACKAGES if not tool(p)]
    if missing and not args.check:
        if not TOOLS_VENV.exists():
            print(f"Creating tools venv at {TOOLS_VENV}")
            venv.create(TOOLS_VENV, with_pip=True)
        py = TOOLS_VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "--upgrade", *PACKAGES], check=True)
    ok = True
    for name in PACKAGES + ["gitleaks"]:
        path = tool(name)
        print(f"{name:10} {'OK  ' + path if path else 'MISSING'}")
        ok &= bool(path)
    if not tool("gitleaks"):
        print("Install gitleaks: winget install Gitleaks.Gitleaks | brew install gitleaks | https://github.com/gitleaks/gitleaks/releases")
    if not shutil.which("git"):
        print("git not on PATH: secret history scan and .env hygiene checks will be skipped.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
