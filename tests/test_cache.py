"""Audio cache (roadmap milestone 2): keys, storage, eviction and service integration."""

from __future__ import annotations

import os
import stat
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from voxello.config import Settings
from voxello.core.models import RequestState
from voxello.core.service import VoxelloService
from voxello.errors import STORAGE_ERROR, TTS_PROVIDER_UNAVAILABLE, VoxelloError
from voxello.storage.cache import AudioCache, CacheKeyParts, normalize_text
from voxello.storage.files import OutputStore, TempStore

from .conftest import FakeNotifier, FakePlayer, FakeProvider, make_cache, make_wav, settle

PARTS = CacheKeyParts(
    text="Claude Code ha finito.",
    voice=None,
    engine="omnivoice",
    language="it",
    speed=None,
    num_step=None,
    guidance_scale=None,
    base_url="http://localhost:3900",
)


# -- keys -------------------------------------------------------------------------------


def test_key_is_stable_and_hex():
    key = AudioCache.key(PARTS)
    assert key == AudioCache.key(PARTS)
    assert len(key) == 64 and int(key, 16) >= 0


def test_key_ignores_whitespace_variants_and_nfc():
    assert normalize_text("  Ciao,\n  mondo  ") == "Ciao, mondo"
    assert AudioCache.key(replace(PARTS, text="Claude   Code ha\tfinito.")) == AudioCache.key(PARTS)
    decomposed = "Caffé"  # e + combining acute
    composed = "Caffé"
    assert AudioCache.key(replace(PARTS, text=decomposed)) == AudioCache.key(
        replace(PARTS, text=composed)
    )


@pytest.mark.parametrize(
    "change",
    [
        {"voice": "marco"},
        {"language": "en"},
        {"engine": "voxcpm2"},
        {"base_url": "http://voicestudio.lan:3900"},
        {"speed": 1.2},
        {"num_step": 16},
        {"guidance_scale": 3.0},
        {"text": "Claude Code ha finito!"},
    ],
)
def test_key_changes_with_every_parameter(change: dict):
    assert AudioCache.key(replace(PARTS, **change)) != AudioCache.key(PARTS)


# -- storage ----------------------------------------------------------------------------


