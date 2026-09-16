"""Structured errors shared by every Voxello layer.

Errors carry a short machine-readable ``code`` (spec section 23) and a concise,
actionable ``message``. The MCP layer turns them into tool errors; the CLI prints them.
"""

from __future__ import annotations

from typing import Any

# Error codes (spec section 23 plus a few needed by the implementation).
INVALID_TEXT = "invalid_text"
TEXT_TOO_LONG = "text_too_long"
INVALID_PARAMETER = "invalid_parameter"
VOICE_NOT_FOUND = "voice_not_found"
TTS_PROVIDER_UNAVAILABLE = "tts_provider_unavailable"
TTS_PROVIDER_UNAUTHORIZED = "tts_provider_unauthorized"
TTS_PROVIDER_ERROR = "tts_provider_error"
PLAYBACK_UNAVAILABLE = "playback_unavailable"
PLAYBACK_ERROR = "playback_error"
QUEUE_FULL = "queue_full"
NOTIFICATION_UNAVAILABLE = "notification_unavailable"
STORAGE_ERROR = "storage_error"


class VoxelloError(Exception):
    """An error with a stable code and a message safe to show to an agent."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": "error", "code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
