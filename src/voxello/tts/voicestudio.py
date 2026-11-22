"""VoiceStudio / omnivoice-server provider.

Talks to an OpenAI-compatible ``POST /v1/audio/speech`` endpoint as exposed by
VoiceStudio (default ``http://localhost:3900``) and by the standalone
``omnivoice-server`` (default port 8880). A successful response is raw audio, so we
never parse a 200 as JSON and always sniff the WAV header, because an unavailable
encoder can fall back to WAV. Discovery endpoints differ between the two servers, so
listing voices and engines tries VoiceStudio's paths first and falls back to the
omnivoice-server ones. See ``docs/voicestudio-api.md``.

Retry policy (roadmap milestone 4): a synthesis request is retried ``retries`` times, with
exponential backoff starting at ``retry_backoff_seconds``, when the connection fails
(connect error or connect timeout, connection dropped) or the server answers HTTP 502/503/504
(typical while the model is still loading). A 4xx is the caller's problem and is never
retried. A read timeout is not retried either: the server accepted the request and is just
slow, and a second attempt would double the time the service holds its synthesis lock.

Voice design and cloning (omnivoice-server only): ``instructions`` carries canonical
voice-design attributes on the synthesis request; cloned voices are server-side profiles under
``/v1/voices/profiles`` (multipart upload of the reference audio) used afterwards as
``voice="clone:<id>"``. Profile requests are never retried: a create is not idempotent and
the caller is a person at the CLI who can simply run it again. VoiceStudio exposes neither
(its design field is ``instruct``), which surfaces as ``voice_cloning_unsupported``.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from typing import Any

import httpx2

from voxello.config import VoiceStudioSettings
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
from voxello.storage.wav import is_wav
from voxello.tts.base import (
    ProviderHealth,
    SynthesisOptions,
    SynthesisResult,
    VoiceInfo,
    VoiceProfile,
)

log = logging.getLogger(__name__)

SPEECH_PATH = "/v1/audio/speech"
HEALTH_PATH = "/health"
VOICES_PATHS = ("/v1/audio/voices", "/v1/voices")  # VoiceStudio, then omnivoice-server
ENGINES_PATHS = ("/engines/tts", "/v1/models")
PROFILES_PATH = "/v1/voices/profiles"  # omnivoice-server cloned voices
DEFAULT_VOICE_LABEL = "server-default"
MAX_INPUT_CHARS = 4096  # hard limit of the VoiceStudio SpeechRequest schema
RETRIABLE_STATUSES = frozenset({502, 503, 504})
# Connection-phase failures. ``NetworkError`` covers ConnectError, ReadError, WriteError and
# CloseError; ``ConnectTimeout`` is the only ``TimeoutException`` worth a second attempt.
RETRIABLE_EXCEPTIONS: tuple[type[Exception], ...] = (httpx2.NetworkError, httpx2.ConnectTimeout)


class VoiceStudioProvider:
    name = "voicestudio"

    def __init__(
        self, settings: VoiceStudioSettings, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        headers = {"Accept": "audio/wav, application/json;q=0.9, */*;q=0.1"}
        if settings.api_key is not None:
            headers["Authorization"] = f"Bearer {settings.api_key.get_secret_value()}"
        self._client = httpx2.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx2.Timeout(
                settings.timeout_seconds, connect=settings.connect_timeout_seconds
            ),
            headers=headers,
            transport=transport,
        )

    @property
    def base_url(self) -> str:
        return self.settings.base_url

    def _payload(
        self,
        text: str,
        voice: str | None,
        language: str | None = None,
        instructions: str | None = None,
        options: SynthesisOptions | None = None,
    ) -> dict[str, Any]:
        s = self.settings
        payload: dict[str, Any] = {
            "input": text,
            "model": s.engine,
            "response_format": "wav",
        }
        optional = {
            # A configured voice would win over the design on the server (profiles beat
            # instructions), so it is only a fallback when no design is requested.
            "voice": voice or (None if instructions else s.voice),
            "instructions": instructions,
            # The request language wins; the deprecated tts.voicestudio.language is the last
            # fallback for callers that bypass the service (roadmap 3.1).
            "language": language if language is not None else s.language,
            "speed": s.speed,
            "num_step": s.num_step,
            "guidance_scale": s.guidance_scale,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        if options is not None:
            payload.update(options.as_payload())
        return payload

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        instructions: str | None = None,
        *,
        options: SynthesisOptions | None = None,
    ) -> SynthesisResult:
        if len(text) > MAX_INPUT_CHARS:
            raise VoxelloError(
                TTS_PROVIDER_ERROR, f"VoiceStudio accepts at most {MAX_INPUT_CHARS} characters."
            )
        payload = self._payload(text, voice, language, instructions, options)
        response = await self._post_speech(payload)

        voice_label = payload.get("voice") or (
            f"design:{instructions}" if instructions else DEFAULT_VOICE_LABEL
        )
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        body = response.content
        if not body:
            raise VoxelloError(TTS_PROVIDER_ERROR, "VoiceStudio returned an empty audio response.")
        if is_wav(body):
            return SynthesisResult(body, "audio/wav", self.name, voice_label)
        if content_type.startswith("audio/"):
            raise VoxelloError(
                TTS_PROVIDER_ERROR,
                f"VoiceStudio returned {content_type} instead of WAV; check the engine's encoders.",
            )
        raise VoxelloError(
            TTS_PROVIDER_ERROR,
            f"VoiceStudio returned an unexpected response ({content_type or 'no content type'}).",
        )

    async def _post_speech(self, payload: dict[str, Any]) -> httpx2.Response:
        """POST the synthesis request, retrying only what the module docstring allows.

        Returns the 200 response; any other outcome is raised as a ``VoxelloError`` whose
        ``details["attempts"]`` says how many requests were made.
        """
        attempts = self.settings.retries + 1
        for attempt in range(1, attempts + 1):
            error: VoxelloError
            reason: str
            try:
                response = await self._client.post(SPEECH_PATH, json=payload)
            except RETRIABLE_EXCEPTIONS as exc:
                error, reason = self._unavailable(exc), type(exc).__name__
            except httpx2.TimeoutException as exc:
                error = self._unavailable(exc)
                error.details["attempts"] = attempt
                raise error from exc
            except httpx2.HTTPError as exc:
                raise VoxelloError(
                    TTS_PROVIDER_ERROR,
                    f"VoiceStudio request failed: {exc}",
                    details={"attempts": attempt},
                ) from exc
            else:
                if response.status_code == 200:
                    return response
                error = self._status_error(
                    response, payload.get("voice"), payload.get("instructions")
                )
                error.details["attempts"] = attempt
                if response.status_code not in RETRIABLE_STATUSES:
                    raise error
                reason = f"HTTP {response.status_code}"
            if attempt == attempts:
                error.details["attempts"] = attempt
                raise error
            delay = self.settings.retry_backoff_seconds * 2 ** (attempt - 1)
            log.warning(
                "TTS attempt %d/%d failed (%s); retrying in %.1fs", attempt, attempts, reason, delay
            )
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def list_voices(self) -> list[VoiceInfo]:
        data = await self._get_json_first(VOICES_PATHS)
        items: Any = data
        if isinstance(data, dict):
            items = data.get("voices") or data.get("data") or data.get("items") or []
        voices: list[VoiceInfo] = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                voices.append(VoiceInfo(id=item))
            elif isinstance(item, dict):
                vid = (
                    item.get("id")
                    or item.get("voice_id")
                    or item.get("profile_id")
                    or item.get("name")
                )
                if vid:
                    voices.append(
                        VoiceInfo(
                            id=str(vid),
                            name=item.get("name")
                            or item.get("display_name")
                            or item.get("description"),
                            language=item.get("language"),
                            kind=item.get("type"),
                        )
                    )
        return voices

    async def design_attributes(self) -> dict[str, list[str]] | None:
        """The voice-design vocabulary the server publishes, or ``None`` if it has none."""
        data = await self._get_json_first(VOICES_PATHS)
        attributes = data.get("design_attributes") if isinstance(data, dict) else None
        if not isinstance(attributes, dict):
            return None
        return {
            str(category): [str(v) for v in values]
            for category, values in attributes.items()
            if isinstance(values, list)
        }

    # -- cloned voices (omnivoice-server profiles) -------------------------------

    async def list_voice_profiles(self) -> list[VoiceProfile]:
        data = await self._get_json_first(VOICES_PATHS)
        items = data.get("voices") if isinstance(data, dict) else data
        profiles: list[VoiceProfile] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("type") != "clone":
                continue
            profile_id = item.get("profile_id") or str(item.get("id", "")).removeprefix("clone:")
            if profile_id:
                profiles.append(
                    VoiceProfile(str(profile_id), item.get("ref_text"), item.get("created_at"))
                )
        return profiles

    async def create_voice_profile(
        self,
        profile_id: str,
        audio: bytes,
        filename: str,
        *,
        ref_text: str | None = None,
        overwrite: bool = False,
    ) -> VoiceProfile:
        data = {"profile_id": profile_id, "overwrite": "true" if overwrite else "false"}
        if ref_text is not None:
            data["ref_text"] = ref_text
        response = await self._profile_request(
            "POST", PROFILES_PATH, profile_id, data=data, files=_audio_file(audio, filename)
        )
        return _parse_profile(response, profile_id)

    async def update_voice_profile(
        self,
        profile_id: str,
        *,
        audio: bytes | None = None,
        filename: str | None = None,
        ref_text: str | None = None,
    ) -> VoiceProfile:
        if audio is None and ref_text is None:
            raise ValueError("nothing to update: pass audio, ref_text or both")
        data = {"ref_text": ref_text} if ref_text is not None else None
        files = _audio_file(audio, filename or "ref_audio.wav") if audio is not None else None
        response = await self._profile_request(
            "PATCH", f"{PROFILES_PATH}/{profile_id}", profile_id, data=data, files=files
        )
        return _parse_profile(response, profile_id)

    async def delete_voice_profile(self, profile_id: str) -> None:
        await self._profile_request("DELETE", f"{PROFILES_PATH}/{profile_id}", profile_id)

    async def _profile_request(
        self,
        method: str,
        path: str,
        profile_id: str,
        *,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> httpx2.Response:
        try:
            response = await self._client.request(method, path, data=data, files=files)
        except (httpx2.NetworkError, httpx2.TimeoutException) as exc:
            raise self._unavailable(exc) from exc
        except httpx2.HTTPError as exc:
            raise VoxelloError(TTS_PROVIDER_ERROR, f"Voice profile request failed: {exc}") from exc
        code = response.status_code
        if code in (200, 201, 204):
            return response
        if _route_missing(response):
            raise VoxelloError(
                VOICE_CLONING_UNSUPPORTED,
                f"The TTS server at {self.base_url} has no voice-profile API ({PROFILES_PATH}); "
                "cloned voices need omnivoice-server.",
                details={"http_status": code},
            )
        if code == 404:
            raise VoxelloError(
                VOICE_NOT_FOUND,
                f"There is no cloned voice '{profile_id}' on the TTS server: "
                f"{_extract_detail(response)}",
                details={"http_status": code},
            )
        if code == 409:
            raise VoxelloError(
                VOICE_EXISTS,
                f"A cloned voice named '{profile_id}' already exists on the TTS server; "
                "use overwrite to replace it.",
                details={"http_status": code},
            )
        raise self._status_error(response, None)

    async def list_engines(self) -> list[str]:
        data = await self._get_json_first(ENGINES_PATHS)
        items: Any = data
        if isinstance(data, dict):
            items = data.get("engines") or data.get("backends") or data.get("data") or []
        engines: list[str] = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                engines.append(item)
            elif isinstance(item, dict):
                eid = item.get("id") or item.get("engine_id") or item.get("name")
                if eid:
                    engines.append(str(eid))
        return engines

    async def health(self) -> ProviderHealth:
        try:
            response = await self._client.get(HEALTH_PATH, timeout=5.0)
        except httpx2.HTTPError as exc:
            return ProviderHealth("error", self._unavailable(exc).message)
        if response.status_code in (401, 403):
            return ProviderHealth("error", "The TTS server rejected the API key (remote access).")
        if response.status_code != 200:
            return ProviderHealth(
                "error", f"The TTS server /health returned HTTP {response.status_code}."
            )
        version: str | None = None
        extra: dict[str, object] = {}
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            version = str(body["version"]) if body.get("version") else None
            extra = {
                k: v
                for k, v in body.items()
                if k in ("status", "device", "version", "model_id", "ready", "model_loaded")
            }
            status = str(body.get("status", "ok")).lower()
            not_ready = body.get("ready") is False or body.get("model_loaded") is False
            if status not in ("ok", "healthy", "ready") or not_ready:
                return ProviderHealth(
                    "error",
                    f"The TTS server reports status '{status}' (not ready).",
                    version,
                    extra,
                )
        return ProviderHealth("ok", None, version, extra)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- helpers -----------------------------------------------------------------

    async def _get_json_first(self, paths: tuple[str, ...]) -> Any:
        """GET the first path that does not answer 404 (servers differ in their routes)."""
        last: VoxelloError | None = None
        for path in paths:
            try:
                return await self._get_json(path)
            except VoxelloError as exc:
                if exc.details.get("http_status") != 404:
                    raise
                last = exc
        assert last is not None
        raise last

    async def _get_json(self, path: str) -> Any:
        try:
            response = await self._client.get(path, timeout=10.0)
        except httpx2.HTTPError as exc:
            raise self._unavailable(exc) from exc
        if response.status_code != 200:
            raise self._status_error(response, None)
        try:
            return response.json()
        except ValueError as exc:
            raise VoxelloError(
                TTS_PROVIDER_ERROR, f"The TTS server {path} did not return JSON."
            ) from exc

    def _unavailable(self, exc: Exception) -> VoxelloError:
        return VoxelloError(
            TTS_PROVIDER_UNAVAILABLE,
            f"The TTS server is not reachable at {self.base_url}. "
            "Make sure VoiceStudio (or omnivoice-server) is running and the URL is correct.",
            details={"reason": type(exc).__name__},
        )

    def _status_error(
        self, response: httpx2.Response, voice: str | None, instructions: str | None = None
    ) -> VoxelloError:
        code = response.status_code
        detail = _extract_detail(response)
        if code in (401, 403):
            return VoxelloError(
                TTS_PROVIDER_UNAUTHORIZED,
                "The TTS server rejected the request: an API key is required for remote access "
                "(set tts.voicestudio.api_key).",
                details={"http_status": code},
            )
        if code == 404 and voice and not _route_missing(response):
            # omnivoice-server answers 404 for an unknown `clone:<id>` voice.
            return VoxelloError(
                VOICE_NOT_FOUND,
                f"The requested voice '{voice}' is not available: {detail} "
                "(create cloned voices with `voxello voice clone NAME FILE`).",
                details={"http_status": code},
            )
        if code == 404:
            return VoxelloError(
                TTS_PROVIDER_ERROR,
                f"Endpoint {response.url.path} not found at {self.base_url}; "
                "is this a VoiceStudio (3900) or omnivoice-server (8880) API?",
                details={"http_status": code},
            )
        if code in (400, 422):
            lowered = detail.lower()
            if instructions and ("attribute" in lowered or "instruction" in lowered):
                return VoxelloError(
                    INVALID_INSTRUCTIONS,
                    f"The TTS server rejected the voice design '{instructions}': {detail}",
                    details={"http_status": code},
                )
            if voice and ("voice" in lowered or "profile" in lowered):
                return VoxelloError(
                    VOICE_NOT_FOUND,
                    f"The requested voice '{voice}' is not available: {detail}",
                    details={"http_status": code},
                )
            return VoxelloError(
                TTS_PROVIDER_ERROR,
                f"The TTS server rejected the request: {detail}",
                details={"http_status": code},
            )
        if code >= 500:
            return VoxelloError(
                TTS_PROVIDER_ERROR,
                f"The TTS server failed to synthesize (HTTP {code}): {detail}",
                details={"http_status": code},
            )
        return VoxelloError(
            TTS_PROVIDER_ERROR,
            f"The TTS server returned HTTP {code}: {detail}",
            details={"http_status": code},
        )


def _route_missing(response: httpx2.Response) -> bool:
    """True when the server lacks the route itself (FastAPI's bare 404/405), not a resource."""
    if response.status_code == 405:
        return True
    if response.status_code != 404:
        return False
    try:
        body = response.json()
    except ValueError:
        return True
    return isinstance(body, dict) and body.get("detail") == "Not Found"


def _audio_file(audio: bytes, filename: str) -> dict[str, tuple[str, bytes, str]]:
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return {"ref_audio": (filename, audio, mime)}


def _parse_profile(response: httpx2.Response, profile_id: str) -> VoiceProfile:
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise VoxelloError(
            TTS_PROVIDER_ERROR,
            f"The TTS server did not describe the cloned voice '{profile_id}' (no JSON body).",
        )
    return VoiceProfile(
        str(body.get("profile_id") or profile_id), body.get("ref_text"), body.get("created_at")
    )


def _extract_detail(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:200] if text else f"HTTP {response.status_code}"
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body.get("message") or body
        if isinstance(detail, list):
            parts = []
            for item in detail:
                if isinstance(item, dict):
                    loc = ".".join(str(x) for x in item.get("loc", []) if x != "body")
                    msg = item.get("msg", "")
                    parts.append(f"{loc}: {msg}".strip(": "))
                else:
                    parts.append(str(item))
            return "; ".join(parts)[:300]
        if isinstance(detail, dict) and "message" in detail:
            return str(detail["message"])[:300]
        return str(detail)[:300]
    return str(body)[:300]
