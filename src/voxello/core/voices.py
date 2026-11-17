"""Cloned voices: server-side profiles managed from the CLI.

OmniVoice clones a voice from a short reference recording. omnivoice-server keeps the
recording as a named profile (``/v1/voices/profiles``) that any request can then use as
``voice="clone:<name>"``, so Voxello stores nothing locally and every client of the server
shares the same voices (see ``docs/decisions/0001-voice-profiles-on-server.md``).

This module holds the policy the CLI must not: name and reference-audio validation, the
upstream 3-10 s recommendation (a warning, the server decides), and dropping cached audio
recorded under a name whose reference audio changes. Cloning is deliberately not exposed
to MCP agents: only voices whose owner has given permission should be cloned (spec 26).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from voxello.config import VOICE_NAME_RE
from voxello.errors import INVALID_PARAMETER, INVALID_REFERENCE_AUDIO, VoxelloError
from voxello.storage.cache import AudioCache
from voxello.storage.wav import is_wav, wav_duration_ms
from voxello.tts.base import VoiceProfile, VoiceProfileStore

log = logging.getLogger(__name__)

CLONE_PREFIX = "clone:"
MAX_REFERENCE_BYTES = 25 * 1024 * 1024  # omnivoice-server's default max_ref_audio_mb
RECOMMENDED_SECONDS = (3.0, 10.0)  # OmniVoice README: longer is slower and may clone worse


@dataclass(frozen=True)
class VoiceChange:
    profile: VoiceProfile
    warnings: list[str] = field(default_factory=list)
    evicted: int = 0  # cache entries dropped because they used the old reference audio


def profile_id_from(name: str) -> str:
    """Accept ``marco`` or ``clone:marco``; enforce the server's profile-id alphabet."""
    profile_id = name.strip().removeprefix(CLONE_PREFIX)
    if not VOICE_NAME_RE.fullmatch(profile_id):
        raise VoxelloError(
            INVALID_PARAMETER,
            f"Invalid voice name {name!r}: use 1-64 letters, digits, '-' or '_'.",
        )
    return profile_id


def read_reference_audio(path: Path) -> tuple[bytes, list[str]]:
    """Read a reference recording; returns its bytes and non-blocking warnings."""
    if not path.is_file():
        raise VoxelloError(INVALID_REFERENCE_AUDIO, f"Reference audio {path} is not a file.")
    size = path.stat().st_size
    if size == 0:
        raise VoxelloError(INVALID_REFERENCE_AUDIO, f"Reference audio {path} is empty.")
    if size > MAX_REFERENCE_BYTES:
        raise VoxelloError(
            INVALID_REFERENCE_AUDIO,
            f"Reference audio {path} is {size // (1024 * 1024)} MB; the server accepts at most "
            f"{MAX_REFERENCE_BYTES // (1024 * 1024)} MB. A 3-10 s clip is enough.",
        )
    try:
        audio = path.read_bytes()
    except OSError as exc:  # pragma: no cover - permission races
        raise VoxelloError(INVALID_REFERENCE_AUDIO, f"Cannot read {path}: {exc}") from exc
    warnings: list[str] = []
    duration = wav_duration_ms(audio) if is_wav(audio) else None
    low, high = RECOMMENDED_SECONDS
    if duration is not None and not low * 1000 <= duration <= high * 1000:
        warnings.append(
            f"The reference audio lasts {duration / 1000:.1f} s; OmniVoice recommends "
            f"{low:.0f}-{high:.0f} s (shorter clones worse, longer is slower)."
        )
    return audio, warnings


def _clean_ref_text(ref_text: str | None) -> str | None:
    """Blank means unknown: the server then transcribes the recording with Whisper."""
    if ref_text is None:
        return None
    return " ".join(ref_text.split()) or None


class VoiceLibrary:
    def __init__(self, store: VoiceProfileStore, audio_cache: AudioCache | None = None) -> None:
        self.store = store
        self.audio_cache = audio_cache

    async def clone(
        self,
        name: str,
        audio_path: Path,
        *,
        ref_text: str | None = None,
        overwrite: bool = False,
    ) -> VoiceChange:
        profile_id = profile_id_from(name)
        audio, warnings = read_reference_audio(audio_path)
        profile = await self.store.create_voice_profile(
            profile_id,
            audio,
            audio_path.name,
            ref_text=_clean_ref_text(ref_text),
            overwrite=overwrite,
        )
        log.info("cloned voice %s created (%d bytes of reference audio)", profile_id, len(audio))
        return VoiceChange(profile, warnings, self._evict(profile_id))

    async def update(
        self, name: str, *, audio_path: Path | None = None, ref_text: str | None = None
    ) -> VoiceChange:
        profile_id = profile_id_from(name)
        text = _clean_ref_text(ref_text)
        if audio_path is None and text is None:
            raise VoxelloError(
                INVALID_PARAMETER, "Nothing to update: pass new reference audio, text or both."
            )
        audio, warnings = read_reference_audio(audio_path) if audio_path is not None else (None, [])
        profile = await self.store.update_voice_profile(
            profile_id,
            audio=audio,
            filename=audio_path.name if audio_path is not None else None,
            ref_text=text,
        )
        log.info("cloned voice %s updated", profile_id)
        return VoiceChange(profile, warnings, self._evict(profile_id))

    async def delete(self, name: str) -> int:
        """Delete the profile; returns the number of cache entries dropped with it."""
        profile_id = profile_id_from(name)
        await self.store.delete_voice_profile(profile_id)
        log.info("cloned voice %s deleted", profile_id)
        return self._evict(profile_id)

    async def list(self) -> list[VoiceProfile]:
        return await self.store.list_voice_profiles()

    def _evict(self, profile_id: str) -> int:
        """Drop audio cached under either spelling of the voice (the server accepts both)."""
        if self.audio_cache is None:
            return 0
        return self.audio_cache.evict_voice({profile_id, f"{CLONE_PREFIX}{profile_id}"})
