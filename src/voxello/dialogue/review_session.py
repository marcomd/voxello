"""Runs a review: executes the effects of ``review.step`` (roadmap 8.5).

The session owns the project state during a review and saves it after every effect, before
the next key is read, so quitting (or Ctrl-C, or a crash) never loses an approval and the next
review resumes at the saved position. The terminal, the player and the provider are injected:
the CLI passes a raw-key console, the subprocess player and the configured server; the tests
pass scripted keys, ``FakePlayer`` and ``FakeProvider``.

Playback runs on its own while the session waits for a key; any key stops it first. A line
without a current take is rendered when review reaches it, once: after a failure only ``r``
tries again, so the server is not called at every key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from voxello.dialogue import qa
from voxello.dialogue.audio import AudioFormatError, Silence, concatenate
from voxello.dialogue.cast import Cast
from voxello.dialogue.project import Line, LineDefaults, Project
from voxello.dialogue.review import (
    Approve,
    ChooseRegister,
    EditText,
    Effect,
    LineView,
    NewTake,
    Notice,
    Play,
    PlayContext,
    Quit,
    Review,
    Select,
    SetSpeed,
    TakeInfo,
    build_queue,
    format_line,
    register_from_key,
    start_index,
    step,
)
from voxello.dialogue.script import Script, pause_between
from voxello.dialogue.segment import default_tts_text
from voxello.dialogue.takes import LineState, State, Take
from voxello.errors import VoxelloError
from voxello.playback.base import AudioPlayer, PlaybackHandle
from voxello.tts.base import TTSProvider


class ReviewTerminal(Protocol):
    def say(self, text: str) -> None: ...

    async def key(self) -> str:
        """The next key, already normalized (``review.normalize_key``)."""
        ...

    def edit(self, text: str) -> str | None:
        """Let the user edit ``text`` (blocking); ``None`` when the editor failed."""
        ...


@dataclass(frozen=True)
class ReviewSummary:
    approved: int
    total: int
    flagged: int
    position: str | None
    interrupted: bool = False


class ReviewSession:
    def __init__(
        self,
        project: Project,
        provider: TTSProvider,
        player: AudioPlayer | None,
        terminal: ReviewTerminal,
        *,
        workdir: Path,
        defaults: LineDefaults | None = None,
    ) -> None:
        self.project = project
        self.provider = provider
        self.player = player
        self.terminal = terminal
        self.workdir = workdir
        self.defaults = defaults or LineDefaults()
        self.state: State = project.load_state()
        self._handle: PlaybackHandle | None = None
        self._carry_seed: dict[str, int] = {}  # seed for the render after an edit
        self._failed: set[str] = set()  # lines whose automatic render failed
        self._typical: dict[str, float] | None = None
        self._dirty = False
        self._load_lines()

    def _load_lines(self) -> None:
        lines = self.project.lines(self.defaults)
        self.lines: dict[str, Line] = {line.segment_id: line for line in lines}
        self.order = list(self.lines)
        self.script: Script = self.project.load_script()
        cast = self.project.load_cast()
        assert cast is not None  # Project.lines refuses a project without one
        self.cast: Cast = cast
        self._typical = None

    # --- the loop ---------------------------------------------------------------------------

    async def run(
        self, *, start: str | None = None, pending: bool = False, flagged: bool = False
    ) -> ReviewSummary:
        if start is not None:
            self.script.segment(start)  # an unknown id is an invalid_script error
        approved = {sid for sid in self.order if self._approved(sid) is not None}
        flagged_ids = {sid for sid in self.order if self._flags(sid)}
        self._flush()
        queue = build_queue(
            self.order,
            approved=approved,
            flagged=flagged_ids,
            pending=pending,
            flagged_first=flagged,
        )
        if not queue:
            self.terminal.say("Nothing to review: every line is approved.")
            return self._summary(self.state.review.position)
        index = start_index(
            queue, self.order, start=start, position=self.state.review.position, approved=approved
        )
        review = Review(queue, index)
        shown: tuple[str, str | None] | None = None
        try:
            while (sid := review.current) is not None:
                self._set_position(sid)
                if not self._current(sid) and sid not in self._failed:
                    await self._render(sid, self._carry_seed.pop(sid, self.lines[sid].request.seed))
                view = self._view(sid)
                self._flush()
                if (sid, view.chosen) != shown:
                    shown = (sid, view.chosen)
                    for text in format_line(view):
                        self.terminal.say(text)
                    await self._play_chosen(sid)
                key = await self.terminal.key()
                await self._stop()
                review, effects = step(review, view, key)
                for effect in effects:
                    if isinstance(effect, Quit):
                        return self._summary(sid, interrupted=effect.interrupted)
                    await self._apply(effect, view)
        finally:
            await self._stop()
            self._flush()
        self._set_position(None)
        return self._summary(None)

    async def _apply(self, effect: Effect, view: LineView) -> None:
        if isinstance(effect, Approve):
            self._entry(effect.segment_id).approved = effect.fingerprint
            self._save()
        elif isinstance(effect, Select):
            self._entry(effect.segment_id).selected = effect.fingerprint
            self._typical = None
            self._save()
        elif isinstance(effect, NewTake):
            await self._render(effect.segment_id, effect.seed)
        elif isinstance(effect, Play):
            await self._play_chosen(effect.segment_id)
        elif isinstance(effect, PlayContext):
            await self._play_context(effect.segment_id)
        elif isinstance(effect, EditText):
            await self._edit_text(effect.segment_id, effect.seed)
        elif isinstance(effect, SetSpeed):
            self._edit(effect.segment_id, {"speed": effect.speed}, effect.seed)
        elif isinstance(effect, ChooseRegister):
            await self._choose_register(view, effect.seed)
        elif isinstance(effect, Notice):
            self.terminal.say(effect.text)

    # --- state ------------------------------------------------------------------------------

    def _entry(self, sid: str) -> LineState:
        return self.state.lines.setdefault(sid, LineState())

    def _save(self) -> None:
        self.project.save_state(self.state)
        self._dirty = False

    def _flush(self) -> None:
        if self._dirty:
            self._save()

    def _set_position(self, sid: str | None) -> None:
        if self.state.review.position != sid:
            self.state.review.position = sid
            self._save()

    def _current(self, sid: str) -> list[Take]:
        """The line's current takes with audio on disk, measured (saved at the next flush)."""
        entry = self.state.lines.get(sid)
        if entry is None:
            return []
        current = [
            t
            for t in entry.current_takes(self.lines[sid].request)
            if self.project.has_audio(sid, t)
        ]
        for take in current:
            if self.project.ensure_measures(sid, take):
                self._dirty = True
        return current

    def _chosen(self, sid: str) -> Take | None:
        entry = self.state.lines.get(sid)
        return entry.chosen(self._current(sid)) if entry is not None else None

    def _approved(self, sid: str) -> Take | None:
        entry = self.state.lines.get(sid)
        if entry is None or entry.approved is None:
            return None
        return next((t for t in self._current(sid) if t.fingerprint == entry.approved), None)

    def _summary(self, position: str | None, *, interrupted: bool = False) -> ReviewSummary:
        approved = sum(1 for sid in self.order if self._approved(sid) is not None)
        flagged = sum(1 for sid in self.order if self._flags(sid))
        self._flush()
        return ReviewSummary(approved, len(self.order), flagged, position, interrupted)

    # --- QA ---------------------------------------------------------------------------------

    def _rate(self, line: Line, take: Take | None) -> float | None:
        if take is None or take.qa is None:
            return None
        chars = qa.spoken_chars(line.request.tts_text)
        return qa.chars_per_second(take.qa.measures(), chars, line.request.speed)

    def _typical_cps(self) -> dict[str, float]:
        if self._typical is None:
            rates: dict[str, list[float]] = {c: [] for c in self.cast.characters}
            for sid, line in self.lines.items():
                rate = self._rate(line, self._chosen(sid))
                if rate is not None:
                    rates[line.speaker].append(rate)
            self._typical = qa.typical_cps(rates)
        return self._typical

    def _flags(self, sid: str) -> list[qa.Flag]:
        take = self._chosen(sid)
        if take is None or take.qa is None:
            return []
        line = self.lines[sid]
        return qa.flags(
            take.qa.measures(),
            cps=self._rate(line, take),
            typical=self._typical_cps()[line.speaker],
            speaker=line.speaker,
        )

    # --- views ------------------------------------------------------------------------------

    def _view(self, sid: str) -> LineView:
        line = self.lines[sid]
        seg = self.script.segment(sid)
        current = self._current(sid)
        chosen = self._chosen(sid)
        approved = self._approved(sid)
        tts_text = line.request.tts_text
        return LineView(
            segment_id=sid,
            number=self.order.index(sid) + 1,
            total=len(self.order),
            speaker=line.speaker,
            register=seg.register_,
            text=seg.text,
            tts_text=None if tts_text == default_tts_text(seg.kind, seg.text) else tts_text,
            speed=line.request.speed,
            base_seed=line.request.seed,
            takes=tuple(TakeInfo(t.fingerprint, t.seed, t.duration_ms) for t in current),
            chosen=chosen.fingerprint if chosen is not None else None,
            approved=approved.fingerprint if approved is not None else None,
            registers=tuple(self.cast.characters[line.speaker].registers),
            flags=tuple(self._flags(sid)),
        )

    # --- rendering and edits ----------------------------------------------------------------

    async def _render(self, sid: str, seed: int) -> None:
        request = self.lines[sid].request.with_seed(seed)
        self.terminal.say(f"Rendering {sid} (seed {seed})...")
        try:
            result = await self.provider.synthesize(
                request.tts_text,
                request.voice,
                request.language,
                request.instructions,
                options=request.options(),
            )
        except VoxelloError as exc:
            self._failed.add(sid)
            self.terminal.say(f"error: {sid}: {exc.message}")
            return
        self._failed.discard(sid)
        take = self.project.store_take(self.state, sid, request, result.audio)
        entry = self._entry(sid)
        entry.selected = take.fingerprint
        if entry.approved not in {t.fingerprint for t in self._current(sid)}:
            entry.approved = None  # like `dialogue render`: an edited line needs a new approval
        self._typical = None
        self._save()

    def _edit(self, sid: str, fields: dict[str, object], seed: int) -> bool:
        problems = self.project.edit_line(sid, fields, self.defaults.designs)
        if problems:
            for problem in problems:
                self.terminal.say(f"Not changed: {problem}")
            return False
        self._load_lines()
        self._carry_seed[sid] = seed
        self._failed.discard(sid)
        return True

    async def _edit_text(self, sid: str, seed: int) -> None:
        seg = self.script.segment(sid)
        current = self.lines[sid].request.tts_text
        edited = await asyncio.to_thread(self.terminal.edit, current)
        new = " ".join((edited or "").split())
        if not new or new == current:
            self.terminal.say("tts_text unchanged.")
            return
        value = None if new == default_tts_text(seg.kind, seg.text) else new
        self._edit(sid, {"tts_text": value}, seed)

    async def _choose_register(self, view: LineView, seed: int) -> None:
        options = " · ".join(
            ["0 main voice", *(f"{n} {r}" for n, r in enumerate(view.registers, 1))]
        )
        self.terminal.say(f"Register for {view.speaker}: {options}")
        valid, register = register_from_key(view.registers, await self.terminal.key())
        if not valid or register == view.register:
            self.terminal.say("Register unchanged.")
            return
        self._edit(view.segment_id, {"register": register}, seed)

    # --- playback ---------------------------------------------------------------------------

    async def _play(self, path: Path) -> None:
        await self._stop()
        if self.player is None:
            self.terminal.say(f"(no audio player: listen to {path})")
            return
        try:
            self._handle = await self.player.play(path)
        except VoxelloError as exc:
            self.terminal.say(f"error: {exc.message}")

    async def _stop(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            await handle.terminate()

    def _path(self, sid: str, pick: Callable[[str], Take | None]) -> Path | None:
        take = pick(sid)
        return self.project.take_path(sid, take.fingerprint) if take is not None else None

    async def _play_chosen(self, sid: str) -> None:
        path = self._path(sid, self._chosen)
        if path is not None:
            await self._play(path)

    def _approved_or_chosen(self, sid: str) -> Take | None:
        return self._approved(sid) or self._chosen(sid)

    async def _play_context(self, sid: str) -> None:
        """The previous line (approved take if any), this take, the next line, with pauses."""
        current = self._path(sid, self._chosen)
        assert current is not None  # step only asks for context when there is a take
        index = self.order.index(sid)
        parts: list[Path | Silence] = []
        if index > 0:
            prev = self.order[index - 1]
            path = self._path(prev, self._approved_or_chosen)
            if path is not None:
                parts += [path, Silence(pause_between(self.script, self.cast, prev, sid))]
        parts.append(current)
        if index + 1 < len(self.order):
            nxt = self.order[index + 1]
            path = self._path(nxt, self._approved_or_chosen)
            if path is not None:
                parts += [Silence(pause_between(self.script, self.cast, sid, nxt)), path]
        self.workdir.mkdir(parents=True, exist_ok=True)
        output = self.workdir / "context.wav"
        try:
            concatenate(parts, output)
        except AudioFormatError as exc:
            self.terminal.say(f"Context unavailable ({exc}); playing the take alone.")
            output = current
        await self._play(output)
