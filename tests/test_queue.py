from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from voxello.core.models import RequestState
from voxello.errors import PLAYBACK_ERROR, QUEUE_FULL, VoxelloError
from voxello.playback.manager import PlaybackItem, PlaybackManager

from .conftest import BlockingPlayer, FakeHandle, FakePlayer, settle


async def test_interrupt_stops_current_and_clears_queue(service, player: FakePlayer):
    first = await service.speak("uno", interrupt=False)
    await player.wait_started()
    second = await service.speak("due", interrupt=False)
    assert second.status == "queued"
    assert service.playback.queue_length == 1

    third = await service.speak("tre", interrupt=True)
    assert third.status == "playing"
    await settle()
    assert player.handles[0].terminated
    assert service.get_record(first.request_id).state == RequestState.CANCELLED
    assert service.get_record(second.request_id).state == RequestState.CANCELLED
    assert service.playback.queue_length == 0
    handle = await player.wait_started()
    assert service.get_record(third.request_id).state == RequestState.PLAYING
    assert handle is player.handles[-1]


async def test_fifo_queue_plays_in_order(service, player: FakePlayer):
    a = await service.speak("a", interrupt=False)
    await player.wait_started()
    b = await service.speak("b", interrupt=False)
    c = await service.speak("c", interrupt=False)
    assert service.playback.queued_ids() == [b.request_id, c.request_id]

    await player.finish_current()
    await player.wait_started()
    assert service.playback.current.request_id == b.request_id
    assert service.get_record(a.request_id).state == RequestState.COMPLETED

    await player.finish_current()
    await player.wait_started()
    assert service.playback.current.request_id == c.request_id
    await player.finish_current()
    await settle()
    assert service.playback.current is None
    status = await service.status()
    assert status.status == "idle" and status.queue_length == 0


async def test_queue_full(service, player: FakePlayer, settings):
    await service.speak("playing", interrupt=False)
    await player.wait_started()
    for i in range(settings.playback.max_queue_size):
        await service.speak(f"q{i}", interrupt=False)
    with pytest.raises(VoxelloError) as exc:
        await service.speak("overflow", interrupt=False)
    assert exc.value.code == QUEUE_FULL
    assert service.playback.queue_length == settings.playback.max_queue_size


async def test_interrupt_keeps_queue_when_configured(
    settings, provider, player, notifier, tmp_path
):
    from voxello.core.service import VoxelloService
    from voxello.storage.files import OutputStore, TempStore

    settings.playback.interrupt_clears_queue = False
    svc = VoxelloService(
        settings,
        provider=provider,
        player=player,
        notifier=notifier,
        temp_store=TempStore(tmp_path / "t", 1, True),
        output_store=OutputStore(tmp_path / "o"),
    )
    await svc.start()
    try:
        await svc.speak("uno", interrupt=False)
        await player.wait_started()
        queued = await svc.speak("due", interrupt=False)
        await svc.speak("tre", interrupt=True)
        await settle()
        assert queued.request_id in svc.playback.queued_ids()
    finally:
        await svc.aclose()


async def _wait_handles(player, n):
    for _ in range(100):
        if len(player.handles) >= n:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"timed out waiting for {n} handle(s)")


async def _make_service(settings, provider, player, notifier, tmp_path):
    from voxello.core.service import VoxelloService
    from voxello.storage.files import OutputStore, TempStore

    svc = VoxelloService(
        settings,
        provider=provider,
        player=player,
        notifier=notifier,
        temp_store=TempStore(tmp_path / "t", 1, True),
        output_store=OutputStore(tmp_path / "o"),
    )
    await svc.start()
    return svc


async def test_stop_all_while_spawn_is_suspended(settings, provider, notifier, tmp_path):
    player = BlockingPlayer()
    svc = await _make_service(settings, provider, player, notifier, tmp_path)
    try:
        first = await svc.speak("uno", interrupt=False)
        await asyncio.wait_for(player.started_play.wait(), 2)

        result = await svc.stop()
        assert result.status == "stopped"

        player.release.set()
        await asyncio.wait_for(svc.playback.wait_idle(), 2)
        handle = player.handles[-1]
        assert handle.terminated
        assert svc.get_record(first.request_id).state == RequestState.CANCELLED
    finally:
        await svc.aclose()


async def test_stop_specific_while_spawn_is_suspended(settings, provider, notifier, tmp_path):
    player = BlockingPlayer()
    svc = await _make_service(settings, provider, player, notifier, tmp_path)
    try:
        first = await svc.speak("uno", interrupt=False)
        await asyncio.wait_for(player.started_play.wait(), 2)

        result = await svc.stop(first.request_id)
        assert result.status == "stopped"

        player.release.set()
        await asyncio.wait_for(svc.playback.wait_idle(), 2)
        assert player.handles[-1].terminated
        assert svc.get_record(first.request_id).state == RequestState.CANCELLED
    finally:
        await svc.aclose()


async def test_interrupt_while_spawn_is_suspended_progresses_new_request(
    settings, provider, notifier, tmp_path
):
    player = BlockingPlayer()
    svc = await _make_service(settings, provider, player, notifier, tmp_path)
    try:
        first = await svc.speak("uno", interrupt=False)
        await asyncio.wait_for(player.started_play.wait(), 2)

        second = await svc.speak("due", interrupt=True)
        assert second.status == "playing"

        player.release.set()
        await _wait_handles(player, 1)
        # the old handle is terminated exactly once and the new item is playing
        old_handle = player.handles[0]
        assert old_handle.terminate_calls == 1
        await settle()
        assert svc.get_record(first.request_id).state == RequestState.CANCELLED
        assert svc.get_record(second.request_id).state == RequestState.PLAYING
        assert svc.playback.current.request_id == second.request_id
    finally:
        await svc.aclose()


