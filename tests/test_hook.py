"""Tests for the Claude Code Notification hook.

The bash script only resolves the ``voxello`` command and execs ``voxello hook
notification`` with the Notification JSON on stdin; a fake ``voxello`` (or ``uv``) on a
temporary PATH records its arguments and stdin, so the tests check exactly what the hook
runs without speaking or touching a TTS server. The sentence selection is Python
(``install.hook_text``) and is tested directly.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from voxello import install
from voxello.errors import INVALID_PARAMETER, VoxelloError

HOOK = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "voxello"
    / "assets"
    / "claude"
    / "hooks"
    / "voxello-notification-hook.sh"
)

STDIN_MARKER = "--stdin--"

# -- sentence selection (Python) ---------------------------------------------------------


@pytest.mark.parametrize("language", ["it", "en"])
def test_hook_text_maps_every_notification_type(language: str):
    phrases = install.hook_phrases(language)
    assert set(phrases) >= {
        "permission_prompt",
        "idle_prompt",
        "agent_needs_input",
        "agent_completed",
        "elicitation_dialog",
        "default",
    }
    for ntype, expected in phrases.items():
        if ntype == "default":
            continue
        assert install.hook_text({"notification_type": ntype}, language) == expected
    # Aliased type shares the sentence of its canonical key.
    assert (
        install.hook_text({"notification_type": "elicitation_url_dialog"}, language)
        == phrases["elicitation_dialog"]
    )
    # Unknown type: the event's own message, else the default sentence.
    assert install.hook_text({"notification_type": "brand_new", "message": "Custom"}, language) == (
        "Custom"
    )
    assert install.hook_text({"notification_type": "brand_new"}, language) == phrases["default"]
    assert install.hook_text({}, language) == phrases["default"]
    assert (
        install.hook_text({"notification_type": 42, "message": ["x"]}, language)
        == (phrases["default"])
    )


def test_hook_text_is_truncated_and_stripped():
    long = "x" * 400
    assert install.hook_text({"notification_type": "new", "message": f"  {long}  "}, "en") == (
        "x" * install.HOOK_TEXT_MAX_CHARS
    )
    assert (
        install.hook_text({"notification_type": "new", "message": "   "}, "en")
        == (install.hook_phrases("en")["default"])
    )


@pytest.mark.parametrize("language", ["it", "en"])
def test_hook_delivery_cached_uses_the_sentence_and_the_cache(language: str):
    payload = {"notification_type": "permission_prompt", "message": "Claude needs Bash"}
    delivery = install.hook_delivery(payload, "cached", language)
    assert delivery == install.HookDelivery(
        install.hook_phrases(language)["permission_prompt"], language, cache=True
    )


def test_hook_delivery_live_speaks_the_event_message_uncached():
    payload = {"notification_type": "permission_prompt", "message": "  Claude needs Bash  "}
    delivery = install.hook_delivery(payload, "live", "it")
    # Claude Code writes its notification messages in English.
    assert delivery == install.HookDelivery("Claude needs Bash", "en", cache=False)
    assert install.hook_delivery(
        {"notification_type": "idle_prompt", "message": "x" * 400}, "live", "it"
    ) == install.HookDelivery("x" * install.HOOK_TEXT_MAX_CHARS, "en", cache=False)


def test_hook_delivery_live_without_a_message_falls_back_to_the_sentence_uncached():
    delivery = install.hook_delivery({"notification_type": "idle_prompt"}, "live", "it")
    assert delivery == install.HookDelivery(
        install.hook_phrases("it")["idle_prompt"], "it", cache=False
    )


def test_hook_delivery_off_is_none():
    assert install.hook_delivery({"notification_type": "idle_prompt"}, "off", "it") is None


def test_hook_text_rejects_unknown_language():
    with pytest.raises(VoxelloError) as exc:
        install.hook_text({"notification_type": "idle_prompt"}, "xx")
    assert exc.value.code == INVALID_PARAMETER


def test_parse_hook_payload_tolerates_garbage():
    assert install.parse_hook_payload("") == {}
    assert install.parse_hook_payload("   \n") == {}
    assert install.parse_hook_payload("{not json") == {}
    assert install.parse_hook_payload("[1, 2]") == {}
    assert install.parse_hook_payload('{"notification_type": "idle_prompt"}') == {
        "notification_type": "idle_prompt"
    }


def test_message_files_have_the_same_keys():
    keys = {lang: set(install.hook_phrases(lang)) for lang in install.hook_languages()}
    assert len(set(map(frozenset, keys.values()))) == 1, keys


# -- the bash script ----------------------------------------------------------------------

# On Windows the runner's Git Bash cannot exec the fake `voxello` scripts these tests create,
# so the script is only checked on POSIX; docs/windows-testing.md covers the hook by hand.
bash_only = pytest.mark.skipif(
    shutil.which("bash") is None or sys.platform == "win32",
    reason="bash not available or Windows (hook verified manually)",
)


def _fake_command(bin_dir: Path, name: str, log: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    # Each argument on its own line, then a marker and whatever arrived on stdin.
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'{{ for a in "$@"; do printf \'%s\\n\' "$a"; done; printf \'%s\\n\' "{STDIN_MARKER}"; '
        f'cat; }} >> "{log}"\n'
    )
    script.chmod(0o755)


def _recorded(log: Path) -> tuple[list[str], str]:
    """(arguments, stdin) as seen by the fake command."""
    lines = log.read_text().split("\n")
    marker = lines.index(STDIN_MARKER)
    return lines[:marker], "\n".join(lines[marker + 1 :]).strip()


def _run(
    hook: Path,
    payload: dict[str, str],
    *,
    path: str,
    home: Path,
    args: tuple[str, ...] = (),
    **env: str,
):
    environment = {
        "PATH": path,
        "HOME": str(home),
        **env,
    }
    return subprocess.run(
        ["bash", str(hook), *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )


def _system_path() -> str:
    """A PATH with bash but without any real ``voxello``.

    ``uv run`` prepends the project venv (which contains ``voxello``) to PATH, so the
    caller's PATH cannot be reused for the fallback tests.
    """
    return os.pathsep.join(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])


@bash_only
def test_uses_voxello_on_path_and_passes_language_channels_and_stdin(tmp_path: Path):
    log = tmp_path / "args.log"
    _fake_command(tmp_path / "bin", "voxello", log)
    payload = {"notification_type": "permission_prompt", "message": "May I?"}
    result = _run(
        HOOK,
        payload,
        path=f"{tmp_path / 'bin'}{os.pathsep}{_system_path()}",
        home=tmp_path,
        VOXELLO_HOOK_LANG="en",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", "voxello's stdout is discarded by the hook"
    args, stdin = _recorded(log)
    assert args == ["hook", "notification", "--language", "en"]
    assert json.loads(stdin) == payload, "the Notification JSON must reach voxello unchanged"


@bash_only
def test_without_env_the_cli_reads_language_and_channels_from_the_config(tmp_path: Path):
    """The script passes no defaults of its own, so integrations.phrases in config.yaml apply."""
    log = tmp_path / "args.log"
    _fake_command(tmp_path / "bin", "voxello", log)
    result = _run(
        HOOK,
        {"notification_type": "idle_prompt"},
        path=f"{tmp_path / 'bin'}{os.pathsep}{_system_path()}",
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert _recorded(log)[0] == ["hook", "notification"]


@bash_only
@pytest.mark.parametrize(
    ("script_args", "agent"), [(("stop",), "claude-code"), (("stop", "codex"), "codex")]
)
def test_stop_mode_runs_hook_stop_and_keeps_its_stdout(
    tmp_path: Path, script_args: tuple[str, ...], agent: str
):
    """The Stop decision is voxello's stdout: the script must pass it through to the agent."""
    log = tmp_path / "args.log"
    _fake_command(tmp_path / "bin", "voxello", log)
    (tmp_path / "bin" / "voxello").write_text(
        (tmp_path / "bin" / "voxello").read_text() + "printf '{}\\n'\n"
    )
    payload = {"hook_event_name": "Stop", "stop_hook_active": False}
    result = _run(
        HOOK,
        payload,
        path=f"{tmp_path / 'bin'}{os.pathsep}{_system_path()}",
        home=tmp_path,
        args=script_args,
        VOXELLO_HOOK_LANG="en",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}\n"
    args, stdin = _recorded(log)
    assert args == ["hook", "stop", "--agent", agent]
    assert json.loads(stdin) == payload


@bash_only
def test_env_channels_are_forwarded(tmp_path: Path):
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
    args, _ = _recorded(log)
    assert args == ["hook", "notification", "--channels", "desktop"]


@bash_only
def test_script_has_no_sentences_of_its_own():
    """Roadmap 3.3: adding a language must not require touching the script."""
    text = HOOK.read_text()
    for language in install.hook_languages():
        for sentence in install.hook_phrases(language).values():
            assert sentence not in text
    assert "jq" not in text and "python3" not in text


@bash_only
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
    args, _ = _recorded(log)
    assert args[:4] == ["hook", "notification", "--language", "en"]


@bash_only
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
    args, _ = _recorded(log)
    assert args[:6] == ["run", "--directory", str(repo), "voxello", "hook", "notification"]


@bash_only
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
    args, _ = _recorded(log)
    assert args[:3] == ["run", "--directory", str(repo)]


@bash_only
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


@bash_only
def test_hook_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(HOOK)], check=False).returncode == 0
    assert shlex.split(HOOK.read_text().splitlines()[0]) == ["#!/usr/bin/env", "bash"]
