"""Playback abstractions (spec section 14)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class PlayerBackend:
    """A command-line audio player and how to invoke it."""

    name: str
    executable: str
    supports_volume: bool

    def build_command(self, path: Path, volume: float) -> list[str]:  # pragma: no cover
        raise NotImplementedError


class PlaybackHandle(Protocol):
    """A running playback that can be awaited or terminated."""

    async def wait(self) -> int: ...

    async def terminate(self) -> None: ...


class AudioPlayer(Protocol):
    """Starts playback of a file and returns a handle."""

    name: str

    async def play(self, path: Path) -> PlaybackHandle: ...
