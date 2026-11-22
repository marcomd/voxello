"""Cheap signal checks that point the reviewer at suspicious takes (roadmap 8.5).

No speech recognition (decision 0003): a take whose speaking rate is far from the speaker's
usual one probably dropped or repeated words, and clipping, long leading or trailing silence
and near-silent takes are plain signal problems. Standard library only (``wave``, ``array``):
every take is 16-bit PCM from one server.

``measure`` reads a take once; its ``Measures`` are stored in ``state.json`` with the take,
because reading every WAV again at each review would be slow on a long chapter. ``flags`` is
recomputed every time, since the usual rate of a speaker changes as takes are added.
Thresholds were tuned on real OmniVoice takes (example2: leading silence up to 580 ms on short
cloned lines, about 14 characters per second on lines of 20 characters or more).
"""

from __future__ import annotations

import array
import io
import math
import operator
import re
import statistics
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

QA_VERSION = 1  # bump when `measure` changes: stored measures of another version are redone
FULL_SCALE = 32768
FLOOR_DB = -120.0
CLIP_LEVEL = 32700
CLIP_RUN = 3  # consecutive samples at the ceiling that count as one clipping event
WINDOW_MS = 20
SILENCE_DB = -45.0  # a window quieter than this is silence
MAX_LEAD_MS = 700
MAX_TRAIL_MS = 900
MIN_VOICED_MS = 300
MIN_CHARS = 20  # shorter lines vary too much in rate to judge
CPS_RANGE = (0.7, 1.4)  # accepted rate, relative to the speaker's usual one
DEFAULT_CPS = 14.0
MIN_LINES_FOR_MEDIAN = 5

TAG_RE = re.compile(r"\[[a-z][a-z-]*\]")


@dataclass(frozen=True)
class Measures:
    duration_ms: int
    peak: int  # largest absolute sample, 0-32768
    clipped_runs: int
    rms_db: float
    lead_ms: int
    trail_ms: int
    voiced_ms: int
    version: int = QA_VERSION


@dataclass(frozen=True)
class Flag:
    code: str  # near-silent, clipping, lead-silence, trail-silence, slow, fast
    message: str


def measure(source: bytes | Path) -> Measures | None:
    """Measures of a 16-bit PCM WAV (first channel), or ``None`` when it cannot be read."""
    try:
        if isinstance(source, Path):
            with wave.open(str(source), "rb") as wav:
                return _measure_wav(wav)
        with wave.open(io.BytesIO(source), "rb") as wav:
            return _measure_wav(wav)
    except (wave.Error, EOFError, OSError):
        return None


def _measure_wav(wav: wave.Wave_read) -> Measures | None:
    if wav.getsampwidth() != 2:
        return None
    rate = wav.getframerate()
    samples = array.array("h", wav.readframes(wav.getnframes()))
    if sys.byteorder == "big":  # pragma: no cover - WAV is little-endian
        samples.byteswap()
    channels = wav.getnchannels()
    if channels > 1:
        samples = samples[::channels]
    return measure_samples(samples, rate)


def measure_samples(samples: array.array[int], rate: int) -> Measures:
    count = len(samples)
    duration_ms = round(count * 1000 / rate)
    window = max(1, rate * WINDOW_MS // 1000)
    loud: list[bool] = []
    for start in range(0, count, window):
        chunk = samples[start : start + window]
        loud.append(_db(sum(map(operator.mul, chunk, chunk)) / len(chunk)) > SILENCE_DB)
    window_ms = window * 1000 / rate
    voiced = sum(loud)
    if voiced:
        lead = round(loud.index(True) * window_ms)
        trail = round(loud[::-1].index(True) * window_ms)
    else:
        lead = trail = duration_ms
    peak = max(max(samples), -min(samples)) if count else 0
    energy = sum(map(operator.mul, samples, samples)) / count if count else 0.0
    return Measures(
        duration_ms=duration_ms,
        peak=peak,
        clipped_runs=_clipped_runs(samples) if peak >= CLIP_LEVEL else 0,
        rms_db=round(_db(energy), 1),
        lead_ms=min(lead, duration_ms),
        trail_ms=min(trail, duration_ms),
        voiced_ms=min(round(voiced * window_ms), duration_ms),
    )


def _db(mean_square: float) -> float:
    if mean_square <= 0:
        return FLOOR_DB
    return max(FLOOR_DB, 10 * math.log10(mean_square / FULL_SCALE**2))


def _clipped_runs(samples: array.array[int]) -> int:
    runs = length = 0
    for sample in samples:
        if abs(sample) >= CLIP_LEVEL:
            length += 1
            if length == CLIP_RUN:
                runs += 1
        else:
            length = 0
    return runs


def spoken_chars(tts_text: str) -> int:
    """Letters and digits that are read aloud: tags, spaces and punctuation do not count."""
    return sum(c.isalnum() for c in TAG_RE.sub("", tts_text))


def chars_per_second(m: Measures, chars: int, speed: float | None) -> float | None:
    """Speaking rate at speed 1.0, or ``None`` for lines too short or silent to judge."""
    speech_ms = m.duration_ms - m.lead_ms - m.trail_ms
    if chars < MIN_CHARS or speech_ms <= 0 or m.voiced_ms == 0:
        return None
    return chars / (speech_ms / 1000) / (speed or 1.0)


def typical_cps(rates: Mapping[str, Sequence[float]]) -> dict[str, float]:
    """Each speaker's median rate; the median of every line for speakers with few lines."""
    every = [rate for values in rates.values() for rate in values]
    overall = statistics.median(every) if len(every) >= MIN_LINES_FOR_MEDIAN else DEFAULT_CPS
    return {
        speaker: statistics.median(values) if len(values) >= MIN_LINES_FOR_MEDIAN else overall
        for speaker, values in rates.items()
    }


def flags(m: Measures, *, cps: float | None, typical: float | None, speaker: str) -> list[Flag]:
    if m.rms_db < SILENCE_DB or m.voiced_ms < MIN_VOICED_MS:
        return [Flag("near-silent", f"near-silent ({m.voiced_ms / 1000:.1f} s of sound)")]
    found: list[Flag] = []
    if m.clipped_runs:
        found.append(Flag("clipping", f"clipping ({m.clipped_runs}x)"))
    if m.lead_ms > MAX_LEAD_MS:
        found.append(Flag("lead-silence", f"leading silence {m.lead_ms / 1000:.1f} s"))
    if m.trail_ms > MAX_TRAIL_MS:
        found.append(Flag("trail-silence", f"trailing silence {m.trail_ms / 1000:.1f} s"))
    if cps is not None and typical:
        low, high = CPS_RANGE
        usual = f"{cps:.1f} c/s ({speaker} ~{typical:.1f})"
        if cps < typical * low:
            found.append(Flag("slow", f"slow {usual}"))
        elif cps > typical * high:
            found.append(Flag("fast", f"fast {usual}"))
    return found
