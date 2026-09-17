"""Per-request language (roadmap milestone 3): validation, voice precedence, cache keys."""

from __future__ import annotations

from pathlib import Path

import pytest

from voxello.config import Settings
from voxello.core.models import RequestState
from voxello.core.service import VoxelloService
from voxello.errors import INVALID_LANGUAGE, VoxelloError
from voxello.storage.files import OutputStore, TempStore

from .conftest import FakeNotifier, FakePlayer, FakeProvider, make_cache


def build_service(settings: Settings, provider: FakeProvider) -> VoxelloService:
    return VoxelloService(
        settings,
        provider=provider,
        player=FakePlayer(),
        notifier=FakeNotifier(),
        temp_store=TempStore(settings.storage.resolved_temp_dir(), 1, True),
        output_store=OutputStore(settings.output.resolved_directory()),
        audio_cache=make_cache(settings),
    )


async def test_default_language_comes_from_settings(service, provider: FakeProvider):
    result = await service.speak("Ciao", play=False)
    assert result.language == "it"
    assert provider.calls == [("Ciao", None, "it")]
    record = service.get_record(result.request_id)
    assert record is not None and record.language == "it"


async def test_request_language_reaches_provider_and_result(service, provider: FakeProvider):
    result = await service.speak("Hello", play=False, language="en")
    assert result.language == "en"
    assert provider.calls[-1] == ("Hello", None, "en")

    notified = await service.notify("Done.", channels=["voice"], language="en")
    assert notified.status == "delivered"
    assert provider.calls[-1] == ("Done.", None, "en")


async def test_language_is_normalized(service, provider: FakeProvider):
    result = await service.speak("Hello", play=False, language=" EN ")
    assert result.language == "en"
    assert provider.calls[-1][2] == "en"


@pytest.mark.parametrize("bad", ["italiano", "i", "it-IT", "1t", ""])
async def test_invalid_language_is_rejected(service, provider: FakeProvider, bad: str):
    with pytest.raises(VoxelloError) as exc:
        await service.speak("ciao", play=False, language=bad)
    assert exc.value.code == INVALID_LANGUAGE
    assert provider.calls == []
    status = await service.status()
    assert status.recent[0].state == RequestState.ERROR
    assert status.recent[0].error == INVALID_LANGUAGE


async def test_status_reports_default_language(service):
    status = await service.status()
    assert status.language == "it"


async def test_voices_by_language_precedence(tmp_path: Path, provider: FakeProvider):
    """Roadmap 3.2: request voice > voices_by_language[language] > global voice > server."""
    settings = Settings(
        storage={"temp_dir": tmp_path / "tmp"},
        cache={"directory": tmp_path / "cache"},
        output={"directory": tmp_path / "out"},
        tts={"voicestudio": {"voice": "global"}},
        speech={
            "default_language": "it",
            "voices_by_language": {"it": "italiana", "en": "english"},
        },
    )
    svc = build_service(settings, provider)
    await svc.start()
    try:
        await svc.speak("a", play=False, voice="explicit", language="en")
        await svc.speak("b", play=False, language="en")
        await svc.speak("c", play=False)  # default language it -> italiana
        await svc.speak("d", play=False, language="fr")  # not in the map -> global voice
    finally:
        await svc.aclose()
    assert [call[1] for call in provider.calls] == ["explicit", "english", "italiana", "global"]


async def test_no_voice_configured_leaves_the_server_default(service, provider: FakeProvider):
    result = await service.speak("x", play=False, language="en")
    assert provider.calls == [("x", None, "en")]
    assert result.voice == "default"  # FakeProvider's label for "no voice requested"


async def test_same_text_in_two_languages_is_cached_twice(service, provider: FakeProvider):
    text = "Claude Code ha finito."
    first_it = await service.speak(text, play=False, mode="notification", language="it")
    first_en = await service.speak(text, play=False, mode="notification", language="en")
    assert (first_it.cached, first_en.cached) == (False, False)
    assert len(provider.calls) == 2

    hit_it = await service.speak(text, play=False, mode="notification")  # default language
    hit_en = await service.speak(text, play=False, mode="notification", language="en")
    assert (hit_it.cached, hit_en.cached) == (True, True)
    assert (hit_it.language, hit_en.language) == ("it", "en")
    assert len(provider.calls) == 2, "hits must not synthesize"
    assert service.audio_cache is not None and service.audio_cache.stats()[0] == 2


async def test_voice_map_is_part_of_the_cache_key(tmp_path: Path, provider: FakeProvider):
    settings = Settings(
        storage={"temp_dir": tmp_path / "tmp"},
        cache={"directory": tmp_path / "cache"},
        output={"directory": tmp_path / "out"},
        speech={"voices_by_language": {"en": "english"}},
    )
    svc = build_service(settings, provider)
    await svc.start()
    try:
        await svc.speak("Hi", play=False, mode="notification", language="en")
        hit = await svc.speak("Hi", play=False, mode="notification", language="en")
        assert hit.cached is True and hit.voice == "english"
        other = await svc.speak("Hi", play=False, mode="notification", voice="other", language="en")
        assert other.cached is False, "a different effective voice is a different entry"
    finally:
        await svc.aclose()
