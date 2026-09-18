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
import time
from datetime import datetime
from pathlib import Path

from voxello import __version__, install
from voxello.config import (
    EXAMPLE_CONFIG,
    Settings,
    describe_config_path,
    load_settings,
    resolve_config_path,
)
from voxello.errors import INVALID_PARAMETER, VoxelloError
from voxello.logging_setup import configure_logging
from voxello.tts.base import TTSProvider, VoiceInfo

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
    doctor = sub.add_parser(
        "doctor",
        help="Check configuration, TTS server, voices, player and notifier",
        description="Check the setup. Configured voices missing from the server's voice list "
        "are reported as warnings, not failures: omnivoice-server accepts ids its list may "
        "not expose. Exit status 1 when the server is unhealthy, no player is found or "
        "--synth fails.",
    )
    doctor.add_argument(
        "--synth",
        action="store_true",
        help="Synthesize a short sample phrase and report the latency (talks to the TTS server)",
    )
    doctor.add_argument(
        "--language",
        "-l",
        default=None,
        help="Language of the sample phrase (default: speech.default_language)",
    )

    speak = sub.add_parser("speak", help="Synthesize and play text without an agent")
    speak.add_argument("text", help="Text to speak")
    speak.add_argument("--voice", default=None)
    speak.add_argument(
        "--language",
        "-l",
        default=None,
        help="ISO 639-1 code of the text (default: speech.default_language)",
    )
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
    notify.add_argument(
        "--language",
        "-l",
        default=None,
        help="ISO 639-1 code of the message (default: speech.default_language)",
    )

    hook = sub.add_parser(
        "hook", help="Entry points for editor hooks (used by the Claude Code hook script)"
    )
    hook_sub = hook.add_subparsers(dest="hook_command", required=True)
    hook_notification = hook_sub.add_parser(
        "notification",
        help="Speak a Claude Code Notification event read as JSON from stdin",
        description=(
            "Reads the Notification JSON that Claude Code pipes to its hooks, picks the "
            "sentence for its notification_type from the package message files "
            "(voxello/assets/claude/messages/<language>.yaml) and delivers it with "
            "'notify --cache' in that language. Unknown types speak the event's own message."
        ),
    )
    hook_notification.add_argument(
        "--language",
        "-l",
        default=None,
        help=f"Language of the sentence (default: ${install.HOOK_LANG_ENV_VAR} or it)",
    )
    hook_notification.add_argument(
        "--channels",
        default=None,
        help=(
            f"Comma-separated channels (default: ${install.HOOK_CHANNELS_ENV_VAR} or "
            f"{install.HOOK_DEFAULT_CHANNELS})"
        ),
    )
    hook_notification.add_argument(
        "--priority", default="high", choices=["low", "normal", "high", "critical"]
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
        "-l",
        default=None,
        help=(
            "ISO 639-1 code the phrases are synthesized in; with --hook-phrases it also picks "
            "the message file (default: $VOXELLO_HOOK_LANG or it, else speech.default_language)"
        ),
    )
    warm.add_argument("--voice", default=None, help="Voice id; omit for the configured default")

    config = sub.add_parser("config", help="Manage the configuration file")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    init = config_sub.add_parser("init", help="Write an example config file")
    init.add_argument("--force", action="store_true", help="Overwrite an existing file")
    config_sub.add_parser("path", help="Print the resolved config path")
    config_sub.add_parser("show", help="Print the effective configuration")

    install_parser = sub.add_parser("install", help="Install Voxello integrations into other tools")
    install_sub = install_parser.add_subparsers(dest="install_command", required=True)
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
        choices=install.hook_languages(),
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
            return asyncio.run(
                cmd_doctor(_load(args), args.config, synth=args.synth, language=args.language)
            )
        if command == "speak":
            return asyncio.run(cmd_speak(_load(args), args))
        if command == "notify":
            return asyncio.run(cmd_notify(_load(args), args))
        if command == "hook":
            return asyncio.run(cmd_hook(_load(args), args))
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


SAMPLE_PHRASES = {"it": "Voxello è pronto.", "en": "Voxello is ready."}
VOICES_PER_LINE = 8


