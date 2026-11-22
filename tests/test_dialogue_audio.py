"""Concatenation with silences and the pause between two lines (roadmap 8.5, reused by 8.6)."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import make_wav
from voxello.dialogue.audio import AudioFormatError, Silence, concatenate
from voxello.dialogue.project import Project
from voxello.dialogue.script import pause_between

SOURCE = """Francesca cercò i suoi occhi.

«Metto il telefono in carica.» disse Marco. «Poi esco.»

«Nello studio?»

---

Marco annuì.
"""

CAST: dict[str, Any] = {
    "language": "it",
    "narrator": "narratore",
    "pauses_ms": {"same_speaker": 10, "speaker_change": 20, "paragraph": 30, "break": 40},
    "characters": {
        "narratore": {"voice": "onyx"},
        "marco": {"voice": "clone:marco"},
        "francesca": {"voice": "clone:francesca"},
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
    concatenate([a, Silence(450), b], out)
    count, rate = frames(out)
    assert count == rate * 1200 // 1000


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
            "p002.1": {"speaker": "marco"},
            "p002.3": {"speaker": "marco"},
            "p003.1": {"speaker": "francesca"},
        }
    )
    project.cast_path.write_text(yaml.safe_dump(CAST), encoding="utf-8")
    return project


@pytest.mark.parametrize(
    ("first", "second", "pause"),
    [
        ("p002.1", "p002.2", 20),  # marco, then the narrator's attribution
        ("p002.1", "p002.3", 10),  # marco on both sides of the attribution
        ("p001.1", "p002.1", 30),  # a new paragraph
        ("p003.1", "p005.1", 40),  # a break in between
    ],
)
def test_pause_between_lines(project: Project, first: str, second: str, pause: int) -> None:
    cast = project.load_cast()
    assert cast is not None
    assert pause_between(project.load_script(), cast, first, second) == pause
