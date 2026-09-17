"""End-to-end MCP round trip over in-memory streams."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from mcp.types import CallToolResult, TextContent

from voxello.core.service import VoxelloService
from voxello.errors import TTS_PROVIDER_UNAVAILABLE, VoxelloError
from voxello.mcp.server import create_server
from voxello.storage.files import OutputStore, TempStore

from .conftest import FakeNotifier, FakePlayer, FakeProvider


@asynccontextmanager
async def connected(settings, service: VoxelloService) -> AsyncIterator[ClientSession]:
    mcp = create_server(settings, service=service)
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                mcp._lowlevel_server.run,
                server_read,
                server_write,
                mcp._lowlevel_server.create_initialization_options(),
            )
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                yield session
            tg.cancel_scope.cancel()


def text_of(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


@pytest.fixture
def stack(settings, tmp_path):
    provider, player, notifier = FakeProvider(), FakePlayer(), FakeNotifier()
    service = VoxelloService(
        settings,
        provider=provider,
        player=player,
        notifier=notifier,
        temp_store=TempStore(tmp_path / "t", 1, True),
        output_store=OutputStore(tmp_path / "o"),
    )
    return provider, player, notifier, service


async def test_tools_are_listed_with_annotations(settings, stack):
    _, _, _, service = stack
    async with connected(settings, service) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert set(tools) == {"speak", "stop_speaking", "get_status", "notify"}
        assert tools["get_status"].annotations.read_only_hint is True
        assert tools["speak"].output_schema is not None
        assert "text" in tools["speak"].input_schema["required"]
        assert "cache" in tools["speak"].input_schema["properties"]
        assert "cache" in tools["notify"].input_schema["properties"]
        assert "language" in tools["speak"].input_schema["properties"]
        assert "language" in tools["notify"].input_schema["properties"]
        assert "cached" in tools["speak"].output_schema["properties"]
        assert "language" in tools["speak"].output_schema["properties"]
        assert "cache_hits" in tools["get_status"].output_schema["properties"]


async def test_speak_status_stop_round_trip(settings, stack):
    provider, player, _, service = stack
    async with connected(settings, service) as session:
        result = await session.call_tool("speak", {"text": "Ciao dal test", "client_id": "pytest"})
        assert isinstance(result, CallToolResult) and not result.is_error
        assert result.structured_content["status"] == "playing"
        assert result.structured_content["cached"] is False
        assert result.structured_content["language"] == "it"
        request_id = result.structured_content["request_id"]
        assert provider.calls[0] == ("Ciao dal test", None, "it")
        await player.wait_started()

        status = await session.call_tool("get_status", {})
        assert status.structured_content["status"] == "playing"
        assert status.structured_content["request_id"] == request_id
        assert status.structured_content["health"]["tts"]["status"] == "ok"
        assert status.structured_content["health"]["playback"]["backend"] == "fake-player"

        stopped = await session.call_tool("stop_speaking", {})
        assert stopped.structured_content["status"] == "stopped"

        idle = await session.call_tool("get_status", {})
        assert idle.structured_content["status"] == "idle"
        assert idle.structured_content["recent"][0]["state"] == "cancelled"


async def test_tool_error_is_reported_not_returned(settings, stack):
    provider, _, _, service = stack
    provider.fail_with = VoxelloError(TTS_PROVIDER_UNAVAILABLE, "VoiceStudio is not reachable")
    async with connected(settings, service) as session:
        result = await session.call_tool("speak", {"text": "ciao"})
        assert result.is_error
        assert "tts_provider_unavailable" in text_of(result)
        assert "not reachable" in text_of(result)


async def test_notify_round_trip(settings, stack):
    _, player, notifier, service = stack
    async with connected(settings, service) as session:
        result = await session.call_tool(
            "notify", {"message": "Fatto.", "channels": ["voice", "desktop"], "priority": "high"}
        )
        assert not result.is_error
        assert result.structured_content["status"] == "delivered"
        assert result.structured_content["channels"] == {"voice": "playing", "desktop": "sent"}
        assert notifier.sent == [("Voxello", "Fatto.")]
        await player.wait_started()


async def test_language_parameter_reaches_the_provider(settings, stack):
    provider, player, _, service = stack
    async with connected(settings, service) as session:
        result = await session.call_tool("speak", {"text": "Hello there", "language": "en"})
        assert not result.is_error
        assert result.structured_content["language"] == "en"
        assert provider.calls[0] == ("Hello there", None, "en")
        await player.wait_started()

        result = await session.call_tool(
            "notify", {"message": "Done.", "channels": ["voice"], "language": "en"}
        )
        assert not result.is_error
        assert provider.calls[1] == ("Done.", None, "en")

        bad = await session.call_tool("speak", {"text": "x", "language": "english"})
        assert bad.is_error
        assert "invalid_language" in text_of(bad)


async def test_invalid_arguments_are_rejected(settings, stack):
    _, _, _, service = stack
    async with connected(settings, service) as session:
        result = await session.call_tool("notify", {"message": "x", "channels": ["pager"]})
        assert result.is_error
        result = await session.call_tool("speak", {"text": ""})
        assert result.is_error
        assert json.loads(json.dumps(result.model_dump(by_alias=True)))  # serializable