async def cmd_doctor(
    settings: Settings,
    config_path: Path | None,
    *,
    synth: bool = False,
    language: str | None = None,
    provider: TTSProvider | None = None,
) -> int:
    """Print the setup report; ``provider`` is injected by tests, otherwise VoiceStudio."""
    from voxello.core.service import VoxelloService, normalize_language
    from voxello.notifications.desktop import detect_notifier_command
    from voxello.playback.detect import detect_backend
    from voxello.tts.voicestudio import VoiceStudioProvider

    language = normalize_language(language, settings.speech.default_language)
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
        f"  engine={vs.engine} voice={vs.voice or 'server-default'} "
        f"api_key={'set' if vs.api_key else 'not set'}"
    )
    for line in describe_speech(settings):
        print(line)

    if provider is None:
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
            except VoxelloError as exc:
                print(f"  voices: could not list ({exc.code})")
            else:
                for line in describe_voices(voices, settings):
                    print(line)
            if synth:
                ok = await _doctor_synth(provider, settings, language) and ok
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


def describe_voices(voices: list[VoiceInfo], settings: Settings) -> list[str]:
    """``doctor`` lines for the server's voices, grouped by language when the server says it,
    followed by one check per configured voice (roadmap milestone 4)."""
    lines: list[str] = []
    if not any(v.language for v in voices):
        ids = [v.id for v in voices[:16]]
        more = f", +{len(voices) - len(ids)} more" if len(voices) > len(ids) else ""
        lines.append(
            f"  voices: {len(voices)} available" + (f": {', '.join(ids)}{more}" if ids else "")
        )
    else:
        lines.append(f"  voices: {len(voices)} available")
        groups: dict[str, list[str]] = {}
        for v in voices:
            groups.setdefault(v.language or "unspecified", []).append(v.id)
        for lang in sorted(groups, key=lambda k: (k == "unspecified", k)):
            ids = groups[lang]
            shown = ", ".join(ids[:VOICES_PER_LINE])
            more = f" (+{len(ids) - VOICES_PER_LINE} more)" if len(ids) > VOICES_PER_LINE else ""
            lines.append(f"    {lang}: {shown}{more}")
    if voices:
        known = {v.id for v in voices}
        configured = [
            (f"voices_by_language[{lang}]", voice)
            for lang, voice in sorted(settings.speech.voices_by_language.items())
        ]
        if settings.tts.voicestudio.voice:
            configured.append(("voice", settings.tts.voicestudio.voice))
        for label, voice in configured:
            verdict = "found" if voice in known else "WARNING not in the server's voice list"
            lines.append(f"    {label}={voice}: {verdict}")
    return lines


async def _doctor_synth(provider: TTSProvider, settings: Settings, language: str) -> bool:
    """Synthesize a sample phrase with the voice a request in ``language`` would get and
    print the latency. Nothing is played, cached or kept."""
    from voxello.core.service import resolve_voice
    from voxello.storage.wav import wav_duration_ms

    text = SAMPLE_PHRASES.get(language, SAMPLE_PHRASES["en"])
    voice = resolve_voice(settings, None, language)
    started = time.perf_counter()
    try:
        result = await provider.synthesize(text, voice, language)
    except VoxelloError as exc:
        print(f"  synthesis: ERROR - {exc.code}: {exc.message}")
        return False
    elapsed = time.perf_counter() - started
    duration = wav_duration_ms(result.audio)
    audio = f", {duration / 1000:.1f} s of audio" if duration is not None else ""
    print(
        f"  synthesis: ok in {elapsed:.2f} s (voice={result.voice}, language={language}, "
        f"{_human_size(len(result.audio))}{audio})"
    )
    return True


def describe_speech(settings: Settings) -> list[str]:
    """``doctor`` lines for the language defaults (roadmap 3.1 / 3.2)."""
    speech = settings.speech
    line = f"  default_language={speech.default_language}"
    if speech.voices_by_language:
        pairs = ", ".join(f"{k}={v}" for k, v in sorted(speech.voices_by_language.items()))
        line += f" voices_by_language: {pairs}"
    lines = [line]
    if settings.tts.voicestudio.language is not None:
        lines.append(
            "  DEPRECATED: tts.voicestudio.language is set; move it to speech.default_language"
        )
    return lines


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
            language=args.language,
        )
        print(result.model_dump_json(indent=2))
        if service.playback is not None and not args.no_play:
            await service.playback.wait_idle()
    finally:
        await service.aclose()
    return 0