async def test_shutdown_while_spawn_is_suspended(settings, provider, notifier, tmp_path):
    player = BlockingPlayer()
    svc = await _make_service(settings, provider, player, notifier, tmp_path)
    await svc.speak("uno", interrupt=False)
    await asyncio.wait_for(player.started_play.wait(), 2)

    first = list(svc._records.values())[-1]

    # shutdown must not hang on the suspended spawn, and the request still ends
    await asyncio.wait_for(svc.aclose(), 2)
    player.release.set()
    assert first.state == RequestState.CANCELLED
    assert first.audio_path is None
    assert not svc.temp_store.namespace_dir.exists()


async def test_shutdown_while_playing_terminates_and_cancels(
    settings, provider, notifier, tmp_path
):
    player = FakePlayer()
    svc = await _make_service(settings, provider, player, notifier, tmp_path)
    result = await svc.speak("uno", interrupt=False)
    handle = await player.wait_started()

    await asyncio.wait_for(svc.aclose(), 2)
    assert handle.terminated
    assert svc.get_record(result.request_id).state == RequestState.CANCELLED


def _recording_manager(player):
    outcomes: dict[str, list[str]] = {}

    def record(item, outcome):
        outcomes.setdefault(item.request_id, []).append(outcome)

    return PlaybackManager(player, on_outcome=record), outcomes


async def test_stop_during_spawn_emits_one_terminal_outcome(tmp_path):
    player = BlockingPlayer()
    manager, outcomes = _recording_manager(player)
    manager.start()
    try:
        await manager.enqueue(PlaybackItem("a", tmp_path / "a.wav"), interrupt=False)
        await asyncio.wait_for(player.started_play.wait(), 2)
        assert await manager.stop() == ("stopped", 0)
        player.release.set()
        await asyncio.wait_for(manager.wait_idle(), 2)
        assert outcomes == {"a": ["cancelled"]}
        assert player.handles[0].terminate_calls == 1
    finally:
        await manager.aclose()


async def test_interrupt_during_spawn_emits_one_outcome_per_item(tmp_path):
    player = BlockingPlayer()
    manager, outcomes = _recording_manager(player)
    manager.start()
    try:
        await manager.enqueue(PlaybackItem("a", tmp_path / "a.wav"), interrupt=False)
        await asyncio.wait_for(player.started_play.wait(), 2)
        await manager.enqueue(PlaybackItem("b", tmp_path / "b.wav"), interrupt=True)
        player.release.set()
        await _wait_handles(player, 2)
        await settle()
        assert outcomes == {"a": ["cancelled"], "b": ["playing"]}
        await player.finish_current()
        await asyncio.wait_for(manager.wait_idle(), 2)
        assert outcomes == {"a": ["cancelled"], "b": ["playing", "completed"]}
    finally:
        await manager.aclose()


class SlowTerminateHandle(FakeHandle):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.terminating = asyncio.Event()
        self.allow_exit = asyncio.Event()

    async def terminate(self) -> None:
        self.terminating.set()
        await self.allow_exit.wait()
        await super().terminate()


async def test_is_stopping_while_a_freshly_spawned_player_is_terminated(tmp_path):
    player = BlockingPlayer()
    handle = SlowTerminateHandle(tmp_path / "a.wav")

    async def spawn(path: Path) -> FakeHandle:
        player.started_play.set()
        await player.release.wait()
        return handle

    player.play = spawn  # type: ignore[method-assign]
    manager, _ = _recording_manager(player)
    manager.start()
    try:
        await manager.enqueue(PlaybackItem("a", tmp_path / "a.wav"), interrupt=False)
        await asyncio.wait_for(player.started_play.wait(), 2)
        await manager.stop()
        player.release.set()
        await asyncio.wait_for(handle.terminating.wait(), 2)
        assert manager.is_stopping
        handle.allow_exit.set()
        await asyncio.wait_for(manager.wait_idle(), 2)
        assert not manager.is_stopping
    finally:
        handle.allow_exit.set()
        await manager.aclose()


@pytest.mark.parametrize(("stop_first", "expected"), [(False, "error"), (True, "cancelled")])
async def test_spawn_failure_is_an_error_unless_stopped(tmp_path, stop_first, expected):
    player = BlockingPlayer()

    async def fail(path: Path) -> FakeHandle:
        player.started_play.set()
        await player.release.wait()
        raise VoxelloError(PLAYBACK_ERROR, "player missing")

    player.play = fail  # type: ignore[method-assign]
    manager, outcomes = _recording_manager(player)
    manager.start()
    try:
        await manager.enqueue(PlaybackItem("a", tmp_path / "a.wav"), interrupt=False)
        await asyncio.wait_for(player.started_play.wait(), 2)
        if stop_first:
            await manager.stop()
        player.release.set()
        await asyncio.wait_for(manager.wait_idle(), 2)
        assert outcomes == {"a": [expected]}
    finally:
        await manager.aclose()
