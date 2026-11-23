"""``dialogue play``: the scene line by line, with the cast's pauses (roadmap 8.6).

Each line plays its approved take, or the chosen one marked ``(not approved)``; a line with no
current take is announced and skipped. The id being played is printed first and kept in
``current``, so after Ctrl-C (a cancellation here) the CLI can offer to review that line.
Playing take by take, rather than one joined file, is what makes the current line known.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from voxello.dialogue.project import LineDefaults, Project
from voxello.dialogue.script import effective_tts_text, pause_between
from voxello.errors import PLAYBACK_ERROR, PLAYBACK_UNAVAILABLE, VoxelloError
from voxello.playback.base import AudioPlayer


@dataclass(frozen=True)
class PlaySummary:
    played: int
    unapproved: int
    skipped: int


class PlayThrough:
    def __init__(
        self,
        project: Project,
        player: AudioPlayer | None,
        say: Callable[[str], None],
        *,
        defaults: LineDefaults | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.project = project
        self.player = player
        self.say = say
        self.defaults = defaults
        self.sleep = sleep
        self.current: str | None = None  # the line playing, or the last one reached

    async def run(self, start: str | None = None) -> PlaySummary:
        lines = self.project.lines(self.defaults)
        script = self.project.load_script()
        cast = self.project.load_cast()
        assert cast is not None  # Project.lines refuses a project without one
        first = 0
        if start is not None:
            ids = [seg.id for seg in script.segments]
            script.segment(start)  # an unknown id is an invalid_script error
            position = ids.index(start)
            first = next(
                (i for i, line in enumerate(lines) if ids.index(line.segment_id) >= position),
                len(lines),
            )
        if self.player is None:
            raise VoxelloError(PLAYBACK_UNAVAILABLE, "No audio player found to play the takes.")
        state = self.project.load_state()
        played = unapproved = skipped = 0
        previous: str | None = None
        for number, line in enumerate(lines[first:], start=first + 1):
            sid = line.segment_id
            self.current = sid
            pick = self.project.pick(state, line)
            head = f"[{number}/{len(lines)}] {sid}"
            if pick.take is None:
                self.say(f"{head}  no take: render it first")
                skipped += 1
                continue
            if previous is not None:
                await self.sleep(pause_between(script, cast, previous, sid) / 1000)
            seg = script.segment(sid)
            label = line.speaker.upper() + (f" ({seg.register_})" if seg.register_ else "")
            marker = "" if pick.approved else "  (not approved)"
            text = " ".join(effective_tts_text(seg).split())
            self.say(f"{head}  {label}  {text}{marker}")
            handle = await self.player.play(self.project.take_path(sid, pick.take.fingerprint))
            try:
                code = await handle.wait()
            except BaseException:
                await handle.terminate()
                raise
            if code != 0:
                raise VoxelloError(PLAYBACK_ERROR, f"{sid}: the player exited with status {code}.")
            played += 1
            unapproved += not pick.approved
            previous = sid
        self.current = None
        return PlaySummary(played, unapproved, skipped)
