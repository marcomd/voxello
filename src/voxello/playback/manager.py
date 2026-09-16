"""Playback queue with interrupt and cancellation semantics (spec section 15)."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from voxello.errors import QUEUE_FULL, VoxelloError
from voxello.playback.base import AudioPlayer, PlaybackHandle

log = logging.getLogger(__name__)

Outcome = Literal["playing", "completed", "cancelled", "error", "dropped"]
OutcomeCallback = Callable[["PlaybackItem", Outcome], Awaitable[None] | None]


@dataclass
class PlaybackItem:
    request_id: str
    path: Path


class PlaybackManager:
    def __init__(
        self,
        player: AudioPlayer,
        *,
        max_queue_size: int = 10,
        interrupt_clears_queue: bool = True,
        on_outcome: OutcomeCallback | None = None,
    ) -> None:
        self.player = player
        self.max_queue_size = max_queue_size
        self.interrupt_clears_queue = interrupt_clears_queue
        self._on_outcome = on_outcome
        self._queue: deque[PlaybackItem] = deque()
        self._current: PlaybackItem | None = None
        self._handle: PlaybackHandle | None = None
        self._cancel_requested = False
        self._stopping = False
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task[None] | None = None

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="voxello-playback")

    async def aclose(self) -> None:
        await self.stop()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    # -- introspection -------------------------------------------------------------

    @property
    def current(self) -> PlaybackItem | None:
        return self._current

    @property
    def queue_length(self) -> int:
        return len(self._queue)

    @property
    def is_playing(self) -> bool:
        return self._current is not None

    @property
    def is_stopping(self) -> bool:
        return self._stopping

    def queued_ids(self) -> list[str]:
        return [item.request_id for item in self._queue]

    async def wait_idle(self) -> None:
        await self._idle.wait()

    # -- commands ------------------------------------------------------------------

    async def enqueue(self, item: PlaybackItem, *, interrupt: bool) -> Literal["playing", "queued"]:
        if self._worker is None:
            self.start()
        if interrupt:
            if self.interrupt_clears_queue:
                await self._clear_queue()
            # Put the new item first, then stop the current one: whatever the worker
            # does next, this item is the one it picks up.
            self._queue.appendleft(item)
            self._idle.clear()
            self._wake.set()
            await self._terminate_current()
            return "playing"
        if len(self._queue) >= self.max_queue_size:
            raise VoxelloError(QUEUE_FULL, "The playback queue is full.")
        starts_now = self._current is None and not self._queue
        self._queue.append(item)
        self._idle.clear()
        self._wake.set()
        return "playing" if starts_now else "queued"

    async def stop(self, request_id: str | None = None) -> tuple[str, int]:
        """Stop playback. Returns (status, number of queued items dropped)."""
        if request_id is None:
            dropped = await self._clear_queue()
            if self._current is None:
                return "idle", dropped
            await self._terminate_current()
            return "stopped", dropped
        if self._current is not None and self._current.request_id == request_id:
            await self._terminate_current()
            return "stopped", 0
        for item in list(self._queue):
            if item.request_id == request_id:
                self._queue.remove(item)
                await self._emit(item, "dropped")
                return "removed", 1
        return "not_found", 0

    # -- internals -----------------------------------------------------------------

    async def _terminate_current(self) -> None:
        handle, current = self._handle, self._current
        if handle is None or current is None:
            return
        self._cancel_requested = True
        self._stopping = True
        try:
            await handle.terminate()
        finally:
            self._stopping = False

    async def _clear_queue(self) -> int:
        dropped = list(self._queue)
        self._queue.clear()
        for item in dropped:
            await self._emit(item, "dropped")
        return len(dropped)

    async def _emit(self, item: PlaybackItem, outcome: Outcome) -> None:
        if self._on_outcome is None:
            return
        try:
            result = self._on_outcome(item, outcome)
            if result is not None:
                await result
        except Exception:
            log.exception("Playback outcome callback failed for %s", item.request_id)

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._queue:
                item = self._queue.popleft()
                self._current = item
                self._cancel_requested = False
                try:
                    self._handle = await self.player.play(item.path)
                except VoxelloError as exc:
                    log.error("Playback failed for %s: %s", item.request_id, exc)
                    self._current = None
                    await self._emit(item, "error")
                    continue
                await self._emit(item, "playing")
                try:
                    returncode = await self._handle.wait()
                finally:
                    self._handle = None
                    self._current = None
                if self._cancel_requested:
                    outcome: Outcome = "cancelled"
                elif returncode == 0:
                    outcome = "completed"
                else:
                    log.warning("Player exited with code %s for %s", returncode, item.request_id)
                    outcome = "error"
                await self._emit(item, outcome)
            if not self._queue and self._current is None:
                self._idle.set()
