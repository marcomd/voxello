"""Command-line entry point.

``voxello`` (or ``voxello serve``) runs the MCP server over stdio. The other
subcommands are for humans: ``doctor`` checks the setup, ``speak`` does a full
round trip without an agent, ``config`` manages the config file.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from voxello import __version__
from voxello.config import (
    EXAMPLE_CONFIG,
    Settings,
    load_settings,
    resolve_config_path,
)
from voxello.errors import VoxelloError
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

    notify = sub.add_parser("notify", help="Send a notification through the configured channels")
    notify.add_argument("message")
    notify.add_argument("--channels", default="voice,desktop", help="Comma-separated channels")
    notify.add_argument(
        "--priority", default="normal", choices=["low", "normal", "high", "critical"]
    )

    config = sub.add_parser("config", help="Manage the configuration file")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    init = config_sub.add_parser("init", help="Write an example config file")
    init.add_argument("--force", action="store_true", help="Overwrite an existing file")
    config_sub.add_parser("path", help="Print the resolved config path")
    config_sub.add_parser("show", help="Print the effective configuration")
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

    path = resolve_config_path(config_path)
    ok = True
    print(f"Voxello {__version__}")
    print(f"Config file: {path} ({'found' if path.is_file() else 'not found, using defaults'})")
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
    del VoxelloService  # imported only to fail fast on broken installs
    print("Result:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


async def cmd_speak(settings: Settings, args: argparse.Namespace) -> int:
    from voxello.core.service import VoxelloService

    service = VoxelloService.from_settings(settings)
    await service.start()
    try:
        result = await service.speak(
            args.text, voice=args.voice, save=args.save, play=not args.no_play, interrupt=True
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
        result = await service.notify(args.message, channels=channels, priority=args.priority)
        print(result.model_dump_json(indent=2))
        if service.playback is not None:
            await service.playback.wait_idle()
    finally:
        await service.aclose()
    return 0 if result.status != "failed" else 1


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
