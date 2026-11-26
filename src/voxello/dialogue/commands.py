"""``voxello dialogue ...``: the terminal side of dialogue production (roadmap 8.3-8.7).

The commands are thin: files and rules live in ``project``/``script``/``render``, review in
``review``/``review_session``, play-through and export in ``play``/``export``. Output for
the agent (``segments``) is one segment per line, ids first, so it can be read in chunks.
``review`` reads single keys from the terminal (cbreak mode on POSIX, so Ctrl-C still
interrupts; ``msvcrt`` on Windows).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import select
import shlex
import subprocess
import sys
import tempfile
import textwrap
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

from voxello.dialogue.cast import NAME_RE
from voxello.dialogue.cast_draft import draft_cast
from voxello.dialogue.export import export
from voxello.dialogue.play import PlayThrough
from voxello.dialogue.project import LineDefaults, Project, write_atomic
from voxello.dialogue.project_lock import project_lock
from voxello.dialogue.render import RenderEvent, render
from voxello.dialogue.review import normalize_key
from voxello.dialogue.review_session import ReviewSession, ReviewSummary
from voxello.dialogue.script import (
    ALLOWED_TAGS,
    ScriptSegment,
    effective_tts_text,
    is_spoken,
    select_range,
    speaker_of,
)
from voxello.dialogue.segment import DEFAULT_MAX_CHARS, default_tts_text, segment_text
from voxello.dialogue.status import format_status, project_status
from voxello.dialogue.sync import format_plan, plan_sync
from voxello.dialogue.sync_storage import apply_sync, recover_sync, recovery_plan
from voxello.errors import INVALID_PARAMETER, INVALID_SCRIPT, VoxelloError
from voxello.tts.base import SPEED_RANGE

if TYPE_CHECKING:
    from voxello.config import Settings, VoiceStudioSettings
    from voxello.dialogue.cast import Cast
    from voxello.dialogue.review_session import ReviewTerminal
    from voxello.playback.base import AudioPlayer
    from voxello.tts.base import TTSProvider

LABEL_WIDTH = 20
KEY_POLL_SECONDS = 0.03
WINDOWS_ARROWS = {"H": "\x1b[A", "P": "\x1b[B", "M": "\x1b[C", "K": "\x1b[D"}
DEFAULT_NARRATOR = {"it": "narratore"}  # other languages: "narrator"

TAGS_HELP = textwrap.fill(
    " ".join(f"[{tag}]" for tag in sorted(ALLOWED_TAGS)),
    width=80,
    initial_indent=" " * 15,
    subsequent_indent=" " * 15,
)
ANNOTATE_HELP = f"""\
Merge annotations into script.yaml by segment id. The input is a YAML mapping
{{id: {{field: value}}}}; null resets a field. All or nothing: an unknown id or
field, a change to `text` or an invalid value rejects the whole input.

Fields:
  speaker      who says a speech segment: a lowercase name (a-z, 0-9, - and _,
               up to 32 characters), the same name in every chapter of a book.
               Narration and headings need none: the cast's narrator reads them.
  register     how the line is said, a register of the speaker in cast.yaml:
               whisper, sad, angry, happy or one you recorded. Unset: main voice.
  tts_text     what is spoken instead of `text`. Never change the words: only
               add a tag at the start or adjust punctuation. Tags:
{TAGS_HELP}
  speed        for this line only: 0.85-1.15 sounds natural (accepted
               {SPEED_RANGE[0]}-{SPEED_RANGE[1]}).
  attribution  true on narration that only says who speaks ("disse lei.");
               `attributions: drop` in cast.yaml then leaves it out.
  skip         true: never spoken (a point-of-view marker, a note).

Example:
  p002.1: {{speaker: anna}}
  p002.2: {{attribution: true}}
  p004.1: {{speaker: bruno, register: whisper, tts_text: "[sigh] Lo so."}}
