from __future__ import annotations

import pytest

from voxello.core.models import RequestState
from voxello.errors import (
    INVALID_TEXT,
    PLAYBACK_UNAVAILABLE,
    TEXT_TOO_LONG,
    TTS_PROVIDER_UNAVAILABLE,
    VoxelloError,
)

from .conftest import FakePlayer, FakeProvider, settle


async def test_speak_plays_and_reports_metadata(
    service, provider: FakeProvider, player: FakePlayer
):
    result = await service.speak("Build completata.", client_id="claude-code")
    assert result.status == "playing"
    assert result.request_id.startswith("vox_")
    assert result.duration_ms == 500
    assert result.saved_path is None
    assert provider.calls == [("Build completata.", None)]

    handle = await player.wait_started()
    assert handle.path.exists()
    assert handle.path.name.startswith("vox_")
    record = service.get_record(result.request_id)
    assert record is not None and record.state == RequestState.PLAYING

    await player.finish_current()
    await settle()
    assert record.state == RequestState.COMPLETED
    assert not handle.path.exists(), "temporary audio must be deleted after playback"


async def test_speak_without_play_generates_and_cleans_up(service, player: FakePlayer):
    result = await service.speak("Solo generazione", play=False)
    assert result.status == "generated"
    assert player.handles == []
    assert list(service.temp_store.directory.iterdir()) == []


async def test_speak_save_persists_copy(service, settings):
    result = await service.speak("Salva questo", save=True, play=False)
    assert result.status == "saved"
    assert result.saved_path is not None
    saved = settings.output.resolved_directory() / result.saved_path.split("/")[-1]
    assert saved.exists()
    assert result.request_id in saved.name
    assert "Salva" not in saved.name


async def test_speak_rejects_empty_and_too_long(service, settings):
    with pytest.raises(VoxelloError) as exc:
        await service.speak("   ")
    assert exc.value.code == INVALID_TEXT
    with pytest.raises(VoxelloError) as exc:
        await service.speak("x" * (settings.limits.max_text_chars + 1))
    assert exc.value.code == TEXT_TOO_LONG


async def test_provider_failure_marks_error(service, provider: FakeProvider):
    provider.fail_with = VoxelloError(TTS_PROVIDER_UNAVAILABLE, "down")
    with pytest.raises(VoxelloError) as exc:
        await service.speak("ciao")
    assert exc.value.code == TTS_PROVIDER_UNAVAILABLE
    status = await service.status()
    assert status.status == "idle"
    assert status.recent[0].state == RequestState.ERROR
    assert status.recent[0].error == TTS_PROVIDER_UNAVAILABLE


async def test_speak_without_player_raises_playback_unavailable(settings, provider, tmp_path):
    from voxello.core.service import VoxelloService
    from voxello.storage.files import OutputStore, TempStore

    svc = VoxelloService(
        settings,
        provider=provider,
        player=None,
        notifier=None,
        temp_store=TempStore(tmp_path / "t", 1, True),
        output_store=OutputStore(tmp_path / "o"),
    )
    await svc.start()
    try:
        with pytest.raises(VoxelloError) as exc:
            await svc.speak("ciao")
        assert exc.value.code == PLAYBACK_UNAVAILABLE
        result = await svc.speak("ciao", play=False)
        assert result.status == "generated"
    finally:
        await svc.aclose()
