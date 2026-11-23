"""Concatenation with silences and gain, and the pause between two lines (roadmap 8.5, 8.6)."""

from __future__ import annotations

import array
import wave
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import make_wav
from voxello.dialogue.audio import AudioFormatError, Clip, Silence, concatenate
from voxello.dialogue.project import Project
from voxello.dialogue.script import pause_between

SOURCE = """Anna aprì la finestra.

«Porto fuori il cane.» disse Bruno. «Poi esco.»

«Fino al parco?»

---

Bruno rise.
"""

CAST: dict[str, Any] = {
    "language": "it",
    "narrator": "narratore",
    "pauses_ms": {"same_speaker": 10, "speaker_change": 20, "paragraph": 30, "break": 40},
    "characters": {
        "narratore": {"voice": "onyx"},
        "bruno": {"voice": "clone:bruno"},
        "anna": {"voice": "clone:anna"},
    },
}


def frames(path: Path) -> tuple[int, int]:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes(), wav.getframerate()


def test_concatenate_writes_takes_and_silences(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(make_wav(500))
    b.write_bytes(make_wav(250))
    out = tmp_path / "out.wav"
    joined = concatenate([a, Silence(450), b], out)
    count, rate = frames(out)
    assert count == rate * 1200 // 1000
    assert joined.frames == count
    assert joined.rate == rate
    assert joined.spans == ((0, 12000), (22800, 28800))


def samples_of(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wav:
        return list(array.array("h", wav.readframes(wav.getnframes())))


def write_samples(path: Path, samples: list[int], *, width: int = 2) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(width)
        wav.setframerate(24000)
        if width == 2:
            wav.writeframes(array.array("h", samples).tobytes())
        else:
            wav.writeframes(bytes(len(samples)))


def test_concatenate_applies_a_gain_per_clip_and_clamps(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    write_samples(a, [1000, -1000, 20000, -20000])
    out = tmp_path / "out.wav"
    concatenate([Clip(a, 2.0), Silence(0), Clip(a)], out)
    assert samples_of(out) == [2000, -2000, 32767, -32768, 1000, -1000, 20000, -20000]


def test_a_gain_needs_16_bit_samples(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    write_samples(a, [0, 0], width=1)
    with pytest.raises(AudioFormatError, match="16-bit"):
        concatenate([Clip(a, 0.5)], tmp_path / "out.wav")
    concatenate([Clip(a)], tmp_path / "plain.wav")  # no gain: any width is copied
    assert not (tmp_path / "out.wav").exists()


def test_concatenate_refuses_mixed_formats_and_no_audio(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    a.write_bytes(make_wav(100, sample_rate=24000))
    b = tmp_path / "b.wav"
    b.write_bytes(make_wav(100, sample_rate=16000))
    with pytest.raises(AudioFormatError):
        concatenate([a, b], tmp_path / "out.wav")
    with pytest.raises(AudioFormatError):
        concatenate([Silence(100)], tmp_path / "out.wav")
    assert not (tmp_path / "out.wav").exists()


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "chapter.txt"
    source.write_text(SOURCE, encoding="utf-8")
    project = Project.init(source, tmp_path / "chapter")
    project.annotate(
        {
            "p002.1": {"speaker": "bruno"},
            "p002.3": {"speaker": "bruno"},
            "p003.1": {"speaker": "anna"},
        }
    )
    project.cast_path.write_text(yaml.safe_dump(CAST), encoding="utf-8")
    return project


@pytest.mark.parametrize(
    ("first", "second", "pause"),
    [
        ("p002.1", "p002.2", 20),  # bruno, then the narrator's attribution
        ("p002.1", "p002.3", 10),  # bruno on both sides of the attribution
        ("p001.1", "p002.1", 30),  # a new paragraph
        ("p003.1", "p005.1", 40),  # a break in between
    ],
)
def test_pause_between_lines(project: Project, first: str, second: str, pause: int) -> None:
    cast = project.load_cast()
    assert cast is not None
    assert pause_between(project.load_script(), cast, first, second) == pause
