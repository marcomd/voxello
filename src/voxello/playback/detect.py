"""Pick a player backend for the current platform."""

from __future__ import annotations

import shutil
import sys

from voxello.playback.backends import ALL_BACKENDS, PLATFORM_PREFERENCES
from voxello.playback.base import PlayerBackend


def detect_backend(
    preferred: str | None = None, platform: str | None = None
) -> PlayerBackend | None:
    """Return the first available backend, honouring an explicit preference."""
    plat = platform or sys.platform
    if preferred:
        backend = ALL_BACKENDS.get(preferred)
        if backend is not None and shutil.which(backend.executable):
            return backend
        return None
    for name in PLATFORM_PREFERENCES.get(plat, ("mpv", "ffplay")):
        backend = ALL_BACKENDS[name]
        if shutil.which(backend.executable):
            return backend
    return None
