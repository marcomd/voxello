"""What to read for each register, and the profile name a register is cloned under.

A register is one way a character speaks (``whisper``, ``sad`` ...). OmniVoice has no emotion
control, but it carries the delivery of the reference recording into what it synthesizes,
so each register is a separate profile cloned from a clip read in that tone
(``docs/decisions/0003-dialogue-production.md``). ``neutral`` is the character's main voice
and keeps the bare name.

Reading scripts live in ``assets/dialogue/registers/<language>.yaml``; a register or language
without one needs the text on the command line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources

import yaml

from voxello.core.voices import profile_id_from
from voxello.errors import INVALID_PARAMETER, VoxelloError

NEUTRAL = "neutral"
REGISTER_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


@dataclass(frozen=True)
class ReadingScript:
    register: str
    hint: str  # how to read it; empty for a custom register
    text: str  # read verbatim; becomes the profile's ref_text


def available_registers(language: str) -> dict[str, ReadingScript]:
    """The built-in reading scripts for ``language``; empty when there is no file for it."""
    node = resources.files("voxello").joinpath("assets", "dialogue", "registers")
    node = node.joinpath(f"{language}.yaml")
    if not node.is_file():
        return {}
    loaded = yaml.safe_load(node.read_text(encoding="utf-8")) or {}
    return {
        str(name): ReadingScript(str(name), str(entry["hint"]).strip(), str(entry["text"]).strip())
        for name, entry in loaded.items()
    }


def reading_script(register: str, language: str, text: str | None = None) -> ReadingScript:
    """The script to read for ``register``; ``text`` replaces the built-in reading."""
    if not REGISTER_RE.fullmatch(register):
        raise VoxelloError(
            INVALID_PARAMETER,
            f"Invalid register {register!r}: use up to 24 lowercase letters, digits or '_', "
            "starting with a letter.",
        )
    builtin = available_registers(language).get(register)
    if text is not None:
        cleaned = " ".join(text.split())
        if not cleaned:
            raise VoxelloError(INVALID_PARAMETER, "The text to read is empty.")
        return ReadingScript(register, builtin.hint if builtin else "", cleaned)
    if builtin is None:
        known = ", ".join(sorted(available_registers(language))) or "none"
        raise VoxelloError(
            INVALID_PARAMETER,
            f"No reading script for register '{register}' in language '{language}' "
            f"(built-in: {known}); pass the sentence to read with --text.",
        )
    return builtin


def profile_name(name: str, register: str) -> str:
    """``marco`` for the neutral register, ``marco-whisper`` otherwise."""
    base = profile_id_from(name)
    if register == NEUTRAL:
        return base
    return profile_id_from(f"{base}-{register}")
