"""``dialogue status``: where a project stands and the command that comes next (roadmap 8.7).

The steps follow the production order: every speech segment needs a speaker, then a cast,
then a project ``check`` accepts, then a current take per line, then an approval per line,
then play and export. Only the first missing step is suggested, so a person or an agent can
always resume a project by running what ``Next`` says. Nothing here calls the TTS server.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path

from voxello.dialogue.project import LineDefaults, Project

ERRORS_SHOWN = 5
NO_SPEAKER = "speech has no speaker"  # script.check's message
LABEL = 10


@dataclass(frozen=True)
class Step:
    command: list[str]  # the arguments after `voxello`, without `-p`
    why: str
    interactive: bool = False  # needs the user's own terminal (keys, audio)


@dataclass(frozen=True)
class ProjectStatus:
    segments: int
    speech: int
    unassigned: int
    characters: list[str] | None  # None without cast.yaml
    errors: list[str]  # `dialogue check` errors
    lines: int | None = None  # spoken lines; None until the project checks
    rendered: int = 0
    approved: int = 0
    exported: bool = False  # final.wav is newer than the script, cast and state
    next: list[Step] = field(default_factory=list)


def _exported(project: Project) -> bool:
    final = project.root / "final.wav"
    if not final.is_file():
        return False
    inputs = [project.script_path, project.cast_path, project.state_path]
    newest = max(p.stat().st_mtime for p in inputs if p.is_file())
    return final.stat().st_mtime >= newest


def project_status(project: Project, defaults: LineDefaults | None = None) -> ProjectStatus:
    script = project.load_script()
    cast = project.load_cast()
    spoken = [s for s in script.segments if s.kind != "break" and not s.skip]
    speech = [s for s in spoken if s.kind == "speech"]
    unassigned = sum(1 for s in speech if s.speaker is None)
    report = project.check(defaults.designs if defaults else None)
    status = ProjectStatus(
        segments=len(script.segments),
        speech=len(speech),
        unassigned=unassigned,
        characters=list(cast.characters) if cast else None,
        # speech without a speaker is counted in `unassigned`, not repeated as errors
        errors=[str(i) for i in report.errors if i.message != NO_SPEAKER],
    )
    if unassigned:
        plural = "segment" if unassigned == 1 else "segments"
        why = (
            f"{unassigned} speech {plural} without a speaker: annotate them "
            "(`voxello dialogue annotate`)."
        )
        return replace(status, next=[Step(["dialogue", "segments", "--pending"], why)])
    if cast is None:
        why = "No cast.yaml: write a draft from the speakers and the server's voices."
        return replace(status, next=[Step(["dialogue", "cast", "--write"], why)])
    if not report.ok:
        why = "`dialogue check` reports errors: fix the script or cast.yaml."
        return replace(status, next=[Step(["dialogue", "check"], why)])

    state = project.load_state()
    picks = [project.pick(state, line) for line in project.lines(defaults)]
    status = replace(
        status,
        lines=len(picks),
        rendered=sum(1 for p in picks if p.take is not None),
        approved=sum(1 for p in picks if p.approved),
    )
    if status.rendered < len(picks):
        why = f"{len(picks) - status.rendered} lines have no current take."
        return replace(status, next=[Step(["dialogue", "render"], why)])
    if status.approved < len(picks):
        why = f"{len(picks) - status.approved} lines are not approved: listen and approve them."
        return replace(
            status, next=[Step(["dialogue", "review", "--pending"], why, interactive=True)]
        )
    if _exported(project):
        return replace(status, exported=True)
    steps = [
        Step(["dialogue", "play"], "Listen to the whole scene.", interactive=True),
        Step(["dialogue", "export", "--srt"], "Write final.wav, its timeline and subtitles."),
    ]
    return replace(status, next=steps)


def command_line(step: Step, root: Path) -> str:
    head, *rest = step.command
    sub, *options = rest
    return shlex.join(["voxello", head, sub, "-p", str(root), *options])


def format_status(status: ProjectStatus, root: Path) -> list[str]:
    def row(label: str, text: str) -> str:
        return f"{label:<{LABEL}} {text}"

    who = (
        f"{status.unassigned} without a speaker"
        if status.unassigned
        else "every one with a speaker"
    )
    out = [row("Segments", f"{status.segments} ({status.speech} speech, {who})")]
    if status.characters is None:
        out.append(row("Cast", "missing"))
    else:
        names = ", ".join(status.characters)
        out.append(row("Cast", f"{len(status.characters)} characters: {names}"))
    if status.errors:
        out.append(row("Check", f"{len(status.errors)} errors"))
        out += [f"  {error}" for error in status.errors[:ERRORS_SHOWN]]
    elif status.characters is not None:
        out.append(row("Check", "OK"))
    if status.lines is not None:
        out.append(
            row(
                "Takes",
                f"{status.rendered}/{status.lines} lines rendered, {status.approved} approved",
            )
        )
    if status.exported:
        out.append("Done: final.wav is up to date.")
    for number, step in enumerate(status.next):
        label = "Next" if number == 0 else "Then"
        out.append(row(label, command_line(step, root)))
        note = step.why
        if step.interactive:
            note += " Interactive: run it in your own terminal."
        out.append(row("", note))
    return out
