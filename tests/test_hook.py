"""Tests for the Claude Code Notification hook script (bash).

A fake ``voxello`` (or ``uv``) on a temporary PATH records its arguments, so the tests
check exactly what the hook runs without speaking or touching a TTS server.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "voxello"
    / "assets"
    / "claude"
    / "hooks"
    / "voxello-notification-hook.sh"
)

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def _fake_command(bin_dir: Path, name: str, log: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    # Each argument on its own line, so tests can split unambiguously.
    script.write_text(
        f'#!/usr/bin/env bash\nfor a in "$@"; do printf \'%s\\n\' "$a"; done >> "{log}"\n'
    )
    script.chmod(0o755)


def _run(hook: Path, payload: dict[str, str], *, path: str, home: Path, **env: str):
    environment = {
        "PATH": path,
        "HOME": str(home),
        **env,
    }
    return subprocess.run(
        ["bash", str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )


def _system_path() -> str:
    """A PATH with bash, python3 and (if installed) jq, but without any real ``voxello``.

    ``uv run`` prepends the project venv (which contains ``voxello``) to PATH, so the
    caller's PATH cannot be reused for the fallback tests.
    """
    dirs = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    jq = shutil.which("jq")
    if jq:
        dirs.append(str(Path(jq).parent))
    return os.pathsep.join(dirs)


def test_uses_voxello_on_path_english(tmp_path: Path):
    log = tmp_path / "args.log"
    _fake_command(tmp_path / "bin", "voxello", log)
    result = _run(
        HOOK,
        {"notification_type": "permission_prompt"},
        path=f"{tmp_path / 'bin'}{os.pathsep}{_system_path()}",
        home=tmp_path,
        VOXELLO_HOOK_LANG="en",
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "notify",
        "Claude Code is asking for permission to continue.",
        "--channels",
        "voice,desktop",
        "--priority",
        "high",
    ]


def test_default_language_is_italian_and_channels_override(tmp_path: Path):
    log = tmp_path / "args.log"
    _fake_command(tmp_path / "bin", "voxello", log)
    result = _run(
        HOOK,
        {"notification_type": "idle_prompt"},
        path=f"{tmp_path / 'bin'}{os.pathsep}{_system_path()}",
        home=tmp_path,
        VOXELLO_HOOK_CHANNELS="desktop",
    )
    assert result.returncode == 0, result.stderr
    args = log.read_text().splitlines()
    assert args[1] == "Claude Code ha finito e aspetta una tua risposta."
    assert args[2:4] == ["--channels", "desktop"]


def test_unknown_type_falls_back_to_message(tmp_path: Path):
    log = tmp_path / "args.log"
    _fake_command(tmp_path / "bin", "voxello", log)
    result = _run(
        HOOK,
        {"notification_type": "something_new", "message": "Custom text"},
        path=f"{tmp_path / 'bin'}{os.pathsep}{_system_path()}",
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[1] == "Custom text"


def test_falls_back_to_local_bin(tmp_path: Path):
    log = tmp_path / "args.log"
    _fake_command(tmp_path / ".local" / "bin", "voxello", log)
    result = _run(
        HOOK,
        {"notification_type": "agent_completed"},
        path=_system_path(),
        home=tmp_path,
        VOXELLO_HOOK_LANG="en",
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[1] == "A Claude agent has completed its work."


def test_falls_back_to_voxello_repo_with_uv(tmp_path: Path):
    log = tmp_path / "args.log"
    _fake_command(tmp_path / "bin", "uv", log)
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "voxello"\n')
    # Copy the hook somewhere that is not inside a checkout.
    hook = tmp_path / "hooks" / HOOK.name
    hook.parent.mkdir()
    shutil.copy(HOOK, hook)
    result = _run(
        hook,
        {"notification_type": "permission_prompt"},
        path=f"{tmp_path / 'bin'}{os.pathsep}{_system_path()}",
        home=tmp_path,
        VOXELLO_REPO=str(repo),
        VOXELLO_HOOK_LANG="en",
    )
    assert result.returncode == 0, result.stderr
    args = log.read_text().splitlines()
    assert args[:5] == ["run", "--directory", str(repo), "voxello", "notify"]
    assert args[5] == "Claude Code is asking for permission to continue."


def test_runs_from_checkout_copy_without_path(tmp_path: Path):
    """The mirror copy in .claude/hooks finds the checkout it lives in."""
    log = tmp_path / "args.log"
    _fake_command(tmp_path / "bin", "uv", log)
    repo = HOOK.parents[5]  # src/voxello/assets/claude/hooks -> repo root
    mirror = repo / ".claude" / "hooks" / HOOK.name
    result = _run(
        mirror,
        {"notification_type": "idle_prompt"},
        path=f"{tmp_path / 'bin'}{os.pathsep}{_system_path()}",
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    args = log.read_text().splitlines()
    assert args[:3] == ["run", "--directory", str(repo)]


def test_logs_and_exits_when_nothing_found(tmp_path: Path):
    hook = tmp_path / "hooks" / HOOK.name
    hook.parent.mkdir()
    shutil.copy(HOOK, hook)
    result = _run(
        hook,
        {"notification_type": "permission_prompt"},
        path=_system_path(),
        home=tmp_path,
    )
    assert result.returncode == 1
    assert "uv tool install voxello" in result.stderr
    assert result.stdout == ""


def test_hook_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(HOOK)], check=False).returncode == 0
    assert shlex.split(HOOK.read_text().splitlines()[0]) == ["#!/usr/bin/env", "bash"]
