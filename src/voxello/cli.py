"""Command-line entry point.

``voxello`` (or ``voxello serve``) runs the MCP server over stdio. The other
subcommands are for humans: ``doctor`` checks the setup, ``speak`` does a full
round trip without an agent, ``config`` manages the config file, ``cache`` inspects
and pre-fills the audio cache, ``install claude`` sets up the Claude Code skill and hook.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from voxello import __version__
from voxello.config import (
    EXAMPLE_CONFIG,
    Settings,
    describe_config_path,
    load_settings,
    resolve_config_path,
)
from voxello.errors import INVALID_PARAMETER, VoxelloError
from voxello.logging_setup import configure_logging

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voxello", description="A local voice and notification layer for AI agents."
    )
    parser.add_argument("--version", action="version", version=f"voxello {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--log-level", default=None, help="debug, info, warning or error")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="Run the MCP server over stdio (default)")
    sub.add_parser("doctor", help="Check configuration, VoiceStudio, player and notifier")

    speak = sub.add_parser("speak", help="Synthesize and play text without an agent")
    speak.add_argument("text", help="Text to speak")
    speak.add_argument("--voice", default=None)
    speak.add_argument("--save", action="store_true", help="Persist the audio file")
    speak.add_argument("--no-play", action="store_true", help="Do not play the audio")
    speak.add_argument(
        "--cache",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Reuse cached audio for repeated phrases (default: off for speak)",
    )

    notify = sub.add_parser("notify", help="Send a notification through the configured channels")
    notify.add_argument("message")
    notify.add_argument("--channels", default="voice,desktop", help="Comma-separated channels")
    notify.add_argument(
        "--priority", default="normal", choices=["low", "normal", "high", "critical"]
    )
    notify.add_argument(
        "--cache",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Reuse cached audio for repeated phrases (default: on for notify)",
    )

    cache = sub.add_parser("cache", help="Inspect, clear or pre-fill the audio cache")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_sub.add_parser("list", help="List cached phrases (hash, size, length, voice, last use)")
    cache_sub.add_parser("clear", help="Delete every cached phrase")
    warm = cache_sub.add_parser(
        "warm",
        help="Synthesize a list of phrases into the cache ahead of time",
        description=(
            "Reads one phrase per line from FILE (or stdin with '-'), or the Claude Code hook "
            "sentences with --hook-phrases, and synthesizes the ones not cached yet so the "
            "first alert does not wait for the TTS server."
        ),
    )
    warm.add_argument("source", nargs="?", help="Text file with one phrase per line, or '-'")
    warm.add_argument(
        "--hook-phrases", action="store_true", help="Warm the Claude Code hook sentences"
    )
    warm.add_argument(
        "--language",
        default=None,
        help="Language of the hook sentences (default: $VOXELLO_HOOK_LANG or it)",
    )
    warm.add_argument("--voice", default=None, help="Voice id; omit for the configured default")

    config = sub.add_parser("config", help="Manage the configuration file")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    init = config_sub.add_parser("init", help="Write an example config file")
    init.add_argument("--force", action="store_true", help="Overwrite an existing file")
    config_sub.add_parser("path", help="Print the resolved config path")
    config_sub.add_parser("show", help="Print the effective configuration")

    install = sub.add_parser("install", help="Install Voxello integrations into other tools")
    install_sub = install.add_subparsers(dest="install_command", required=True)
    claude = install_sub.add_parser(
        "claude",
        help="Install the voice-notify skill and the Notification hook for Claude Code",
        description=(
            "Copies the voice-notify skill and the Notification hook into the Claude Code "
            "user directory and offers to register the hook in settings.json. The settings "
            "file is only modified with --yes or after an interactive confirmation."
        ),
    )
    claude.add_argument(
        "--claude-dir",
        type=Path,
        default=None,
        help="Claude Code user directory (default: $CLAUDE_CONFIG_DIR or ~/.claude)",
    )
    claude.add_argument(
        "--yes", "-y", action="store_true", help="Register the hook in settings.json without asking"
    )
    claude.add_argument("--no-skill", action="store_true", help="Do not install the skill")
    claude.add_argument("--no-hook", action="store_true", help="Do not install the hook")
    claude.add_argument(
        "--lang",
        choices=["it", "en"],
        default=None,
        help="Language of the spoken hook sentences (default: the hook's own default, Italian)",
    )
    return parser


def _load(args: argparse.Namespace) -> Settings:
    settings = load_settings(args.config)
    level = args.log_level or settings.logging.level
    configure_logging(level, settings.logging.file)
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "serve"
    try:
        if command == "serve":
            return cmd_serve(_load(args))
        if command == "doctor":
            return asyncio.run(cmd_doctor(_load(args), args.config))
        if command == "speak":
            return asyncio.run(cmd_speak(_load(args), args))
        if command == "notify":
            return asyncio.run(cmd_notify(_load(args), args))
        if command == "config":
            return cmd_config(args)
        if command == "cache":
            return cmd_cache(_load(args), args)
        if command == "install":
            return cmd_install(args)
    except VoxelloError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    parser.print_help()
    return 2


def cmd_serve(settings: Settings) -> int:
    from voxello.mcp.server import create_server

    server = create_server(settings)
    log.info("Starting Voxello MCP server over stdio")
    server.run(transport="stdio")
    return 0


async def cmd_doctor(settings: Settings, config_path: Path | None) -> int:
    from voxello.core.service import VoxelloService
    from voxello.notifications.desktop import detect_notifier_command
    from voxello.playback.detect import detect_backend
    from voxello.tts.voicestudio import VoiceStudioProvider

    path, source = describe_config_path(config_path)
    ok = True
    print(f"Voxello {__version__}")
    for line in describe_installation():
        print(line)
    found = "found" if path.is_file() else "not found, using defaults"
    print(f"Config file: {path} ({found}; source: {source})")
    vs = settings.tts.voicestudio
    print(f"TTS provider: {settings.tts.provider} at {vs.base_url}")
    print(
        f"  engine={vs.engine} voice={vs.voice or 'server-default'} language={vs.language} "
        f"api_key={'set' if vs.api_key else 'not set'}"
    )

    provider = VoiceStudioProvider(vs)
    try:
        health = await provider.health()
        if health.status == "ok":
            details = [f"version {health.version}"] if health.version else []
            details += [f"{k}={v}" for k, v in health.extra.items() if k in ("model_id", "device")]
            print(f"  health: ok{' (' + ', '.join(details) + ')' if details else ''}")
            try:
                engines = await provider.list_engines()
                if engines:
                    print(f"  engines: {', '.join(engines[:12])}")
            except VoxelloError as exc:
                print(f"  engines: could not list ({exc.code})")
            try:
                voices = await provider.list_voices()
                ids = [v.id for v in voices[:16]]
                more = f", +{len(voices) - len(ids)} more" if len(voices) > len(ids) else ""
                print(
                    f"  voices: {len(voices)} available"
                    + (f": {', '.join(ids)}{more}" if ids else "")
                )
            except VoxelloError as exc:
                print(f"  voices: could not list ({exc.code})")
        else:
            ok = False
            print(f"  health: ERROR - {health.detail}")
    finally:
        await provider.aclose()

    backend = detect_backend(settings.playback.backend)
    if backend is None:
        ok = False
        print("Playback: no supported audio player found")
    else:
        volume = f" volume={settings.playback.volume}" if backend.supports_volume else ""
        print(f"Playback: {backend.name}{volume}")

    notifier = detect_notifier_command()
    print(f"Desktop notifications: {notifier or 'no tool found'}")
    print(f"Temp dir: {settings.storage.resolved_temp_dir()}")
    print(f"Output dir: {settings.output.resolved_directory()}")
    print(describe_cache(settings))
    del VoxelloService  # imported only to fail fast on broken installs
    print("Result:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


async def cmd_speak(settings: Settings, args: argparse.Namespace) -> int:
    from voxello.core.service import VoxelloService

    service = VoxelloService.from_settings(settings)
    await service.start()
    try:
        result = await service.speak(
            args.text,
            voice=args.voice,
            save=args.save,
            play=not args.no_play,
            interrupt=True,
            cache=args.cache,
        )
        print(result.model_dump_json(indent=2))
        if service.playback is not None and not args.no_play:
            await service.playback.wait_idle()
    finally:
        await service.aclose()
    return 0


async def cmd_notify(settings: Settings, args: argparse.Namespace) -> int:
    from voxello.core.service import VoxelloService

    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    service = VoxelloService.from_settings(settings)
    await service.start()
    try:
        result = await service.notify(
            args.message, channels=channels, priority=args.priority, cache=args.cache
        )
        print(result.model_dump_json(indent=2))
        if service.playback is not None:
            await service.playback.wait_idle()
    finally:
        await service.aclose()
    return 0 if result.status != "failed" else 1


def describe_cache(settings: Settings) -> str:
    """One ``doctor`` line: where the cache is and how full it is."""
    from voxello.core.service import VoxelloService

    cache = VoxelloService.cache_from_settings(settings)
    if cache is None:
        return "Cache: disabled"
    count, size = cache.stats()
    limits = f"max {settings.cache.max_entries} entries / {settings.cache.max_age_days} days"
    return f"Cache: {cache.directory} ({count} entries, {_human_size(size)}, {limits})"


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def cmd_cache(settings: Settings, args: argparse.Namespace) -> int:
    from voxello.core.service import VoxelloService

    cache = VoxelloService.cache_from_settings(settings)
    if cache is None:
        print("The audio cache is disabled (cache.enabled: false).", file=sys.stderr)
        return 1
    if args.cache_command == "list":
        entries = cache.entries()
        for entry in entries:
            used = datetime.fromtimestamp(entry.last_used).strftime("%Y-%m-%d %H:%M")
            print(
                f"{entry.key[:12]}  {_human_size(entry.size):>7}  chars={entry.text_chars:<4} "
                f"voice={entry.voice}  last used {used}"
            )
        total = sum(e.size for e in entries)
        plural = "y" if len(entries) == 1 else "ies"
        print(f"{len(entries)} entr{plural}, {_human_size(total)} in {cache.directory}")
        return 0
    if args.cache_command == "clear":
        removed = cache.clear()
        print(
            f"Removed {removed} cached phrase{'s' if removed != 1 else ''} from {cache.directory}"
        )
        return 0
    if args.cache_command == "warm":
        return asyncio.run(cmd_cache_warm(settings, args))
    return 2


def _read_phrases(args: argparse.Namespace) -> list[str]:
    from voxello import install

    if args.hook_phrases and args.source:
        raise VoxelloError(INVALID_PARAMETER, "Pass either FILE or --hook-phrases, not both.")
    if args.hook_phrases:
        language = args.language or os.environ.get("VOXELLO_HOOK_LANG") or "it"
        return list(dict.fromkeys(install.hook_phrases(language).values()))
    if not args.source:
        raise VoxelloError(
            INVALID_PARAMETER, "Pass a FILE with one phrase per line, '-' or --hook-phrases."
        )
    try:
        content = sys.stdin.read() if args.source == "-" else Path(args.source).read_text("utf-8")
    except OSError as exc:
        raise VoxelloError(INVALID_PARAMETER, f"Could not read {args.source}: {exc}") from exc
    lines = [line.strip() for line in content.splitlines()]
    return list(dict.fromkeys(line for line in lines if line and not line.startswith("#")))


async def cmd_cache_warm(settings: Settings, args: argparse.Namespace) -> int:
    from voxello.core.service import VoxelloService

    phrases = _read_phrases(args)
    if not phrases:
        print("No phrases to warm.", file=sys.stderr)
        return 1
    service = VoxelloService.from_settings(settings)
    await service.start()
    synthesized = already = failed = 0
    try:
        for phrase in phrases:
            try:
                result = await service.speak(
                    phrase, voice=args.voice, play=False, mode="notification", cache=True
                )
            except VoxelloError as exc:
                failed += 1
                print(f"error: {exc}", file=sys.stderr)
                continue
            if result.cached:
                already += 1
            else:
                synthesized += 1
    finally:
        await service.aclose()
    plural = "" if synthesized == 1 else "s"
    print(f"Warmed {synthesized} phrase{plural} ({already} already cached, {failed} failed)")
    return 0 if failed == 0 else 1


def describe_installation() -> list[str]:
    """Lines for ``doctor`` saying which binary runs and how Voxello was installed.

    Roadmap 1.1/1.3: from a checkout or a project venv the user may wonder why
    ``voxello`` is not on their PATH; the hint explains the two supported modes.
    """
    executable = shutil.which("voxello") or str(Path(sys.argv[0]).resolve())
    lines = [f"Executable: {executable}", f"Python: {sys.executable}"]
    kind, checkout = _installation_kind()
    if kind == "editable":
        lines.append(f"Install: editable checkout at {checkout}")
    elif kind == "uv-tool":
        lines.append("Install: installed package (uv tool)")
    else:
        lines.append("Install: installed package")
    if kind != "uv-tool":
        lines.append("Hint: 'uv tool install voxello' puts the command on your PATH everywhere;")
        lines.append("      'uv add --dev voxello' makes 'uv run voxello' work inside one project.")
    return lines


def _installation_kind() -> tuple[str, str | None]:
    """Return ("editable", checkout_dir) | ("uv-tool", None) | ("package", None)."""
    import json
    from importlib import metadata

    try:
        direct_url = metadata.distribution("voxello").read_text("direct_url.json")
        if direct_url:
            info = json.loads(direct_url)
            if info.get("dir_info", {}).get("editable"):
                url = str(info.get("url", ""))
                return "editable", url.removeprefix("file://") or None
    except (metadata.PackageNotFoundError, ValueError, OSError):
        pass
    if "/uv/tools/" in Path(sys.executable).as_posix():
        return "uv-tool", None
    return "package", None


def cmd_install(args: argparse.Namespace) -> int:
    if args.install_command == "claude":
        return cmd_install_claude(args)
    return 2


def cmd_install_claude(args: argparse.Namespace) -> int:
    from voxello import install

    claude_dir = (args.claude_dir or install.default_claude_dir()).expanduser()
    print(f"Claude Code directory: {claude_dir}")
    if not args.no_skill:
        skill = install.install_skill(claude_dir)
        print(f"Skill voice-notify: {skill.status} ({skill.path})")
    if args.no_hook:
        return 0

    hook = install.install_hook(claude_dir)
    print(f"Notification hook: {hook.status} ({hook.path})")
    settings_path = claude_dir / "settings.json"
    entry = install.hook_settings_entry(hook.path, lang=args.lang)
    if install.hook_registered(settings_path):
        print(f"Hook already registered in {settings_path}; nothing to change.")
        _print_next_steps()
        return 0

    print(f"\nTo enable the hook, add this to {settings_path}:\n")
    print(install.render_settings_snippet(entry))
    print()
    if args.yes:
        write = True
    elif sys.stdin.isatty():
        answer = input(f"Add it to {settings_path} now? [y/N] ")
        write = answer.strip().lower() in ("y", "yes")
    else:
        write = False
        print("Re-run with --yes to write it, or add it by hand.")
    if write:
        install.merge_hook_into_settings(settings_path, entry)
        print(f"Registered the hook in {settings_path}")
    else:
        print(f"{settings_path} left untouched.")
    _print_next_steps()
    return 0


def _print_next_steps() -> None:
    print(
        "\nNext steps:\n"
        "  claude mcp add --scope user --transport stdio voxello -- voxello serve\n"
        "  voxello doctor"
    )


def cmd_config(args: argparse.Namespace) -> int:
    path = resolve_config_path(args.config)
    if args.config_command == "path":
        print(path)
        return 0
    if args.config_command == "show":
        settings = load_settings(args.config)
        print(settings.model_dump_json(indent=2, exclude={"tts": {"voicestudio": {"api_key"}}}))
        return 0
    if args.config_command == "init":
        if path.exists() and not args.force:
            print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        print(f"Wrote {path}")
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
