"""Conservative source synchronization: plan in memory, confirm in the CLI, then apply.

Matching never uses audio fingerprints as text identity, nor similarity as a transfer rule.
The script is the old logical source even when source.txt has already been overwritten.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from voxello.dialogue.project import LineDefaults, Project, read_source, resolve_line
from voxello.dialogue.project_lock import project_lock
from voxello.dialogue.script import Script, ScriptSegment, check, is_spoken, new_script
from voxello.dialogue.sync_storage import snapshot
from voxello.dialogue.takes import State
from voxello.errors import INVALID_SCRIPT, STORAGE_ERROR, VoxelloError

SegmentKey = tuple[str, str]
ParagraphKey = tuple[SegmentKey, ...]
SEGMENT_ID = re.compile(r"p[0-9]+\.[0-9]+\Z")


@dataclass(frozen=True)
class SyncPlan:
    source: Path
    raw: bytes
    original: dict[str, str]
    old_script: Script
    script: Script
    state: State
    mapping: dict[str, str]  # old -> new
    ambiguous_old: list[str]
    ambiguous_new: list[str]
    statuses: dict[str, str]
    problems: list[str]
    changed: bool
    old_source_changed: bool
    archive_name: str

    def report(self) -> dict[str, object]:
        matched_old, matched_new = set(self.mapping), set(self.mapping.values())
        return {
            "version": 1,
            "mapping": self.mapping,
            "unmatched_old": [
                s.model_dump() for s in self.old_script.segments if s.id not in matched_old
            ],
            "unmatched_new": [
                s.model_dump() for s in self.script.segments if s.id not in matched_new
            ],
            "ambiguous_old": self.ambiguous_old,
            "ambiguous_new": self.ambiguous_new,
            "statuses": self.statuses,
            "problems": self.problems,
            "old_source_changed": self.old_source_changed,
        }


def _key(seg: ScriptSegment) -> SegmentKey:
    return seg.kind, seg.text


def _paragraphs(script: Script) -> dict[ParagraphKey, list[list[ScriptSegment]]]:
    groups: dict[str, list[ScriptSegment]] = defaultdict(list)
    for seg in script.segments:
        groups[seg.id.split(".")[0]].append(seg)
    by_content: dict[ParagraphKey, list[list[ScriptSegment]]] = defaultdict(list)
    for group in groups.values():
        by_content[tuple(_key(seg) for seg in group)].append(group)
    return by_content


def match_segments(old: Script, new: Script) -> tuple[dict[str, str], list[str], list[str]]:
    """Unique intact paragraphs first, then unique remaining exact segments.

    An identical complete sequence needs no identity guesses, including repeated phrases.
    Once a duplicate paragraph is added/removed/moved ambiguously, its ids confer no identity.
    """
    if [(s.id, _key(s)) for s in old.segments] == [(s.id, _key(s)) for s in new.segments]:
        return {s.id: s.id for s in old.segments}, [], []
    mapping: dict[str, str] = {}
    old_paras, new_paras = _paragraphs(old), _paragraphs(new)
    for key, before in old_paras.items():
        after = new_paras.get(key, [])
        if len(before) == len(after) == 1:
            mapping.update((a.id, b.id) for a, b in zip(before[0], after[0], strict=True))
    used = set(mapping.values())
    old_left = [s for s in old.segments if s.id not in mapping]
    new_left = [s for s in new.segments if s.id not in used]
    old_count, new_count = Counter(map(_key, old_left)), Counter(map(_key, new_left))
    new_unique = {_key(s): s.id for s in new_left if new_count[_key(s)] == 1}
    for seg in old_left:
        key = _key(seg)
        if old_count[key] == new_count[key] == 1:
            mapping[seg.id] = new_unique[key]
    ambiguous = {
        k for k in old_count.keys() & new_count.keys() if old_count[k] > 1 or new_count[k] > 1
    }
    return (
        mapping,
        [s.id for s in old_left if _key(s) in ambiguous],
        [s.id for s in new_left if _key(s) in ambiguous],
    )


def plan_sync(
    project: Project,
    source: Path,
    defaults: LineDefaults | None = None,
) -> SyncPlan:
    """Read a consistent snapshot without changing any project files."""
    with project_lock(project.root):
        Project.open(project.root)
        try:
            return _plan(project, source, defaults or LineDefaults())
        except OSError as exc:
            raise VoxelloError(STORAGE_ERROR, f"Cannot plan sync: {exc}") from exc


def _plan(project: Project, source: Path, defaults: LineDefaults) -> SyncPlan:
    original = snapshot(project.root)
    raw, text = read_source(source)
    old = project.load_script()
    ids = [s.id for s in old.segments]
    if len(set(ids)) != len(ids) or any(not SEGMENT_ID.fullmatch(sid) for sid in ids):
        raise VoxelloError(
            INVALID_SCRIPT, "Sync requires unique, valid segment ids in script.yaml."
        )
    candidate = new_script(text, max_chars=old.max_chars)
    mapping, ambiguous_old, ambiguous_new = match_segments(old, candidate)
    by_old = {seg.id: seg for seg in old.segments}
    by_new = {new: by_old[before] for before, new in mapping.items()}
    candidate.segments = [
        by_new[s.id].model_copy(deep=True, update={"id": s.id}) if s.id in by_new else s
        for s in candidate.segments
    ]
    before = project.load_state()
    after = State()
    after.lines = {
        new: before.lines[old_id].model_copy(deep=True)
        for old_id, new in mapping.items()
        if old_id in before.lines
    }
    after.review.position = mapping.get(before.review.position or "")
    cast = project.load_cast()
    report = check(candidate, text, cast, defaults.designs)
    bad = {issue.segment_id for issue in report.errors}
    statuses: dict[str, str] = {}
    new_to_old = {new: old_id for old_id, new in mapping.items()}
    for seg in candidate.segments:
        if seg.kind == "break" or seg.skip or (cast is not None and not is_spoken(seg, cast)):
            statuses[seg.id] = "not spoken"
        elif cast is None or seg.id in bad or None in bad:
            statuses[seg.id] = "annotation/check needed"
        else:
            line = resolve_line(seg, cast, defaults)
            entry = after.lines.get(seg.id)
            current = (
                []
                if entry is None
                else [
                    take
                    for take in entry.current_takes(line.request)
                    if project.has_audio(new_to_old[seg.id], take)
                ]
            )
            if not current:
                statuses[seg.id] = "render needed"
            elif entry is not None and entry.approved in {t.fingerprint for t in current}:
                statuses[seg.id] = "approved"
            else:
                statuses[seg.id] = "review needed"
    if snapshot(project.root) != original or source.read_bytes() != raw:
        raise VoxelloError(STORAGE_ERROR, "Project or source changed while planning; retry sync.")
    return SyncPlan(
        source.absolute(),
        raw,
        original,
        old,
        candidate,
        after,
        mapping,
        ambiguous_old,
        ambiguous_new,
        statuses,
        [str(i) for i in report.errors],
        changed=(candidate != old or project.source_path.read_bytes() != raw),
        old_source_changed=original.get("source.txt") != old.source_sha256,
        archive_name=uuid4().hex,
    )


def format_plan(plan: SyncPlan, root: Path) -> list[str]:
    if not plan.changed:
        return ["No changes; project files are unchanged."]
    new_ids = set(plan.mapping.values())
    removed = [s for s in plan.old_script.segments if s.id not in plan.mapping]
    added = [s for s in plan.script.segments if s.id not in new_ids]
    out = [
        f"Sync: {len(plan.mapping)} matched, {len(added)} new/unmatched, "
        f"{len(removed)} old/unmatched."
    ]
    by_new = {seg.id: seg for seg in plan.script.segments}
    for old, new in plan.mapping.items():
        seg = by_new[new]
        custom = " (custom pronunciation kept)" if seg.tts_text is not None else ""
        out.append(f"  {old} -> {new}: {plan.statuses[new]}{custom} | {seg.text}")
    for seg in added:
        label = "ambiguous" if seg.id in plan.ambiguous_new else "new/unmatched"
        out.append(f"  + {seg.id} {label}: {plan.statuses[seg.id]} | {seg.text}")
    for seg in removed:
        label = "ambiguous" if seg.id in plan.ambiguous_old else "removed/changed/unmatched"
        out.append(f"  - {seg.id} {label}: archived | {seg.text}")
        if seg.tts_text is not None:
            out.append(f"    Custom pronunciation not transferred; archived: {seg.tts_text}")
    if plan.old_source_changed:
        out.append(
            "source.txt differs from the old script hash: old source bytes cannot be "
            "verified/recovered; the old script preserves the logical text."
        )
    old_position = plan.state.review.position
    out.append(f"Review position after sync: {old_position or 'reset (start at first pending)'}.")
    out.extend(f"  Check: {problem}" for problem in plan.problems)
    out.append(
        f"Recovery copy (all old takes and annotations): "
        f"{root / 'sync-backups' / plan.archive_name / 'before'}"
    )
    out.append("Existing exports are not updated; export again after render/review.")
    return out
