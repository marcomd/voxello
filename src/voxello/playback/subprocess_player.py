"""AudioPlayer implementation that spawns a command-line player per file."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from voxello.errors import PLAYBACK_ERROR, VoxelloError
from voxello.playback.base import PlaybackHandle, PlayerBackend

log = logging.getLogger(__name__)


class SubprocessHandle:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    async def wait(self) -> int:
        return await self._process.wait()

    async def terminate(self) -> None:
        if self._process.returncode is not None:
            return
        try:
            self._process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
        except TimeoutError:
            log.warning("Player did not exit after SIGTERM; killing it")
            try:
                self._process.kill()
            except ProcessLookupError:
                return
            await self._process.wait()


class SubprocessPlayer:
    def __init__(self, backend: PlayerBackend, volume: float) -> None:
        self.backend = backend
        self.volume = volume
        self.name = backend.name

    async def play(self, path: Path) -> PlaybackHandle:
        command = self.backend.build_command(path, self.volume)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (OSError, ValueError) as exc:
            raise VoxelloError(
                PLAYBACK_ERROR, f"Could not start the audio player '{self.backend.name}'."
            ) from exc
        return SubprocessHandle(process)
