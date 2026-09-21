"""Small WAV helpers built on the standard library."""

from __future__ import annotations

import io
import wave
from pathlib import Path


def is_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def wav_duration_ms(source: bytes | Path) -> int | None:
    """Return the duration in milliseconds, or None if the header cannot be parsed."""
    try:
        if isinstance(source, Path):
            with wave.open(str(source), "rb") as wf:
                return _duration(wf)
        with wave.open(io.BytesIO(source), "rb") as wf:
            return _duration(wf)
    except (wave.Error, EOFError, OSError):
        return None


def _duration(wf: wave.Wave_read) -> int | None:
    rate = wf.getframerate()
    if rate <= 0:
        return None
    return round(wf.getnframes() * 1000 / rate)


def write_silence(path: Path, duration_ms: int, sample_rate: int = 24000) -> Path:
    """Write a silent mono 16-bit WAV file (used by the tests)."""
    frames = int(sample_rate * duration_ms / 1000)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frames)
    return path
