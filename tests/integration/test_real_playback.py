"""Opt-in tests that touch real audio hardware or a real VoiceStudio.

Run with: uv run pytest -m integration tests/integration
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from voxello.config import VoiceStudioSettings
from voxello.playback.detect import detect_backend
from voxello.playback.subprocess_player import SubprocessPlayer
from voxello.storage.wav import write_silence
from voxello.tts.voicestudio import VoiceStudioProvider

pytestmark = pytest.mark.integration


@pytest.mark.skipif(sys.platform != "darwin", reason="uses afplay")
async def test_afplay_plays_and_can_be_terminated(tmp_path):
    backend = detect_backend("afplay")
    assert backend is not None
    player = SubprocessPlayer(backend, volume=0.0)
    path = write_silence(tmp_path / "silence.wav", 3000)
    handle = await player.play(path)
    await asyncio.sleep(0.3)
    await handle.terminate()
    assert await asyncio.wait_for(handle.wait(), 3) != 0


@pytest.mark.skipif(not os.environ.get("VOXELLO_VOICESTUDIO_URL"), reason="no VoiceStudio URL")
async def test_real_voicestudio_round_trip():
    settings = VoiceStudioSettings(
        base_url=os.environ["VOXELLO_VOICESTUDIO_URL"],
        api_key=os.environ.get("VOXELLO_VOICESTUDIO_API_KEY"),
    )
    provider = VoiceStudioProvider(settings)
    try:
        health = await provider.health()
        assert health.status == "ok", health.detail
        result = await provider.synthesize("Ciao, sono Voxello.")
        assert result.mime_type == "audio/wav" and len(result.audio) > 1000
    finally:
        await provider.aclose()
