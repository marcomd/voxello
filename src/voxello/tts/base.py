"""TTS provider abstraction (spec section 16)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    mime_type: str
    provider: str
    voice: str


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    name: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class ProviderHealth:
    status: str  # "ok" | "error"
    detail: str | None = None
    version: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


class TTSProvider(Protocol):
    name: str

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> SynthesisResult:
        """Synthesize ``text``; ``language`` is the ISO 639-1 code of the text (roadmap 3.1)."""
        ...

    async def list_voices(self) -> list[VoiceInfo]: ...

    async def list_engines(self) -> list[str]: ...

    async def health(self) -> ProviderHealth: ...

    async def aclose(self) -> None: ...
