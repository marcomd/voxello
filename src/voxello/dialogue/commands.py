"""``voxello dialogue ...``: the terminal side of dialogue production (roadmap 8.3-8.4).

The commands are thin: files and rules live in ``project``/``script``/``render``. Output for
the agent (``segments``) is one segment per line, ids first, so it can be read in chunks.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from voxello.dialogue.project import LineDefaults, Project
from voxello.dialogue.render import RenderEvent, render
from voxello.dialogue.script import (
    ScriptSegment,
    effective_tts_text,
    is_spoken,
    select_range,
    speaker_of,
)
from voxello.dialogue.segment import DEFAULT_MAX_CHARS, default_tts_text, segment_text
from voxello.errors import INVALID_SCRIPT, VoxelloError

if TYPE_CHECKING:
    from voxello.config import Settings, VoiceStudioSettings
    from voxello.dialogue.cast import Cast
    from voxello.tts.base import TTSProvider

LABEL_WIDTH = 20


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

    segments = dsub.add_parser(
        "segments", parents=[project], help="Print segments with their annotations"
    )
    _add_window(segments)
    segments.add_argument(
        "--pending", action="store_true", help="Only speech that has no speaker yet"
    )

    annotate = dsub.add_parser(
        "annotate", parents=[project], help="Merge annotations ({id: {field: value}}) by id"
    )
    annotate.add_argument("file", help="YAML file, or - for stdin")

    dsub.add_parser("check", parents=[project], help="Validate script, source and cast")
    show = dsub.add_parser("show", parents=[project], help="Screenplay view of the spoken text")
    _add_window(show)
    dsub.add_parser("stats", parents=[project], help="Lines and characters per speaker")

    render_parser = dsub.add_parser(
        "render", parents=[project], help="Synthesize every line without a current take"
    )
    render_parser.add_argument("--lines", help="ID, A-B, A- or -B (default: every line)")
    render_parser.add_argument(
        "--force", action="store_true", help="Synthesize again even when a take is current"
    )


def _add_window(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="start", help="First segment id")
    parser.add_argument("--count", type=int, help="How many segments")


def run(settings: Settings, args: argparse.Namespace) -> int:
    command = args.dialogue_command
    if command == "init":
        return cmd_init(args)
    project = Project.open(args.project)
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
    return asyncio.run(cmd_render(project, settings, args))


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


async def cmd_render(project: Project, settings: Settings, args: argparse.Namespace) -> int:
    vs = settings.tts.voicestudio
    defaults = LineDefaults(
        designs=settings.speech.voice_designs,
        speed=vs.speed,
        num_step=vs.num_step,
        guidance_scale=vs.guidance_scale,
    )

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


def make_provider(settings: VoiceStudioSettings) -> TTSProvider:
    from voxello.tts.voicestudio import VoiceStudioProvider

    return VoiceStudioProvider(settings)


def _one_line(text: str) -> str:
    return " ".join(text.split())
