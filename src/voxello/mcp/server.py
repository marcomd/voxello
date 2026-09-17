"""MCP server factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import MCPServer

from voxello import __version__
from voxello.config import Settings
from voxello.core.service import VoxelloService
from voxello.mcp.tools import ServiceHolder, register_tools

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Voxello gives you a voice on the user's machine. Use `speak` to read a short summary or \
answer aloud when the user asks for it, and `notify` when a long task finishes or needs \
attention. Keep spoken text under about 20 seconds (one to three sentences); the textual \
reply remains the place for details. Pass `language` (ISO 639-1, e.g. `it` or `en`) matching \
the language the text is written in, so pronunciation is right; omit it for the configured \
default. Use `stop_speaking` to interrupt playback and `get_status` to check what is playing \
and whether the TTS provider is reachable. Voxello speaks exactly the text you pass; it never \
summarizes or rewrites it."""


def create_server(settings: Settings, service: VoxelloService | None = None) -> MCPServer:
    """Build the MCP server. ``service`` may be injected for tests."""
    holder = ServiceHolder()

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[ServiceHolder]:
        svc = service if service is not None else VoxelloService.from_settings(settings)
        await svc.start()
        holder.service = svc
        try:
            yield holder
        finally:
            holder.service = None
            await svc.aclose()

    mcp = MCPServer(
        name=settings.server.name,
        title="Voxello",
        description="A local voice and notification layer for AI agents.",
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
    )
    register_tools(mcp, holder)
    return mcp
