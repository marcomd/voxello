"""TTS provider abstraction (spec section 16)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from voxello.errors import INVALID_PARAMETER, VoxelloError

# omnivoice-server's SpeechRequest bounds; checked locally so a bad value in a dialogue script
# is reported with the line it came from instead of as an HTTP 422.
SEED_RANGE = (0, 2**32 - 1)
SPEED_RANGE = (0.25, 4.0)
NUM_STEP_RANGE = (1, 64)


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    mime_type: str
    provider: str
    voice: str


@dataclass(frozen=True)
class SynthesisOptions:
    """Per-request generation values (roadmap 8.1); ``None`` keeps the configured value.

    ``seed`` makes a take reproducible: the same text, voice, values and seed give the same
    audio on omnivoice-server, and another seed gives another take.
    """

    seed: int | None = None
    speed: float | None = None
    num_step: int | None = None

    def __post_init__(self) -> None:
        for name, value, (low, high) in (
            ("seed", self.seed, SEED_RANGE),
            ("speed", self.speed, SPEED_RANGE),
            ("num_step", self.num_step, NUM_STEP_RANGE),
        ):
            if value is not None and not low <= value <= high:
                raise VoxelloError(
                    INVALID_PARAMETER, f"{name} must be between {low} and {high}, not {value}."
                )

    def as_payload(self) -> dict[str, Any]:
        """The values that are set, as synthesis request fields."""
        values = {"seed": self.seed, "speed": self.speed, "num_step": self.num_step}
        return {k: v for k, v in values.items() if v is not None}


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    name: str | None = None
    language: str | None = None
    kind: str | None = None  # omnivoice-server `type`: auto, design, preset, file, clone


@dataclass(frozen=True)
class VoiceProfile:
    """A cloned voice stored on the TTS server (omnivoice-server `/v1/voices/profiles`)."""

    profile_id: str
    ref_text: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class ProviderHealth:
    status: str  # "ok" | "error"
    detail: str | None = None
    version: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


class TTSProvider(Protocol):
    name: str

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        instructions: str | None = None,
        *,
        options: SynthesisOptions | None = None,
    ) -> SynthesisResult:
        """Synthesize ``text``.

        ``language`` is the ISO 639-1 code of the text (roadmap 3.1); ``instructions`` are
        canonical voice-design attributes, sent instead of a voice (the service never passes
        both). ``options`` override the configured generation values for this request only;
        the service never passes them, so cached audio keeps its key.
        """
        ...

    async def list_voices(self) -> list[VoiceInfo]: ...

    async def list_engines(self) -> list[str]: ...

    async def health(self) -> ProviderHealth: ...

    async def aclose(self) -> None: ...


class VoiceProfileStore(Protocol):
    """Server-side storage of cloned voices, referenced afterwards as ``clone:<profile_id>``."""

    async def create_voice_profile(
        self,
        profile_id: str,
        audio: bytes,
        filename: str,
        *,
        ref_text: str | None = None,
        overwrite: bool = False,
    ) -> VoiceProfile: ...

    async def update_voice_profile(
        self,
        profile_id: str,
        *,
        audio: bytes | None = None,
        filename: str | None = None,
        ref_text: str | None = None,
    ) -> VoiceProfile: ...

    async def delete_voice_profile(self, profile_id: str) -> None: ...

    async def list_voice_profiles(self) -> list[VoiceProfile]: ...
