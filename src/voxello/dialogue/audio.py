"""Joining takes with silences (roadmap 8.5 context playback, 8.6 export).

Every take comes from one server as the same PCM format, so joining is a ``wave`` copy: the
first file fixes the format, silences are zero frames of it, and a file in another format is
refused rather than resampled.
"""

from __future__ import annotations

import contextlib
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class AudioFormatError(ValueError):
    """The parts cannot be joined (different formats, or no audio file at all)."""


@dataclass(frozen=True)
class Silence:
    ms: int


def concatenate(parts: Sequence[Path | Silence], output: Path) -> None:
    files = [part for part in parts if isinstance(part, Path)]
    if not files:
        raise AudioFormatError("nothing to join: no audio file among the parts")
    with wave.open(str(files[0]), "rb") as first:
        params = (first.getnchannels(), first.getsampwidth(), first.getframerate())
    channels, width, rate = params
    try:
        with wave.open(str(output), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(width)
            out.setframerate(rate)
            for part in parts:
                if isinstance(part, Silence):
                    out.writeframes(bytes(rate * part.ms // 1000 * width * channels))
                    continue
                with wave.open(str(part), "rb") as wav:
                    found = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
                    if found != params:
                        raise AudioFormatError(
                            f"{part.name} is {found}, expected {params} (channels, width, rate)"
                        )
                    out.writeframes(wav.readframes(wav.getnframes()))
    except BaseException:
        with contextlib.suppress(OSError):
            output.unlink()
        raise
