"""`dialogue play` (roadmap 8.6): the scene line by line with the cast's pauses."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import FakePlayer
from tests.test_dialogue_export import LINES, ToneProvider, approve_all
from tests.test_dialogue_export import project as project
from voxello.dialogue.play import PlaySummary, PlayThrough
from voxello.dialogue.project import Project
from voxello.dialogue.render import render
from voxello.errors import INVALID_SCRIPT, PLAYBACK_ERROR, PLAYBACK_UNAVAILABLE, VoxelloError


class Run:
    def __init__(self, project: Project, player: FakePlayer | None) -> None:
        self.out: list[str] = []
        self.pauses: list[float] = []
        self.player = player or FakePlayer()
        self.play = PlayThrough(project, player, self.out.append, sleep=self.sleep)

    async def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)


def played(player: FakePlayer) -> list[str]:
    return [h.path.parent.name for h in player.handles]


async def play_all(run: Run, start: str | None = None) -> PlaySummary:
    task = asyncio.create_task(run.play.run(start))
    while not task.done():
        started = asyncio.create_task(run.player.wait_started())
        await asyncio.wait({task, started}, timeout=2, return_when=asyncio.FIRST_COMPLETED)
        if started.done():
            await run.player.finish_current()
        else:
            started.cancel()
    return await task


async def test_plays_every_line_with_the_cast_pauses(project: Project) -> None:
    await render(project, ToneProvider())
    approve_all(project)
    run = Run(project, FakePlayer())
    summary = await play_all(run)
    assert played(run.player) == LINES
    assert run.pauses == [0.3, 0.3, 0.2, 0.4]
    assert run.out[0].startswith("[1/5] p001.1  NARRATORE  Capitolo uno")
    assert run.out[2].startswith("[3/5] p003.1  BRUNO  Porto fuori il cane.")
    assert summary.played == 5
    assert summary.unapproved == 0
    assert run.play.current is None


async def test_marks_unapproved_takes_and_skips_lines_without_one(
    project: Project,
) -> None:
    await render(project, ToneProvider(), lines="p001.1-p003.1")
    run = Run(project, FakePlayer())
    summary = await play_all(run)
    assert played(run.player) == ["p001.1", "p002.1", "p003.1"]
    assert "(not approved)" in run.out[0]
    assert run.out[3] == "[4/5] p003.2  no take: render it first"
    assert summary.skipped == 2
    assert summary.unapproved == 3
    assert run.pauses == [0.3, 0.3]


async def test_starts_at_a_line_and_rejects_unknown_ids(project: Project) -> None:
    await render(project, ToneProvider())
    run = Run(project, FakePlayer())
    await play_all(run, "p003.2")
    assert played(run.player) == ["p003.2", "p005.1"]
    assert run.out[0].startswith("[4/5] p003.2")
    with pytest.raises(VoxelloError) as excinfo:
        await Run(project, FakePlayer()).play.run("p999.1")
    assert excinfo.value.code == INVALID_SCRIPT


async def test_starting_on_a_break_plays_from_the_next_line(project: Project) -> None:
    await render(project, ToneProvider())
    run = Run(project, FakePlayer())
    await play_all(run, "p004.1")
    assert played(run.player) == ["p005.1"]


async def test_cancelling_stops_the_take_and_keeps_the_line(project: Project) -> None:
    await render(project, ToneProvider())
    player = FakePlayer()
    run = Run(project, player)
    task = asyncio.create_task(run.play.run())
    await player.wait_started()
    await player.finish_current()
    handle = await player.wait_started()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handle.terminated
    assert run.play.current == "p002.1"


async def test_needs_a_player(project: Project) -> None:
    await render(project, ToneProvider())
    with pytest.raises(VoxelloError) as excinfo:
        await Run(project, None).play.run()
    assert excinfo.value.code == PLAYBACK_UNAVAILABLE


async def test_a_failing_player_stops_the_play_through(project: Project) -> None:
    await render(project, ToneProvider())
    player = FakePlayer()
    run = Run(project, player)
    task = asyncio.create_task(run.play.run())
    await player.wait_started()
    await player.finish_current(returncode=1)
    with pytest.raises(VoxelloError) as excinfo:
        await task
    assert excinfo.value.code == PLAYBACK_ERROR
    assert "p001.1" in excinfo.value.message
