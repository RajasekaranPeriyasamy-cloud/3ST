"""Atomic JSON writes for the flat-file stores under ``data/``.

Every store here is a module-level singleton guarded by a ``threading.Lock``,
which serialises writers *inside one process only*. That matches the documented
deployment — single process, single operator — but a second Python process
touching the same file is easy to create by accident: a script, a profiling
run, a REPL, a test that forgot to redirect ``data_dir``.

The stores each used a fixed ``<name>.tmp`` scratch path, so two writers shared
it. The failure that produces is not a lost update, it is a *corrupted file*:
writer B truncates the shared temp mid-write and replaces the store, while
writer A's remaining bytes land past the end of B's shorter payload. The result
is a valid JSON document followed by trailing garbage, which every subsequent
read rejects — and the loaders fall back to an empty store.

That is exactly what happened to ``data/gamma_density_history.json`` on
2026-08-27: 348 bytes of a longer payload left past the end of a shorter one,
after which the Gamma Density session chart showed "Waiting for in-session GEX
samples" for every underlying while the write path refused to persist anything
new. No data was lost, but the desk was blind for a session.

Giving each writer its own temp path makes that impossible. The worst case
degrades to a lost update, which the single-process design already assumes.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


def temp_path_for(path: Path) -> Path:
    """Writer-private scratch path beside ``path``.

    Keyed on pid *and* a random suffix. Pid alone is not enough: the OS reuses
    pids after a restart, and two threads that race the store lock inside one
    process would otherwise share the path.
    """
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")


#: Windows rename contention is short-lived (a reader holding the file for one
#: read_text), so a handful of quick retries covers it without masking a real
#: permission problem for long.
_REPLACE_ATTEMPTS = 8
_REPLACE_BACKOFF_SEC = 0.02


def _replace_with_retry(tmp: Path, path: Path) -> None:
    """``os.replace``, retried briefly on Windows rename contention.

    POSIX rename onto an open path succeeds. Windows fails it with
    ``PermissionError`` (WinError 5) while any other handle has the target
    open — including a reader midway through ``read_text``. The rename is
    still atomic when it succeeds; it just needs another go. Raises the last
    error if the contention does not clear, so a genuine ACL problem is not
    swallowed.
    """
    last: OSError | None = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(_REPLACE_BACKOFF_SEC * (attempt + 1))
    assert last is not None
    raise last


def atomic_write_json(path: Path, data: Any, **dumps_kwargs: Any) -> None:
    """Serialise ``data`` to JSON and replace ``path`` atomically.

    ``dumps_kwargs`` is forwarded to :func:`json.dumps`, so callers keep their
    own formatting (``indent``, ``sort_keys``, ``default``).

    The temp file is removed if serialisation or the replace fails, so a
    crashed write cannot leave scratch files accumulating beside the store.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, **dumps_kwargs)
    tmp = temp_path_for(path)
    try:
        tmp.write_text(payload, encoding="utf-8")
        _replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
