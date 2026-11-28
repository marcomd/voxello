"""Temporary and persistent audio storage (spec section 18).

Temporary files live in an application-controlled directory with restrictive
permissions, get random names, and are swept after a retention period. Persistent
output only happens on explicit request and is confined to the configured directory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import shutil
import stat
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol

from voxello.errors import STORAGE_ERROR, VoxelloError

log = logging.getLogger(__name__)

_TEMP_PREFIX = "vox_"
_NAMESPACE = re.compile(r"^voxello-temp-([1-9][0-9]*)-([A-Za-z0-9_-]+)$")
# Versions before per-instance namespaces wrote loose vox_* files directly into the temp
# directory and swept any of them older than the retention period every 60 seconds, so a
# loose file older than retention plus this margin is no longer in use by such an instance.
LEGACY_MARGIN_SECONDS = 120
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TempStore:
    """Owns one private namespace inside the configured temporary directory.

    Every running service gets its own namespace so a one-shot CLI/hook command never
    sweeps files owned by a long-lived MCP server (or vice versa). Startup cleanup removes
    only namespaces whose owner is provably dead and loose legacy files past the retention
    period of older versions; the retention sweeper only touches the store's own namespace.
    See docs/decisions/0005-temp-audio-ownership.md.
    """

    def __init__(
        self,
        directory: Path,
        retention_minutes: int,
        cleanup_on_start: bool,
        *,
        namespace: str | None = None,
        stale_grace_seconds: float = 60.0,
    ) -> None:
        self.directory = directory
        self.retention_seconds = retention_minutes * 60
        self.cleanup_on_start = cleanup_on_start
        self.stale_grace_seconds = stale_grace_seconds
        self.namespace = namespace or f"voxello-temp-{os.getpid()}-{secrets.token_urlsafe(8)}"

    @property
    def namespace_dir(self) -> Path:
        return self.directory / self.namespace

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, stat.S_IRWXU)
        except OSError:  # pragma: no cover - e.g. Windows
            pass
        self.namespace_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.namespace_dir, stat.S_IRWXU)
        except OSError:  # pragma: no cover - e.g. Windows
            pass
        (self.namespace_dir / "owner").write_text(str(os.getpid()), encoding="utf-8")
        if self.cleanup_on_start:
            namespaces = self._sweep_abandoned()
            legacy = self._sweep_legacy()
            if namespaces or legacy:
                log.info(
                    "Removed %d abandoned temp namespace(s) and %d legacy temp file(s)",
                    namespaces,
                    legacy,
                )

    def new_path(self, suffix: str = ".wav") -> Path:
        return self.namespace_dir / f"{_TEMP_PREFIX}{secrets.token_urlsafe(12)}{suffix}"

    def write(self, data: bytes, suffix: str = ".wav") -> Path:
        path = self.new_path(suffix)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            raise VoxelloError(STORAGE_ERROR, "Could not write temporary audio file.") from exc
        return path

    def owns(self, path: Path) -> bool:
        try:
            return path.resolve().parent == self.namespace_dir.resolve()
        except OSError:
            return False

    def discard(self, path: Path) -> None:
        if not self.owns(path):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover
            log.warning("Could not delete %s: %s", path.name, exc)

    def dispose(self) -> None:
        """Remove the owned namespace directory (best effort, idempotent)."""
        shutil.rmtree(self.namespace_dir, ignore_errors=True)

    def sweep(self, max_age_seconds: float | None = None) -> int:
        """Delete own temp files older than the retention period; return how many."""
        max_age = self.retention_seconds if max_age_seconds is None else max_age_seconds
        cutoff = time.time() - max_age
        removed = 0
        if not self.namespace_dir.is_dir():
            return 0
        for entry in self.namespace_dir.iterdir():
            if not entry.name.startswith(_TEMP_PREFIX) or not entry.is_file():
                continue
            try:
                if entry.stat().st_mtime <= cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:  # pragma: no cover
                continue
        return removed

    def _sweep_abandoned(self) -> int:
        """Remove namespaces whose owner is provably dead. Age alone is not enough."""
        removed = 0
        try:
            entries = list(self.directory.iterdir())
        except OSError:
            return 0
        now = time.time()
        for entry in entries:
            match = _NAMESPACE.fullmatch(entry.name)
            if (
                match is None
                or entry.is_symlink()
                or not entry.is_dir()
                or entry.name == self.namespace
            ):
                continue
            try:
                if now - entry.stat().st_mtime < self.stale_grace_seconds:
                    continue
            except OSError:
                continue
            pid = self._read_owner_pid(entry)
            if pid is None or pid != int(match.group(1)) or _pid_alive(pid) is not False:
                continue
            try:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
            except OSError:  # pragma: no cover
                continue
        return removed

    def _sweep_legacy(self) -> int:
        """Remove loose ``vox_*`` files left by older versions once past their retention."""
        cutoff = time.time() - self.retention_seconds - LEGACY_MARGIN_SECONDS
        removed = 0
        try:
            entries = list(self.directory.iterdir())
        except OSError:  # pragma: no cover
            return 0
        for entry in entries:
            if not entry.name.startswith(_TEMP_PREFIX):
                continue
            try:
                info = entry.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_mtime > cutoff:
                    continue
                entry.unlink()
                removed += 1
            except OSError:  # pragma: no cover
                continue
        return removed

    @staticmethod
    def _read_owner_pid(namespace: Path) -> int | None:
        try:
            return int((namespace / "owner").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    async def run_sweeper(self, interval_seconds: float = 60) -> None:
        """Background task: sweep own namespace periodically until cancelled."""
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                removed = self.sweep()
                if removed:
                    log.debug("Swept %d expired temporary file(s)", removed)
        except asyncio.CancelledError:
            raise


def _pid_alive(pid: int) -> bool | None:
    """True if ``pid`` exists, False if it provably does not, None when unknown."""
    if sys.platform == "win32":
        # Never os.kill(pid, 0) here: signal 0 is CTRL_C_EVENT on Windows.
        query = _windows_process_query()
        return None if query is None else _windows_pid_alive(pid, query)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by someone else
    except OSError:
        return None
    return True


_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87  # OpenProcess on a PID that does not exist


class _ProcessQuery(Protocol):
    """The Win32 calls behind ``_windows_pid_alive``; a fake in tests."""

    def open(self, pid: int) -> int | None: ...
    def last_error(self) -> int: ...
    def exit_code(self, handle: int) -> int | None: ...
    def close(self, handle: int) -> None: ...


def _windows_pid_alive(pid: int, query: _ProcessQuery) -> bool | None:
    handle = query.open(pid)
    if handle is None:
        error = query.last_error()
        if error == _ERROR_INVALID_PARAMETER:
            return False
        if error == _ERROR_ACCESS_DENIED:
            return True
        return None
    try:
        code = query.exit_code(handle)
    finally:
        query.close(handle)
    if code is None:
        return None
    # A process that exited with code 259 reads as alive: unknown owners are kept.
    return code == _STILL_ACTIVE


def _windows_process_query() -> _ProcessQuery | None:  # pragma: no cover - Windows only
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
    except (AttributeError, OSError):
        return None

    process_query_limited_information = 0x1000

    class Kernel32Query:
        def open(self, pid: int) -> int | None:
            return open_process(process_query_limited_information, False, pid) or None

        def last_error(self) -> int:
            return ctypes.get_last_error()

        def exit_code(self, handle: int) -> int | None:
            code = wintypes.DWORD()
            if not get_exit_code(handle, ctypes.byref(code)):
                return None
            return code.value

        def close(self, handle: int) -> None:
            close_handle(handle)

    return Kernel32Query()


class OutputStore:
    """Persists audio requested with ``save=true`` into the configured directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _target(self, request_id: str, suffix: str) -> Path:
        if not _SAFE_ID.match(request_id):
            raise VoxelloError(STORAGE_ERROR, "Invalid request id for saved file.")
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = (self.directory / f"{stamp}_{request_id}{suffix}").resolve()
        if target.parent != self.directory.resolve():
            raise VoxelloError(STORAGE_ERROR, "Refusing to write outside the output directory.")
        return target

    def save_audio(self, source: Path, request_id: str) -> Path:
        target = self._target(request_id, source.suffix or ".wav")
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise VoxelloError(
                STORAGE_ERROR, "Could not save audio to the output directory."
            ) from exc
        return target

    def save_text(self, text: str, request_id: str) -> Path:
        target = self._target(request_id, ".txt")
        try:
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise VoxelloError(
                STORAGE_ERROR, "Could not save text to the output directory."
            ) from exc
        return target
