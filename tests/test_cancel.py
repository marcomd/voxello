from __future__ import annotations

from voxello.core.models import RequestState

from .conftest import FakePlayer, settle


async def test_stop_all(service, player: FakePlayer):
    current = await service.speak("uno")
    await player.wait_started()
    queued = await service.speak("due", interrupt=False)
    result = await service.stop()
    assert result.status == "stopped"
    assert result.cleared_queue == 1
    await settle()
    assert service.get_record(current.request_id).state == RequestState.CANCELLED
    assert service.get_record(queued.request_id).state == RequestState.CANCELLED
    assert (await service.status()).status == "idle"
    assert list(service.temp_store.directory.iterdir()) == []


async def test_stop_when_idle(service):
    result = await service.stop()
    assert result.status == "idle"


async def test_stop_specific_current(service, player: FakePlayer):
    current = await service.speak("uno")
    await player.wait_started()
    queued = await service.speak("due", interrupt=False)
    result = await service.stop(current.request_id)
    assert result.status == "stopped"
    await player.wait_started()
    assert service.playback.current.request_id == queued.request_id


async def test_stop_specific_queued_and_unknown(service, player: FakePlayer):
    await service.speak("uno")
    await player.wait_started()
    queued = await service.speak("due", interrupt=False)
    result = await service.stop(queued.request_id)
    assert result.status == "removed"
    assert service.playback.queue_length == 0
    assert service.get_record(queued.request_id).state == RequestState.CANCELLED
    assert (await service.stop("vox_UNKNOWN")).status == "not_found"


async def test_player_error_is_recorded(service, player: FakePlayer):
    req = await service.speak("uno")
    await player.wait_started()
    await player.finish_current(returncode=1)
    await settle()
    record = service.get_record(req.request_id)
    assert record.state == RequestState.ERROR
    assert record.error == "playback_error"
