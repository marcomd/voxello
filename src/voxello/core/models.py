"""Core data models (spec sections 8-11, 21, 22, 24)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class RequestState(StrEnum):
    RECEIVED = "received"
    VALIDATING = "validating"
    GENERATING = "generating"
    GENERATED = "generated"
    PERSISTED = "persisted"
    QUEUED = "queued"
    PLAYING = "playing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


TERMINAL_STATES = frozenset({RequestState.COMPLETED, RequestState.CANCELLED, RequestState.ERROR})

SpeechMode = Literal["verbatim", "summary", "notification"]
Priority = Literal["low", "normal", "high", "critical"]
Channel = Literal["voice", "desktop", "file"]
ServiceStatus = Literal["idle", "generating", "playing", "queued", "stopping", "error"]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class SpeechRequest:
    id: str
    text: str
    voice: str | None
    mode: SpeechMode
    interrupt: bool
    play: bool
    save: bool
    client_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class RequestRecord:
    """Mutable lifecycle record kept for status, cancellation and logging."""

    id: str
    state: RequestState
    text_chars: int
    voice: str | None
    mode: SpeechMode
    client_id: str | None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    audio_path: Path | None = None
    saved_path: Path | None = None
    duration_ms: int | None = None
    error: str | None = None
    cached: bool = False

    def set_state(self, state: RequestState) -> None:
        self.state = state
        self.updated_at = utcnow()


class SpeechResult(BaseModel):
    status: Literal["playing", "queued", "generated", "saved"] = Field(
        description="playing/queued when audio was handed to the player; generated/saved otherwise."
    )
    request_id: str
    duration_ms: int | None = None
    saved_path: str | None = None
    provider: str
    voice: str
    cached: bool = Field(
        default=False, description="True when the audio came from the on-disk cache."
    )


class StopResult(BaseModel):
    status: Literal["stopped", "removed", "idle", "not_found"]
    request_id: str | None = None
    cleared_queue: int = 0


class RequestSummary(BaseModel):
    request_id: str
    state: RequestState
    duration_ms: int | None = None
    client_id: str | None = None
    error: str | None = None


class TTSHealth(BaseModel):
    provider: str
    status: str
    detail: str | None = None
    version: str | None = None


class PlaybackHealth(BaseModel):
    status: str
    backend: str | None = None


class HealthReport(BaseModel):
    voxello: str = "ok"
    tts: TTSHealth
    playback: PlaybackHealth
    desktop_notifications: str


class StatusReport(BaseModel):
    status: ServiceStatus
    request_id: str | None = None
    provider: str
    voice: str
    queue_length: int
    cache_hits: int = Field(default=0, description="Cache hits since the server started.")
    health: HealthReport
    recent: list[RequestSummary] = Field(default_factory=list)


class NotifyResult(BaseModel):
    status: Literal["delivered", "partial", "failed"]
    channels: dict[str, str]
    request_id: str | None = None
    saved_path: str | None = None
