from __future__ import annotations

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
