"""Signal checks on takes (roadmap 8.5): synthetic PCM, standard library only."""

from __future__ import annotations

import array
import io
import math
import wave
from pathlib import Path

import pytest

from voxello.dialogue import qa

RATE = 24000


def tone(ms: int, amplitude: int = 8000, freq: float = 220.0) -> list[int]:
    frames = RATE * ms // 1000
    return [round(amplitude * math.sin(2 * math.pi * freq * i / RATE)) for i in range(frames)]


def silence(ms: int) -> list[int]:
    return [0] * (RATE * ms // 1000)


def wav_bytes(samples: list[int], *, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(RATE)
        if width == 2:
            interleaved = [s for s in samples for _ in range(channels)]
            wav.writeframes(array.array("h", interleaved).tobytes())
        else:
            wav.writeframes(bytes(len(samples) * width * channels))
    return buffer.getvalue()


# --- measure --------------------------------------------------------------------------------


def test_measure_finds_leading_and_trailing_silence() -> None:
    m = qa.measure(wav_bytes(silence(500) + tone(2000) + silence(1000)))
    assert m is not None
    assert m.duration_ms == 3500
    assert m.lead_ms == 500
    assert m.trail_ms == 1000
    assert m.voiced_ms == 2000
    assert m.clipped_runs == 0
    assert m.peak == 8000
    assert -30 < m.rms_db < -15


def test_measure_reads_a_file_and_the_first_channel(tmp_path: Path) -> None:
    path = tmp_path / "take.wav"
    path.write_bytes(wav_bytes(tone(1000), channels=2))
    m = qa.measure(path)
    assert m is not None
    assert m.duration_ms == 1000
    assert m.lead_ms == 0


def test_measure_counts_runs_of_clipped_samples() -> None:
    clipped = tone(500) + [32767] * 5 + tone(100) + [-32768] * 3 + tone(100) + [32767] * 2
    m = qa.measure(wav_bytes(clipped))
    assert m is not None
    assert m.clipped_runs == 2  # the run of 2 is too short to count
    assert m.peak == 32768


def test_measure_of_pure_silence() -> None:
    m = qa.measure(wav_bytes(silence(1000)))
    assert m is not None
    assert m.voiced_ms == 0
    assert m.lead_ms == m.trail_ms == 1000
    assert m.rms_db == qa.FLOOR_DB


@pytest.mark.parametrize("audio", [b"not a wav", wav_bytes(silence(10), width=1), b""])
def test_measure_returns_none_for_unreadable_or_unsupported_audio(audio: bytes) -> None:
    assert qa.measure(audio) is None


def test_measure_of_an_empty_wav() -> None:
    m = qa.measure(wav_bytes([]))
    assert m is not None
    assert m.duration_ms == 0
    assert m.peak == 0


# --- characters per second -------------------------------------------------------------------


def test_spoken_chars_ignores_tags_spaces_and_punctuation() -> None:
    assert qa.spoken_chars("[sigh] Non è vero, Marco!") == len("Nonèveromarco")


def test_chars_per_second_uses_speech_time_and_speed() -> None:
    m = qa.measure(wav_bytes(silence(500) + tone(2000) + silence(500)))
    assert m is not None
    text = "a" * 28
    assert qa.chars_per_second(m, qa.spoken_chars(text), None) == pytest.approx(14.0)
    assert qa.chars_per_second(m, qa.spoken_chars(text), 1.25) == pytest.approx(11.2)


def test_chars_per_second_skips_short_lines_and_silent_takes() -> None:
    m = qa.measure(wav_bytes(tone(1000)))
    silent = qa.measure(wav_bytes(silence(1000)))
    assert m is not None and silent is not None
    assert qa.chars_per_second(m, qa.MIN_CHARS - 1, None) is None
    assert qa.chars_per_second(silent, 40, None) is None


def test_typical_cps_per_speaker_falls_back_to_all_lines_then_default() -> None:
    marco = [10.0, 11.0, 12.0, 13.0, 14.0]
    typical = qa.typical_cps({"marco": marco, "francesca": [20.0]})
    assert typical["marco"] == 12.0
    assert typical["francesca"] == 12.5  # median of all six values
    assert qa.typical_cps({"marco": [9.0, 9.0]}) == {"marco": qa.DEFAULT_CPS}


# --- flags ----------------------------------------------------------------------------------


def measures(**overrides: int | float) -> qa.Measures:
    values: dict[str, int | float] = {
        "duration_ms": 3000,
        "peak": 16000,
        "clipped_runs": 0,
        "rms_db": -20.0,
        "lead_ms": 200,
        "trail_ms": 300,
        "voiced_ms": 2500,
    }
    values.update(overrides)
    return qa.Measures(**values)  # type: ignore[arg-type]


def codes(flags: list[qa.Flag]) -> list[str]:
    return [f.code for f in flags]


def test_a_clean_take_has_no_flags() -> None:
    assert qa.flags(measures(), cps=14.0, typical=14.0, speaker="marco") == []


def test_signal_flags() -> None:
    m = measures(clipped_runs=3, lead_ms=qa.MAX_LEAD_MS + 1, trail_ms=qa.MAX_TRAIL_MS + 1)
    assert codes(qa.flags(m, cps=None, typical=None, speaker="marco")) == [
        "clipping",
        "lead-silence",
        "trail-silence",
    ]


@pytest.mark.parametrize(
    ("override", "expected"),
    [({"rms_db": qa.SILENCE_DB - 1}, "near-silent"), ({"voiced_ms": 100}, "near-silent")],
)
def test_a_near_silent_take_gets_only_that_flag(override: dict[str, float], expected: str) -> None:
    m = measures(lead_ms=2000, **override)
    assert codes(qa.flags(m, cps=3.0, typical=14.0, speaker="marco")) == [expected]


def test_speed_flags_against_the_speakers_usual_rate() -> None:
    slow = qa.flags(measures(), cps=9.1, typical=14.2, speaker="marco")
    assert codes(slow) == ["slow"]
    assert slow[0].message == "slow 9.1 c/s (marco ~14.2)"
    assert codes(qa.flags(measures(), cps=21.0, typical=14.0, speaker="marco")) == ["fast"]
    assert qa.flags(measures(), cps=10.0, typical=14.0, speaker="marco") == []
