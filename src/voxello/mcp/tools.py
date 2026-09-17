"""MCP tool definitions: speak, stop_speaking, get_status, notify."""

from __future__ import annotations

import logging
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from voxello.core.models import (
    Channel,
    NotifyResult,
    Priority,
    SpeechMode,
    SpeechResult,
    StatusReport,
    StopResult,
)
from voxello.errors import VoxelloError

log = logging.getLogger(__name__)


class ServiceHolder:
    """Late-bound reference to the running service, filled in by the lifespan."""

    def __init__(self) -> None:
        from voxello.core.service import VoxelloService  # local import keeps startup lean

        self._type = VoxelloService
        self.service: VoxelloService | None = None

    def get(self):
        if self.service is None:
            raise ToolError("voxello_not_ready: the Voxello service has not started yet.")
        return self.service


def register_tools(mcp: MCPServer, holder: ServiceHolder) -> None:
    @mcp.tool(
        title="Speak",
        description=(
            "Synthesize text with the configured TTS provider and play it on the user's local "
            "speaker. Keep spoken text short (under ~20 seconds, a few sentences): voice is for "
            "summaries, status, alerts and completion notices; details belong in your textual "
            "answer. Returns metadata, never audio. Do not paraphrase the user's request into "
            "speech unless asked to read something aloud."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def speak(
        text: Annotated[str, Field(description="Text to speak, verbatim.", min_length=1)],
        voice: Annotated[
            str | None, Field(description="Voice profile id; omit for the configured default.")
        ] = None,
        interrupt: Annotated[
            bool | None,
            Field(description="Stop current playback first (default from config, usually true)."),
        ] = None,
        save: Annotated[bool, Field(description="Also persist the audio file.")] = False,
        play: Annotated[bool, Field(description="Play the audio locally.")] = True,
        mode: Annotated[
            SpeechMode,
            Field(description="Presentation hint: verbatim, summary or notification."),
        ] = "verbatim",
        client_id: Annotated[
            str | None, Field(description="Optional identifier of the calling agent, for logs.")
        ] = None,
        cache: Annotated[
            bool | None,
            Field(
                description=(
                    "Reuse or store the synthesized audio on disk for repeated phrases. Default: "
                    "only for mode='notification'. Set true for a fixed phrase you will repeat, "
                    "false for one-off text."
                )
            ),
        ] = None,
        language: Annotated[
            str | None,
            Field(
                description=(
                    "ISO 639-1 code of the language the text is written in (e.g. 'it', 'en'). "
                    "Pass it whenever you know it so pronunciation matches the text; omit for "
                    "the configured default."
                )
            ),
        ] = None,
    ) -> SpeechResult:
        service = holder.get()
        try:
            return await service.speak(
                text,
                voice=voice,
                interrupt=interrupt,
                save=save,
                play=play,
                mode=mode,
                client_id=client_id,
                cache=cache,
                language=language,
            )
        except VoxelloError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Stop speaking",
        description=(
            "Stop the audio currently playing and clear the queue. Pass request_id to stop or "
            "dequeue only that request."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def stop_speaking(
        request_id: Annotated[
            str | None, Field(description="Request to stop; omit to stop everything.")
        ] = None,
    ) -> StopResult:
        service = holder.get()
        try:
            return await service.stop(request_id)
        except VoxelloError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Get status",
        description=(
            "Report Voxello's state (idle, generating, playing, queued, stopping), the current "
            "request, queue length, recent requests and provider/playback health."
        ),
        annotations=ToolAnnotations(
            read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
        ),
    )
    async def get_status() -> StatusReport:
        service = holder.get()
        try:
            return await service.status()
        except VoxelloError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Notify",
        description=(
            "Deliver a concise notification to the user through one or more local channels: "
            "'voice' speaks it, 'desktop' shows a desktop notification, 'file' saves audio and "
            "text. Use it when a long task finishes or needs attention. Keep the message to one "
            "or two sentences. Priority high/critical interrupts current speech."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def notify(
        message: Annotated[str, Field(description="Short message to deliver.", min_length=1)],
        channels: Annotated[
            list[Channel] | None,
            Field(description="Channels to use; default ['voice', 'desktop']."),
        ] = None,
        priority: Annotated[
            Priority, Field(description="low, normal, high or critical.")
        ] = "normal",
        title: Annotated[
            str | None, Field(description="Desktop notification title; default 'Voxello'.")
        ] = None,
        client_id: Annotated[
            str | None, Field(description="Optional identifier of the calling agent, for logs.")
        ] = None,
        cache: Annotated[
            bool | None,
            Field(
                description=(
                    "Reuse or store the synthesized audio on disk. Default true: notifications "
                    "repeat, so the same message is synthesized once. Set false for one-off text."
                )
            ),
        ] = None,
        language: Annotated[
            str | None,
            Field(
                description=(
                    "ISO 639-1 code of the language the message is written in (e.g. 'it', "
                    "'en'). Pass it whenever you know it so pronunciation matches the text; "
                    "omit for the configured default."
                )
            ),
        ] = None,
    ) -> NotifyResult:
        service = holder.get()
        try:
            return await service.notify(
                message,
                channels=list(channels) if channels else None,
                priority=priority,
                title=title,
                client_id=client_id,
                cache=cache,
                language=language,
            )
        except VoxelloError as exc:
            raise ToolError(str(exc)) from exc
