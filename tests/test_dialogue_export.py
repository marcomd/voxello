"""`dialogue export` (roadmap 8.6): gain, timeline, SRT and refusals."""

from __future__ import annotations

import array
import io
import itertools
import json
import math
import wave
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import FakeProvider
from voxello.dialogue.audio import AudioFormatError
from voxello.dialogue.export import (
    MAX_GAIN_DB,
    PEAK_CEILING,
    TARGET_DB,
    Level,
    SubtitleEntry,
    export,
    format_srt_time,
    level_gain,
    measure_level,
    to_srt,
)
from voxello.dialogue.project import Project
from voxello.dialogue.render import render
from voxello.errors import DIALOGUE_NOT_READY, VoxelloError
from voxello.tts.base import SynthesisOptions, SynthesisResult

RATE = 24000
SOURCE = """# Capitolo uno

Anna aprì la finestra.

«Porto fuori il cane.» disse Bruno.

---

«Fino al parco?»
"""

CAST: dict[str, Any] = {
    "language": "it",
    "narrator": "narratore",
    "pauses_ms": {"same_speaker": 100, "speaker_change": 200, "paragraph": 300, "break": 400},
    "characters": {
        "narratore": {"voice": "onyx"},
        "bruno": {"voice": "clone:bruno"},
        "anna": {"voice": "clone:anna"},
    },
}

LINES = ["p001.1", "p002.1", "p003.1", "p003.2", "p005.1"]


