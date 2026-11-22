"""The interactive record / listen / redo loop behind ``voxello voice record``.

The loop is deliberately simple for the speaker: it shows how to read and what to read,
counts down, records until Enter, cleans the clip, says how long it is (warning outside the
3-10 s OmniVoice recommends) and plays it back; the speaker accepts, records again, replays or
quits. Terminal, recorder and player are injected so the loop is tested without a microphone.

Files: the raw capture never outlives one take, and the cleaned clip at ``output`` only
survives when it is accepted.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from voxello.core.voices import RECOMMENDED_SECONDS
from voxello.errors import RECORDING_FAILED, VoxelloError
from voxello.recording.registers import ReadingScript
from voxello.storage.wav import wav_duration_ms

MAX_SECONDS = 15.0
COUNTDOWN = 3
COUNTDOWN_STEP_SECONDS = 0.7


class Recording(Protocol):
    def stop(self) -> None: ...

    def abort(self) -> None: ...


class Recorder(Protocol):
    def start(self, output: Path, max_seconds: float) -> Recording: ...

    def prepare(self, raw: Path, output: Path) -> None: ...

    def describe(self) -> str: ...


class Terminal(Protocol):
    def say(self, text: str) -> None: ...

    def ask(self, prompt: str) -> str: ...


class ConsoleTerminal:
    def say(self, text: str) -> None:
        print(text, flush=True)

    def ask(self, prompt: str) -> str:
        return input(prompt)


@dataclass(frozen=True)
class Clip:
    path: Path
    seconds: float
    warnings: list[str] = field(default_factory=list)


def duration_warnings(seconds: float) -> list[str]:
    low, high = RECOMMENDED_SECONDS
    if low <= seconds <= high:
        return []
    return [f"The clip lasts {seconds:.1f} s; OmniVoice clones best from {low:.0f}-{high:.0f} s."]


class GuidedRecording:
    def __init__(
        self,
        recorder: Recorder,
        play: Callable[[Path], None],
        terminal: Terminal,
        *,
        sleep: Callable[[float], None] | None = None,
        max_seconds: float = MAX_SECONDS,
    ) -> None:
        self.recorder = recorder
        self.play = play
        self.terminal = terminal
        self._sleep = sleep
        self.max_seconds = max_seconds

    def run(self, script: ReadingScript, output: Path) -> Clip | None:
        """Record until the speaker accepts a take; ``None`` when they quit."""
        say = self.terminal.say
        say(f"\n== {script.register}" + (f": {script.hint}" if script.hint else ""))
        say(f"\n    {script.text}\n")
        say("Read it exactly as written: it becomes the transcript of the cloned voice.")
        raw = output.with_name(f"{output.stem}.raw{output.suffix}")
        accepted = False
        try:
            while True:
                if self.terminal.ask("Enter to record, q to quit > ").strip().lower() == "q":
                    return None
                self._capture(raw)
                try:
                    self.recorder.prepare(raw, output)
                finally:
                    raw.unlink(missing_ok=True)
                duration_ms = wav_duration_ms(output)
                if duration_ms is None:
                    raise VoxelloError(
                        RECORDING_FAILED, "The cleaned recording is not a readable WAV file."
                    )
                seconds = duration_ms / 1000
                warnings = duration_warnings(seconds)
                say(" ".join([f"{seconds:.1f} s after cleanup.", *warnings]))
                self.play(output)
                choice = self._review(output)
                if choice == "a":
                    accepted = True
                    return Clip(output, seconds, warnings)
                if choice == "q":
                    return None
        finally:
            raw.unlink(missing_ok=True)
            if not accepted:
                output.unlink(missing_ok=True)

    def _capture(self, raw: Path) -> None:
        sleep = self._sleep or time.sleep
        for n in range(COUNTDOWN, 0, -1):
            self.terminal.say(f"{n}...")
            sleep(COUNTDOWN_STEP_SECONDS)
        self.terminal.say(
            f"Recording: press Enter to stop (it stops by itself after {self.max_seconds:g} s)."
        )
        recording = self.recorder.start(raw, self.max_seconds)
        try:
            self.terminal.ask("")
        except BaseException:
            recording.abort()
            raise
        recording.stop()

    def _review(self, output: Path) -> str:
        while True:
            choice = self.terminal.ask("a accept, r record again, p play again, q quit > ")
            choice = choice.strip().lower()
            if choice in ("a", "r", "q"):
                return choice
            if choice == "p":
                self.play(output)
