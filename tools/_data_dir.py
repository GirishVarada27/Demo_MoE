"""Where the mock data stores live (applicants, escalation queue, drafts).

Defaults to the repo's data/ directory. Set MOE_DATA_URL to a file:// URL
(e.g. file:///D:/perf-data/moe) to point every store at another
directory instead -- load tests use this so they never touch data/.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def data_dir() -> Path:
    url = os.environ.get("MOE_DATA_URL", "").strip()
    if not url:
        return DEFAULT_DATA_DIR
    parsed = urlparse(url)
    if parsed.scheme != "file":
        raise RuntimeError(f"MOE_DATA_URL must be a file:// URL to a directory, got scheme '{parsed.scheme}'.")
    path = unquote(parsed.path)
    # file:///D:/x parses to path '/D:/x' on Windows -- drop the leading slash before the drive.
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(parsed.netloc + path if parsed.netloc else path)
