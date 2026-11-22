"""``dialogue render``: one take per line that has no current take (roadmap 8.4).

Lines are synthesized one at a time (the server serializes them anyway) and ``state.json`` is
saved after each, so an interrupted render resumes where it stopped and a second render with
no edits calls the provider zero times. An approval that no longer matches the line's inputs
is cleared.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from voxello.dialogue.project import LineDefaults, Project
from voxello.dialogue.script import select_range
from voxello.errors import VoxelloError
from voxello.tts.base import TTSProvider


@dataclass(frozen=True)
class RenderEvent:
    segment_id: str
    index: int  # 1-based position among the selected lines
    total: int
    reused: bool
    duration_ms: int | None = None


@dataclass(frozen=True)
class RenderSummary:
    rendered: int
    reused: int


async def render(
    project: Project,
    provider: TTSProvider,
    *,
    lines: str | None = None,
    force: bool = False,
    defaults: LineDefaults | None = None,
    progress: Callable[[RenderEvent], None] | None = None,
) -> RenderSummary:
    all_lines = project.lines(defaults)
    wanted = {seg.id for seg in select_range(project.load_script(), lines)}
    selected = [line for line in all_lines if line.segment_id in wanted]
    state = project.load_state()
    rendered = reused = 0
    for index, line in enumerate(selected, start=1):
        entry = state.lines.get(line.segment_id)
        current = []
        if entry is not None:
            current = [
                t
                for t in entry.current_takes(line.request)
                if project.has_audio(line.segment_id, t)
            ]
            if entry.approved not in {t.fingerprint for t in current}:
                entry.approved = None
        if current and not force:
            reused += 1
            event = RenderEvent(line.segment_id, index, len(selected), True, current[0].duration_ms)
        else:
            request = line.request
            try:
                result = await provider.synthesize(
                    request.tts_text,
                    request.voice,
                    request.language,
                    request.instructions,
                    options=request.options(),
                )
            except VoxelloError as exc:
                project.save_state(state)
                raise VoxelloError(
                    exc.code,
                    f"{line.segment_id}: {exc.message}",
                    details={**exc.details, "segment_id": line.segment_id},
                ) from exc
            take = project.store_take(state, line.segment_id, request, result.audio)
            rendered += 1
            event = RenderEvent(line.segment_id, index, len(selected), False, take.duration_ms)
        project.save_state(state)
        if progress is not None:
            progress(event)
    return RenderSummary(rendered, reused)
