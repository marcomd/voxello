from __future__ import annotations

import json

import httpx2
import pytest
from pydantic import SecretStr

from voxello.config import VoiceStudioSettings
from voxello.errors import (
    TTS_PROVIDER_ERROR,
    TTS_PROVIDER_UNAUTHORIZED,
    TTS_PROVIDER_UNAVAILABLE,
    VOICE_NOT_FOUND,
    VoxelloError,
)
from voxello.tts.voicestudio import VoiceStudioProvider

from ..conftest import make_wav


def make_provider(handler, **overrides) -> tuple[VoiceStudioProvider, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    settings = VoiceStudioSettings(base_url="http://vs.test:3900", **overrides)
    return VoiceStudioProvider(settings, transport=httpx2.MockTransport(wrapped)), seen


async def test_synthesize_sends_expected_payload_and_returns_wav():
    wav = make_wav(300)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/audio/speech"
        body = json.loads(request.content)
        assert body == {
            "input": "Ciao",
            "model": "omnivoice",
            "voice": "marco",
            "response_format": "wav",
            "language": "it",
            "num_step": 16,
        }
        assert request.headers["authorization"] == "Bearer k"
        return httpx2.Response(200, content=wav, headers={"content-type": "audio/wav"})

    provider, _ = make_provider(handler, api_key=SecretStr("k"), num_step=16)
    result = await provider.synthesize("Ciao", "marco", "it")
    assert result.audio == wav and result.mime_type == "audio/wav" and result.voice == "marco"
    await provider.aclose()


async def test_request_language_wins_over_deprecated_setting():
    """Roadmap 3.1: the payload carries the request language, not the global one."""
    seen: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, content=make_wav(10))

    provider, _ = make_provider(handler, language="it")
    await provider.synthesize("Hello", language="en")
    await provider.synthesize("Ciao")
    assert seen[0]["language"] == "en"
    assert seen[1]["language"] == "it", "deprecated tts.voicestudio.language is the fallback"


async def test_language_omitted_when_nothing_is_set():
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert "language" not in json.loads(request.content)
        return httpx2.Response(200, content=make_wav(10))

    provider, _ = make_provider(handler)
    await provider.synthesize("x")


async def test_voice_omitted_by_default_and_no_auth_header():
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert "voice" not in body, "server default must apply when no voice is configured"
        assert "authorization" not in request.headers
        return httpx2.Response(200, content=make_wav(10), headers={"content-type": "audio/wav"})

    provider, _ = make_provider(handler)
    result = await provider.synthesize("x")
    assert result.voice == "server-default"


async def test_configured_voice_is_sent():
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert json.loads(request.content)["voice"] == "alloy"
        return httpx2.Response(200, content=make_wav(10))

    provider, _ = make_provider(handler, voice="alloy")
    assert (await provider.synthesize("x")).voice == "alloy"


async def test_omnivoice_server_error_shape_maps_to_voice_not_found():
    provider, _ = make_provider(
        lambda r: httpx2.Response(
            422,
            json={
                "error": {
                    "code": "validation_error",
                    "message": "Unsupported voice value 'default'. Use a known preset.",
                }
            },
        )
    )
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("x", "default")
    assert exc.value.code == VOICE_NOT_FOUND
    assert "known preset" in exc.value.message


async def test_discovery_falls_back_to_omnivoice_server_routes():
    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/health":
            return httpx2.Response(
                200,
                json={"status": "healthy", "ready": True, "model_loaded": True, "model_id": "k2"},
            )
        if path == "/v1/voices":
            return httpx2.Response(
                200, json={"voices": [{"id": "auto", "description": "fallback"}]}
            )
        if path == "/v1/models":
            return httpx2.Response(200, json={"object": "list", "data": [{"id": "omnivoice"}]})
        return httpx2.Response(404, json={"detail": "Not Found"})

    provider, seen = make_provider(handler)
    health = await provider.health()
    assert health.status == "ok" and health.extra["model_id"] == "k2"
    voices = await provider.list_voices()
    assert voices[0].id == "auto" and voices[0].name == "fallback"
    assert await provider.list_engines() == ["omnivoice"]
    assert [r.url.path for r in seen][1:] == [
        "/v1/audio/voices",
        "/v1/voices",
        "/engines/tts",
        "/v1/models",
    ]


async def test_health_not_ready_is_an_error():
    provider, _ = make_provider(
        lambda r: httpx2.Response(
            200, json={"status": "healthy", "ready": False, "model_loaded": False}
        )
    )
    health = await provider.health()
    assert health.status == "error" and "not ready" in (health.detail or "")


async def test_wav_fallback_with_wrong_content_type_is_accepted():
    provider, _ = make_provider(
        lambda r: httpx2.Response(
            200, content=make_wav(10), headers={"content-type": "application/json"}
        )
    )
    result = await provider.synthesize("x")
    assert result.mime_type == "audio/wav"


async def test_non_wav_audio_is_an_error():
    provider, _ = make_provider(
        lambda r: httpx2.Response(
            200, content=b"ID3\x03mp3", headers={"content-type": "audio/mpeg"}
        )
    )
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("x")
    assert exc.value.code == TTS_PROVIDER_ERROR
    assert "audio/mpeg" in exc.value.message


async def test_connection_refused_is_unavailable():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    provider, _ = make_provider(handler)
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("x")
    assert exc.value.code == TTS_PROVIDER_UNAVAILABLE
    assert "vs.test:3900" in exc.value.message
    health = await provider.health()
    assert health.status == "error"


async def test_unauthorized():
    provider, _ = make_provider(lambda r: httpx2.Response(401, json={"detail": "unauthorized"}))
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("x")
    assert exc.value.code == TTS_PROVIDER_UNAUTHORIZED


async def test_422_voice_error_maps_to_voice_not_found():
    provider, _ = make_provider(
        lambda r: httpx2.Response(
            422, json={"detail": [{"loc": ["body", "voice"], "msg": "voice profile not found"}]}
        )
    )
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("x", "ghost")
    assert exc.value.code == VOICE_NOT_FOUND
    assert "ghost" in exc.value.message and "voice profile not found" in exc.value.message


async def test_422_other_error_is_provider_error():
    provider, _ = make_provider(
        lambda r: httpx2.Response(
            422, json={"detail": [{"loc": ["body", "speed"], "msg": "too fast"}]}
        )
    )
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("x")
    assert exc.value.code == TTS_PROVIDER_ERROR
    assert "speed: too fast" in exc.value.message


async def test_too_long_input_rejected_locally():
    provider, seen = make_provider(lambda r: httpx2.Response(200, content=make_wav(10)))
    with pytest.raises(VoxelloError):
        await provider.synthesize("x" * 5000)
    assert seen == []


async def test_health_and_voices_parsing():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/health":
            return httpx2.Response(200, json={"status": "ok", "version": "0.5.0", "device": "mps"})
        if request.url.path == "/v1/audio/voices":
            return httpx2.Response(
                200, json={"voices": [{"id": "p1", "name": "Marco"}, "default", {"voice_id": "p2"}]}
            )
        if request.url.path == "/engines/tts":
            return httpx2.Response(200, json=[{"id": "omnivoice"}, "kittentts"])
        return httpx2.Response(404)

    provider, _ = make_provider(handler)
    health = await provider.health()
    assert health.status == "ok" and health.version == "0.5.0"
    voices = await provider.list_voices()
    assert [v.id for v in voices] == ["p1", "default", "p2"]
    assert voices[0].name == "Marco"
    assert await provider.list_engines() == ["omnivoice", "kittentts"]
