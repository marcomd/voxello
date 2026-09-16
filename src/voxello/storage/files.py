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
import time
from datetime import datetime
from pathlib import Path

from voxello.errors import STORAGE_ERROR, VoxelloError

log = logging.getLogger(__name__)

_TEMP_PREFIX = "vox_"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TempStore:
    """Owns the temporary audio directory."""

    def __init__(self, directory: Path, retention_minutes: int, cleanup_on_start: bool) -> None:
        self.directory = directory
        self.retention_seconds = retention_minutes * 60
        self.cleanup_on_start = cleanup_on_start

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, stat.S_IRWXU)
        except OSError:  # pragma: no cover - e.g. Windows
            pass
        if self.cleanup_on_start:
            removed = self.sweep(max_age_seconds=0)
            if removed:
                log.info("Removed %d leftover temporary audio file(s)", removed)

    def new_path(self, suffix: str = ".wav") -> Path:
        return self.directory / f"{_TEMP_PREFIX}{secrets.token_urlsafe(12)}{suffix}"

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
            return path.resolve().parent == self.directory.resolve()
        except OSError:
            return False

    def discard(self, path: Path) -> None:
        if not self.owns(path):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover
            log.warning("Could not delete %s: %s", path.name, exc)

    def sweep(self, max_age_seconds: float | None = None) -> int:
        """Delete temp files older than the retention period; return how many."""
        max_age = self.retention_seconds if max_age_seconds is None else max_age_seconds
        cutoff = time.time() - max_age
        removed = 0
        if not self.directory.is_dir():
            return 0
        for entry in self.directory.iterdir():
            if not entry.name.startswith(_TEMP_PREFIX) or not entry.is_file():
                continue
            try:
                if entry.stat().st_mtime <= cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:  # pragma: no cover
                continue
        return removed

    async def run_sweeper(self, interval_seconds: float = 60) -> None:
        """Background task: sweep periodically until cancelled."""
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                removed = self.sweep()
                if removed:
                    log.debug("Swept %d expired temporary file(s)", removed)
        except asyncio.CancelledError:
            raise


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
