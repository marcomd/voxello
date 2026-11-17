"""Opt-in: voice design and cloned voices against a real omnivoice-server.

Run with: VOXELLO_VOICESTUDIO_URL=http://127.0.0.1:8880 uv run pytest -m integration \
tests/integration/test_voice_profiles.py

The reference recording is synthesized by the server itself, so no real person's voice is
needed. The temporary profile is always deleted.
"""

from __future__ import annotations

import os
import secrets

import pytest

from voxello.config import VoiceStudioSettings
from voxello.core.voice_design import VOICE_DESIGN_ATTRIBUTES
from voxello.errors import INVALID_INSTRUCTIONS, VOICE_NOT_FOUND, VoxelloError
from voxello.storage.wav import wav_duration_ms
from voxello.tts.voicestudio import VoiceStudioProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("VOXELLO_VOICESTUDIO_URL"), reason="no TTS server URL"),
]

REFERENCE_TEXT = "Good morning. This is a short recording used as a reference for cloning."


@pytest.fixture
async def provider():
    settings = VoiceStudioSettings(
        base_url=os.environ["VOXELLO_VOICESTUDIO_URL"],
        api_key=os.environ.get("VOXELLO_VOICESTUDIO_API_KEY"),
    )
    provider = VoiceStudioProvider(settings)
    try:
        yield provider
    finally:
        await provider.aclose()


async def test_voice_design_vocabulary_and_synthesis(provider: VoiceStudioProvider):
    attributes = await provider.design_attributes()
    assert attributes is not None, "omnivoice-server publishes design_attributes"
    assert {k: set(v) for k, v in attributes.items()} == {
        k: set(v) for k, v in VOICE_DESIGN_ATTRIBUTES.items()
    }
    result = await provider.synthesize(
        "Voice design works.", None, "en", "female, young adult, high pitch"
    )
    assert result.voice == "design:female, young adult, high pitch"
    assert len(result.audio) > 1000
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("Nope.", None, "en", "female, italian accent")
    assert exc.value.code == INVALID_INSTRUCTIONS


async def test_clone_speak_update_delete(provider: VoiceStudioProvider):
    reference = await provider.synthesize(REFERENCE_TEXT, None, "en", "male, middle-aged")
    duration = wav_duration_ms(reference.audio)
    assert duration is not None and duration > 1000
    profile_id = f"voxello-test-{secrets.token_hex(4)}"
    try:
        profile = await provider.create_voice_profile(
            profile_id, reference.audio, "reference.wav", ref_text=REFERENCE_TEXT
        )
        assert profile.profile_id == profile_id
        assert profile_id in [p.profile_id for p in await provider.list_voice_profiles()]

        spoken = await provider.synthesize(
            "Hello from a cloned voice.", f"clone:{profile_id}", "en"
        )
        assert spoken.voice == f"clone:{profile_id}" and len(spoken.audio) > 1000

        updated = await provider.update_voice_profile(profile_id, ref_text=REFERENCE_TEXT + " ")
        assert updated.profile_id == profile_id
    finally:
        await provider.delete_voice_profile(profile_id)

    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("Gone.", f"clone:{profile_id}", "en")
    assert exc.value.code == VOICE_NOT_FOUND