def test_store_and_lookup_round_trip(tmp_path: Path):
    cache = AudioCache(tmp_path / "audio", max_entries=10, max_age_days=30)
    cache.prepare()
    key = AudioCache.key(PARTS)
    assert cache.lookup(key) is None
    entry = cache.store(key, make_wav(200), voice="server-default", provider="fake", text_chars=22)
    assert entry.path == cache.directory / f"{key}.wav"
    assert (cache.directory / f"{key}.json").exists()
    hit = cache.lookup(key)
    assert hit is not None
    assert (hit.voice, hit.provider, hit.text_chars) == ("server-default", "fake", 22)
    assert hit.path.read_bytes() == make_wav(200)
    assert cache.stats() == (1, len(make_wav(200)))
    assert cache.owns(hit.path) and not cache.owns(tmp_path / f"{key}.wav")

    names = "\n".join(p.name for p in cache.directory.iterdir())
    contents = "".join(p.read_text(errors="ignore") for p in cache.directory.glob("*.json"))
    assert "Claude" not in names and "Claude" not in contents, "text must never reach the disk"
    if sys.platform != "win32":
        assert stat.S_IMODE(cache.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(hit.path.stat().st_mode) == 0o600


def test_store_rejects_bad_key_and_non_wav(tmp_path: Path):
    cache = AudioCache(tmp_path / "audio", max_entries=10, max_age_days=30)
    with pytest.raises(VoxelloError) as exc:
        cache.store("not-a-key", make_wav(), voice="v", provider="p", text_chars=1)
    assert exc.value.code == STORAGE_ERROR
    with pytest.raises(VoxelloError):
        cache.store(AudioCache.key(PARTS), b"garbage", voice="v", provider="p", text_chars=1)
    assert cache.lookup("not-a-key") is None


def test_lookup_drops_orphans_and_corrupt_entries(tmp_path: Path):
    cache = AudioCache(tmp_path / "audio", max_entries=10, max_age_days=30)
    cache.prepare()
    key = AudioCache.key(PARTS)
    (cache.directory / f"{key}.wav").write_bytes(make_wav())  # no sidecar
    assert cache.lookup(key) is None
    assert not (cache.directory / f"{key}.wav").exists()

    cache.store(key, make_wav(), voice="v", provider="p", text_chars=5)
    (cache.directory / f"{key}.wav").write_bytes(b"RIFFbroken")
    assert cache.lookup(key) is None
    assert list(cache.directory.iterdir()) == []


def test_eviction_by_count_keeps_most_recently_used(tmp_path: Path):
    cache = AudioCache(tmp_path / "audio", max_entries=2, max_age_days=30)
    keys = [AudioCache.key(replace(PARTS, text=f"frase {i}")) for i in range(3)]
    now = time.time()
    for i, key in enumerate(keys[:2]):
        cache.store(key, make_wav(), voice="v", provider="p", text_chars=7)
        past = now - (100 - i)
        os.utime(cache.directory / f"{key}.wav", (past, past))
    assert cache.lookup(keys[0]) is not None  # touch: keys[0] is now the most recent
    cache.store(keys[2], make_wav(), voice="v", provider="p", text_chars=7)
    remaining = {e.key for e in cache.entries()}
    assert remaining == {keys[0], keys[2]}, "the least recently used entry is evicted"
    assert not (cache.directory / f"{keys[1]}.json").exists()


def test_eviction_by_age_and_clear(tmp_path: Path):
    cache = AudioCache(tmp_path / "audio", max_entries=10, max_age_days=1)
    old, new = (AudioCache.key(replace(PARTS, text=t)) for t in ("vecchia", "nuova"))
    cache.store(old, make_wav(), voice="v", provider="p", text_chars=7)
    past = time.time() - 2 * 86400
    os.utime(cache.directory / f"{old}.wav", (past, past))
    assert cache.evict() == 1
    assert cache.lookup(old) is None
    cache.store(new, make_wav(), voice="v", provider="p", text_chars=5)
    (cache.directory / "unrelated.txt").write_text("keep")
    assert cache.clear() == 1
    assert cache.stats() == (0, 0)
    assert (cache.directory / "unrelated.txt").exists()
    assert cache.clear() == 0


def test_entries_sorted_most_recent_first(tmp_path: Path):
    cache = AudioCache(tmp_path / "audio", max_entries=10, max_age_days=30)
    keys = [AudioCache.key(replace(PARTS, text=f"frase {i}")) for i in range(3)]
    now = time.time()
    for i, key in enumerate(keys):
        cache.store(key, make_wav(), voice="v", provider="p", text_chars=7)
        os.utime(cache.directory / f"{key}.wav", (now - 10 * i, now - 10 * i))
    assert [e.key for e in cache.entries()] == keys


# -- service integration ----------------------------------------------------------------


async def test_notification_is_cached_and_hit_skips_synthesis(
    service: VoxelloService, provider: FakeProvider, player: FakePlayer
):
    first = await service.speak("Claude Code ha finito.", mode="notification")
    assert first.cached is False
    assert len(provider.calls) == 1
    handle = await player.wait_started()
    assert service.temp_store.owns(handle.path), "a miss plays from the temp store"
    await player.finish_current()
    await settle()

    second = await service.speak("Claude Code ha finito.", mode="notification")
    assert second.cached is True
    assert second.status == "playing"
    assert second.duration_ms == 500
    assert (second.provider, second.voice) == ("fake", "default")
    assert len(provider.calls) == 1, "a hit must not call the provider"
    handle = await player.wait_started()
    assert service.audio_cache is not None and service.audio_cache.owns(handle.path)
    record = service.get_record(second.request_id)
    assert record is not None and record.cached and record.state == RequestState.PLAYING
    status = await service.status()
    assert status.cache_hits == 1

    # Neither the end of playback nor the sweeper nor shutdown may delete a cache file.
    await player.finish_current()
    await settle()
    assert record.state == RequestState.COMPLETED
    assert handle.path.exists()
    service.temp_store.sweep(max_age_seconds=0)
    assert handle.path.exists()
    await service.aclose()
    assert handle.path.exists()


async def test_hit_works_while_tts_is_down(
    service: VoxelloService, provider: FakeProvider, player: FakePlayer, notifier: FakeNotifier
):
    primed = await service.notify("Claude Code chiede un permesso.", channels=["voice"])
    assert primed.status == "delivered"
    await player.wait_started()
    await player.finish_current()
    await settle()

    provider.fail_with = VoxelloError(TTS_PROVIDER_UNAVAILABLE, "down")
    result = await service.notify("Claude Code chiede un permesso.")
    assert result.status == "delivered"
    assert result.channels == {"voice": "playing", "desktop": "sent"}
    assert len(provider.calls) == 1

    other = await service.notify("Frase mai sentita.", channels=["voice"])
    assert other.status == "failed"
    assert other.channels["voice"] == "error:tts_provider_unavailable"


async def test_cache_policy(service: VoxelloService, provider: FakeProvider, player: FakePlayer):
    async def speak(text: str, **kwargs) -> bool:
        result = await service.speak(text, play=False, **kwargs)
        return result.cached

    # verbatim is not cached by default, but can be on request.
    assert await speak("Risposta unica.") is False
    assert await speak("Risposta unica.") is False
    assert await speak("Risposta unica.", cache=True) is False  # stores
    assert await speak("Risposta unica.", cache=True) is True
    assert await speak("Risposta unica.") is False, "default policy ignores the cache for verbatim"
    # notifications are cached by default; cache=False opts out on both ends.
    assert await speak("Avviso.", mode="notification", cache=False) is False
    assert await speak("Avviso.", mode="notification") is False
    assert await speak("Avviso.", mode="notification") is True
    assert await speak("Avviso.", mode="notification", cache=False) is False
    # length bounds apply even to explicit requests.
    long = "x" * (service.settings.cache.max_text_chars + 1)
    assert await speak(long, cache=True) is False
    assert await speak(long, cache=True) is False
    assert service.audio_cache is not None and service.audio_cache.stats()[0] == 2
    assert list(service.temp_store.directory.iterdir()) == []
    assert player.handles == []


async def test_disabled_cache_never_caches(tmp_path: Path, provider: FakeProvider):
    settings = Settings(
        storage={"temp_dir": tmp_path / "tmp"},
        cache={"directory": tmp_path / "cache", "enabled": False},
        output={"directory": tmp_path / "out"},
    )
    svc = VoxelloService.from_settings(settings, provider=provider, player=FakePlayer())
    assert svc.audio_cache is None
    await svc.start()
    try:
        for _ in range(2):
            result = await svc.speak("Avviso.", mode="notification", cache=True, play=False)
            assert result.cached is False
        assert len(provider.calls) == 2
        assert not (tmp_path / "cache").exists()
    finally:
        await svc.aclose()


class FailingCache(AudioCache):
    def store(self, *args, **kwargs):
        raise VoxelloError(STORAGE_ERROR, "disk full")


async def test_cache_write_failure_does_not_fail_speech(
    settings: Settings, provider: FakeProvider, player: FakePlayer
):
    svc = VoxelloService(
        settings,
        provider=provider,
        player=player,
        notifier=FakeNotifier(),
        temp_store=TempStore(settings.storage.resolved_temp_dir(), 1, True),
        output_store=OutputStore(settings.output.resolved_directory()),
        audio_cache=FailingCache(
            settings.cache.resolved_directory(), max_entries=5, max_age_days=1
        ),
    )
    await svc.start()
    try:
        result = await svc.speak("Avviso.", mode="notification")
        assert result.status == "playing" and result.cached is False
    finally:
        await svc.aclose()


async def test_save_works_from_a_cache_hit(service: VoxelloService, player: FakePlayer):
    await service.speak("Salvami.", mode="notification", play=False)
    result = await service.speak("Salvami.", mode="notification", save=True, play=False)
    assert result.cached is True and result.status == "saved"
    assert result.saved_path is not None and Path(result.saved_path).exists()
    assert make_cache(service.settings).stats()[0] == 1
