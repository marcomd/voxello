"""Voice design and cloned-voice profiles against omnivoice-server (mocked HTTP).

Response bodies are the ones omnivoice-server 0.2.5 returned on 2026-11-17.
"""

from __future__ import annotations

import json

import httpx2
import pytest

from voxello.errors import (
    INVALID_INSTRUCTIONS,
    TTS_PROVIDER_ERROR,
    TTS_PROVIDER_UNAUTHORIZED,
    TTS_PROVIDER_UNAVAILABLE,
    VOICE_CLONING_UNSUPPORTED,
    VOICE_EXISTS,
    VOICE_NOT_FOUND,
    VoxelloError,
)

from ..conftest import make_wav
from .test_voicestudio import make_provider

ROUTE_NOT_FOUND = {"detail": "Not Found"}
PROFILE_NOT_FOUND = {"error": {"code": "not_found", "message": "Profile 'marco' not found"}}
PROFILE_JSON = {
    "profile_id": "marco",
    "name": "marco",
    "ref_text": "Ciao a tutti",
    "created_at": "2026-11-17T10:00:00Z",
}


def wav_response(_request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, content=make_wav(200), headers={"content-type": "audio/wav"})


# -- voice design ------------------------------------------------------------------


async def test_instructions_are_sent_and_label_the_result():
    provider, seen = make_provider(wav_response)
    result = await provider.synthesize("Hi", None, "en", "female, young adult")
    body = json.loads(seen[0].content)
    assert body["instructions"] == "female, young adult"
    assert "voice" not in body
    assert result.voice == "design:female, young adult"
    await provider.aclose()


async def test_instructions_omitted_when_none():
    provider, seen = make_provider(wav_response)
    await provider.synthesize("Hi")
    assert "instructions" not in json.loads(seen[0].content)
    await provider.aclose()


@pytest.mark.parametrize(
    "message",
    [
        "Unsupported attributes: italian accent. See /v1/voices for supported design attributes.",
        "Conflicting gender attributes: male, female. Only one gender attribute is allowed.",
    ],
)
async def test_rejected_instructions_map_to_invalid_instructions(message):
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            422, json={"error": {"code": "validation_error", "message": message}}
        )

    provider, _ = make_provider(handler)
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("Hi", None, "en", "female")
    assert exc.value.code == INVALID_INSTRUCTIONS
    assert message in exc.value.message
    await provider.aclose()


async def test_missing_clone_voice_404_maps_to_voice_not_found():
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            404,
            json={
                "error": {
                    "code": "not_found",
                    "message": "Voice profile 'ghost' not found. Create it via POST "
                    "/v1/audio/voices/profiles first.",
                }
            },
        )

    provider, _ = make_provider(handler)
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("Hi", "clone:ghost")
    assert exc.value.code == VOICE_NOT_FOUND
    assert "voxello voice clone" in exc.value.message
    await provider.aclose()


async def test_route_404_on_synthesis_stays_a_provider_error():
    provider, _ = make_provider(lambda _r: httpx2.Response(404, json=ROUTE_NOT_FOUND))
    with pytest.raises(VoxelloError) as exc:
        await provider.synthesize("Hi", "clone:x")
    assert exc.value.code == TTS_PROVIDER_ERROR
    await provider.aclose()


async def test_list_voices_keeps_the_type_and_design_attributes_are_exposed():
    voices_body = {
        "voices": [
            {"id": "auto", "type": "auto", "description": "default"},
            {"id": "nova", "type": "preset", "description": "preset"},
            {
                "id": "clone:marco",
                "type": "clone",
                "description": "Cloned voice",
                "profile_id": "marco",
                "created_at": "2026-11-17T10:00:00Z",
                "ref_text": "Ciao a tutti",
            },
        ],
        "design_attributes": {"gender": ["male", "female"], "style": ["whisper"]},
        "total": 3,
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/audio/voices":
            return httpx2.Response(404, json=ROUTE_NOT_FOUND)
        return httpx2.Response(200, json=voices_body)

    provider, _ = make_provider(handler)
    voices = await provider.list_voices()
    assert [(v.id, v.kind) for v in voices] == [
        ("auto", "auto"),
        ("nova", "preset"),
        ("clone:marco", "clone"),
    ]
    assert await provider.design_attributes() == {
        "gender": ["male", "female"],
        "style": ["whisper"],
    }
    profiles = await provider.list_voice_profiles()
    assert [(p.profile_id, p.ref_text, p.created_at) for p in profiles] == [
        ("marco", "Ciao a tutti", "2026-11-17T10:00:00Z")
    ]
    await provider.aclose()


async def test_design_attributes_absent_returns_none():
    provider, _ = make_provider(lambda _r: httpx2.Response(200, json={"voices": []}))
    assert await provider.design_attributes() is None
    await provider.aclose()


# -- cloned-voice profiles ---------------------------------------------------------


def multipart_fields(request: httpx2.Request) -> bytes:
    assert request.headers["content-type"].startswith("multipart/form-data")
    return request.content


async def test_create_profile_posts_multipart_and_parses_the_profile():
    audio = make_wav(3000)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST" and request.url.path == "/v1/voices/profiles"
        return httpx2.Response(201, json=PROFILE_JSON)

    provider, seen = make_provider(handler)
    profile = await provider.create_voice_profile(
        "marco", audio, "sample.wav", ref_text="Ciao a tutti", overwrite=True
    )
    assert profile.profile_id == "marco" and profile.ref_text == "Ciao a tutti"
    body = multipart_fields(seen[0])
    assert b'name="profile_id"\r\n\r\nmarco' in body
    assert b'name="ref_text"\r\n\r\nCiao a tutti' in body
    assert b'name="overwrite"\r\n\r\ntrue' in body
    assert b'name="ref_audio"; filename="sample.wav"' in body
    assert audio in body
    await provider.aclose()


async def test_create_profile_without_ref_text_omits_the_field():
    provider, seen = make_provider(lambda _r: httpx2.Response(201, json=PROFILE_JSON))
    await provider.create_voice_profile("marco", make_wav(100), "a.wav")
    body = multipart_fields(seen[0])
    assert b'name="ref_text"' not in body
    assert b'name="overwrite"\r\n\r\nfalse' in body
    await provider.aclose()


async def test_create_profile_is_never_retried():
    calls = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(503, json={"detail": "loading"})

    provider, _ = make_provider(handler, retries=3, retry_backoff_seconds=0)
    with pytest.raises(VoxelloError) as exc:
        await provider.create_voice_profile("marco", make_wav(100), "a.wav")
    assert exc.value.code == TTS_PROVIDER_ERROR
    assert calls == 1
    await provider.aclose()


async def test_existing_profile_maps_to_voice_exists():
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            409, json={"error": {"code": "conflict", "message": "Profile 'marco' exists"}}
        )

    provider, _ = make_provider(handler)
    with pytest.raises(VoxelloError) as exc:
        await provider.create_voice_profile("marco", make_wav(100), "a.wav")
    assert exc.value.code == VOICE_EXISTS
    assert "overwrite" in exc.value.message
    await provider.aclose()


