"""Shared fixtures: fake provider, fake player, fake notifier, isolated settings."""

from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path

import pytest

from voxello.config import Settings
from voxello.core.service import VoxelloService
from voxello.errors import TTS_PROVIDER_UNAVAILABLE, VoxelloError
from voxello.storage.cache import AudioCache
from voxello.storage.files import OutputStore, TempStore
from voxello.tts.base import ProviderHealth, SynthesisResult, VoiceInfo


def make_wav(duration_ms: int = 500, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * int(sample_rate * duration_ms / 1000))
    return buf.getvalue()


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []
        self.fail_with: VoxelloError | None = None
        self.duration_ms = 500
        self.delay = 0.0

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> SynthesisResult:
        self.calls.append((text, voice, language))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_with is not None:
            raise self.fail_with
        return SynthesisResult(
            make_wav(self.duration_ms), "audio/wav", self.name, voice or "default"
        )

    async def list_voices(self) -> list[VoiceInfo]:
        return [VoiceInfo("default", "Default")]

    async def health(self) -> ProviderHealth:
        if self.fail_with is not None and self.fail_with.code == TTS_PROVIDER_UNAVAILABLE:
            return ProviderHealth("error", "down")
        return ProviderHealth("ok", None, "test")

    async def aclose(self) -> None:
        return None


class FakeHandle:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.done = asyncio.Event()
        self.returncode = 0
        self.terminated = False

    async def wait(self) -> int:
        await self.done.wait()
        return self.returncode

    async def terminate(self) -> None:
        self.terminated = True
        self.done.set()

    def finish(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.done.set()


class FakePlayer:
    """Playback never finishes on its own; tests call ``finish_current()``."""

    name = "fake-player"

    def __init__(self) -> None:
        self.handles: list[FakeHandle] = []
        self.started = asyncio.Event()

    async def play(self, path: Path) -> FakeHandle:
        handle = FakeHandle(path)
        self.handles.append(handle)
        self.started.set()
        return handle

    @property
    def current(self) -> FakeHandle | None:
        for handle in reversed(self.handles):
            if not handle.done.is_set():
                return handle
        return None

    async def wait_started(self) -> FakeHandle:
        await asyncio.wait_for(self.started.wait(), 2)
        self.started.clear()
        return self.handles[-1]

    async def finish_current(self, returncode: int = 0) -> None:
        handle = self.current
        assert handle is not None, "nothing is playing"
        handle.finish(returncode)
        await asyncio.sleep(0)


class FakeNotifier:
    name = "desktop"

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.sent: list[tuple[str, str]] = []
        self.fail: VoxelloError | None = None

    def available(self) -> bool:
        return self._available

    async def send(self, title: str, body: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append((title, body))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        storage={"temp_dir": tmp_path / "tmp", "temp_retention_minutes": 1},
        cache={"directory": tmp_path / "cache"},
        output={"directory": tmp_path / "out"},
        playback={"max_queue_size": 3},
    )


def make_cache(settings: Settings) -> AudioCache:
    return AudioCache(
        settings.cache.resolved_directory(),
        max_entries=settings.cache.max_entries,
        max_age_days=settings.cache.max_age_days,
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def player() -> FakePlayer:
    return FakePlayer()


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
async def service(
    settings: Settings, provider: FakeProvider, player: FakePlayer, notifier: FakeNotifier
):
    svc = VoxelloService(
        settings,
        provider=provider,
        player=player,
        notifier=notifier,
        temp_store=TempStore(settings.storage.resolved_temp_dir(), 1, True),
        output_store=OutputStore(settings.output.resolved_directory()),
        audio_cache=make_cache(settings),
    )
    await svc.start()
    try:
        yield svc
    finally:
        await svc.aclose()


async def settle() -> None:
    """Let the playback worker run a few iterations."""
    for _ in range(5):
        await asyncio.sleep(0)
