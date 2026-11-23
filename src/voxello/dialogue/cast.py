"""``cast.yaml``: characters, their voices and registers, generation values and pauses.

The agent writes it with the user (roadmap 8.7). A character has exactly one of ``voice``
(a server voice, ``clone:<id>`` or a name from ``speech.voice_designs``) or ``instructions``;
leaving both out is an error because the server's ``auto`` voice changes from line to line.
A register is one way the character speaks (``whisper``, ``sad`` ...), usually a profile
cloned from a recording in that tone (``voxello voice record NAME --register R``); a register
the cast does not list falls back to the character's main voice.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from voxello.core.models import VoiceChoice
from voxello.core.voice_design import normalize_instructions
from voxello.errors import INVALID_SCRIPT, VoxelloError
from voxello.tts.base import NUM_STEP_RANGE, SEED_RANGE, SPEED_RANGE

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def validation_error(source: str, exc: ValidationError) -> VoxelloError:
    """One ``invalid_script`` error listing every problem with its location."""
    problems = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        problems.append(f"{location}: {message}" if location else message)
    return VoxelloError(INVALID_SCRIPT, f"{source}: " + "; ".join(problems))


class VoiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: str | None = None
    instructions: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.voice is None) == (self.instructions is None):
            raise ValueError("give exactly one of `voice` or `instructions`")
        return self


class Character(VoiceSpec):
    speed: float | None = Field(default=None, ge=SPEED_RANGE[0], le=SPEED_RANGE[1])
    registers: dict[str, VoiceSpec] = Field(default_factory=dict)

    @field_validator("registers", mode="before")
    @classmethod
    def _shorthand(cls, value: Any) -> Any:
        # `whisper: clone:bruno-whisper` is short for `whisper: {voice: clone:bruno-whisper}`
        if isinstance(value, dict):
            return {k: {"voice": v} if isinstance(v, str) else v for k, v in value.items()}
        return value


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int = Field(default=1, ge=SEED_RANGE[0], le=SEED_RANGE[1])
    num_step: int | None = Field(default=16, ge=NUM_STEP_RANGE[0], le=NUM_STEP_RANGE[1])
    speed: float | None = Field(default=None, ge=SPEED_RANGE[0], le=SPEED_RANGE[1])


class Pauses(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    same_speaker: int = Field(default=250, ge=0)
    speaker_change: int = Field(default=450, ge=0)
    paragraph: int = Field(default=700, ge=0)
    break_: int = Field(default=1500, ge=0, alias="break")


class Cast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    language: str = Field(pattern=r"^[a-z]{2}$")
    narrator: str
    attributions: Literal["keep", "drop"] = "keep"
    generation: Generation = Field(default_factory=Generation)
    pauses_ms: Pauses = Field(default_factory=Pauses)
    characters: dict[str, Character]

    @field_validator("characters")
    @classmethod
    def _names(cls, value: dict[str, Character]) -> dict[str, Character]:
        for name, character in value.items():
            if not NAME_RE.fullmatch(name):
                raise ValueError(
                    f"character name {name!r}: use up to 32 lowercase letters, digits, '-' or '_'"
                )
            for register in character.registers:
                if not NAME_RE.fullmatch(register):
                    raise ValueError(f"register name {register!r} of {name}: same rules as names")
        return value

    @model_validator(mode="after")
    def _narrator_exists(self) -> Self:
        if self.narrator not in self.characters:
            raise ValueError(f"narrator {self.narrator!r} is not a character")
        return self

    @classmethod
    def from_yaml_data(cls, data: object) -> Cast:
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise validation_error("cast.yaml", exc) from exc

    @classmethod
    def from_yaml(cls, text: str) -> Cast:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise VoxelloError(INVALID_SCRIPT, f"cast.yaml is not valid YAML: {exc}") from exc
        return cls.from_yaml_data(data)


def resolve_spec(spec: VoiceSpec, designs: dict[str, str]) -> VoiceChoice:
    """A voice id, or instructions for a design (inline or named in ``speech.voice_designs``)."""
    if spec.instructions is not None:
        return VoiceChoice(None, normalize_instructions(spec.instructions))
    assert spec.voice is not None  # guaranteed by VoiceSpec
    if spec.voice in designs:
        return VoiceChoice(None, normalize_instructions(designs[spec.voice]))
    return VoiceChoice(spec.voice, None)


def resolve_line_voice(
    cast: Cast, speaker: str, register: str | None, designs: dict[str, str]
) -> VoiceChoice:
    """Line register → ``characters.X.registers`` → ``characters.X`` (the main voice)."""
    character = cast.characters[speaker]
    spec: VoiceSpec = character
    if register is not None and register in character.registers:
        spec = character.registers[register]
    return resolve_spec(spec, designs)
