"""Microphone capture and reference-clip cleanup with ffmpeg.

Capture runs ffmpeg with the platform's input device (avfoundation on macOS, PulseAudio or
ALSA on Linux) until the speaker presses Enter, which sends ffmpeg the ``q`` key on stdin so
it finalizes the WAV header; ``-t`` bounds a forgotten recording. Cleanup turns the capture
into what OmniVoice clones best (the spike in ``docs/decisions/0003-dialogue-production.md``):
a high-pass against rumble, leading and trailing silence trimmed, loudness normalized, 24 kHz
mono 16-bit like the server's own output.

Commands are argument lists; the only variable parts are paths Voxello chose and the device
the user passed. Windows (dshow needs a device name) is not supported yet.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from voxello.errors import RECORDER_UNAVAILABLE, RECORDING_FAILED, VoxelloError

SAMPLE_RATE = 24000
STOP_TIMEOUT_SECONDS = 5.0
_TRIM = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15"
CLEANUP_FILTER = f"highpass=f=70,{_TRIM},areverse,{_TRIM},areverse,loudnorm=I=-20:TP=-2"
_QUIET = ("-hide_banner", "-loglevel", "error", "-y")


class _Process(Protocol):
    returncode: int | None

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[Any, Any]: ...

    def kill(self) -> None: ...


Popen = Callable[..., _Process]
Run = Callable[..., "subprocess.CompletedProcess[bytes]"]


def capture_input(
    platform: str, device: str | None, which: Callable[[str], str | None]
) -> list[str]:
    """The ffmpeg input arguments for the default (or given) microphone."""
    if platform == "darwin":
        # avfoundation names inputs "video:audio"; ":0" is the first audio device.
        audio = device or "0"
        return ["-f", "avfoundation", "-i", audio if audio.startswith(":") else f":{audio}"]
    if platform.startswith("linux"):
        fmt = "pulse" if which("pactl") else "alsa"  # PipeWire also answers PulseAudio clients
        return ["-f", fmt, "-i", device or "default"]
    raise VoxelloError(
        RECORDER_UNAVAILABLE,
        f"Recording is not supported on {platform} yet; record a 3-10 s WAV with another tool "
        "and use `voxello voice clone NAME FILE --ref-text TEXT`.",
    )


def _find_ffmpeg(which: Callable[[str], str | None], purpose: str) -> str:
    ffmpeg = which("ffmpeg")
    if ffmpeg is None:
        raise VoxelloError(
            RECORDER_UNAVAILABLE,
            f"{purpose} needs ffmpeg on the PATH (macOS: `brew install ffmpeg`; "
            "Debian/Ubuntu: `apt install ffmpeg`; Windows: `winget install ffmpeg`).",
        )
    return ffmpeg


def detect_recorder(
    *,
    device: str | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> FfmpegRecorder:
    plat = platform or sys.platform
    args = capture_input(plat, device, which)
    return FfmpegRecorder(_find_ffmpeg(which, "Recording"), args, plat)


def detect_cleaner(*, which: Callable[[str], str | None] = shutil.which) -> FfmpegCleaner:
    """Cleanup only (``voice clone --prepare``): any platform, no microphone involved."""
    return FfmpegCleaner(_find_ffmpeg(which, "Preparing a recording"))


class FfmpegRecording:
    """A running capture; ``stop`` finalizes the file, ``abort`` only makes sure ffmpeg ends."""

    def __init__(self, process: _Process, platform: str) -> None:
        self._process = process
        self._platform = platform

    def _finish(self) -> bytes:
        try:
            _, stderr = self._process.communicate(input=b"q", timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._process.kill()
            _, stderr = self._process.communicate()
        return stderr or b""

    def stop(self) -> None:
        stderr = self._finish()
        if self._process.returncode not in (0, None):
            detail = stderr.decode(errors="replace").strip().splitlines()[-3:]
            hint = (
                " Allow your terminal in System Settings > Privacy & Security > Microphone."
                if self._platform == "darwin"
                else " Check the input device (--device) and that nothing else holds it."
            )
            raise VoxelloError(
                RECORDING_FAILED,
                f"ffmpeg could not record (exit {self._process.returncode}): "
                f"{' / '.join(detail) or 'no output'}.{hint}",
            )

    def abort(self) -> None:
        self._finish()


class FfmpegCleaner:
    """Turns any recording ffmpeg can read into a reference clip, optionally a window of it."""

    def __init__(self, ffmpeg: str, *, run: Run = subprocess.run) -> None:
        self.ffmpeg = ffmpeg
        self._run = run

    def prepare_command(
        self,
        raw: Path,
        output: Path,
        *,
        start: float | None = None,
        duration: float | None = None,
    ) -> list[str]:
        window: list[str] = []
        if start is not None:
            window += ["-ss", f"{start:g}"]
        if duration is not None:
            window += ["-t", f"{duration:g}"]
        return [
            self.ffmpeg,
            *_QUIET,
            "-nostdin",
            *window,
            "-i",
            str(raw),
            "-af",
            CLEANUP_FILTER,
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(output),
        ]

    def prepare(
        self,
        raw: Path,
        output: Path,
        *,
        start: float | None = None,
        duration: float | None = None,
    ) -> None:
        """Clean ``raw`` (from ``start`` s, for ``duration`` s) into a reference clip."""
        try:
            result = self._run(
                self.prepare_command(raw, output, start=start, duration=duration),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise VoxelloError(
                RECORDER_UNAVAILABLE, f"Could not run ffmpeg ({self.ffmpeg}): {exc}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.decode(errors="replace").strip().splitlines()[-3:]
            raise VoxelloError(
                RECORDING_FAILED,
                f"ffmpeg could not clean up the recording: {' / '.join(detail) or 'no output'}",
            )


class FfmpegRecorder(FfmpegCleaner):
    def __init__(
        self,
        ffmpeg: str,
        input_args: list[str],
        platform: str,
        *,
        popen: Popen = subprocess.Popen,
        run: Run = subprocess.run,
    ) -> None:
        super().__init__(ffmpeg, run=run)
        self.input_args = input_args
        self.platform = platform
        self._popen = popen

    def describe(self) -> str:
        return f"ffmpeg ({self.input_args[1]} {self.input_args[3]})"

    def capture_command(self, output: Path, max_seconds: float) -> list[str]:
        # stdin stays open: the q key is how the capture is stopped cleanly.
        return [
            self.ffmpeg,
            *_QUIET,
            *self.input_args,
            "-t",
            f"{max_seconds:g}",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            str(output),
        ]

    def start(self, output: Path, max_seconds: float) -> FfmpegRecording:
        try:
            process = self._popen(
                self.capture_command(output, max_seconds),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise VoxelloError(
                RECORDER_UNAVAILABLE, f"Could not start ffmpeg ({self.ffmpeg}): {exc}"
            ) from exc
        return FfmpegRecording(process, self.platform)