def _split_channels(raw: str) -> list[str]:
    return [c.strip() for c in raw.split(",") if c.strip()]


async def _deliver_notification(
    settings: Settings,
    message: str,
    *,
    channels: list[str],
    priority: str,
    cache: bool | None,
    language: str | None,
) -> int:
    """Shared body of ``notify`` and ``hook notification``: deliver, print, wait, exit code."""
    from voxello.core.service import VoxelloService

    service = VoxelloService.from_settings(settings)
    await service.start()
    try:
        result = await service.notify(
            message,
            channels=channels,
            priority=priority,  # type: ignore[arg-type]
            cache=cache,
            language=language,
        )
        print(result.model_dump_json(indent=2))
        if service.playback is not None:
            await service.playback.wait_idle()
    finally:
        await service.aclose()
    return 0 if result.status != "failed" else 1


async def cmd_notify(settings: Settings, args: argparse.Namespace) -> int:
    return await _deliver_notification(
        settings,
        args.message,
        channels=_split_channels(args.channels),
        priority=args.priority,
        cache=args.cache,
        language=args.language,
    )


async def cmd_hook(settings: Settings, args: argparse.Namespace) -> int:
    if args.hook_command == "notification":
        return await cmd_hook_notification(settings, args)
    return 2


async def cmd_hook_notification(settings: Settings, args: argparse.Namespace) -> int:
    """Claude Code Notification event (JSON on stdin) -> spoken sentence (roadmap 3.3).

    The bash hook only resolves the ``voxello`` command and execs this; the sentence
    selection lives in :func:`voxello.install.hook_text` so it has one source of truth.
    """
    language = (
        args.language or os.environ.get(install.HOOK_LANG_ENV_VAR) or install.HOOK_DEFAULT_LANGUAGE
    )
    channels = _split_channels(
        args.channels
        or os.environ.get(install.HOOK_CHANNELS_ENV_VAR)
        or install.HOOK_DEFAULT_CHANNELS
    )
    payload = install.parse_hook_payload(sys.stdin.read() if not sys.stdin.isatty() else "")
    text = install.hook_text(payload, language)
    return await _deliver_notification(
        settings,
        text,
        channels=channels,
        priority=args.priority,
        cache=True,
        language=language,
    )


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


def _read_phrases(args: argparse.Namespace) -> tuple[list[str], str | None]:
    """Phrases to warm and the language to synthesize them in (``None`` = default).

    With ``--hook-phrases`` the language also selects the message file, so the English
    sentences are cached under the English key (roadmap 3.1).
    """
    if args.hook_phrases and args.source:
        raise VoxelloError(INVALID_PARAMETER, "Pass either FILE or --hook-phrases, not both.")
    if args.hook_phrases:
        language = (
            args.language
            or os.environ.get(install.HOOK_LANG_ENV_VAR)
            or install.HOOK_DEFAULT_LANGUAGE
        )
        return list(dict.fromkeys(install.hook_phrases(language).values())), language
    if not args.source:
        raise VoxelloError(
            INVALID_PARAMETER, "Pass a FILE with one phrase per line, '-' or --hook-phrases."
        )
    try:
        content = sys.stdin.read() if args.source == "-" else Path(args.source).read_text("utf-8")
    except OSError as exc:
        raise VoxelloError(INVALID_PARAMETER, f"Could not read {args.source}: {exc}") from exc
    lines = [line.strip() for line in content.splitlines()]
    phrases = list(dict.fromkeys(line for line in lines if line and not line.startswith("#")))
    return phrases, args.language


async def cmd_cache_warm(settings: Settings, args: argparse.Namespace) -> int:
    from voxello.core.service import VoxelloService

    phrases, language = _read_phrases(args)
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
                    phrase,
                    voice=args.voice,
                    play=False,
                    mode="notification",
                    cache=True,
                    language=language,
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
