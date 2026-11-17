"""Install Voxello integrations into other tools (Claude Code and Codex).

``voxello install claude`` copies the ``voice-notify`` skill and the hook script shipped
inside the package (``voxello/assets/claude``) into the user's Claude Code directory and
offers to register the Notification hook (and, on request, the Stop hook) in
``settings.json``. ``voxello install codex`` copies the same skill and script into the Codex
home and offers the Stop hook in ``hooks.json``, which has the same shape. The functions
here are pure filesystem operations so they can be tested without a TTY; the interactive
part lives in the CLI.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml

from voxello.errors import INVALID_PARAMETER, INVALID_SETTINGS_FILE, VoxelloError

CLAUDE_DIR_ENV_VAR = "CLAUDE_CONFIG_DIR"
CODEX_DIR_ENV_VAR = "CODEX_HOME"
SKILL_NAME = "voice-notify"
HOOK_FILENAME = "voxello-notification-hook.sh"
HOOK_MATCHER = "permission_prompt|idle_prompt|agent_needs_input|elicitation_dialog"
HOOK_TIMEOUT_SECONDS = 30
HOOK_EVENT = "Notification"
STOP_EVENT = "Stop"
# Synchronous: the agent waits for the decision, but the hook never waits for audio.
STOP_HOOK_TIMEOUT_SECONDS = 30
HOOK_LANG_ENV_VAR = "VOXELLO_HOOK_LANG"
HOOK_CHANNELS_ENV_VAR = "VOXELLO_HOOK_CHANNELS"
HOOK_DEFAULT_LANGUAGE = "it"
HOOK_DEFAULT_CHANNELS = "voice,desktop"
# Claude Code notification types that share a sentence with another key of the message files.
HOOK_TYPE_ALIASES = {"elicitation_url_dialog": "elicitation_dialog"}
HOOK_TEXT_MAX_CHARS = 250  # it is spoken: keep it short
# Claude Code writes the `message` of its Notification events in English.
HOOK_LIVE_LANGUAGE = "en"

HookMode = Literal["cached", "live", "off"]

InstallStatus = Literal["installed", "updated", "unchanged"]


@dataclass(frozen=True)
class InstalledFile:
    path: Path
    status: InstallStatus


def asset_bytes(*parts: str) -> bytes:
    """Read a file from ``voxello/assets`` inside the installed package."""
    node = resources.files("voxello").joinpath("assets")
    for part in parts:
        node = node.joinpath(part)
    return node.read_bytes()


def hook_languages() -> list[str]:
    """Languages with a message file under ``voxello/assets/claude/messages``."""
    node = resources.files("voxello").joinpath("assets", "claude", "messages")
    return sorted(p.name.removesuffix(".yaml") for p in node.iterdir() if p.name.endswith(".yaml"))


def hook_phrases(language: str) -> dict[str, str]:
    """The sentences the Notification hook speaks for ``language`` (roadmap 2.2 / 3.3).

    Keys are Claude Code ``notification_type`` values plus ``default``. This is the single
    source of the hook sentences: ``voxello hook notification`` picks from it and
    ``voxello cache warm --hook-phrases`` pre-synthesizes it. Adding a language means adding
    ``assets/claude/messages/<code>.yaml``; nothing else changes.
    """
    if language not in hook_languages():
        raise VoxelloError(
            INVALID_PARAMETER,
            f"No hook phrases for language '{language}'. Available: {', '.join(hook_languages())}.",
        )
    loaded = yaml.safe_load(asset_bytes("claude", "messages", f"{language}.yaml")) or {}
    if not isinstance(loaded, dict):
        raise VoxelloError(INVALID_PARAMETER, f"Invalid hook phrases file for '{language}'.")
    return {str(k): str(v) for k, v in loaded.items()}


def parse_hook_payload(raw: str) -> dict[str, Any]:
    """The JSON object Claude Code pipes to a Notification hook; ``{}`` when unusable."""
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def hook_text(payload: Mapping[str, Any], language: str) -> str:
    """The sentence to speak for a Claude Code Notification event (roadmap 3.3).

    A known ``notification_type`` maps to its sentence in the message file; an unknown type
    falls back to the event's own ``message``, then to the ``default`` sentence. The result
    is truncated to ``HOOK_TEXT_MAX_CHARS`` because it is spoken.
    """
    phrases = hook_phrases(language)
    ntype = payload.get("notification_type")
    if isinstance(ntype, str):
        ntype = HOOK_TYPE_ALIASES.get(ntype, ntype)
    message = payload.get("message")
    text = (
        (phrases.get(ntype) if isinstance(ntype, str) else None)
        or (message.strip() if isinstance(message, str) else "")
        or phrases.get("default", "")
    )
    if not text:
        raise VoxelloError(
            INVALID_PARAMETER, f"No sentence for this notification in '{language}.yaml'."
        )
    return text[:HOOK_TEXT_MAX_CHARS]


@dataclass(frozen=True)
class HookDelivery:
    """What a Notification hook speaks: the text, its language and whether it is cached."""

    text: str
    language: str
    cache: bool


def hook_delivery(payload: Mapping[str, Any], mode: HookMode, language: str) -> HookDelivery | None:
    """Apply ``integrations.phrases.mode`` to a Notification event; ``None`` when ``off``.

    ``cached`` is the sentence of :func:`hook_text`, synthesized once. ``live`` speaks the
    event's own ``message`` (in English, as Claude Code writes it) and synthesizes it every
    time, falling back to the sentence when the event carries no message.
    """
    if mode == "off":
        return None
    if mode == "live":
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return HookDelivery(
                message.strip()[:HOOK_TEXT_MAX_CHARS], HOOK_LIVE_LANGUAGE, cache=False
            )
        return HookDelivery(hook_text(payload, language), language, cache=False)
    return HookDelivery(hook_text(payload, language), language, cache=True)


def default_claude_dir() -> Path:
    """Claude Code's user directory: ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``."""
    env_value = os.environ.get(CLAUDE_DIR_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / ".claude"


def default_codex_dir() -> Path:
    """Codex's home: ``$CODEX_HOME`` or ``~/.codex`` (skills, hooks.json, config.toml)."""
    env_value = os.environ.get(CODEX_DIR_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / ".codex"


def _write_if_changed(path: Path, data: bytes, *, mode: int | None = None) -> InstalledFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        status: InstallStatus = "unchanged" if path.read_bytes() == data else "updated"
    else:
        status = "installed"
    if status != "unchanged":
        path.write_bytes(data)
    if mode is not None:
        path.chmod(mode)
    return InstalledFile(path, status)


def install_skill(claude_dir: Path) -> InstalledFile:
    """Write ``skills/voice-notify/SKILL.md`` under ``claude_dir``."""
    data = asset_bytes("claude", "skills", SKILL_NAME, "SKILL.md")
    return _write_if_changed(claude_dir / "skills" / SKILL_NAME / "SKILL.md", data)


def install_hook(claude_dir: Path) -> InstalledFile:
    """Write the executable Notification hook under ``claude_dir/hooks``."""
    data = asset_bytes("claude", "hooks", HOOK_FILENAME)
    return _write_if_changed(claude_dir / "hooks" / HOOK_FILENAME, data, mode=0o755)


def hook_command(hook_path: Path, *, lang: str | None = None) -> str:
    command = str(hook_path)
    if lang:
        command = f"VOXELLO_HOOK_LANG={lang} {command}"
    return command


def hook_settings_entry(hook_path: Path, *, lang: str | None = None) -> dict[str, Any]:
    """The object to append to ``hooks.Notification`` in Claude Code's settings.json."""
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": hook_command(hook_path, lang=lang),
                "async": True,
                "timeout": HOOK_TIMEOUT_SECONDS,
            }
        ],
    }


