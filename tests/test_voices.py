"""Cloned voices: validation of names and reference audio, cache invalidation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from voxello.core.voices import MAX_REFERENCE_BYTES, VoiceLibrary, profile_id_from
from voxello.errors import (
    INVALID_PARAMETER,
    INVALID_REFERENCE_AUDIO,
    VOICE_EXISTS,
    VOICE_NOT_FOUND,
    VoxelloError,
)
from voxello.storage.cache import AudioCache, CacheKeyParts

from .conftest import FakeVoiceProfileStore, make_wav

PARTS = CacheKeyParts(
    text="Fatto.",
    voice=None,
    engine="omnivoice",
    language="it",
    speed=None,
    num_step=None,
    guidance_scale=None,
    base_url="http://127.0.0.1:8880",
)


@pytest.fixture
def store() -> FakeVoiceProfileStore:
    return FakeVoiceProfileStore()


@pytest.fixture
def cache(tmp_path: Path) -> AudioCache:
    return AudioCache(tmp_path / "audio", max_entries=20, max_age_days=30)


def sample(tmp_path: Path, duration_ms: int = 5000, name: str = "sample.wav") -> Path:
    path = tmp_path / name
    path.write_bytes(make_wav(duration_ms))
    return path


def cache_entry(cache: AudioCache, voice: str) -> None:
    key = AudioCache.key(replace(PARTS, voice=voice))
    cache.store(key, make_wav(100), voice=voice, provider="fake", text_chars=6)


@pytest.mark.parametrize(
    ("raw", "expected"), [("marco", "marco"), (" clone:marco ", "marco"), ("a-b_9", "a-b_9")]
)
def test_profile_id_accepts_the_clone_prefix(raw, expected):
    assert profile_id_from(raw) == expected


@pytest.mark.parametrize("raw", ["", "clone:", "has space", "x" * 65, "../etc", "ciao!"])
def test_profile_id_rejects_what_the_server_would(raw):
    with pytest.raises(VoxelloError) as exc:
        profile_id_from(raw)
    assert exc.value.code == INVALID_PARAMETER


async def test_clone_uploads_audio_and_evicts_stale_cache(store, cache, tmp_path):
    cache_entry(cache, "clone:marco")
    cache_entry(cache, "nova")
    outcome = await VoiceLibrary(store, cache).clone(
        "clone:marco", sample(tmp_path), ref_text="  Ciao a tutti  "
    )
    assert outcome.profile.profile_id == "marco"
    assert outcome.warnings == []
    assert outcome.evicted == 1
    audio, filename, ref_text = store.profiles["marco"]
    assert audio == make_wav(5000) and filename == "sample.wav" and ref_text == "Ciao a tutti"
    assert [e.voice for e in cache.entries()] == ["nova"]


async def test_blank_ref_text_lets_the_server_transcribe(store, tmp_path):
    await VoiceLibrary(store).clone("marco", sample(tmp_path), ref_text="  ")
    assert store.profiles["marco"][2] is None


@pytest.mark.parametrize("duration_ms", [1500, 15000])
async def test_duration_outside_recommendation_is_a_warning(store, tmp_path, duration_ms):
    outcome = await VoiceLibrary(store).clone("marco", sample(tmp_path, duration_ms))
    assert len(outcome.warnings) == 1
    assert "3-10 s" in outcome.warnings[0]
    assert "marco" in store.profiles


async def test_non_wav_reference_is_sent_without_duration_check(store, tmp_path):
    path = tmp_path / "voice.mp3"
    path.write_bytes(b"ID3\x04fake-mp3")
    outcome = await VoiceLibrary(store).clone("marco", path)
    assert outcome.warnings == []
    assert store.profiles["marco"][1] == "voice.mp3"


@pytest.mark.parametrize("problem", ["missing", "directory", "empty", "huge"])
async def test_unusable_reference_audio(store, tmp_path, problem):
    path = tmp_path / "ref.wav"
    if problem == "directory":
        path.mkdir()
    elif problem == "empty":
        path.write_bytes(b"")
    elif problem == "huge":
        with path.open("wb") as fh:
            fh.truncate(MAX_REFERENCE_BYTES + 1)
    with pytest.raises(VoxelloError) as exc:
        await VoiceLibrary(store).clone("marco", path)
    assert exc.value.code == INVALID_REFERENCE_AUDIO
    assert store.profiles == {}


async def test_existing_profile_needs_overwrite(store, cache, tmp_path):
    library = VoiceLibrary(store, cache)
    await library.clone("marco", sample(tmp_path))
    with pytest.raises(VoxelloError) as exc:
        await library.clone("marco", sample(tmp_path, 4000))
    assert exc.value.code == VOICE_EXISTS
    await library.clone("marco", sample(tmp_path, 4000), overwrite=True)
    assert store.profiles["marco"][0] == make_wav(4000)


async def test_update_replaces_audio_or_text_and_evicts(store, cache, tmp_path):
    library = VoiceLibrary(store, cache)
    await library.clone("marco", sample(tmp_path), ref_text="vecchio")
    cache_entry(cache, "marco")
    outcome = await library.update("marco", ref_text="nuovo")
    assert outcome.evicted == 1
    assert store.profiles["marco"][2] == "nuovo"
    outcome = await library.update("marco", audio_path=sample(tmp_path, 2000, "b.wav"))
    assert store.profiles["marco"][:2] == (make_wav(2000), "b.wav")
    assert len(outcome.warnings) == 1


async def test_update_without_changes_is_invalid(store):
    for kwargs in ({}, {"ref_text": "   "}):
        with pytest.raises(VoxelloError) as exc:
            await VoiceLibrary(store).update("marco", **kwargs)
        assert exc.value.code == INVALID_PARAMETER


async def test_delete_and_list(store, cache, tmp_path):
    library = VoiceLibrary(store, cache)
    await library.clone("marco", sample(tmp_path))
    await library.clone("anna", sample(tmp_path))
    assert [p.profile_id for p in await library.list()] == ["anna", "marco"]
    cache_entry(cache, "clone:anna")
    assert await library.delete("clone:anna") == 1
    assert [p.profile_id for p in await library.list()] == ["marco"]
    with pytest.raises(VoxelloError) as exc:
        await library.delete("anna")
    assert exc.value.code == VOICE_NOT_FOUND


async def test_server_failure_keeps_the_cache(store, cache, tmp_path):
    cache_entry(cache, "clone:marco")
    store.fail_with = VoxelloError("tts_provider_unavailable", "down")
    with pytest.raises(VoxelloError):
        await VoiceLibrary(store, cache).delete("marco")
    assert len(cache.entries()) == 1
