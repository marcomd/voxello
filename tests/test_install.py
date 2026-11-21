"""Tests for ``voxello install claude`` and the package assets it copies."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from voxello import install
from voxello.cli import main
from voxello.errors import INVALID_SETTINGS_FILE, VoxelloError

REPO = Path(__file__).resolve().parents[1]
PACKAGE_ASSETS = REPO / "src" / "voxello" / "assets" / "claude"


@pytest.fixture
def claude_dir(tmp_path: Path) -> Path:
    return tmp_path / "claude"


def test_repo_copies_match_package_assets():
    """`.claude/` keeps mirror copies for the checkout; they must not drift."""
    pairs = [
        (
            REPO / ".claude" / "skills" / "voice-notify" / "SKILL.md",
            PACKAGE_ASSETS / "skills" / "voice-notify" / "SKILL.md",
        ),
        (
            REPO / ".claude" / "hooks" / "voxello-notification-hook.sh",
            PACKAGE_ASSETS / "hooks" / "voxello-notification-hook.sh",
        ),
    ]
    for mirror, canonical in pairs:
        assert mirror.read_bytes() == canonical.read_bytes(), f"{mirror} differs from {canonical}"


def test_install_skill_and_hook(claude_dir: Path):
    skill = install.install_skill(claude_dir)
    hook = install.install_hook(claude_dir)
    assert skill.status == "installed"
    assert hook.status == "installed"
    assert skill.path == claude_dir / "skills" / "voice-notify" / "SKILL.md"
    assert hook.path == claude_dir / "hooks" / "voxello-notification-hook.sh"
    assert skill.path.read_bytes() == install.asset_bytes(
        "claude", "skills", "voice-notify", "SKILL.md"
    )
    assert hook.path.read_bytes() == install.asset_bytes(
        "claude", "hooks", "voxello-notification-hook.sh"
    )
    if sys.platform != "win32":  # no POSIX exec bit on Windows
        assert hook.path.stat().st_mode & stat.S_IXUSR

    assert install.install_skill(claude_dir).status == "unchanged"
    hook.path.write_text("#!/bin/sh\necho old\n")
    assert install.install_hook(claude_dir).status == "updated"


def test_cli_does_not_touch_settings_without_yes(
    claude_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("sys.stdin", _NotATty())
    assert main(["install", "claude", "--claude-dir", str(claude_dir)]) == 0
    out = capsys.readouterr().out
    assert not (claude_dir / "settings.json").exists()
    assert (claude_dir / "skills" / "voice-notify" / "SKILL.md").is_file()
    assert (claude_dir / "hooks" / "voxello-notification-hook.sh").is_file()
    assert '"Notification"' in out
    assert "--yes" in out
    assert "claude mcp add" in out


def test_cli_yes_merges_and_is_idempotent(claude_dir: Path, capsys: pytest.CaptureFixture[str]):
    settings = claude_dir / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "hooks": {}}))

    assert (
        main(["install", "claude", "--claude-dir", str(claude_dir), "--yes", "--lang", "en"]) == 0
    )
    data = json.loads(settings.read_text())
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    entries = data["hooks"]["Notification"]
    assert len(entries) == 1
    assert entries[0]["matcher"] == install.HOOK_MATCHER
    command = entries[0]["hooks"][0]["command"]
    assert command.startswith("VOXELLO_HOOK_LANG=en ")
    assert command.endswith(str(claude_dir / "hooks" / "voxello-notification-hook.sh"))
    assert entries[0]["hooks"][0]["async"] is True
    before = settings.read_text()

    capsys.readouterr()
    assert main(["install", "claude", "--claude-dir", str(claude_dir), "--yes"]) == 0
    assert "already registered" in capsys.readouterr().out
    assert settings.read_text() == before


def test_merge_preserves_other_notification_hooks(claude_dir: Path):
    settings = claude_dir / "settings.json"
    settings.parent.mkdir()
    other = {"matcher": "idle_prompt", "hooks": [{"type": "command", "command": "say hi"}]}
    settings.write_text(json.dumps({"hooks": {"Notification": [other]}}))
    entry = install.hook_settings_entry(claude_dir / "hooks" / "voxello-notification-hook.sh")
    assert install.merge_hook_into_settings(settings, entry) is True
    data = json.loads(settings.read_text())
    assert data["hooks"]["Notification"] == [other, entry]
    assert install.merge_hook_into_settings(settings, entry) is False


def test_invalid_settings_file(claude_dir: Path, capsys: pytest.CaptureFixture[str]):
    settings = claude_dir / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{not json")
    with pytest.raises(VoxelloError) as exc:
        install.hook_registered(settings)
    assert exc.value.code == INVALID_SETTINGS_FILE

    assert main(["install", "claude", "--claude-dir", str(claude_dir), "--yes"]) == 1
    assert INVALID_SETTINGS_FILE in capsys.readouterr().err


def test_no_hook_and_no_skill_flags(claude_dir: Path):
    assert main(["install", "claude", "--claude-dir", str(claude_dir), "--no-hook"]) == 0
    assert (claude_dir / "skills" / "voice-notify" / "SKILL.md").is_file()
    assert not (claude_dir / "hooks").exists()


def test_claude_config_dir_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "from-env"))
    assert install.default_claude_dir() == tmp_path / "from-env"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert install.default_claude_dir() == Path.home() / ".claude"


class _NotATty:
    def isatty(self) -> bool:
        return False


HOOK_NAME = "voxello-notification-hook.sh"


def test_stop_hook_flag_registers_a_synchronous_stop_entry(
    claude_dir: Path, capsys: pytest.CaptureFixture[str]
):
    settings = claude_dir / "settings.json"
    assert main(["install", "claude", "--claude-dir", str(claude_dir), "--yes", "--stop-hook"]) == 0
    data = json.loads(settings.read_text())
    assert len(data["hooks"]["Notification"]) == 1
    (stop,) = data["hooks"]["Stop"]
    assert "matcher" not in stop, "Stop events have no matcher"
    (hook,) = stop["hooks"]
    assert hook["command"] == f"{claude_dir / 'hooks' / HOOK_NAME} stop"
    assert "async" not in hook, "its stdout is the decision Claude Code reads"
    assert hook["timeout"] == install.STOP_HOOK_TIMEOUT_SECONDS
    assert "integrations" in capsys.readouterr().out, "points to the summaries settings"

    before = settings.read_text()
    assert main(["install", "claude", "--claude-dir", str(claude_dir), "--yes", "--stop-hook"]) == 0
    assert settings.read_text() == before, "idempotent per event"


def test_without_stop_hook_flag_no_stop_entry(claude_dir: Path):
    assert main(["install", "claude", "--claude-dir", str(claude_dir), "--yes"]) == 0
    assert "Stop" not in json.loads((claude_dir / "settings.json").read_text())["hooks"]


def test_merge_is_per_event(claude_dir: Path):
    settings = claude_dir / "settings.json"
    hook_path = claude_dir / "hooks" / HOOK_NAME
    assert install.merge_hook_into_settings(settings, install.hook_settings_entry(hook_path))
    assert not install.hook_registered(settings, "Stop")
    stop = install.stop_settings_entry(hook_path, "claude-code")
    assert install.merge_hook_into_settings(settings, stop, "Stop") is True
    assert install.hook_registered(settings, "Stop")
    assert install.merge_hook_into_settings(settings, stop, "Stop") is False


@pytest.fixture
def codex_dir(tmp_path: Path) -> Path:
    return tmp_path / "codex"


def test_install_codex_copies_skill_and_hook_and_offers_hooks_json(
    codex_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("sys.stdin", _NotATty())
    assert main(["install", "codex", "--codex-dir", str(codex_dir)]) == 0
    out = capsys.readouterr().out
    assert (codex_dir / "skills" / "voice-notify" / "SKILL.md").read_bytes() == (
        install.asset_bytes("claude", "skills", "voice-notify", "SKILL.md")
    )
    assert (codex_dir / "hooks" / HOOK_NAME).is_file()
    assert not (codex_dir / "hooks.json").exists(), "never written without consent"
    assert '"Stop"' in out and "--yes" in out
    assert "[mcp_servers.voxello]" in out, "config.toml is only shown, never edited"
    assert not (codex_dir / "config.toml").exists()


def test_install_codex_yes_merges_hooks_json(codex_dir: Path):
    hooks_json = codex_dir / "hooks.json"
    hooks_json.parent.mkdir()
    other = {"hooks": [{"type": "command", "command": "notify-me"}]}
    hooks_json.write_text(json.dumps({"hooks": {"Stop": [other]}}))
    assert main(["install", "codex", "--codex-dir", str(codex_dir), "--yes"]) == 0
    stops = json.loads(hooks_json.read_text())["hooks"]["Stop"]
    assert stops[0] == other
    assert stops[1]["hooks"][0]["command"] == f"{codex_dir / 'hooks' / HOOK_NAME} stop codex"
    assert "async" not in stops[1]["hooks"][0]
    assert main(["install", "codex", "--codex-dir", str(codex_dir), "--yes"]) == 0
    assert len(json.loads(hooks_json.read_text())["hooks"]["Stop"]) == 2


def test_install_codex_flags_and_home(
    codex_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assert main(["install", "codex", "--codex-dir", str(codex_dir), "--no-hook"]) == 0
    assert (codex_dir / "skills").is_dir() and not (codex_dir / "hooks").exists()
    assert main(["install", "codex", "--codex-dir", str(tmp_path / "c2"), "--no-skill", "-y"]) == 0
    assert not (tmp_path / "c2" / "skills").exists()
    assert (tmp_path / "c2" / "hooks.json").is_file()

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "from-env"))
    assert install.default_codex_dir() == tmp_path / "from-env"
    monkeypatch.delenv("CODEX_HOME")
    assert install.default_codex_dir() == Path.home() / ".codex"
