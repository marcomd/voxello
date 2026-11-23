"""Joining takes with silences (roadmap 8.5 context playback, 8.6 export).

Every take comes from one server as the same PCM format, so joining is a ``wave`` copy: the
first file fixes the format, silences are zero frames of it, and a file in another format is
refused rather than resampled. A ``Clip`` can carry a gain (export evens loudness per line),
applied to 16-bit samples with clamping at full scale.
"""

from __future__ import annotations

import array
import contextlib
import sys
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SAMPLE_MIN, SAMPLE_MAX = -32768, 32767


class AudioFormatError(ValueError):
    """The parts cannot be joined (different formats, or no audio file at all)."""


@dataclass(frozen=True)
class Silence:
    ms: int


@dataclass(frozen=True)
class Clip:
    path: Path
    gain: float = 1.0  # linear factor; 1.0 copies the frames unchanged


@dataclass(frozen=True)
class Joined:
    rate: int
    frames: int  # total frames written
    spans: tuple[tuple[int, int], ...]  # start and end frame of each audio part, in order


def concatenate(parts: Sequence[Path | Clip | Silence], output: Path) -> Joined:
    items = [part if isinstance(part, Clip | Silence) else Clip(part) for part in parts]
    clips = [item for item in items if isinstance(item, Clip)]
    if not clips:
        raise AudioFormatError("nothing to join: no audio file among the parts")
    with wave.open(str(clips[0].path), "rb") as first:
        params = (first.getnchannels(), first.getsampwidth(), first.getframerate())
    channels, width, rate = params
    spans: list[tuple[int, int]] = []
    written = 0
    try:
        with wave.open(str(output), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(width)
            out.setframerate(rate)
            for clip in items:
                if isinstance(clip, Silence):
                    count = rate * clip.ms // 1000
                    out.writeframes(bytes(count * width * channels))
                    written += count
                    continue
                with wave.open(str(clip.path), "rb") as wav:
                    found = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
                    if found != params:
                        raise AudioFormatError(
                            f"{clip.path.name} is {found}, expected {params} "
                            "(channels, width, rate)"
                        )
                    count = wav.getnframes()
                    data = wav.readframes(count)
                if clip.gain != 1.0:
                    data = scale(data, width, clip.gain, clip.path.name)
                out.writeframes(data)
                spans.append((written, written + count))
                written += count
    except BaseException:
        with contextlib.suppress(OSError):
            output.unlink()
        raise
    return Joined(rate, written, tuple(spans))


def scale(data: bytes, width: int, gain: float, name: str = "audio") -> bytes:
    """Multiply 16-bit little-endian samples by ``gain``, clamped to full scale."""
    if width != 2:
        raise AudioFormatError(f"{name}: a gain needs 16-bit samples, not {width * 8}-bit")
    samples = array.array("h", data)
    if sys.byteorder == "big":  # pragma: no cover - WAV is little-endian
        samples.byteswap()
    scaled = array.array("h", (min(SAMPLE_MAX, max(SAMPLE_MIN, round(s * gain))) for s in samples))
    if sys.byteorder == "big":  # pragma: no cover
        scaled.byteswap()
    return scaled.tobytes()
