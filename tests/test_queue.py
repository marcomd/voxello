from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from voxello.core.models import RequestState
from voxello.errors import QUEUE_FULL, VoxelloError

from .conftest import FakePlayer, settle


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


class BlockingPlayer(FakePlayer):
    """A player whose spawn phase blocks until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.started_play = asyncio.Event()
        self.release = asyncio.Event()

    async def play(self, path: Path):
        self.started_play.set()
        await self.release.wait()
        return await super().play(path)


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
        assert old_handle.terminated
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

    # shutdown must not hang on the suspended spawn; it is allowed to complete
    await asyncio.wait_for(svc.aclose(), 2)
    player.release.set()
