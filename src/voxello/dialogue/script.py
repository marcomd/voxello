"""``script.yaml``: the segments of the source and the agent's annotations (roadmap 8.3).

``id``, ``kind`` and ``text`` come from the segmenter and never change; the agent sets the
other fields through ``dialogue annotate`` (partial updates by id). ``tts_text`` is what is
spoken; when unset it is derived from ``text`` (``segment.default_tts_text``). ``check``
proves the segments rebuild ``source.txt`` modulo whitespace, so no sentence can be lost or
paraphrased, and validates the annotations against the cast.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from voxello.dialogue.cast import Cast, resolve_spec, validation_error
from voxello.dialogue.segment import (
    DEFAULT_MAX_CHARS,
    SegmentKind,
    default_tts_text,
    normalize_source,
    segment_text,
)
from voxello.errors import INVALID_SCRIPT, VoxelloError
from voxello.tts.base import SPEED_RANGE

# Non-verbal tags that work in Italian at the start of a line (decision 0003, spike results).
ALLOWED_TAGS = frozenset(
    {
        "sigh",
        "laughter",
        "confirmation-en",
        "question-ah",
        "question-oh",
        "surprise-ah",
        "surprise-oh",
        "dissatisfaction-hnn",
    }
)
TAG_RE = re.compile(r"\[([a-z][a-z-]*)\]")
LEADING_TAGS_RE = re.compile(r"^(?:\s*\[[a-z][a-z-]*\])*")
ANNOTATABLE = ("speaker", "register", "tts_text", "speed", "attribution", "skip")
FIXED = ("id", "text")
FIELD_NAMES = {"register": "register_"}
WHITESPACE_RE = re.compile(r"\s+")


class ScriptSegment(BaseModel):
    # `register` is spelled `register_` in Python: BaseModel already has a `register` method.
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    id: str
    kind: SegmentKind
    text: str
    speaker: str | None = None
    register_: str | None = Field(default=None, alias="register")
    tts_text: str | None = None
    speed: float | None = Field(default=None, ge=SPEED_RANGE[0], le=SPEED_RANGE[1])
    attribution: bool = False  # narration that only says who speaks ("disse Marco.")
    skip: bool = False  # never spoken (a scene title's point-of-view marker, a note)


class Script(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    source_sha256: str
    max_chars: int = Field(default=DEFAULT_MAX_CHARS, gt=0)
    segments: list[ScriptSegment]

    def to_yaml(self) -> str:
        data = {
            "version": self.version,
            "source_sha256": self.source_sha256,
            "max_chars": self.max_chars,
            "segments": [s.model_dump(exclude_defaults=True) for s in self.segments],
        }
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1_000_000)

    @classmethod
    def from_yaml(cls, text: str) -> Script:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise VoxelloError(INVALID_SCRIPT, f"script.yaml is not valid YAML: {exc}") from exc
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise validation_error("script.yaml", exc) from exc

    def segment(self, seg_id: str) -> ScriptSegment:
        for seg in self.segments:
            if seg.id == seg_id:
                return seg
        raise VoxelloError(INVALID_SCRIPT, f"{seg_id}: unknown segment id")


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def new_script(source: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> Script:
    result = segment_text(source, max_chars=max_chars)
    return Script(
        source_sha256=source_hash(source),
        max_chars=max_chars,
        segments=[ScriptSegment(id=s.id, kind=s.kind, text=s.text) for s in result.segments],
    )


# --- annotations ----------------------------------------------------------------------------


@dataclass(frozen=True)
class AnnotationResult:
    script: Script
    changed: list[str]


def apply_annotations(script: Script, data: object) -> AnnotationResult:
    """Merge ``{id: {field: value}}`` into a copy of ``script``; ``null`` resets a field.

    All or nothing: any unknown id or field, any change to ``text`` and any invalid value
    rejects the whole annotation, with every problem and its id in the message.
    """
    if not isinstance(data, dict):
        raise VoxelloError(
            INVALID_SCRIPT, "An annotation is a mapping of segment ids to fields to set."
        )
    merged = script.model_copy(deep=True)
    index = {seg.id: i for i, seg in enumerate(merged.segments)}
    problems: list[str] = []
    changed: list[str] = []
    for raw_id, fields in data.items():
        seg_id = str(raw_id)
        if seg_id not in index:
            problems.append(f"{seg_id}: unknown segment id")
            continue
        if not isinstance(fields, dict):
            problems.append(f"{seg_id}: expected a mapping of fields")
            continue
        current = merged.segments[index[seg_id]]
        values = current.model_dump()
        bad = False
        for name, value in fields.items():
            if name in FIXED:
                if value != values[name]:
                    problems.append(f"{seg_id}: `{name}` cannot change")
                    bad = True
            elif name == "kind":
                problems.append(f"{seg_id}: `kind` cannot be annotated")
                bad = True
            elif name not in ANNOTATABLE:
                problems.append(f"{seg_id}: unknown field `{name}`")
                bad = True
            else:
                default = ScriptSegment.model_fields[FIELD_NAMES.get(name, name)].default
                values[name] = default if value is None else value
        if bad:
            continue
        try:
            updated = ScriptSegment.model_validate(values)
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"])
                problems.append(f"{seg_id}: {location}: {error['msg']}")
            continue
        if updated != current:
            merged.segments[index[seg_id]] = updated
            changed.append(seg_id)
    if problems:
        raise VoxelloError(
            INVALID_SCRIPT,
            "Annotation rejected, nothing applied: " + "; ".join(problems),
            details={"problems": problems},
        )
    return AnnotationResult(merged, changed)


# --- effective values -----------------------------------------------------------------------


def effective_tts_text(seg: ScriptSegment) -> str:
    return seg.tts_text if seg.tts_text is not None else default_tts_text(seg.kind, seg.text)


def speaker_of(seg: ScriptSegment, cast: Cast | None) -> str | None:
    """Who reads the segment: its speaker, the narrator for narration and headings, or
    ``None`` (a break, or speech nobody has been assigned to yet)."""
    if seg.kind == "break":
        return None
    if seg.speaker is not None:
        return seg.speaker
    if seg.kind in ("narration", "heading") and cast is not None:
        return cast.narrator
    return None


def is_spoken(seg: ScriptSegment, cast: Cast) -> bool:
    """False for breaks, skipped segments and, with ``attributions: drop``, attributions."""
    if seg.kind == "break" or seg.skip:
        return False
    return not (seg.attribution and cast.attributions == "drop")


# --- check ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    segment_id: str | None
    message: str

    def __str__(self) -> str:
        return f"{self.segment_id}: {self.message}" if self.segment_id else self.message


@dataclass
class CheckReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check(script: Script, source: str, cast: Cast | None, designs: dict[str, str]) -> CheckReport:
    """Validate the script against its source and, when there is one, the cast."""
    report = CheckReport()
    if source_hash(source) != script.source_sha256:
        report.errors.append(Issue(None, "source.txt changed after `dialogue init`"))
    _check_rebuild(script, source, report)
    seen: set[str] = set()
    for seg in script.segments:
        if seg.id in seen:
            report.errors.append(Issue(seg.id, "duplicate id"))
        seen.add(seg.id)
        _check_segment(seg, cast, report)
    if cast is None:
        report.warnings.append(
            Issue(None, "cast.yaml is missing: speakers, registers and voices are not checked")
        )
    else:
        _check_voices(cast, designs, report)
    return report


def _squash(text: str) -> str:
    return WHITESPACE_RE.sub("", text)


def _check_rebuild(script: Script, source: str, report: CheckReport) -> None:
    expected = _squash(normalize_source(source))
    position = 0
    for seg in script.segments:
        piece = _squash(seg.text)
        if not expected.startswith(piece, position):
            report.errors.append(Issue(seg.id, "the segments do not rebuild source.txt from here"))
            return
        position += len(piece)
    if position < len(expected):
        report.errors.append(Issue(None, "the segments end before source.txt does"))


def _check_segment(seg: ScriptSegment, cast: Cast | None, report: CheckReport) -> None:
    if seg.attribution and seg.kind != "narration":
        report.errors.append(Issue(seg.id, "attribution applies to narration only"))
    if seg.kind == "break" or seg.skip:
        return
    if seg.kind == "speech" and seg.speaker is None:
        report.errors.append(Issue(seg.id, "speech has no speaker"))
    tts_text = effective_tts_text(seg)
    if not tts_text.strip():
        report.errors.append(Issue(seg.id, "tts_text is empty"))
    _check_tags(seg.id, tts_text, report)
    if cast is None:
        return
    speaker = speaker_of(seg, cast)
    if speaker is not None and speaker not in cast.characters:
        report.errors.append(Issue(seg.id, f"speaker '{speaker}' is not in cast.yaml"))
    elif speaker is not None and seg.register_ is not None:
        if seg.register_ not in cast.characters[speaker].registers:
            report.warnings.append(
                Issue(
                    seg.id,
                    f"register '{seg.register_}' is not in the cast for {speaker}; "
                    "the main voice is used",
                )
            )


def _check_tags(seg_id: str, tts_text: str, report: CheckReport) -> None:
    leading_end = LEADING_TAGS_RE.match(tts_text)
    start_of_text = leading_end.end() if leading_end else 0
    for match in TAG_RE.finditer(tts_text):
        tag = match.group(1)
        if tag not in ALLOWED_TAGS:
            allowed = ", ".join(sorted(ALLOWED_TAGS))
            report.errors.append(Issue(seg_id, f"tag [{tag}] is not allowed; use one of {allowed}"))
        elif match.start() >= start_of_text:
            report.warnings.append(
                Issue(
                    seg_id,
                    f"tag [{tag}] is not at the start of the line, where it is most reliable",
                )
            )


def _check_voices(cast: Cast, designs: dict[str, str], report: CheckReport) -> None:
    for name, character in cast.characters.items():
        specs = [(name, character), *((f"{name}.{r}", s) for r, s in character.registers.items())]
        for label, spec in specs:
            try:
                resolve_spec(spec, designs)
            except VoxelloError as exc:
                report.errors.append(Issue(None, f"{label}: {exc.message}"))


# --- ranges ---------------------------------------------------------------------------------


def select_range(
    script: Script,
    spec: str | None,
    *,
    start: str | None = None,
    count: int | None = None,
) -> list[ScriptSegment]:
    """Segments named by ``spec`` (``ID``, ``A-B``, ``A-`` or ``-B``), then from ``start`` on,
    at most ``count`` of them."""
    ids = [seg.id for seg in script.segments]

    def position(seg_id: str) -> int:
        try:
            return ids.index(seg_id)
        except ValueError:
            raise VoxelloError(INVALID_SCRIPT, f"{seg_id}: unknown segment id") from None

    first, last = 0, len(ids) - 1
    if spec:
        if "-" in spec:
            left, right = spec.split("-", 1)
            first = position(left) if left else 0
            last = position(right) if right else len(ids) - 1
        else:
            first = last = position(spec)
        if first > last:
            raise VoxelloError(INVALID_SCRIPT, f"{spec}: the range ends before it starts")
    if start:
        first = max(first, position(start))
    chosen = script.segments[first : last + 1]
    return chosen[:count] if count is not None else chosen
