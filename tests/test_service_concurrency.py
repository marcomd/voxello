"""Synthesis is serialised behind ``_synth_lock`` (spec section 15, roadmap milestone 4).

These tests pin the behaviour the roadmap flagged: a slow TTS request makes the next uncached
request wait, but never a cache hit, because the lookup runs before the lock.
"""

from __future__ import annotations

import asyncio

import pytest

from voxello.tts.base import SynthesisResult

from .conftest import FakeProvider, make_wav, settle


class GatedProvider(FakeProvider):
    """A provider whose synthesis blocks until the test opens the gate."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> SynthesisResult:
        self.calls.append((text, voice, language))
        self.entered.set()
        await self.gate.wait()
        return SynthesisResult(make_wav(self.duration_ms), "audio/wav", self.name, "default")


@pytest.fixture
def provider() -> GatedProvider:  # overrides the conftest fixture for the `service` fixture
    return GatedProvider()


async def test_second_uncached_request_waits_for_the_first_synthesis(service, provider):
    first = asyncio.create_task(service.speak("prima frase", play=False))
    await asyncio.wait_for(provider.entered.wait(), 2)
    second = asyncio.create_task(service.speak("seconda frase", play=False))
    await settle()

    assert [c[0] for c in provider.calls] == ["prima frase"], "second call must wait"
    assert (await service.status()).status == "generating"

    provider.gate.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert [c[0] for c in provider.calls] == ["prima frase", "seconda frase"]
    assert {r.status for r in results} == {"generated"}


async def test_cache_hit_does_not_wait_for_a_slow_synthesis(service, provider):
    provider.gate.set()
    warmed = await service.speak("Build completata.", play=False, cache=True)
    assert warmed.cached is False
    provider.gate.clear()
    provider.entered.clear()

    blocked = asyncio.create_task(service.speak("frase lenta", play=False))
    await asyncio.wait_for(provider.entered.wait(), 2)

    hit = await asyncio.wait_for(service.speak("Build completata.", play=False, cache=True), 1)
    assert hit.cached is True
    assert [c[0] for c in provider.calls] == ["Build completata.", "frase lenta"]

    provider.gate.set()
    assert (await asyncio.wait_for(blocked, 2)).status == "generated"
