"""A dialogue project directory (roadmap 8.3-8.4)::

    chapter1/
      source.txt     # byte copy of the input, updated only by explicit sync
      script.yaml    # segments and annotations
      cast.yaml      # characters, voices, registers, generation values, pauses
      state.json     # machine-owned: takes per line and the approved fingerprint
      takes/<id>/<fingerprint[:16]>.wav

Every file is written atomically (a temporary sibling, then ``os.replace``), so an interrupted
command never leaves a half-written script, state or take. Multi-file source synchronization
adds a project lock, backup and recovery journal (``sync_storage``).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from voxello.dialogue import qa
from voxello.dialogue.cast import Cast, resolve_line_voice
from voxello.dialogue.project_lock import project_lock
from voxello.dialogue.script import (
    AnnotationResult,
    CheckReport,
    Script,
    ScriptSegment,
    apply_annotations,
    check,
    effective_tts_text,
    is_spoken,
    new_script,
    speaker_of,
)
from voxello.dialogue.segment import DEFAULT_MAX_CHARS
from voxello.dialogue.takes import LineRequest, LineState, Signal, State, Take, fingerprint
from voxello.errors import INVALID_SCRIPT, STORAGE_ERROR, VoxelloError

TAKE_NAME_CHARS = 16
SYNC_JOURNAL = ".sync-transaction.json"


def _ensure_recovered(root: Path) -> None:
    marker = root / SYNC_JOURNAL
    if marker.exists() or marker.is_symlink():
        raise VoxelloError(
            STORAGE_ERROR,
            "An interrupted sync needs recovery; run `voxello dialogue sync --recover "
            f"-p {root}` before using this project.",
        )


@dataclass(frozen=True)
class LineDefaults:
    """Values from config.yaml that apply when the cast does not set them."""

    designs: dict[str, str] = field(default_factory=dict)  # speech.voice_designs
    speed: float | None = None
    num_step: int | None = None
    guidance_scale: float | None = None


@dataclass(frozen=True)
class Line:
    segment_id: str
    speaker: str
    request: LineRequest


@dataclass(frozen=True)
class Pick:
    """The take a play-through or an export uses for a line."""

    line: Line
    take: Take | None  # the approved take if it is current, else the chosen current one
    approved: bool


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def read_source(source: Path) -> tuple[bytes, str]:
    """Read the exact input bytes and UTF-8 text for both init and sync."""
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise VoxelloError(INVALID_SCRIPT, f"Cannot read {source}: {exc}") from exc
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VoxelloError(INVALID_SCRIPT, f"{source} is not UTF-8 text.") from exc


def resolve_line(seg: ScriptSegment, cast: Cast, defaults: LineDefaults) -> Line:
    """Resolve one validated, spoken segment, also usable by an in-memory sync preview."""
    speaker = speaker_of(seg, cast)
    assert speaker is not None
    generation = cast.generation
    choice = resolve_line_voice(cast, speaker, seg.register_, defaults.designs)
    speed = next(
        (
            value
            for value in (
                seg.speed,
                cast.characters[speaker].speed,
                generation.speed,
                defaults.speed,
            )
            if value is not None
        ),
        None,
    )
    return Line(
        seg.id,
        speaker,
        LineRequest(
            tts_text=effective_tts_text(seg),
            voice=choice.voice,
            instructions=choice.instructions,
            language=cast.language,
            speed=speed,
            num_step=generation.num_step if generation.num_step is not None else defaults.num_step,
            seed=generation.seed,
            guidance_scale=defaults.guidance_scale,
        ),
    )


def wav_duration_ms(audio: bytes) -> int | None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            return round(wav.getnframes() * 1000 / wav.getframerate())
    except (wave.Error, EOFError):
        return None


class Project:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.source_path = root / "source.txt"
        self.script_path = root / "script.yaml"
        self.cast_path = root / "cast.yaml"
        self.state_path = root / "state.json"
        self.takes_dir = root / "takes"

    # --- lifecycle ------------------------------------------------------------------------

    @classmethod
    def init(cls, source: Path, root: Path, *, max_chars: int = DEFAULT_MAX_CHARS) -> Project:
        ownership = project_lock(root) if root.is_dir() else contextlib.nullcontext()
        with ownership:
            _ensure_recovered(root)
            return cls._init(source, root, max_chars=max_chars)

    @classmethod
    def _init(cls, source: Path, root: Path, *, max_chars: int) -> Project:
        project = cls(root)
        if project.script_path.exists():
            raise VoxelloError(
                INVALID_SCRIPT,
                f"{root} already holds a dialogue project; choose another directory.",
            )
        raw, text = read_source(source)
        root.mkdir(parents=True, exist_ok=True)
        write_atomic(project.source_path, raw)
        project.save_script(new_script(text, max_chars=max_chars))
        return project

    @classmethod
    def open(cls, root: Path) -> Project:
        project = cls(root)
        _ensure_recovered(root)
        if not project.script_path.is_file():
            raise VoxelloError(
                INVALID_SCRIPT,
                f"{root} is not a dialogue project (no script.yaml); run `voxello dialogue "
                "init SOURCE` first.",
            )
        return project

    # --- files ----------------------------------------------------------------------------

    def load_source(self) -> str:
        return self.source_path.read_bytes().decode("utf-8")

    def load_script(self) -> Script:
        return Script.from_yaml(self.script_path.read_text(encoding="utf-8"))

    def save_script(self, script: Script) -> None:
        write_atomic(self.script_path, script.to_yaml().encode("utf-8"))

    def load_cast(self) -> Cast | None:
        if not self.cast_path.is_file():
            return None
        return Cast.from_yaml(self.cast_path.read_text(encoding="utf-8"))

    def load_state(self) -> State:
        if not self.state_path.is_file():
            return State()
        try:
            return State.model_validate_json(self.state_path.read_bytes())
        except ValidationError as exc:
            raise VoxelloError(
                STORAGE_ERROR, f"{self.state_path} is damaged ({exc.error_count()} problems)."
            ) from exc

    def save_state(self, state: State) -> None:
        data = json.dumps(state.model_dump(), indent=1, ensure_ascii=False) + "\n"
        write_atomic(self.state_path, data.encode("utf-8"))

    def take_path(self, segment_id: str, take_fingerprint: str) -> Path:
        return self.takes_dir / segment_id / f"{take_fingerprint[:TAKE_NAME_CHARS]}.wav"

    def store_take(self, state: State, segment_id: str, request: LineRequest, audio: bytes) -> Take:
        """Write the audio and record the take in ``state`` (not saved: the caller does)."""
        take_fp = fingerprint(request)
        write_atomic(self.take_path(segment_id, take_fp), audio)
        measures = qa.measure(audio)
        take = Take(
            fingerprint=take_fp,
            seed=request.seed,
            duration_ms=wav_duration_ms(audio),
            created=datetime.now(UTC).isoformat(timespec="seconds"),
            qa=Signal.from_measures(measures) if measures is not None else None,
        )
        line = state.lines.setdefault(segment_id, LineState())
        line.takes = [t for t in line.takes if t.fingerprint != take_fp] + [take]
        return take

    def has_audio(self, segment_id: str, take: Take) -> bool:
        return self.take_path(segment_id, take.fingerprint).is_file()

    def pick(self, state: State, line: Line) -> Pick:
        entry = state.lines.get(line.segment_id)
        if entry is None:
            return Pick(line, None, approved=False)
        current = [
            t for t in entry.current_takes(line.request) if self.has_audio(line.segment_id, t)
        ]
        approved = next((t for t in current if t.fingerprint == entry.approved), None)
        if approved is not None:
            return Pick(line, approved, approved=True)
        return Pick(line, entry.chosen(current), approved=False)

    def ensure_measures(self, segment_id: str, take: Take) -> bool:
        """Measure a take stored without (or with outdated) measures; True if ``take`` changed."""
        if take.qa is not None and take.qa.current:
            return False
        measures = qa.measure(self.take_path(segment_id, take.fingerprint))
        if measures is None:
            return False
        take.qa = Signal.from_measures(measures)
        return True

    # --- script operations ----------------------------------------------------------------

    def annotate(self, data: object) -> AnnotationResult:
        result = apply_annotations(self.load_script(), data)
        if result.changed:
            self.save_script(result.script)
        return result

    def edit_line(
        self, segment_id: str, fields: dict[str, object], designs: dict[str, str] | None = None
    ) -> list[str]:
        """Annotate one line from review; nothing is saved when the change breaks that line.

        Returns the problems (empty when saved): an invalid value, or a ``check`` error on the
        line, such as a tag outside the allowed list, which would otherwise block every
        command that needs a valid project.
        """
        try:
            result = apply_annotations(self.load_script(), {segment_id: fields})
        except VoxelloError as exc:
            return [str(p) for p in exc.details.get("problems", [exc.message])]
        report = check(result.script, self.load_source(), self.load_cast(), designs or {})
        problems = [str(issue) for issue in report.errors if issue.segment_id == segment_id]
        if problems:
            return problems
        if result.changed:
            self.save_script(result.script)
        return []

    def check(self, designs: dict[str, str] | None = None) -> CheckReport:
        return check(self.load_script(), self.load_source(), self.load_cast(), designs or {})

    def lines(self, defaults: LineDefaults | None = None) -> list[Line]:
        """The spoken lines with their resolved requests; refuses a project `check` rejects."""
        defaults = defaults or LineDefaults()
        cast = self.load_cast()
        if cast is None:
            raise VoxelloError(
                INVALID_SCRIPT, f"{self.cast_path} is missing: write the cast before rendering."
            )
        script = self.load_script()
        report = check(script, self.load_source(), cast, defaults.designs)
        if not report.ok:
            problems = "; ".join(str(issue) for issue in report.errors)
            raise VoxelloError(INVALID_SCRIPT, f"`dialogue check` fails: {problems}")
        return [
            resolve_line(seg, cast, defaults) for seg in script.segments if is_spoken(seg, cast)
        ]
