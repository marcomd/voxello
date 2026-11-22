"""Reproducible takes (roadmap 8.4, decision 0003).

A take is fully described by what shapes the audio: the spoken text, the resolved voice or
voice-design instructions, the language and the generation values, seed included. Their
SHA-256 is the take's fingerprint; ``state.json`` stores takes and the approved one by
fingerprint, so a take is *current* when recomputing the fingerprint from the line's present
inputs and the take's seed gives the same value. Editing a line therefore invalidates its
approval by construction. The server URL is deliberately not part of it: a take stays valid
when the server moves.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Literal

from pydantic import BaseModel, Field

from voxello.dialogue.qa import QA_VERSION, Measures
from voxello.tts.base import SynthesisOptions

FINGERPRINT_VERSION = 1


@dataclass(frozen=True)
class LineRequest:
    """Everything that shapes one line's audio. ``None`` leaves a value to the server."""

    tts_text: str
    voice: str | None
    instructions: str | None
    language: str
    speed: float | None
    num_step: int | None
    seed: int
    # Not a per-request option: it comes from config.yaml, but it changes the audio.
    guidance_scale: float | None

    def options(self) -> SynthesisOptions:
        return SynthesisOptions(seed=self.seed, speed=self.speed, num_step=self.num_step)

    def with_seed(self, seed: int) -> LineRequest:
        return replace(self, seed=seed)


def fingerprint(request: LineRequest) -> str:
    payload = {"v": FINGERPRINT_VERSION, **asdict(request)}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Signal(BaseModel):
    """``qa.Measures`` of a take, stored so a review does not read every WAV again."""

    version: int
    duration_ms: int
    peak: int
    clipped_runs: int
    rms_db: float
    lead_ms: int
    trail_ms: int
    voiced_ms: int

    @classmethod
    def from_measures(cls, measures: Measures) -> Signal:
        return cls(**asdict(measures))

    def measures(self) -> Measures:
        return Measures(**self.model_dump())

    @property
    def current(self) -> bool:
        return self.version == QA_VERSION


class Take(BaseModel):
    fingerprint: str
    seed: int
    duration_ms: int | None = None
    created: str
    qa: Signal | None = None


class LineState(BaseModel):
    approved: str | None = None  # fingerprint of the approved take
    selected: str | None = None  # fingerprint of the take picked in review
    takes: list[Take] = Field(default_factory=list)

    def current_takes(self, request: LineRequest) -> list[Take]:
        """Takes whose fingerprint still matches the line's inputs (their own seed aside)."""
        return [t for t in self.takes if fingerprint(request.with_seed(t.seed)) == t.fingerprint]

    def chosen(self, current: list[Take]) -> Take | None:
        """The take picked in review if it is still current, else the latest current one."""
        for take in current:
            if take.fingerprint == self.selected:
                return take
        return current[-1] if current else None


class ReviewState(BaseModel):
    position: str | None = None  # the line review showed last; None starts at the first pending


class State(BaseModel):
    version: Literal[1] = 1
    review: ReviewState = Field(default_factory=ReviewState)
    lines: dict[str, LineState] = Field(default_factory=dict)