def stop_settings_entry(hook_path: Path, agent: str) -> dict[str, Any]:
    """The object to append to ``hooks.Stop`` (Claude Code settings.json or Codex hooks.json).

    Synchronous on purpose: the hook's stdout is the decision the agent reads. Stop events
    take no matcher.
    """
    command = f"{hook_path} stop" if agent == "claude-code" else f"{hook_path} stop {agent}"
    return {
        "hooks": [{"type": "command", "command": command, "timeout": STOP_HOOK_TIMEOUT_SECONDS}]
    }


def render_settings_snippet(entry: dict[str, Any], event: str = HOOK_EVENT) -> str:
    return json.dumps({"hooks": {event: [entry]}}, indent=2)


def _load_settings_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as exc:
        raise VoxelloError(
            INVALID_SETTINGS_FILE, f"Could not parse {path}: {exc}. Fix it or add the hook by hand."
        ) from exc
    if not isinstance(loaded, dict):
        raise VoxelloError(
            INVALID_SETTINGS_FILE, f"{path} must contain a JSON object at the top level."
        )
    return loaded


def _entry_runs_hook(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []) or []:
        if isinstance(hook, dict) and HOOK_FILENAME in str(hook.get("command", "")):
            return True
    return False


def hook_registered(settings_path: Path, event: str = HOOK_EVENT) -> bool:
    """True when the settings file already runs ``voxello-notification-hook.sh`` for ``event``."""
    data = _load_settings_file(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return any(_entry_runs_hook(e) for e in hooks.get(event, []) or [])


def merge_hook_into_settings(
    settings_path: Path, entry: dict[str, Any], event: str = HOOK_EVENT
) -> bool:
    """Append ``entry`` to ``hooks.<event>``; return False if already registered.

    Every other key in the file is preserved. The file is replaced atomically.
    """
    data = _load_settings_file(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    entries = hooks.get(event)
    if not isinstance(entries, list):
        entries = []
        hooks[event] = entries
    if any(_entry_runs_hook(e) for e in entries):
        return False
    entries.append(entry)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_name(settings_path.name + ".voxello-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)
    return True
