"""Install Voxello integrations into other tools (today: Claude Code).

``voxello install claude`` copies the ``voice-notify`` skill and the Notification hook
shipped inside the package (``voxello/assets/claude``) into the user's Claude Code
directory and offers to register the hook in ``settings.json``. The functions here are
pure filesystem operations so they can be tested without a TTY; the interactive part
lives in the CLI.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from voxello.errors import INVALID_SETTINGS_FILE, VoxelloError

CLAUDE_DIR_ENV_VAR = "CLAUDE_CONFIG_DIR"
SKILL_NAME = "voice-notify"
HOOK_FILENAME = "voxello-notification-hook.sh"
HOOK_MATCHER = "permission_prompt|idle_prompt|agent_needs_input|elicitation_dialog"
HOOK_TIMEOUT_SECONDS = 30
HOOK_EVENT = "Notification"

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


def default_claude_dir() -> Path:
    """Claude Code's user directory: ``$CLAUDE_CONFIG_DIR`` or ``~/.claude``."""
    env_value = os.environ.get(CLAUDE_DIR_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return Path.home() / ".claude"


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


def render_settings_snippet(entry: dict[str, Any]) -> str:
    return json.dumps({"hooks": {HOOK_EVENT: [entry]}}, indent=2)


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


def hook_registered(settings_path: Path) -> bool:
    """True when ``settings.json`` already runs a ``voxello-notification-hook.sh``."""
    data = _load_settings_file(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return any(_entry_runs_hook(e) for e in hooks.get(HOOK_EVENT, []) or [])


def merge_hook_into_settings(settings_path: Path, entry: dict[str, Any]) -> bool:
    """Append ``entry`` to ``hooks.Notification``; return False if already registered.

    Every other key in the file is preserved. The file is replaced atomically.
    """
    data = _load_settings_file(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    notification = hooks.get(HOOK_EVENT)
    if not isinstance(notification, list):
        notification = []
        hooks[HOOK_EVENT] = notification
    if any(_entry_runs_hook(e) for e in notification):
        return False
    notification.append(entry)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_name(settings_path.name + ".voxello-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)
    return True