@pytest.mark.parametrize("status", [404, 405])
async def test_server_without_profiles_maps_to_unsupported(status):
    provider, _ = make_provider(lambda _r: httpx2.Response(status, json=ROUTE_NOT_FOUND))
    with pytest.raises(VoxelloError) as exc:
        await provider.create_voice_profile("marco", make_wav(100), "a.wav")
    assert exc.value.code == VOICE_CLONING_UNSUPPORTED
    with pytest.raises(VoxelloError) as exc:
        await provider.delete_voice_profile("marco")
    assert exc.value.code == VOICE_CLONING_UNSUPPORTED
    await provider.aclose()


async def test_update_profile_patches_only_given_fields():
    audio = make_wav(500)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "PATCH" and request.url.path == "/v1/voices/profiles/marco"
        return httpx2.Response(200, json=PROFILE_JSON)

    provider, seen = make_provider(handler)
    await provider.update_voice_profile("marco", ref_text="Nuovo testo")
    # Without a file the form is URL-encoded, which FastAPI's Form() accepts as well.
    assert seen[0].content == b"ref_text=Nuovo+testo"

    await provider.update_voice_profile("marco", audio=audio, filename="b.wav")
    body = multipart_fields(seen[1])
    assert b'name="ref_audio"; filename="b.wav"' in body
    assert b'name="ref_text"' not in body
    await provider.aclose()


async def test_update_without_changes_is_rejected_locally():
    provider, seen = make_provider(lambda _r: httpx2.Response(200, json=PROFILE_JSON))
    with pytest.raises(ValueError, match="nothing to update"):
        await provider.update_voice_profile("marco")
    assert seen == []
    await provider.aclose()


async def test_delete_profile_and_missing_profile():
    responses = [httpx2.Response(204), httpx2.Response(404, json=PROFILE_NOT_FOUND)]

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "DELETE" and request.url.path == "/v1/voices/profiles/marco"
        return responses.pop(0)

    provider, _ = make_provider(handler)
    await provider.delete_voice_profile("marco")
    with pytest.raises(VoxelloError) as exc:
        await provider.delete_voice_profile("marco")
    assert exc.value.code == VOICE_NOT_FOUND
    assert "marco" in exc.value.message
    await provider.aclose()


async def test_profile_requests_reuse_auth_and_connection_errors():
    provider, _ = make_provider(lambda _r: httpx2.Response(401, json={"detail": "no"}))
    with pytest.raises(VoxelloError) as exc:
        await provider.delete_voice_profile("marco")
    assert exc.value.code == TTS_PROVIDER_UNAUTHORIZED
    await provider.aclose()

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    provider, _ = make_provider(refuse)
    with pytest.raises(VoxelloError) as exc:
        await provider.create_voice_profile("marco", make_wav(100), "a.wav")
    assert exc.value.code == TTS_PROVIDER_UNAVAILABLE
    await provider.aclose()

    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.RemoteProtocolError("garbage", request=request)

    provider, _ = make_provider(broken)
    with pytest.raises(VoxelloError) as exc:
        await provider.delete_voice_profile("marco")
    assert exc.value.code == TTS_PROVIDER_ERROR
    await provider.aclose()


async def test_profile_success_without_json_body_is_a_provider_error():
    provider, _ = make_provider(lambda _r: httpx2.Response(201, content=b"ok"))
    with pytest.raises(VoxelloError) as exc:
        await provider.create_voice_profile("marco", make_wav(100), "a.wav")
    assert exc.value.code == TTS_PROVIDER_ERROR
    await provider.aclose()


async def test_plain_text_404_means_no_profile_api():
    provider, _ = make_provider(lambda _r: httpx2.Response(404, text="nothing here"))
    with pytest.raises(VoxelloError) as exc:
        await provider.update_voice_profile("marco", ref_text="x")
    assert exc.value.code == VOICE_CLONING_UNSUPPORTED
    await provider.aclose()
