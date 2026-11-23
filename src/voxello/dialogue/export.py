"""``dialogue export``: the approved takes joined into one WAV (roadmap 8.6).

Each line's take (the approved one; the chosen one only with ``allow_unapproved``) is joined
with the cast's pauses (``script.pause_between``). Loudness is evened per line: the RMS of the
windows that are not silence is brought to ``TARGET_DB`` with a gain bounded by
``MAX_GAIN_DB`` and by a peak ceiling, so short lines that OmniVoice renders quietly (a
narrator's "chiese Francesca.") sit at the level of the rest. Next to the WAV go a timeline
(id, speaker, start and end in seconds and frames, from the frames actually written) and,
optionally, SRT subtitles of the source words without quote marks.

Every file is written to a temporary sibling first, so a failed export leaves no partial
output.
"""

from __future__ import annotations

import array
import contextlib
import json
import math
import os
import sys
import textwrap
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from voxello.dialogue import qa
from voxello.dialogue.audio import AudioFormatError, Clip, Silence, concatenate
from voxello.dialogue.project import LineDefaults, Pick, Project, write_atomic
from voxello.dialogue.script import pause_between
from voxello.dialogue.segment import default_tts_text
from voxello.errors import DIALOGUE_NOT_READY, VoxelloError

TARGET_DB = -20.0  # RMS of speech, silence excluded (OmniVoice takes have a median near -20)
PEAK_CEILING = round(qa.FULL_SCALE * 10 ** (-1 / 20))  # -1 dBFS
MAX_GAIN_DB = 15.0
SRT_WIDTH = 42  # characters per subtitle row
SRT_ROWS = 2
TIMELINE_VERSION = 1
LISTED_IDS = 10  # ids named in a refusal; the rest are counted


@dataclass(frozen=True)
class Level:
    active_db: float | None  # RMS of the windows louder than qa.SILENCE_DB; None if all silent
    peak: int


@dataclass(frozen=True)
class SubtitleEntry:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class ExportResult:
    wav: Path
    timeline: Path
    srt: Path | None
    lines: int
    unapproved: int
    duration_ms: int


# --- levels ------------------------------------------------------------------------------------


