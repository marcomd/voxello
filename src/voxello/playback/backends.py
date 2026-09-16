"""Command-line player backends for macOS, Linux and Windows.

Commands are built as argument lists (never shell strings), and the only variable
parts are the audio path Voxello itself generated and a numeric volume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from voxello.playback.base import PlayerBackend


@dataclass(frozen=True)
class Afplay(PlayerBackend):
    name: str = "afplay"
    executable: str = "afplay"
    supports_volume: bool = True

    def build_command(self, path: Path, volume: float) -> list[str]:
        return [self.executable, "-v", f"{volume:.2f}", str(path)]


@dataclass(frozen=True)
class Mpv(PlayerBackend):
    name: str = "mpv"
    executable: str = "mpv"
    supports_volume: bool = True

    def build_command(self, path: Path, volume: float) -> list[str]:
        return [
            self.executable,
            "--no-video",
            "--no-terminal",
            "--really-quiet",
            f"--volume={round(volume * 100)}",
            str(path),
        ]


@dataclass(frozen=True)
class Paplay(PlayerBackend):
    name: str = "paplay"
    executable: str = "paplay"
    supports_volume: bool = True

    def build_command(self, path: Path, volume: float) -> list[str]:
        return [self.executable, f"--volume={round(volume * 65536)}", str(path)]


@dataclass(frozen=True)
class Aplay(PlayerBackend):
    name: str = "aplay"
    executable: str = "aplay"
    supports_volume: bool = False

    def build_command(self, path: Path, volume: float) -> list[str]:
        return [self.executable, "-q", str(path)]


@dataclass(frozen=True)
class Ffplay(PlayerBackend):
    name: str = "ffplay"
    executable: str = "ffplay"
    supports_volume: bool = True

    def build_command(self, path: Path, volume: float) -> list[str]:
        return [
            self.executable,
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "quiet",
            "-volume",
            str(round(volume * 100)),
            str(path),
        ]


@dataclass(frozen=True)
class PowerShellSoundPlayer(PlayerBackend):
    name: str = "powershell"
    executable: str = "powershell"
    supports_volume: bool = False

    def build_command(self, path: Path, volume: float) -> list[str]:
        # Single quotes are escaped by doubling; the path is one we generated ourselves.
        escaped = str(path).replace("'", "''")
        script = f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()"
        return [self.executable, "-NoProfile", "-NonInteractive", "-Command", script]


ALL_BACKENDS: dict[str, PlayerBackend] = {
    b.name: b for b in (Afplay(), Mpv(), Paplay(), Aplay(), Ffplay(), PowerShellSoundPlayer())
}

PLATFORM_PREFERENCES: dict[str, tuple[str, ...]] = {
    "darwin": ("afplay", "mpv", "ffplay"),
    "linux": ("mpv", "paplay", "aplay", "ffplay"),
    "win32": ("powershell", "mpv", "ffplay"),
}
