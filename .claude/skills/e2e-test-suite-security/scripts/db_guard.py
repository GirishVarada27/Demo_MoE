"""Refuse to load-test against a production (or the app's real) data store.

check_db_url(url, app_config, repo) raises GuardError with the reason if
the URL is missing, unparseable, names a production-looking host/database,
equals a known production URL (listed in the app config, or held in the
app's prod env vars / .env), points inside a protected path (e.g. the
app's own data/ dir), or misses the app's allow-list pattern. A URL that
passes is safe to seed and reset.

check_target_url(url, app_config) applies the same production-marker and
known-URL checks to the HTTP target when testing an already-running app.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

PROD_MARKER = re.compile(r"(?:^|[^a-z0-9])(prod|production|prd|live)(?:[^a-z0-9]|$)", re.IGNORECASE)


class GuardError(Exception):
    pass


def redact(url: str) -> str:
    """Hide credentials in a URL before it's printed or written to a report."""
    parsed = urlparse(url)
    if parsed.password or parsed.username:
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        user = parsed.username or ""
        parsed = parsed._replace(netloc=f"{user}:***@{host}" if parsed.password else f"{user}@{host}")
    return urlunparse(parsed)


def file_url_path(url: str) -> Path:
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(parsed.netloc + path if parsed.netloc else path).resolve()


def _normalize(url: str) -> str:
    """Same database = same scheme, host, port and path; credentials and
    query options (sslmode etc.) don't make it a different database."""
    parsed = urlparse(url.strip())
    if parsed.scheme == "file":
        return "file:" + str(file_url_path(url)).lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{(parsed.hostname or '').lower()}{port}{parsed.path.rstrip('/')}".lower()


def _dotenv_values(repo: Path) -> dict[str, str]:
    values = {}
    env_file = repo / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _known_prod_urls(app_config: dict, repo: Path) -> list[str]:
    guard = app_config.get("db_guard", {})
    urls = list(guard.get("prod_db_urls", []))
    dotenv = _dotenv_values(repo)
    for var in guard.get("prod_env_vars", []):
        for source in (os.environ, dotenv):
            if source.get(var):
                urls.append(source[var])
    return urls


def check_db_url(url: str | None, app_config: dict, repo: Path) -> str:
    """Return the redacted URL if it's safe to load-test against, else raise."""
    if not url or not url.strip():
        raise GuardError("A non-production load-test database URL is required; none was given.")
    url = url.strip()
    parsed = urlparse(url)
    if not parsed.scheme:
        raise GuardError(f"'{redact(url)}' isn't a URL (expected e.g. postgres://host/db or file:///D:/perf-data/app).")

    guard = app_config.get("db_guard", {})
    allowed = guard.get("allowed_schemes")
    if allowed and parsed.scheme not in allowed:
        raise GuardError(f"Scheme '{parsed.scheme}' isn't one this app uses ({', '.join(allowed)}).")

    if parsed.scheme == "file":
        path = file_url_path(url)
        for protected in guard.get("protected_paths", []):
            protected_path = (repo / protected).resolve()
            if path == protected_path or protected_path in path.parents or path in protected_path.parents:
                raise GuardError(f"{path} overlaps the app's real data at {protected_path}; use a separate directory.")
        if path == repo.resolve():
            raise GuardError("The load-test data directory can't be the repository itself.")
        name_to_check = str(path)
    else:
        name_to_check = f"{parsed.hostname or ''} {parsed.path}"

    marker = PROD_MARKER.search(name_to_check)
    if marker:
        raise GuardError(f"'{redact(url)}' looks like production (contains '{marker.group(1)}'). Use a non-prod load-test database.")

    normalized = _normalize(url)
    for prod in _known_prod_urls(app_config, repo):
        if prod and _normalize(prod) == normalized:
            raise GuardError(f"'{redact(url)}' is the app's configured production/real database. Use a non-prod copy.")

    pattern = guard.get("allowed_url_pattern")
    if pattern and not re.search(pattern, url):
        raise GuardError(f"'{redact(url)}' doesn't match this app's load-test DB pattern ({pattern}).")
    return redact(url)


def check_target_url(url: str, app_config: dict) -> None:
    parsed = urlparse(url)
    if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
        return
    marker = PROD_MARKER.search(parsed.hostname or "")
    if marker:
        raise GuardError(f"Target {url} looks like production (contains '{marker.group(1)}'). Load tests must not run against prod.")
    for prod in app_config.get("db_guard", {}).get("prod_base_urls", []):
        if url.rstrip("/").lower() == prod.rstrip("/").lower():
            raise GuardError(f"Target {url} is listed as this app's production URL.")
