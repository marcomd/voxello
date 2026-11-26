"""Cross-process CLI project ownership; the lock inode is never replaced or unlinked.

The persistent lock is a sibling of the resolved project directory, so a preview creates no
project files. OS locks are released even on process death. Direct library callers must own
the same lock when running commands concurrently (like the CLI does).
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from voxello.errors import STORAGE_ERROR, VoxelloError


@contextmanager
def project_lock(root: Path) -> Iterator[None]:
    root = root.resolve()
    if not root.is_dir():
        raise VoxelloError(STORAGE_ERROR, f"Project directory does not exist: {root}")
    path = root.with_name(f".{root.name}.voxello.lock")
    try:
        handle = path.open("a+b")
    except OSError as exc:
        raise VoxelloError(STORAGE_ERROR, f"Cannot lock {root}: {exc}") from exc
    with handle:
        try:
            if sys.platform == "win32":
                api = importlib.import_module("msvcrt")
                if path.stat().st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                api.locking(handle.fileno(), api.LK_NBLCK, 1)
            else:
                api = importlib.import_module("fcntl")
                api.flock(handle.fileno(), api.LOCK_EX | api.LOCK_NB)
        except OSError as exc:
            raise VoxelloError(
                STORAGE_ERROR, f"Project is busy or cannot be locked: {root}"
            ) from exc
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                api.locking(handle.fileno(), api.LK_UNLCK, 1)
            else:
                api.flock(handle.fileno(), api.LOCK_UN)
