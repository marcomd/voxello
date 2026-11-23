"""A first ``cast.yaml`` from the annotated script (roadmap 8.7, ``dialogue cast``).

Deterministic, no LLM: the characters are the narrator plus every speaker of the script,
ordered by number of lines; each one's voice is the cloned profile with the same name
(``clone:anna``), its registers the profiles named ``anna-<register>``, and a name found in
``speech.voice_designs`` becomes that design. A character without a profile still gets
``clone:<name>``: the cast then points at the profile to record, and works unchanged once
``voxello voice record <name>`` has created it. Comments in the draft say what is missing
and how to record it; the agent (or the user) edits the rest.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from voxello.dialogue.cast import NAME_RE, Generation, Pauses
from voxello.dialogue.script import Script

PLAIN_RE = re.compile(r"^[a-z0-9][a-z0-9:_-]*$")


@dataclass(frozen=True)
class CastDraft:
    text: str
    missing_voices: list[str] = field(default_factory=list)  # characters without a profile
    # (character, register, lines) used in the script without a profile
    missing_registers: list[tuple[str, str, int]] = field(default_factory=list)
    unassigned: int = 0  # speech segments without a speaker


def _scalar(value: str) -> str:
    return value if PLAIN_RE.fullmatch(value) else json.dumps(value, ensure_ascii=False)


def draft_cast(
    script: Script,
    *,
    language: str,
    narrator: str,
    profiles: set[str] | None,
    designs: dict[str, str],
) -> CastDraft:
    """``profiles`` are the server's profile ids, or ``None`` when they could not be listed."""
    lines: Counter[str] = Counter()
    used: defaultdict[str, Counter[str]] = defaultdict(Counter)
    unassigned = 0
    for seg in script.segments:
        if seg.kind == "break" or seg.skip:
            continue
        speaker = seg.speaker or (narrator if seg.kind != "speech" else None)
        if speaker is None:
            unassigned += 1
            continue
        lines[speaker] += 1
        if seg.register_:
            used[speaker][seg.register_] += 1
    names = [narrator, *(n for n, _ in lines.most_common() if n != narrator)]
    known = profiles or set()

    out = [
        "# Draft written by `voxello dialogue cast`: check every voice before rendering.",
        "version: 1",
        f"language: {language}",
        f"narrator: {_scalar(narrator)}",
        'attributions: keep   # drop: skip narration flagged as attribution ("disse lei")',
    ]
    defaults_generation = Generation()
    pauses = Pauses()
    out += [
        "generation:",
        f"  seed: {defaults_generation.seed}",
        f"  num_step: {defaults_generation.num_step}",
        "pauses_ms:",
        f"  same_speaker: {pauses.same_speaker}",
        f"  speaker_change: {pauses.speaker_change}",
        f"  paragraph: {pauses.paragraph}",
        f"  break: {pauses.break_}",
    ]
    if profiles is None:
        out.append(
            "# The server's cloned voices were not listed: every voice below is the expected "
            "clone:<name>."
        )
    if unassigned:
        plural = "line has" if unassigned == 1 else "lines have"
        out.append(
            f"# {unassigned} speech {plural} no speaker yet: annotate them, then run "
            "`voxello dialogue cast` again."
        )
    out.append("characters:")
    missing_voices: list[str] = []
    missing_registers: list[tuple[str, str, int]] = []
    for name in names:
        count = lines[name]
        out.append(f"  {name}:   # {count} line{'s' if count != 1 else ''}")
        if name in designs:
            out.append(f"    voice: {_scalar(name)}   # design from speech.voice_designs")
        else:
            if profiles is not None and name not in known:
                missing_voices.append(name)
                out.append(
                    f"    # TODO: no clone:{name} on the server. Record it "
                    f"(`voxello voice record {name}`), or use another clone or a design."
                )
            out.append(f"    voice: clone:{name}")
        prefix = f"{name}-"
        registers = sorted(
            p.removeprefix(prefix)
            for p in known
            if p.startswith(prefix) and NAME_RE.fullmatch(p.removeprefix(prefix))
        )
        if registers:
            out.append("    registers:")
            out += [f"      {register}: clone:{name}-{register}" for register in registers]
        for register, used_lines in sorted(used[name].items()):
            if register in registers or profiles is None:
                continue
            missing_registers.append((name, register, used_lines))
            out.append(
                f"    # register {register} ({used_lines} line{'s' if used_lines != 1 else ''}) "
                f"has no clone:{name}-{register}, the main voice reads it; record it with "
                f"`voxello voice record {name} --register {register}`."
            )
    return CastDraft(
        text="\n".join(out) + "\n",
        missing_voices=missing_voices,
        missing_registers=missing_registers,
        unassigned=unassigned,
    )
