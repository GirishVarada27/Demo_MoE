"""Safe read-modify-write for the JSON-file mock stores.

The API runs turns concurrently, so two escalations (or drafts) could each
read the same list and the later write would drop the other's entry -- or
a reader could catch a half-written file. update_json_list() serializes
writers with a per-file threading lock (this process) plus an OS lock on a
sidecar `<file>.lock` (other processes), and writes atomically: a temp file
in the same directory, then os.replace(), so readers see the old or the new
list, never a partial one.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


@contextmanager
def _os_lock(path: Path):
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as fh:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt

            # LK_LOCK retries for ~10s, then raises OSError.
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(20):  # Windows refuses to replace a file another process has open
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def update_json_list(path: Path, mutate: Callable[[list], T]) -> T:
    """Lock `path`, load its JSON list ([] if missing), call mutate(list) to
    change it in place, write it back atomically, and return mutate's result.
    Raises OSError / json.JSONDecodeError for the caller to turn into a typed
    tool error."""
    with _thread_lock(path), _os_lock(path):
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        result = mutate(items)
        _atomic_write(path, json.dumps(items, indent=2))
        return result