def tone(ms: int, amplitude: int, lead_ms: int = 0) -> list[int]:
    lead = [0] * (RATE * lead_ms // 1000)
    return lead + [
        round(amplitude * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(RATE * ms // 1000)
    ]


def wav_bytes(samples: list[int], rate: int = RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(array.array("h", samples).tobytes())
    return buffer.getvalue()


def read_samples(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wav:
        return list(array.array("h", wav.readframes(wav.getnframes())))


class ToneProvider(FakeProvider):
    """A tone per line: lengths differ by text, amplitude by voice (a quiet narrator)."""

    def __init__(self) -> None:
        super().__init__()
        self.rate = RATE

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        instructions: str | None = None,
        *,
        options: SynthesisOptions | None = None,
    ) -> SynthesisResult:
        await super().synthesize(text, voice, language, instructions, options=options)
        amplitude = 1500 if voice == "onyx" else 6000
        audio = wav_bytes(tone(20 * len(text), amplitude, lead_ms=40), self.rate)
        return SynthesisResult(audio, "audio/wav", self.name, voice or "x")


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "chapter.txt"
    source.write_text(SOURCE, encoding="utf-8")
    project = Project.init(source, tmp_path / "chapter")
    project.annotate({"p003.1": {"speaker": "bruno"}, "p005.1": {"speaker": "anna"}})
    project.cast_path.write_text(yaml.safe_dump(CAST), encoding="utf-8")
    return project


async def rendered(project: Project, provider: FakeProvider | None = None) -> Project:
    await render(project, provider or ToneProvider())
    return project


def approve_all(project: Project) -> None:
    state = project.load_state()
    for line in state.lines.values():
        line.approved = line.takes[-1].fingerprint
    project.save_state(state)


# --- gain arithmetic ---------------------------------------------------------------------------


def db_to_gain(db: float) -> float:
    return 10 ** (db / 20)


@pytest.mark.parametrize(
    ("level", "gain"),
    [
        (Level(active_db=TARGET_DB - 6, peak=3000), db_to_gain(6)),  # quiet: raised
        (Level(active_db=TARGET_DB + 6, peak=20000), db_to_gain(-6)),  # loud: lowered
        (Level(active_db=TARGET_DB - 10, peak=20000), PEAK_CEILING / 20000),  # peak ceiling
        (Level(active_db=-90.0, peak=100), db_to_gain(MAX_GAIN_DB)),  # bounded boost
        (Level(active_db=None, peak=0), 1.0),  # silence stays silence
    ],
)
def test_level_gain(level: Level, gain: float) -> None:
    assert level_gain(level) == pytest.approx(gain)


def test_measure_level_ignores_silence(tmp_path: Path) -> None:
    path = tmp_path / "a.wav"
    path.write_bytes(wav_bytes(tone(500, 10000, lead_ms=400)))
    level = measure_level(path)
    assert level.peak == 10000
    assert level.active_db is not None
    assert level.active_db == pytest.approx(20 * math.log10(10000 / math.sqrt(2) / 32768), abs=0.3)
    silent = tmp_path / "s.wav"
    silent.write_bytes(wav_bytes([0] * 2400))
    assert measure_level(silent) == Level(active_db=None, peak=0)


# --- SRT ---------------------------------------------------------------------------------------


def test_srt_time_format() -> None:
    assert format_srt_time(0) == "00:00:00,000"
    assert format_srt_time(3_723_004) == "01:02:03,004"


def test_srt_numbers_entries_and_splits_long_text_into_two_row_blocks() -> None:
    long = " ".join(["parola"] * 30)  # 209 characters: 5 rows of 41, 3 blocks
    text = to_srt(
        [SubtitleEntry(0, 1500, "Anna aprì la finestra."), SubtitleEntry(2000, 4500, long)]
    )
    blocks = text.strip().split("\n\n")
    assert blocks[0] == "1\n00:00:00,000 --> 00:00:01,500\nAnna aprì la finestra."
    assert len(blocks) == 4
    rows = [block.split("\n") for block in blocks[1:]]
    assert [r[0] for r in rows] == ["2", "3", "4"]
    assert [len(r) - 2 for r in rows] == [2, 2, 1]
    assert all(len(row) <= 42 for r in rows for row in r[2:])
    times = [r[1] for r in rows]
    assert times[0].startswith("00:00:02,000 --> ")
    assert times[-1].endswith(" --> 00:00:04,500")
    assert times[0].split(" --> ")[1] == times[1].split(" --> ")[0]  # contiguous
    assert text.endswith("\n")


def test_srt_skips_a_line_without_words() -> None:
    text = to_srt([SubtitleEntry(0, 100, "  "), SubtitleEntry(100, 200, "Sì.")])
    assert text == "1\n00:00:00,100 --> 00:00:00,200\nSì.\n"


# --- export ------------------------------------------------------------------------------------


async def test_export_refuses_lines_without_a_take(project: Project) -> None:
    await render(project, ToneProvider(), lines="p001.1-p002.1")
    with pytest.raises(VoxelloError) as excinfo:
        export(project, allow_unapproved=True)
    assert excinfo.value.code == DIALOGUE_NOT_READY
    assert "3 lines have no current take" in excinfo.value.message
    assert "p003.1" in excinfo.value.message
    assert excinfo.value.details["missing"] == ["p003.1", "p003.2", "p005.1"]
    assert not (project.root / "final.wav").exists()


async def test_export_refuses_lines_whose_approval_an_edit_cleared(project: Project) -> None:
    await rendered(project)
    approve_all(project)
    project.annotate({"p005.1": {"tts_text": "Fino al parco? Davvero?"}})
    await render(project, ToneProvider())
    with pytest.raises(VoxelloError) as excinfo:
        export(project)
    assert excinfo.value.code == DIALOGUE_NOT_READY
    assert excinfo.value.details["unapproved"] == ["p005.1"]
    assert "--allow-unapproved" in excinfo.value.message


async def test_export_writes_audio_and_a_timeline_that_matches_it(project: Project) -> None:
    await rendered(project)
    approve_all(project)
    result = export(project)
    assert result.wav == project.root / "final.wav"
    assert result.srt is None
    assert not (project.root / "final.srt").exists()
    timeline = json.loads(result.timeline.read_text())
    assert result.timeline == project.root / "final.timeline.json"
    with wave.open(str(result.wav), "rb") as wav:
        frames = wav.getnframes()
    assert timeline["frames"] == frames
    assert timeline["sample_rate"] == RATE
    assert timeline["duration_ms"] == round(frames * 1000 / RATE)
    lines = timeline["lines"]
    assert [entry["id"] for entry in lines] == LINES
    assert [entry["speaker"] for entry in lines] == [
        "narratore",
        "narratore",
        "bruno",
        "narratore",
        "anna",
    ]
    assert lines[0]["start_frame"] == 0
    assert lines[-1]["end_frame"] == frames
    pauses = [b["start_frame"] - a["end_frame"] for a, b in itertools.pairwise(lines)]
    assert pauses == [RATE * ms // 1000 for ms in (300, 300, 200, 400)]
    assert all(entry["approved"] for entry in lines)
    assert lines[2]["start"] == pytest.approx(lines[2]["start_frame"] / RATE, abs=0.001)
    assert result.unapproved == 0


async def test_export_evens_loudness_per_line(project: Project) -> None:
    await rendered(project)
    approve_all(project)
    result = export(project)
    timeline = json.loads(result.timeline.read_text())
    samples = read_samples(result.wav)
    for entry in timeline["lines"]:
        chunk = samples[entry["start_frame"] : entry["end_frame"]]
        peak = max(abs(s) for s in chunk)
        # a sine at the target RMS: the quiet narrator and the louder characters meet
        assert 20 * math.log10(peak / math.sqrt(2) / 32768) == pytest.approx(TARGET_DB, abs=0.3)
    assert timeline["lines"][0]["gain_db"] > timeline["lines"][2]["gain_db"]


async def test_export_without_levelling_copies_the_takes(project: Project) -> None:
    await rendered(project)
    approve_all(project)
    result = export(project, level=False)
    timeline = json.loads(result.timeline.read_text())
    first = timeline["lines"][0]
    state = project.load_state()
    take = state.lines["p001.1"].takes[-1]
    original = read_samples(project.take_path("p001.1", take.fingerprint))
    assert read_samples(result.wav)[first["start_frame"] : first["end_frame"]] == original
    assert first["gain_db"] == 0.0


async def test_export_unapproved_on_request_with_srt_to_a_path(
    project: Project, tmp_path: Path
) -> None:
    await rendered(project)
    output = tmp_path / "out" / "book.wav"
    result = export(project, output, allow_unapproved=True, srt=True)
    assert result.wav == output
    assert result.timeline == tmp_path / "out" / "book.timeline.json"
    assert result.srt == tmp_path / "out" / "book.srt"
    assert result.unapproved == len(LINES)
    timeline = json.loads(result.timeline.read_text())
    assert not any(entry["approved"] for entry in timeline["lines"])
    srt = result.srt.read_text(encoding="utf-8")
    assert "Capitolo uno" in srt and "#" not in srt  # headings are read without the marks
    assert "Porto fuori il cane." in srt and "«" not in srt  # quotes are not subtitled
    assert srt.count(" --> ") == len(LINES)


async def test_export_refuses_mixed_formats_and_leaves_nothing(project: Project) -> None:
    provider = ToneProvider()
    await render(project, provider, lines="p001.1-p003.1")
    provider.rate = 16000
    await render(project, provider)
    approve_all(project)
    with pytest.raises(VoxelloError) as excinfo:
        export(project)
    assert excinfo.value.code == DIALOGUE_NOT_READY
    assert "expected" in excinfo.value.message
    assert sorted(p.name for p in project.root.iterdir() if p.name.startswith("final")) == []


async def test_export_skips_dropped_attributions(project: Project) -> None:
    project.annotate({"p003.2": {"attribution": True}})
    cast = {**CAST, "attributions": "drop"}
    project.cast_path.write_text(yaml.safe_dump(cast), encoding="utf-8")
    await rendered(project)
    approve_all(project)
    timeline = json.loads(export(project).timeline.read_text())
    assert [entry["id"] for entry in timeline["lines"]] == ["p001.1", "p002.1", "p003.1", "p005.1"]


def test_measure_level_reads_the_first_channel_and_needs_16_bit(tmp_path: Path) -> None:
    stereo = tmp_path / "stereo.wav"
    left = tone(200, 8000)
    with wave.open(str(stereo), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        interleaved = [s for pair in zip(left, [0] * len(left), strict=True) for s in pair]
        wav.writeframes(array.array("h", interleaved).tobytes())
    assert measure_level(stereo).peak == 8000
    narrow = tmp_path / "narrow.wav"
    with wave.open(str(narrow), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(RATE)
        wav.writeframes(bytes(100))
    with pytest.raises(AudioFormatError, match="16-bit"):
        measure_level(narrow)


async def test_export_refuses_a_take_it_cannot_level(project: Project) -> None:
    await rendered(project)
    approve_all(project)
    take = project.load_state().lines["p002.1"].takes[-1]
    with wave.open(str(project.take_path("p002.1", take.fingerprint)), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(RATE)
        wav.writeframes(bytes(100))
    with pytest.raises(VoxelloError) as excinfo:
        export(project)
    assert excinfo.value.code == DIALOGUE_NOT_READY
    assert excinfo.value.message.startswith("p002.1: ")


async def test_the_timeline_names_registers(project: Project) -> None:
    cast = {**CAST, "characters": {**CAST["characters"]}}
    cast["characters"]["bruno"] = {
        "voice": "clone:bruno",
        "registers": {"whisper": "clone:bruno-whisper"},
    }
    project.cast_path.write_text(yaml.safe_dump(cast), encoding="utf-8")
    project.annotate({"p003.1": {"register": "whisper"}})
    await rendered(project)
    approve_all(project)
    lines = json.loads(export(project).timeline.read_text())["lines"]
    assert lines[2]["register"] == "whisper"
    assert "register" not in lines[0]


async def test_a_failed_write_leaves_no_partial_audio(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rendered(project)
    approve_all(project)

    def full_disk(_path: Path, _data: bytes) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr("voxello.dialogue.export.write_atomic", full_disk)
    with pytest.raises(OSError, match="No space"):
        export(project)
    assert not [p for p in project.root.iterdir() if p.name.startswith("final")]
