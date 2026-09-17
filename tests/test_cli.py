from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxello import __version__
from voxello.cli import describe_installation, main
from voxello.config import describe_config_path


def test_version_flag(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"voxello {__version__}"


def test_config_path_prints_bare_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    cfg = tmp_path / "c.yaml"
    assert main(["--config", str(cfg), "config", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(cfg)


def test_describe_config_path_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VOXELLO_CONFIG", raising=False)
    path, source = describe_config_path(tmp_path / "x.yaml")
    assert (path, source) == (tmp_path / "x.yaml", "--config")

    monkeypatch.setenv("VOXELLO_CONFIG", str(tmp_path / "env.yaml"))
    path, source = describe_config_path()
    assert (path, source) == (tmp_path / "env.yaml", "VOXELLO_CONFIG")

    monkeypatch.delenv("VOXELLO_CONFIG")
    path, source = describe_config_path()
    assert path.name == "config.yaml"
    assert source == "default (platformdirs)"


def test_describe_installation_lines():
    lines = describe_installation()
    assert lines[0].startswith("Executable: ")
    assert lines[1].startswith("Python: ")
    assert lines[2].startswith("Install: ")
    # In the test venv Voxello is an editable install, so the hint is shown.
    assert any(line.startswith("Hint: ") for line in lines)


def test_doctor_reports_sources_and_unreachable_tts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("VOXELLO_VOICESTUDIO_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VOXELLO_CONFIG", str(tmp_path / "missing.yaml"))
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Executable: " in out
    assert "Python: " in out
    assert "Install: " in out
    assert "not found, using defaults; source: VOXELLO_CONFIG" in out
    assert "health: ERROR" in out
    assert "Cache: " in out and "0 entries" in out
    assert "PROBLEMS FOUND" in out


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VOXELLO_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("VOXELLO_STORAGE__TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("VOXELLO_CACHE__DIRECTORY", str(tmp_path / "cache"))
    monkeypatch.setenv("VOXELLO_OUTPUT__DIRECTORY", str(tmp_path / "out"))
    monkeypatch.delenv("VOXELLO_HOOK_LANG", raising=False)
    return tmp_path


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch):
    """Make `VoxelloService.from_settings` build a service on the fake provider (no audio)."""
    from voxello.core.service import VoxelloService
    from voxello.storage.files import OutputStore, TempStore

    from .conftest import FakeProvider, make_cache

    provider = FakeProvider()

    def build(settings, **_):
        return VoxelloService(
            settings,
            provider=provider,
            player=None,
            notifier=None,
            temp_store=TempStore(settings.storage.resolved_temp_dir(), 1, True),
            output_store=OutputStore(settings.output.resolved_directory()),
            audio_cache=make_cache(settings),
        )

    monkeypatch.setattr(VoxelloService, "from_settings", staticmethod(build))
    return provider


def test_cache_list_and_clear_on_empty_cache(
    isolated_env: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["cache", "list"]) == 0
    out = capsys.readouterr().out
    assert out.strip().startswith("0 entries")
    assert main(["cache", "clear"]) == 0
    assert capsys.readouterr().out.startswith("Removed 0 cached phrases")


def test_cache_warm_hook_phrases_then_list_and_clear(
    isolated_env: Path, fake_service, capsys: pytest.CaptureFixture[str]
):
    from voxello.install import hook_phrases

    distinct = len(set(hook_phrases("en").values()))
    assert main(["cache", "warm", "--hook-phrases", "--language", "en"]) == 0
    assert (
        capsys.readouterr().out.strip() == f"Warmed {distinct} phrases (0 already cached, 0 failed)"
    )
    assert len(fake_service.calls) == distinct

    assert main(["cache", "warm", "--hook-phrases", "--language", "en"]) == 0
    assert (
        capsys.readouterr().out.strip() == f"Warmed 0 phrases ({distinct} already cached, 0 failed)"
    )
    assert len(fake_service.calls) == distinct, "a warm run over cached phrases must not synthesize"

    assert main(["cache", "list"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == distinct + 1
    assert lines[-1].startswith(f"{distinct} entries")
    for line in lines[:-1]:
        assert "voice=default" in line and "chars=" in line
        assert "Claude" not in line, "the listing must never show the text"

    assert main(["cache", "clear"]) == 0
    assert capsys.readouterr().out.startswith(f"Removed {distinct} cached phrases")
    assert not any((isolated_env / "cache").glob("*.wav"))


def test_cache_warm_from_file_and_stdin(
    isolated_env: Path,
    fake_service,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    phrases = isolated_env / "phrases.txt"
    phrases.write_text("# comment\nBuild completata.\n\nBuild completata.\nTest falliti.\n")
    assert main(["cache", "warm", str(phrases)]) == 0
    assert capsys.readouterr().out.strip() == "Warmed 2 phrases (0 already cached, 0 failed)"

    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("Deploy finito.\n"))
    assert main(["cache", "warm", "-"]) == 0
    assert capsys.readouterr().out.strip() == "Warmed 1 phrase (0 already cached, 0 failed)"


def test_cache_warm_rejects_bad_arguments(isolated_env: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["cache", "warm"]) == 1
    assert "error: invalid_parameter" in capsys.readouterr().err
    assert main(["cache", "warm", "--hook-phrases", "--language", "xx"]) == 1
    assert "No hook phrases for language 'xx'" in capsys.readouterr().err


def test_cache_commands_report_disabled_cache(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("VOXELLO_CACHE__ENABLED", "false")
    assert main(["cache", "list"]) == 1
    assert "disabled" in capsys.readouterr().err


def test_cache_warm_uses_the_requested_language(isolated_env: Path, fake_service, capsys):
    """Roadmap 3.1: English hook phrases are synthesized (and keyed) as English."""
    assert main(["cache", "warm", "--hook-phrases", "--language", "en"]) == 0
    assert {call[2] for call in fake_service.calls} == {"en"}
    capsys.readouterr()

    phrases = isolated_env / "phrases.txt"
    phrases.write_text("Build completata.\n")
    assert main(["cache", "warm", str(phrases)]) == 0
    assert fake_service.calls[-1] == ("Build completata.", None, "it"), "default language"
    assert main(["cache", "warm", str(phrases), "-l", "en"]) == 0
    assert fake_service.calls[-1] == ("Build completata.", None, "en")


def test_doctor_shows_language_and_deprecation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("VOXELLO_VOICESTUDIO_URL", "http://127.0.0.1:1")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "tts:\n  voicestudio:\n    language: en\nspeech:\n  voices_by_language:\n    en: english\n"
    )
    main(["--config", str(cfg), "doctor"])
    out = capsys.readouterr().out
    assert "default_language=en voices_by_language: en=english" in out
    assert "DEPRECATED: tts.voicestudio.language" in out
    assert " language=" not in out.split("default_language")[0]


@pytest.fixture
def hook_stdin(monkeypatch: pytest.MonkeyPatch):
    import io

    def feed(payload) -> None:
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        monkeypatch.setattr("sys.stdin", io.StringIO(raw))

    return feed


def test_hook_notification_speaks_the_phrase_for_the_type(
    isolated_env: Path, fake_service, hook_stdin, capsys: pytest.CaptureFixture[str]
):
    from voxello.install import hook_phrases

    hook_stdin({"notification_type": "permission_prompt", "message": "raw"})
    code = main(["hook", "notification", "--language", "en", "--channels", "file"])
    assert code == 0, capsys.readouterr().err
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "delivered" and result["channels"] == {"file": "saved"}
    assert fake_service.calls == [(hook_phrases("en")["permission_prompt"], None, "en")]
    saved = Path(result["saved_path"])
    assert saved.exists()

    # Same event again: served from the cache, the provider is not called twice.
    hook_stdin({"notification_type": "permission_prompt"})
    assert main(["hook", "notification", "--language", "en", "--channels", "file"]) == 0
    assert len(fake_service.calls) == 1


def test_hook_notification_defaults_and_fallbacks(
    isolated_env: Path,
    fake_service,
    hook_stdin,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from voxello.install import hook_phrases

    # Language and channels from the hook environment variables.
    monkeypatch.setenv("VOXELLO_HOOK_LANG", "it")
    monkeypatch.setenv("VOXELLO_HOOK_CHANNELS", "file")
    hook_stdin({"notification_type": "idle_prompt"})
    assert main(["hook", "notification"]) == 0
    assert fake_service.calls[-1] == (hook_phrases("it")["idle_prompt"], None, "it")
    capsys.readouterr()

    # Unknown type: the event's own message; no type at all: the default sentence.
    hook_stdin({"notification_type": "something_new", "message": "Custom text"})
    assert main(["hook", "notification"]) == 0
    assert fake_service.calls[-1][0] == "Custom text"
    hook_stdin("")
    assert main(["hook", "notification"]) == 0
    assert fake_service.calls[-1][0] == hook_phrases("it")["default"]
    capsys.readouterr()

    # Unknown language: a clean error, nothing spoken.
    hook_stdin({"notification_type": "idle_prompt"})
    before = len(fake_service.calls)
    assert main(["hook", "notification", "-l", "xx"]) == 1
    assert "No hook phrases for language 'xx'" in capsys.readouterr().err
    assert len(fake_service.calls) == before


def test_speak_and_notify_accept_language(isolated_env: Path, fake_service, capsys):
    assert main(["speak", "Hello", "--no-play", "-l", "en"]) == 0
    assert json.loads(capsys.readouterr().out)["language"] == "en"
    assert fake_service.calls[-1] == ("Hello", None, "en")
    assert main(["notify", "Done", "--channels", "file", "--language", "en"]) == 0
    capsys.readouterr()
    assert fake_service.calls[-1] == ("Done", None, "en")
    assert main(["speak", "x", "--no-play", "-l", "nope"]) == 1
    assert "invalid_language" in capsys.readouterr().err