"""


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    dialogue = sub.add_parser(
        "dialogue", help="Turn a text into a multi-voice audio dialogue (segments, cast, takes)"
    )
    dsub = dialogue.add_subparsers(dest="dialogue_command", required=True)
    project = argparse.ArgumentParser(add_help=False)
    project.add_argument(
        "-p", "--project", type=Path, default=Path(), help="Project directory (default: .)"
    )

    init = dsub.add_parser("init", help="Create a project and split the source into segments")
    init.add_argument("source", type=Path, help="UTF-8 text file")
    init.add_argument(
        "directory", type=Path, nargs="?", help="Project directory (default: next to the source)"
    )
    init.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Longest run of whole sentences in one segment (default {DEFAULT_MAX_CHARS})",
    )

    sync = dsub.add_parser(
        "sync",
        parents=[project],
        help="Preview and confirm source changes, preserving unambiguous takes and annotations",
    )
    sync.add_argument(
        "source", type=Path, nargs="?", help="New UTF-8 source (may be project/source.txt)"
    )
    sync.add_argument(
        "--dry-run", action="store_true", help="Preview without prompting or writing project files"
    )
    sync.add_argument(
        "--recover",
        action="store_true",
        help="Confirm recovery of an interrupted sync; omit SOURCE",
    )

    segments = dsub.add_parser(
        "segments", parents=[project], help="Print segments with their annotations"
    )
    _add_window(segments)
    segments.add_argument(
        "--pending", action="store_true", help="Only speech that has no speaker yet"
    )

    annotate = dsub.add_parser(
        "annotate",
        parents=[project],
        help="Merge annotations ({id: {field: value}}) by id",
        description=ANNOTATE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    annotate.add_argument("file", help="YAML file, or - for stdin")

    dsub.add_parser("check", parents=[project], help="Validate script, source and cast")
    show = dsub.add_parser("show", parents=[project], help="Screenplay view of the spoken text")
    _add_window(show)
    dsub.add_parser("stats", parents=[project], help="Lines and characters per speaker")
    dsub.add_parser(
        "status", parents=[project], help="Where the project stands and the next command"
    )

    cast = dsub.add_parser(
        "cast",
        parents=[project],
        help="Draft cast.yaml from the speakers and the server's cloned voices",
        description=(
            "Prints a cast.yaml with the narrator and every speaker of the script. Each voice "
            "is the cloned profile with the character's name (clone:anna), each register a "
            "profile named anna-<register>; a name in speech.voice_designs becomes that "
            "design. Voices missing on the server are marked TODO with the command to record "
            "them."
        ),
    )
    cast.add_argument("--write", action="store_true", help="Write cast.yaml instead of printing")
    cast.add_argument("--force", action="store_true", help="With --write, replace cast.yaml")
    cast.add_argument(
        "--narrator", help="Name of the narrator (default: narratore for Italian, else narrator)"
    )
    cast.add_argument(
        "-l", "--language", help="Language of the text (default: speech.default_language)"
    )

    render_parser = dsub.add_parser(
        "render", parents=[project], help="Synthesize every line without a current take"
    )
    render_parser.add_argument("--lines", help="ID, A-B, A- or -B (default: every line)")
    render_parser.add_argument(
        "--force", action="store_true", help="Synthesize again even when a take is current"
    )

    review = dsub.add_parser(
        "review",
        parents=[project],
        help="Listen to every line: approve, regenerate, edit (single keys)",
        description=(
            "Plays each line's take. Enter approve and next, r new take, 1-9 pick a take, "
            "p replay, c play in context, e edit tts_text in $EDITOR, g register, +/- speed, "
            "n/b next/back, q quit. Every action is saved at once; review resumes where it "
            "stopped."
        ),
    )
    review.add_argument("--from", dest="start", help="Start at this segment id")
    review.add_argument("--pending", action="store_true", help="Only lines not approved yet")
    review.add_argument(
        "--flagged", action="store_true", help="Lines with QA flags first, then the others"
    )

    play = dsub.add_parser(
        "play",
        parents=[project],
        help="Play the scene line by line with the cast's pauses",
        description=(
            "Plays each line's approved take (or its chosen take, marked) with the pauses of "
            "cast.yaml, printing the line being played. Ctrl-C stops and offers to open "
            "review at that line."
        ),
    )
    play.add_argument("--from", dest="start", help="Start at this segment id")

    export_parser = dsub.add_parser(
        "export",
        parents=[project],
        help="Join the approved takes into one WAV, with a timeline and optional subtitles",
    )
    export_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="WAV to write (default: PROJECT/final.wav); NAME.timeline.json and NAME.srt go "
        "next to it",
    )
    export_parser.add_argument("--srt", action="store_true", help="Also write SRT subtitles")
    export_parser.add_argument(
        "--allow-unapproved",
        action="store_true",
        help="Export the chosen take of lines not approved yet instead of refusing",
    )
    export_parser.add_argument(
        "--no-level", action="store_true", help="Keep each take's loudness as rendered"
    )


def _add_window(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="start", help="First segment id")
    parser.add_argument("--count", type=int, help="How many segments")


def run(settings: Settings, args: argparse.Namespace) -> int:
    command = args.dialogue_command
    if command == "init":
        return cmd_init(args)
    if command == "sync":
        return cmd_sync(settings, args)
    project = Project.open(args.project)
    with project_lock(project.root):
        Project.open(project.root)  # recheck after acquiring ownership
        return _run_project(project, settings, args)


def _run_project(project: Project, settings: Settings, args: argparse.Namespace) -> int:
    command = args.dialogue_command
    if command == "segments":
        return cmd_segments(project, args)
    if command == "annotate":
        return cmd_annotate(project, args)
    if command == "check":
        return cmd_check(project, settings)
    if command == "show":
        return cmd_show(project, args)
    if command == "stats":
        return cmd_stats(project)
    if command == "status":
        return cmd_status(project, settings)
    if command == "cast":
        return asyncio.run(cmd_cast(project, settings, args))
    if command == "review":
        return _run_review(project, settings, args)
    if command == "play":
        return cmd_play(project, settings, args)
    if command == "export":
        return cmd_export(project, settings, args)
    return asyncio.run(cmd_render(project, settings, args))


def cmd_sync(settings: Settings, args: argparse.Namespace) -> int:
    if args.recover:
        if args.source is not None or args.dry_run:
            raise VoxelloError(INVALID_PARAMETER, "--recover accepts neither SOURCE nor --dry-run.")
        project = Project(args.project)
        recovery = recovery_plan(project)
        if recovery is None:
            print("No interrupted sync to recover.")
            return 0
        action = (
            "Finish committed sync cleanup"
            if recovery.journal.phase == "committed"
            else "Restore pre-sync files"
        )
        print(f"{action}. Recovery copy: {recovery.archive / 'before'}")
        if ask("Recover this project? [y/N] ").strip().lower() not in ("y", "s"):
            print("Recovery cancelled; project remains blocked.")
            return 0
        recover_sync(project, recovery)
        print("Recovery complete; recovery copies have been retained.")
        return 0
    if args.source is None:
        raise VoxelloError(INVALID_PARAMETER, "sync requires SOURCE, or --recover without SOURCE.")
    project = Project.open(args.project)
    plan = plan_sync(project, args.source, _defaults(settings))
    print("\n".join(format_plan(plan, project.root)))
    if args.dry_run or not plan.changed:
        return 0
    if ask("Apply this sync? [y/N] ").strip().lower() not in ("y", "s"):
        print("Sync cancelled; project files are unchanged.")
        return 0
    archive = apply_sync(project, plan)
    print(f"Sync complete. Recovery copy: {archive}")
    print(f"Next: voxello dialogue status -p {shlex.quote(str(project.root))}")
    return 0


def _run_review(project: Project, settings: Settings, args: argparse.Namespace) -> int:
    try:
        return asyncio.run(cmd_review(project, settings, args))
    except KeyboardInterrupt:
        print(f"\n{_saved_at(project.load_state().review.position)}")
        return 130


def cmd_init(args: argparse.Namespace) -> int:
    source: Path = args.source
    directory: Path = args.directory or source.with_suffix("")
    if directory == source:
        directory = source.with_name(source.name + ".dialogue")
    project = Project.init(source, directory, max_chars=args.max_chars)
    script = project.load_script()
    result = segment_text(project.load_source(), max_chars=script.max_chars)
    for warning in result.warnings:
        print(f"warning: {warning.segment_id}: {warning.message}", file=sys.stderr)
    kinds = Counter(seg.kind for seg in script.segments)
    paragraphs = len({seg.id.split(".")[0] for seg in script.segments})
    detail = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
    print(
        f"Created {directory}: {len(script.segments)} segments in {paragraphs} paragraphs "
        f"({detail})."
    )
    print(f"Next: annotate speakers (voxello dialogue segments -p {directory}), then cast.yaml.")
    return 0


def cmd_segments(project: Project, args: argparse.Namespace) -> int:
    script = project.load_script()
    chosen = select_range(script, None, start=args.start)
    if args.pending:
        chosen = [s for s in chosen if s.kind == "speech" and s.speaker is None and not s.skip]
    if args.count is not None:
        chosen = chosen[: args.count]
    for seg in chosen:
        annotations = seg.model_dump(exclude_defaults=True, exclude={"id", "kind", "text"})
        shown = ""
        if annotations:
            flow = yaml.safe_dump(
                annotations, default_flow_style=True, allow_unicode=True, sort_keys=False
            )
            shown = flow.strip() + " "
        print(f"{seg.id} {seg.kind} {shown}{_one_line(seg.text)}")
    return 0


def cmd_annotate(project: Project, args: argparse.Namespace) -> int:
    try:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text("utf-8")
    except OSError as exc:
        raise VoxelloError(INVALID_SCRIPT, f"Cannot read {args.file}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise VoxelloError(INVALID_SCRIPT, f"{args.file} is not valid YAML: {exc}") from exc
    result = project.annotate(data)
    if not result.changed:
        print("No segment changed.")
        return 0
    plural = "s" if len(result.changed) != 1 else ""
    print(f"Annotated {len(result.changed)} segment{plural}: {', '.join(result.changed)}")
    return 0


def cmd_check(project: Project, settings: Settings) -> int:
    report = project.check(settings.speech.voice_designs)
    for issue in report.errors:
        print(f"error: {issue}")
    for issue in report.warnings:
        print(f"warning: {issue}")
    if not report.ok:
        print(f"{len(report.errors)} error(s).")
        return 1
    script = project.load_script()
    cast = project.load_cast()
    spoken = sum(
        1
        for seg in script.segments
        if (is_spoken(seg, cast) if cast else seg.kind != "break" and not seg.skip)
    )
    print(f"OK: {len(script.segments)} segments rebuild source.txt; {spoken} lines to speak.")
    return 0


def cmd_show(project: Project, args: argparse.Namespace) -> int:
    script = project.load_script()
    cast = project.load_cast()
    for seg in select_range(script, None, start=args.start, count=args.count):
        if seg.kind == "break":
            print(f"{seg.id:<9} {'':<{LABEL_WIDTH}} {seg.text}")
            continue
        print(
            f"{seg.id:<9} {_label(seg, cast):<{LABEL_WIDTH}} {_one_line(effective_tts_text(seg))}"
        )
        if seg.tts_text is not None and seg.tts_text != default_tts_text(seg.kind, seg.text):
            print(f"{'':<9} text: {_one_line(seg.text)}")
    return 0


def _label(seg: ScriptSegment, cast: Cast | None) -> str:
    speaker = speaker_of(seg, cast)
    if speaker is None:
        label = "???" if seg.kind == "speech" else f"({seg.kind})"
    else:
        label = speaker.upper()
    if seg.register_:
        label += f" ({seg.register_})"
    if seg.attribution:
        label += " (attribution)"
    if seg.skip:
        label += " (skip)"
    return label


def cmd_stats(project: Project) -> int:
    script = project.load_script()
    cast = project.load_cast()
    lines: Counter[str] = Counter()
    chars: Counter[str] = Counter()
    unassigned = 0
    for seg in script.segments:
        if seg.kind == "break" or seg.skip:
            continue
        speaker = speaker_of(seg, cast)
        if speaker is None and seg.kind == "speech":
            unassigned += 1
            continue
        name = speaker or "(narration)"
        lines[name] += 1
        chars[name] += len(effective_tts_text(seg))
    print(f"{'speaker':<20} {'lines':>6} {'chars':>7}")
    for name, count in lines.most_common():
        print(f"{name:<20} {count:>6} {chars[name]:>7}")
    if unassigned:
        print(f"unassigned speech: {unassigned} line{'s' if unassigned != 1 else ''}")
    return 0


def cmd_status(project: Project, settings: Settings) -> int:
    for line in format_status(project_status(project, _defaults(settings)), project.root):
        print(line)
    return 0


async def list_profile_ids(settings: Settings) -> set[str] | None:
    """The cloned voices on the server, or ``None`` (with a warning) when they cannot be read."""
    from voxello.tts.voicestudio import VoiceStudioProvider

    store = VoiceStudioProvider(settings.tts.voicestudio)
    try:
        return {profile.profile_id for profile in await store.list_voice_profiles()}
    except VoxelloError as exc:
        print(
            f"warning: cannot list the server's cloned voices ({exc.code}); the draft uses "
            "clone:<name> for every character.",
            file=sys.stderr,
        )
        return None
    finally:
        await store.aclose()


async def cmd_cast(project: Project, settings: Settings, args: argparse.Namespace) -> int:
    language = (args.language or settings.speech.default_language).strip().lower()
    narrator = args.narrator or DEFAULT_NARRATOR.get(language, "narrator")
    if not NAME_RE.fullmatch(narrator):
        raise VoxelloError(
            INVALID_PARAMETER,
            f"Narrator name {narrator!r}: use up to 32 lowercase letters, digits, '-' or '_'.",
        )
    if args.write and project.cast_path.exists() and not args.force:
        raise VoxelloError(
            INVALID_SCRIPT,
            f"{project.cast_path} already exists: edit it, or pass --force to replace it.",
        )
    draft = draft_cast(
        project.load_script(),
        language=language,
        narrator=narrator,
        profiles=await list_profile_ids(settings),
        designs=settings.speech.voice_designs,
    )
    if args.write:
        write_atomic(project.cast_path, draft.text.encode("utf-8"))
        print(f"Wrote {project.cast_path}.")
    else:
        print(draft.text, end="")
    for name in draft.missing_voices:
        print(f"missing voice: clone:{name} (voxello voice record {name})", file=sys.stderr)
    for name, register, _lines in draft.missing_registers:
        print(
            f"missing register: clone:{name}-{register} "
            f"(voxello voice record {name} --register {register})",
            file=sys.stderr,
        )
    if draft.unassigned:
        print(
            f"warning: {draft.unassigned} speech segments have no speaker yet "
            "(voxello dialogue segments --pending).",
            file=sys.stderr,
        )
    return 0


def _defaults(settings: Settings) -> LineDefaults:
    vs = settings.tts.voicestudio
    return LineDefaults(
        designs=settings.speech.voice_designs,
        speed=vs.speed,
        num_step=vs.num_step,
        guidance_scale=vs.guidance_scale,
    )


async def cmd_render(project: Project, settings: Settings, args: argparse.Namespace) -> int:
    vs = settings.tts.voicestudio
    defaults = _defaults(settings)

    def progress(event: RenderEvent) -> None:
        what = "current" if event.reused else "rendered"
        seconds = f" ({event.duration_ms / 1000:.1f} s)" if event.duration_ms is not None else ""
        print(f"[{event.index}/{event.total}] {event.segment_id} {what}{seconds}", file=sys.stderr)

    provider = make_provider(vs)
    try:
        summary = await render(
            project,
            provider,
            lines=args.lines,
            force=args.force,
            defaults=defaults,
            progress=progress,
        )
    finally:
        await provider.aclose()
    print(f"Rendered {summary.rendered} lines, {summary.reused} already current.")
    return 0


async def cmd_review(project: Project, settings: Settings, args: argparse.Namespace) -> int:
    if not stdin_is_tty():
        raise VoxelloError(
            INVALID_PARAMETER,
            "`voxello dialogue review` needs an interactive terminal: run it in your own shell.",
        )
    provider = make_provider(settings.tts.voicestudio)
    try:
        with (
            tempfile.TemporaryDirectory(prefix="voxello-review-") as work,
            make_review_terminal(Path(work)) as terminal,
        ):
            session = ReviewSession(
                project,
                provider,
                make_player(settings),
                terminal,
                workdir=Path(work),
                defaults=_defaults(settings),
            )
            summary = await session.run(
                start=args.start, pending=args.pending, flagged=args.flagged
            )
    finally:
        await provider.aclose()
    print(_review_summary(project, summary))
    return 130 if summary.interrupted else 0


def cmd_play(project: Project, settings: Settings, args: argparse.Namespace) -> int:
    session = PlayThrough(
        project,
        make_player(settings),
        lambda text: print(text, flush=True),
        defaults=_defaults(settings),
    )
    try:
        summary = asyncio.run(session.run(args.start))
    except KeyboardInterrupt:
        print()
        sid = session.current
        if sid is None:
            return 130
        print(f"Stopped at {sid}.")
        if stdin_is_tty() and ask(f"Open review at {sid}? [y/N] ").strip().lower() in ("y", "s"):
            review = argparse.Namespace(start=sid, pending=False, flagged=False)
            return _run_review(project, settings, review)
        root = shlex.quote(str(project.root))
        print(f"Review it with: voxello dialogue review -p {root} --from {sid}")
        return 130
    details = []
    if summary.unapproved:
        details.append(f"{summary.unapproved} not approved")
    if summary.skipped:
        details.append(f"{summary.skipped} without a take")
    extra = f" ({', '.join(details)})" if details else ""
    print(f"Played {summary.played} line{'s' if summary.played != 1 else ''}{extra}.")
    return 0


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def cmd_export(project: Project, settings: Settings, args: argparse.Namespace) -> int:
    result = export(
        project,
        args.output,
        defaults=_defaults(settings),
        allow_unapproved=args.allow_unapproved,
        srt=args.srt,
        level=not args.no_level,
    )
    if result.unapproved:
        plural = "s" if result.unapproved != 1 else ""
        print(
            f"warning: {result.unapproved} line{plural} not approved, exported with the "
            "chosen take.",
            file=sys.stderr,
        )
    print(f"Exported {result.lines} lines ({result.duration_ms / 1000:.1f} s) to {result.wav}")
    print(f"Timeline: {result.timeline}")
    if result.srt is not None:
        print(f"Subtitles: {result.srt}")
    return 0


def _review_summary(project: Project, summary: ReviewSummary) -> str:
    counts = f"approved {summary.approved}/{summary.total} lines, {summary.flagged} flagged"
    if summary.position is None:
        return f"Review finished: {counts}."
    resume = f"Resume with: voxello dialogue review -p {shlex.quote(str(project.root))}"
    if summary.interrupted:
        return f"{_saved_at(summary.position)}: {counts}. {resume}"
    return f"{counts[0].upper()}{counts[1:]}. {resume}"


def _saved_at(position: str | None) -> str:
    return f"Review saved at {position}" if position else "Review saved"


def stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def make_player(settings: Settings) -> AudioPlayer | None:
    from voxello.playback.detect import detect_backend
    from voxello.playback.subprocess_player import SubprocessPlayer

    backend = detect_backend(settings.playback.backend)
    return SubprocessPlayer(backend, settings.playback.volume) if backend else None


class _ReviewConsole(Protocol):
    def __enter__(self) -> ReviewTerminal: ...

    def __exit__(self, *exc: object) -> None: ...


def make_review_terminal(workdir: Path) -> _ReviewConsole:
    fd = None if sys.platform == "win32" else sys.stdin.fileno()
    return ConsoleReviewTerminal(workdir, fd=fd)


def editor_command(env: Mapping[str, str], platform: str) -> list[str]:
    editor = env.get("VISUAL") or env.get("EDITOR")
    if editor:
        return shlex.split(editor)
    return ["notepad"] if platform == "win32" else ["vi"]


async def windows_key(msvcrt: Any, *, interval: float = KEY_POLL_SECONDS) -> str:
    """One key from ``msvcrt`` (polled, so the wait can be cancelled); arrows as on POSIX."""
    while not msvcrt.kbhit():  # noqa: ASYNC110 - msvcrt has no readiness callback to await
        await asyncio.sleep(interval)
    raw = msvcrt.getwch()
    if raw in ("\x00", "\xe0"):
        raw = WINDOWS_ARROWS.get(msvcrt.getwch(), "")
    return normalize_key(raw)


class ConsoleReviewTerminal:
    """Single keys without Enter: cbreak mode on ``fd`` while the review runs (signals stay
    on, so Ctrl-C interrupts even during a render), normal mode for the editor."""

    def __init__(self, workdir: Path, *, fd: int | None) -> None:
        self.workdir = workdir
        self.fd = fd
        self._saved: Any = None

    def __enter__(self) -> ConsoleReviewTerminal:
        self._cbreak()
        return self

    def __exit__(self, *exc: object) -> None:
        self._restore()

    def _cbreak(self) -> None:
        if sys.platform == "win32":
            return
        if self.fd is None:
            return
        import termios
        import tty

        self._saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def _restore(self) -> None:
        if sys.platform == "win32":
            return
        if self.fd is None or self._saved is None:
            return
        import termios

        termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)
        self._saved = None

    def say(self, text: str) -> None:
        print(text, flush=True)

    async def key(self) -> str:
        if self.fd is None:
            return await windows_key(importlib.import_module("msvcrt"))
        # Polled: loop.add_reader would be the Event-style wait, but kqueue (macOS) rejects ttys.
        while not select.select([self.fd], [], [], 0)[0]:  # noqa: ASYNC110
            await asyncio.sleep(KEY_POLL_SECONDS)
        return normalize_key(os.read(self.fd, 32).decode(errors="replace"))

    def edit(self, text: str) -> str | None:
        path = self.workdir / "tts_text.txt"
        path.write_text(text + "\n", encoding="utf-8")
        restore = self._saved is not None
        self._restore()
        try:
            result = subprocess.run(  # the user's own editor on a file we chose
                [*editor_command(os.environ, sys.platform), str(path)], check=False
            )
        except OSError as exc:
            self.say(f"error: could not start the editor: {exc}")
            return None
        finally:
            if restore:
                self._cbreak()
        if result.returncode != 0:
            self.say(f"The editor exited with status {result.returncode}; nothing changed.")
            return None
        return path.read_text(encoding="utf-8")


def make_provider(settings: VoiceStudioSettings) -> TTSProvider:
    from voxello.tts.voicestudio import VoiceStudioProvider

    return VoiceStudioProvider(settings)


def _one_line(text: str) -> str:
    return " ".join(text.split())
