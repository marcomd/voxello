from __future__ import annotations

import pytest

from voxello.errors import (
    INVALID_PARAMETER,
    NOTIFICATION_UNAVAILABLE,
    STORAGE_ERROR,
    TTS_PROVIDER_UNAVAILABLE,
    VoxelloError,
)

from .conftest import FakeNotifier, FakePlayer, FakeProvider


async def test_notify_voice_and_desktop(service, player: FakePlayer, notifier: FakeNotifier):
    result = await service.notify("Refactoring completato.", title="Claude Code")
    assert result.status == "delivered"
    assert result.channels == {"voice": "playing", "desktop": "sent"}
    assert notifier.sent == [("Claude Code", "Refactoring completato.")]
    assert result.request_id is not None
    await player.wait_started()


async def test_notify_partial_when_desktop_fails(service, notifier: FakeNotifier):
    notifier.fail = VoxelloError(NOTIFICATION_UNAVAILABLE, "nope")
    result = await service.notify("ciao")
    assert result.status == "partial"
    assert result.channels["voice"] == "playing"
    assert result.channels["desktop"] == "error:notification_unavailable"


async def test_notify_failed_when_everything_fails(
    service, provider: FakeProvider, notifier: FakeNotifier
):
    provider.fail_with = VoxelloError(TTS_PROVIDER_UNAVAILABLE, "down")
    notifier.fail = VoxelloError(NOTIFICATION_UNAVAILABLE, "nope")
    result = await service.notify("ciao")
    assert result.status == "failed"
    assert result.channels["voice"] == "error:tts_provider_unavailable"


async def test_notify_desktop_only_does_not_synthesize(service, provider: FakeProvider, notifier):
    result = await service.notify("solo desktop", channels=["desktop"])
    assert result.status == "delivered"
    assert result.channels == {"desktop": "sent"}
    assert provider.calls == []


async def test_notify_file_channel_saves_audio_and_text(service, settings):
    result = await service.notify("archivia", channels=["file"])
    assert result.status == "delivered"
    assert result.channels == {"file": "saved"}
    out = settings.output.resolved_directory()
    names = sorted(p.name for p in out.iterdir())
    assert any(n.endswith(".wav") for n in names) and any(n.endswith(".txt") for n in names)


async def test_notify_priority_controls_interrupt(service, player: FakePlayer):
    first = await service.notify("uno", channels=["voice"], priority="low")
    await player.wait_started()
    second = await service.notify("due", channels=["voice"], priority="low")
    assert second.channels["voice"] == "queued"
    third = await service.notify("tre", channels=["voice"], priority="critical")
    assert third.channels["voice"] == "playing"
    assert first.request_id != third.request_id


async def test_notify_rejects_unknown_channel(service):
    with pytest.raises(VoxelloError) as exc:
        await service.notify("ciao", channels=["pager"])
    assert exc.value.code == INVALID_PARAMETER


@pytest.mark.parametrize("channel", ["voice", "desktop"])
async def test_disabled_channel_skips_delivery(service, provider, notifier, channel):
    setattr(service.settings.notifications, channel, False)
    result = await service.notify("disabled", channels=[channel])
    assert result.channels == {channel: "disabled"}
    assert provider.calls == []
    assert notifier.sent == []


@pytest.mark.parametrize("missing", [True, False])
async def test_unavailable_desktop_is_reported(service, notifier, missing):
    if missing:
        service.notifier = None
    else:
        notifier._available = False
    result = await service.notify("hello", channels=["desktop"])
    assert result.status == "failed"
    assert result.channels == {"desktop": "unavailable"}


async def test_file_failure_preserves_successful_voice_delivery(service, monkeypatch):
    def fail(*args):
        raise VoxelloError(STORAGE_ERROR, "disk full")

    monkeypatch.setattr(service.output_store, "save_text", fail)
    result = await service.notify("hello", channels=["voice", "file"])
    assert result.status == "partial"
    assert result.channels == {"voice": "playing", "file": "error:storage_error"}
    assert result.saved_path is not None


async def test_file_provider_failure_does_not_block_desktop(service, provider, notifier):
    provider.fail_with = VoxelloError(TTS_PROVIDER_UNAVAILABLE, "down")
    result = await service.notify("hello", channels=["file", "desktop"])
    assert result.status == "partial"
    assert result.channels == {"file": "error:tts_provider_unavailable", "desktop": "sent"}
    assert notifier.sent == [("Voxello", "hello")]