def measure_level(path: Path) -> Level:
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise AudioFormatError(f"{path.name}: levelling needs 16-bit samples")
        rate = wav.getframerate()
        channels = wav.getnchannels()
        samples = array.array("h", wav.readframes(wav.getnframes()))
    if sys.byteorder == "big":  # pragma: no cover - WAV is little-endian
        samples.byteswap()
    if channels > 1:
        samples = samples[::channels]
    window = max(1, rate * qa.WINDOW_MS // 1000)
    energy = 0
    count = 0
    threshold = qa.FULL_SCALE**2 * 10 ** (qa.SILENCE_DB / 10)
    for start in range(0, len(samples), window):
        chunk = samples[start : start + window]
        chunk_energy = sum(s * s for s in chunk)
        if chunk_energy / len(chunk) > threshold:
            energy += chunk_energy
            count += len(chunk)
    peak = max(max(samples), -min(samples)) if samples else 0
    if not count:
        return Level(active_db=None, peak=peak)
    return Level(active_db=10 * math.log10(energy / count / qa.FULL_SCALE**2), peak=peak)


def level_gain(level: Level) -> float:
    """The factor that brings a line to ``TARGET_DB``, never above ``MAX_GAIN_DB`` and never
    pushing the peak over ``PEAK_CEILING``; 1.0 for a silent take."""
    if level.active_db is None or level.peak == 0:
        return 1.0
    db = min(TARGET_DB - level.active_db, MAX_GAIN_DB)
    return min(10 ** (db / 20), PEAK_CEILING / level.peak)


# --- subtitles ---------------------------------------------------------------------------------


def format_srt_time(ms: int) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def to_srt(entries: Sequence[SubtitleEntry]) -> str:
    """Numbered blocks of at most ``SRT_ROWS`` rows of ``SRT_WIDTH`` characters; a long line
    is split into several blocks, its time shared in proportion to their characters."""
    blocks = []
    for entry in entries:
        rows = textwrap.wrap(" ".join(entry.text.split()), width=SRT_WIDTH)
        chunks = [rows[i : i + SRT_ROWS] for i in range(0, len(rows), SRT_ROWS)]
        total = sum(len(row) for row in rows)  # 0 for a line with no words: no block
        start, done = entry.start_ms, 0
        for chunk in chunks:
            done += sum(len(row) for row in chunk)
            end = entry.start_ms + round((entry.end_ms - entry.start_ms) * done / total)
            blocks.append((start, end, "\n".join(chunk)))
            start = end
    return "\n".join(
        f"{n}\n{format_srt_time(a)} --> {format_srt_time(b)}\n{text}\n"
        for n, (a, b, text) in enumerate(blocks, start=1)
    )


# --- export ------------------------------------------------------------------------------------


def outputs(wav: Path) -> tuple[Path, Path]:
    """The timeline and subtitle paths that go with ``wav``."""
    return wav.with_suffix(".timeline.json"), wav.with_suffix(".srt")


def _listed(ids: Sequence[str]) -> str:
    shown = ", ".join(ids[:LISTED_IDS])
    more = len(ids) - LISTED_IDS
    return f"{shown} and {more} more" if more > 0 else shown


def _plural(count: int) -> str:
    return f"{count} line has" if count == 1 else f"{count} lines have"


def picks_for_export(
    project: Project, defaults: LineDefaults | None, *, allow_unapproved: bool
) -> list[Pick]:
    state = project.load_state()
    picks = [project.pick(state, line) for line in project.lines(defaults)]
    missing = [p.line.segment_id for p in picks if p.take is None]
    if missing:
        raise VoxelloError(
            DIALOGUE_NOT_READY,
            f"{_plural(len(missing))} no current take ({_listed(missing)}): run `voxello "
            "dialogue render` first.",
            details={"missing": missing},
        )
    unapproved = [p.line.segment_id for p in picks if not p.approved]
    if unapproved and not allow_unapproved:
        raise VoxelloError(
            DIALOGUE_NOT_READY,
            f"{_plural(len(unapproved))} no approved take ({_listed(unapproved)}): "
            "approve them with `voxello dialogue review --pending`, or pass "
            "--allow-unapproved to export the chosen takes.",
            details={"unapproved": unapproved},
        )
    return picks


def export(
    project: Project,
    output: Path | None = None,
    *,
    defaults: LineDefaults | None = None,
    allow_unapproved: bool = False,
    srt: bool = False,
    level: bool = True,
) -> ExportResult:
    picks = picks_for_export(project, defaults, allow_unapproved=allow_unapproved)
    script = project.load_script()
    cast = project.load_cast()
    assert cast is not None  # Project.lines refuses a project without one
    wav_path = output or project.root / "final.wav"
    timeline_path, srt_path = outputs(wav_path)

    parts: list[Clip | Silence] = []
    gains: list[float] = []
    for index, pick in enumerate(picks):
        assert pick.take is not None  # picks_for_export refuses missing takes
        path = project.take_path(pick.line.segment_id, pick.take.fingerprint)
        if index:
            previous = picks[index - 1].line.segment_id
            parts.append(Silence(pause_between(script, cast, previous, pick.line.segment_id)))
        try:
            gain = level_gain(measure_level(path)) if level else 1.0
        except AudioFormatError as exc:
            raise VoxelloError(DIALOGUE_NOT_READY, f"{pick.line.segment_id}: {exc}") from exc
        gains.append(gain)
        parts.append(Clip(path, gain))

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav_path.with_name(wav_path.name + ".tmp")
    try:
        joined = concatenate(parts, tmp)
    except AudioFormatError as exc:
        raise VoxelloError(
            DIALOGUE_NOT_READY,
            f"Takes in different formats cannot be joined ({exc}); render the odd lines again "
            "with `voxello dialogue render --force --lines ID`.",
        ) from exc

    def seconds(frame: int) -> float:
        return round(frame / joined.rate, 3)

    def ms(frame: int) -> int:
        return round(frame * 1000 / joined.rate)

    entries = []
    subtitles = []
    for pick, gain, (start, end) in zip(picks, gains, joined.spans, strict=True):
        seg = script.segment(pick.line.segment_id)
        assert pick.take is not None
        entry: dict[str, object] = {
            "id": seg.id,
            "speaker": pick.line.speaker,
            "start": seconds(start),
            "end": seconds(end),
            "start_frame": start,
            "end_frame": end,
            "approved": pick.approved,
            "take": pick.take.fingerprint[:16],
            "gain_db": round(20 * math.log10(gain), 1),
        }
        if seg.register_:
            entry["register"] = seg.register_
        entries.append(entry)
        subtitles.append(SubtitleEntry(ms(start), ms(end), default_tts_text(seg.kind, seg.text)))
    timeline = {
        "version": TIMELINE_VERSION,
        "audio": wav_path.name,
        "sample_rate": joined.rate,
        "frames": joined.frames,
        "duration_ms": ms(joined.frames),
        "lines": entries,
    }
    try:
        write_atomic(
            timeline_path,
            (json.dumps(timeline, indent=1, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        if srt:
            write_atomic(srt_path, to_srt(subtitles).encode("utf-8"))
        os.replace(tmp, wav_path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return ExportResult(
        wav=wav_path,
        timeline=timeline_path,
        srt=srt_path if srt else None,
        lines=len(picks),
        unapproved=sum(1 for p in picks if not p.approved),
        duration_ms=ms(joined.frames),
    )
