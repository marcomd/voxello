from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import ClassVar

import pytest

from voxello import __version__
from voxello.cli import cmd_doctor, describe_installation, main
from voxello.config import IntegrationsSettings, Settings, describe_config_path
from voxello.errors import INVALID_LANGUAGE, TTS_PROVIDER_ERROR, VoxelloError
from voxello.tts.base import VoiceInfo

from .conftest import FakeProvider, FakeVoiceProfileStore, make_wav


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


def seed_cache_entry(directory: Path, voice: str, text: str = "Fatto.") -> None:
    """Store one cached phrase for ``voice`` the way a past ``speak`` would have."""
    from voxello.storage.cache import AudioCache, CacheKeyParts

    cache = AudioCache(directory, max_entries=20, max_age_days=30)
    parts = CacheKeyParts(
        text=text,
        voice=voice,
        engine="omnivoice",
        language="it",
        speed=None,
        num_step=None,
        guidance_scale=None,
        base_url="http://127.0.0.1:8880",
    )
    cache.store(AudioCache.key(parts), make_wav(100), voice=voice, provider="fake", text_chars=6)


def test_cache_warm_refuses_while_the_cache_is_disabled(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("VOXELLO_CACHE__ENABLED", "false")
    assert main(["cache", "warm", "--hook-phrases"]) == 1
    assert "disabled" in capsys.readouterr().err


def test_cache_list_and_clear_reach_files_left_while_disabled(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Disabling the cache keeps its files; list and clear must still see and remove them."""
    seed_cache_entry(isolated_env / "cache", "clone:marco")
    monkeypatch.setenv("VOXELLO_CACHE__ENABLED", "false")
    assert main(["cache", "list"]) == 0
    captured = capsys.readouterr()
    assert "voice=clone:marco" in captured.out and "1 entry" in captured.out
    assert "disabled" in captured.err
    assert main(["cache", "clear"]) == 0
    assert capsys.readouterr().out.startswith("Removed 1 cached phrase ")
    assert main(["cache", "list"]) == 0
    assert "0 entries" in capsys.readouterr().out


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


def write_config(path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("VOXELLO_CONFIG", str(path))


def test_hook_notification_reads_mode_voice_language_and_channels_from_config(
    isolated_env: Path, fake_service, hook_stdin, monkeypatch: pytest.MonkeyPatch, capsys
):
    from voxello.install import hook_phrases

    monkeypatch.delenv("VOXELLO_HOOK_CHANNELS", raising=False)
    write_config(
        isolated_env / "config.yaml",
        monkeypatch,
        "integrations:\n  phrases:\n    voice: clone:marco\n    language: en\n"
        "    channels: [file]\n",
    )
    hook_stdin({"notification_type": "idle_prompt"})
    assert main(["hook", "notification"]) == 0
    assert fake_service.calls[-1] == (hook_phrases("en")["idle_prompt"], "clone:marco", "en")
    assert json.loads(capsys.readouterr().out)["channels"] == {"file": "saved"}

    # The environment variables and the arguments still win over the configuration.
    monkeypatch.setenv("VOXELLO_HOOK_LANG", "it")
    hook_stdin({"notification_type": "idle_prompt"})
    assert main(["hook", "notification"]) == 0
    assert fake_service.calls[-1][2] == "it"
    hook_stdin({"notification_type": "permission_prompt"})  # not cached yet in English
    assert main(["hook", "notification", "-l", "en"]) == 0
    assert fake_service.calls[-1][2] == "en"


def test_hook_notification_default_language_follows_speech_default(
    isolated_env: Path, fake_service, hook_stdin, monkeypatch: pytest.MonkeyPatch
):
    from voxello.install import hook_phrases

    monkeypatch.setenv("VOXELLO_HOOK_CHANNELS", "file")
    monkeypatch.setenv("VOXELLO_LANGUAGE", "en")
    hook_stdin({"notification_type": "idle_prompt"})
    assert main(["hook", "notification"]) == 0
    assert fake_service.calls[-1] == (hook_phrases("en")["idle_prompt"], None, "en")

    # A default language without message files keeps the hook's own default (Italian).
    monkeypatch.setenv("VOXELLO_LANGUAGE", "fr")
    hook_stdin({"notification_type": "idle_prompt"})
    assert main(["hook", "notification"]) == 0
    assert fake_service.calls[-1] == (hook_phrases("it")["idle_prompt"], None, "it")


def test_hook_notification_live_mode_is_not_cached(
    isolated_env: Path, fake_service, hook_stdin, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("VOXELLO_HOOK_CHANNELS", "file")
    write_config(
        isolated_env / "config.yaml", monkeypatch, "integrations:\n  phrases:\n    mode: live\n"
    )
    for _ in range(2):
        hook_stdin({"notification_type": "permission_prompt", "message": "Claude needs Bash"})
        assert main(["hook", "notification"]) == 0
    assert fake_service.calls == [("Claude needs Bash", None, "en")] * 2
    assert main(["cache", "list"]) == 0


def test_hook_notification_off_mode_drops_the_voice(
    isolated_env: Path, fake_service, hook_stdin, monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.setenv("VOXELLO_HOOK_CHANNELS", "voice")
    hook_stdin({"notification_type": "idle_prompt"})
    assert main(["hook", "notification", "--mode", "off"]) == 0
    assert fake_service.calls == []
    assert capsys.readouterr().out == ""

    # Other channels are still delivered, without synthesizing for the voice.
    hook_stdin({"notification_type": "idle_prompt"})
    assert main(["hook", "notification", "--mode", "off", "--channels", "voice,file"]) == 0
    assert json.loads(capsys.readouterr().out)["channels"] == {"file": "saved"}


def test_cache_warm_hook_phrases_uses_the_phrase_voice(
    isolated_env: Path, fake_service, monkeypatch: pytest.MonkeyPatch, hook_stdin, capsys
):
    """Warm and hook must compute the same cache keys, so they must pick the same voice."""
    monkeypatch.setenv("VOXELLO_HOOK_CHANNELS", "file")
    write_config(
        isolated_env / "config.yaml",
        monkeypatch,
        "integrations:\n  phrases:\n    voice: clone:marco\n",
    )
    assert main(["cache", "warm", "--hook-phrases"]) == 0
    assert {call[1] for call in fake_service.calls} == {"clone:marco"}
    warmed = len(fake_service.calls)
    hook_stdin({"notification_type": "idle_prompt"})
    assert main(["hook", "notification"]) == 0
    assert len(fake_service.calls) == warmed, "the hook hit the warmed entry"


def test_speak_and_notify_accept_language(isolated_env: Path, fake_service, capsys):
    assert main(["speak", "Hello", "--no-play", "-l", "en"]) == 0
    assert json.loads(capsys.readouterr().out)["language"] == "en"
    assert fake_service.calls[-1] == ("Hello", None, "en")
    assert main(["notify", "Done", "--channels", "file", "--language", "en"]) == 0
    capsys.readouterr()
    assert fake_service.calls[-1] == ("Done", None, "en")
    assert main(["speak", "x", "--no-play", "-l", "nope"]) == 1
    assert "invalid_language" in capsys.readouterr().err


# -- doctor against a healthy (fake) server ------------------------------------------------------


@pytest.fixture
def doctor_provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    # CI runners (Linux especially) have no audio player; doctor must not fail for that here.
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    provider = FakeProvider()
    provider.voices = [
        VoiceInfo("marco", "Marco", "it"),
        VoiceInfo("giulia", "Giulia", "it"),
        VoiceInfo("alice", "Alice", "en"),
        VoiceInfo("robot", None, None),
    ]
    return provider


@pytest.fixture
def doctor_settings(tmp_path: Path) -> Settings:
    return Settings(
        storage={"temp_dir": tmp_path / "tmp"},
        cache={"directory": tmp_path / "cache"},
        output={"directory": tmp_path / "out"},
        speech={"voices_by_language": {"it": "marco", "en": "nope"}},
        tts={"voicestudio": {"voice": "robot"}},
    )


async def test_doctor_groups_voices_by_language_and_checks_configured_ones(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    out = capsys.readouterr().out
    assert "  health: ok (version test)" in out
    assert "  engines: omnivoice" in out
    lines = out.splitlines()
    start = lines.index("  voices: 4 available")
    assert lines[start + 1 : start + 4] == [
        "    en: alice",
        "    it: marco, giulia",
        "    unspecified: robot",
    ]
    assert "    voices_by_language[en]=nope: WARNING not in the server's voice list" in lines
    assert "    voices_by_language[it]=marco: found" in lines
    assert "    voice=robot: found" in lines
    assert "synthesis:" not in out, "no sample phrase without --synth"
    assert doctor_provider.calls == []


async def test_doctor_keeps_flat_voice_list_when_server_reports_no_languages(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    doctor_provider.voices = [VoiceInfo(f"v{i}") for i in range(20)]
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    out = capsys.readouterr().out
    assert "  voices: 20 available: v0, v1," in out and ", +4 more" in out
    assert "    voices_by_language[it]=marco: WARNING" in out


async def test_doctor_checks_configured_voices_when_server_lists_none(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    # A healthy server with an empty voice list: every configured id is absent, say so.
    doctor_provider.voices = []
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    lines = capsys.readouterr().out.splitlines()
    assert "  voices: 0 available" in lines
    assert "    voices_by_language[en]=nope: WARNING not in the server's voice list" in lines
    assert "    voices_by_language[it]=marco: WARNING not in the server's voice list" in lines
    assert "    voice=robot: WARNING not in the server's voice list" in lines


async def test_doctor_caps_long_language_groups(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    doctor_provider.voices = [VoiceInfo(f"v{i}", None, "it") for i in range(10)]
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    assert "    it: v0, v1, v2, v3, v4, v5, v6, v7 (+2 more)" in capsys.readouterr().out


async def test_doctor_synth_reports_latency_with_the_resolved_voice(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    code = await cmd_doctor(doctor_settings, None, synth=True, provider=doctor_provider)
    out = capsys.readouterr().out
    assert code == 0
    assert re.search(
        r"^  synthesis: ok in \d+\.\d\d s \(voice=marco, language=it, \d+ KB, 0\.5 s of audio\)$",
        out,
        re.M,
    ), out
    assert doctor_provider.calls == [("Voxello è pronto.", "marco", "it")]
    assert "Result: OK" in out


async def test_doctor_synth_uses_the_requested_language(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    await cmd_doctor(doctor_settings, None, synth=True, language=" EN ", provider=doctor_provider)
    assert doctor_provider.calls == [("Voxello is ready.", "nope", "en")]
    assert "language=en" in capsys.readouterr().out
    await cmd_doctor(doctor_settings, None, synth=True, language="de", provider=doctor_provider)
    assert doctor_provider.calls[-1] == ("Voxello is ready.", "robot", "de"), "English fallback"


async def test_doctor_synth_failure_is_a_problem(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    doctor_provider.fail_with = VoxelloError(TTS_PROVIDER_ERROR, "engine crashed")
    code = await cmd_doctor(doctor_settings, None, synth=True, provider=doctor_provider)
    out = capsys.readouterr().out
    assert code == 1
    assert "  synthesis: ERROR - tts_provider_error: engine crashed" in out
    assert "PROBLEMS FOUND" in out


async def test_doctor_rejects_invalid_language(doctor_settings: Settings, doctor_provider):
    with pytest.raises(VoxelloError) as exc:
        await cmd_doctor(doctor_settings, None, language="xx1", provider=doctor_provider)
    assert exc.value.code == INVALID_LANGUAGE


def test_doctor_flags_are_wired_through_main(
    isolated_env: Path,
    doctor_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    import voxello.tts.voicestudio as voicestudio

    monkeypatch.setattr(voicestudio, "VoiceStudioProvider", lambda _settings: doctor_provider)
    assert main(["doctor", "--synth", "-l", "en"]) == 0
    assert doctor_provider.calls == [("Voxello is ready.", None, "en")]
    assert "synthesis: ok" in capsys.readouterr().out
    assert main(["doctor", "-l", "italiano"]) == 1
    assert "invalid_language" in capsys.readouterr().err


# -- voice design and cloned voices ----------------------------------------------------------


def test_speak_and_notify_accept_instructions_and_voice(isolated_env: Path, fake_service, capsys):
    assert main(["speak", "Hi", "--no-play", "-l", "en", "-i", "Female, Whisper"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["instructions"] == "female, whisper"
    assert fake_service.instructions[-1] == "female, whisper"
    assert main(["notify", "Done", "--channels", "file", "--instructions", "child"]) == 0
    assert fake_service.instructions[-1] == "child"
    assert main(["notify", "Done", "--channels", "file", "--voice", "clone:marco"]) == 0
    assert fake_service.calls[-1] == ("Done", "clone:marco", "it")
    capsys.readouterr()
    assert main(["speak", "Hi", "--no-play", "-i", "robot"]) == 1
    assert "invalid_instructions" in capsys.readouterr().err
    assert main(["speak", "Hi", "--no-play", "-i", "male", "--voice", "nova"]) == 1
    assert "invalid_parameter" in capsys.readouterr().err


def test_cache_warm_with_instructions(isolated_env: Path, fake_service, capsys):
    phrases = isolated_env / "phrases.txt"
    phrases.write_text("Fatto.\n")
    assert main(["cache", "warm", str(phrases), "--instructions", "male, elderly"]) == 0
    assert fake_service.instructions == ["male, elderly"]
    assert "voice=design:male, elderly" in (main(["cache", "list"]), capsys.readouterr().out)[1]


@pytest.fixture
def profile_store(monkeypatch: pytest.MonkeyPatch) -> FakeVoiceProfileStore:
    import voxello.tts.voicestudio as voicestudio

    store = FakeVoiceProfileStore()
    monkeypatch.setattr(voicestudio, "VoiceStudioProvider", lambda _settings: store)
    return store


@pytest.fixture
def reference(isolated_env: Path) -> Path:
    path = isolated_env / "marco.wav"
    path.write_bytes(make_wav(5000))
    return path


def test_voice_clone_list_update_delete(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, reference: Path, capsys
):
    args = ["voice", "clone", "marco", str(reference), "--ref-text", "Ciao", "--yes"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "Cloned voice 'marco' created" in out
    assert "--voice clone:marco" in out
    assert profile_store.profiles["marco"][2] == "Ciao"
    assert profile_store.closed is True

    assert main(["voice", "list"]) == 0
    out = capsys.readouterr().out
    assert "clone:marco" in out and "Ciao" in out

    assert main(["voice", "update", "marco", "--ref-text", "Ciao a tutti"]) == 0
    assert profile_store.profiles["marco"][2] == "Ciao a tutti"
    assert "updated" in capsys.readouterr().out

    assert main(["voice", "delete", "clone:marco", "--yes"]) == 0
    assert profile_store.profiles == {}
    assert "deleted" in capsys.readouterr().out


def test_voice_clone_needs_consent(
    isolated_env: Path,
    profile_store: FakeVoiceProfileStore,
    reference: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["voice", "clone", "marco", str(reference)]) == 1
    assert "--yes" in capsys.readouterr().err
    assert profile_store.profiles == {}

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["voice", "clone", "marco", str(reference)]) == 1
    assert profile_store.profiles == {}
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert main(["voice", "clone", "marco", str(reference)]) == 0
    assert "marco" in profile_store.profiles


def test_voice_delete_needs_confirmation(
    isolated_env: Path,
    profile_store: FakeVoiceProfileStore,
    reference: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    assert main(["voice", "clone", "marco", str(reference), "-y"]) == 0
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["voice", "delete", "marco"]) == 1
    assert "marco" in profile_store.profiles
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert main(["voice", "delete", "marco"]) == 0
    assert profile_store.profiles == {}


def test_voice_clone_warns_about_duration_and_reports_errors(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, capsys
):
    short = isolated_env / "short.wav"
    short.write_bytes(make_wav(1000))
    assert main(["voice", "clone", "marco", str(short), "-y"]) == 0
    assert "warning: The reference audio lasts 1.0 s" in capsys.readouterr().err
    assert main(["voice", "clone", "marco", str(short), "-y"]) == 1
    assert "voice_exists" in capsys.readouterr().err
    assert main(["voice", "clone", "marco", str(short), "-y", "--overwrite"]) == 0
    assert main(["voice", "clone", "bad name", str(short), "-y"]) == 1
    assert "invalid_parameter" in capsys.readouterr().err
    assert main(["voice", "update", "marco"]) == 1
    assert "Nothing to update" in capsys.readouterr().err


# -- voice record (roadmap 8.2) --------------------------------------------------------------


@pytest.fixture
def recorder(isolated_env: Path, monkeypatch: pytest.MonkeyPatch):
    """A fake microphone, no countdown, no player and an interactive terminal."""
    import voxello.cli as cli
    import voxello.recording.ffmpeg as ffmpeg

    from .test_recording import FakeRecorder

    fake = FakeRecorder(6000)
    monkeypatch.setattr(ffmpeg, "detect_recorder", lambda **_kw: fake)
    monkeypatch.setattr("voxello.recording.session.time.sleep", lambda _s: None)
    played: list[Path] = []
    monkeypatch.setattr(cli, "_blocking_player", lambda _settings: played.append)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setenv("VOXELLO_STORAGE__RECORDINGS_DIR", str(isolated_env / "voices"))
    fake.played = played  # type: ignore[attr-defined]
    return fake


def answer(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    """Feed ``input()``; returns the prompts it was called with."""
    queue, prompts = list(answers), []

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


def test_voice_record_clones_the_register_with_the_read_text(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    from voxello.recording.registers import reading_script

    prompts = answer(monkeypatch, "y", "", "", "a")
    assert main(["voice", "record", "marco", "--register", "whisper"]) == 0
    expected = reading_script("whisper", "it").text
    audio, filename, ref_text = profile_store.profiles["marco-whisper"]
    assert ref_text == expected and filename == "marco-whisper.wav"
    assert "permission" in prompts[0]
    clip = isolated_env / "voices" / "marco-whisper.wav"
    assert clip.read_bytes() == audio, "the cleaned clip is kept for re-cloning"
    meta = json.loads((isolated_env / "voices" / "marco-whisper.json").read_text())
    assert meta["ref_text"] == expected and meta["register"] == "whisper"
    assert meta["language"] == "it" and meta["seconds"] == 6.0
    out = capsys.readouterr().out
    assert expected in out
    assert "--voice clone:marco-whisper" in out
    assert len(recorder.played) == 1, "the take is played back once before accepting"


def test_voice_record_neutral_uses_the_bare_name_and_no_keep_removes_the_clip(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    recorder.durations = [2000]
    answer(monkeypatch, "", "", "a")
    args = ["voice", "record", "clone:marco", "-y", "--no-keep", "-l", "en", "--text", "Hi."]
    assert main(args) == 0
    assert profile_store.profiles["marco"][2] == "Hi."
    assert "warning: The reference audio lasts 2.0 s" in capsys.readouterr().err
    assert list((isolated_env / "voices").iterdir()) == []


def test_voice_record_needs_a_terminal_and_consent(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["voice", "record", "marco", "-y"]) == 1
    assert "recorder_unavailable" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answer(monkeypatch, "n")
    assert main(["voice", "record", "marco"]) == 1
    assert "permission" in capsys.readouterr().err
    assert recorder.started == 0 and profile_store.profiles == {}


def test_voice_record_asks_before_replacing_a_profile(
    isolated_env: Path,
    profile_store: FakeVoiceProfileStore,
    reference: Path,
    recorder,
    monkeypatch,
    capsys,
):
    assert main(["voice", "clone", "marco", str(reference), "--ref-text", "old", "-y"]) == 0
    answer(monkeypatch, "n")
    assert main(["voice", "record", "marco", "-y"]) == 1
    assert recorder.started == 0
    assert profile_store.profiles["marco"][2] == "old"
    answer(monkeypatch, "", "", "a")
    assert main(["voice", "record", "marco", "-y", "--overwrite"]) == 0
    assert profile_store.profiles["marco"][2] != "old"
    profile_store.profiles["marco"] = (b"", "x.wav", "old")
    answer(monkeypatch, "y", "", "", "a")
    assert main(["voice", "record", "marco", "-y"]) == 0, "answering yes replaces it too"
    assert profile_store.profiles["marco"][2] != "old"


def test_voice_record_quit_records_nothing(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    answer(monkeypatch, "q")
    assert main(["voice", "record", "marco", "-y"]) == 1
    assert "Nothing recorded" in capsys.readouterr().err
    assert profile_store.profiles == {}


def test_voice_record_keeps_the_clip_when_the_upload_fails(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    from voxello.errors import TTS_PROVIDER_UNAVAILABLE

    answer(monkeypatch, "", "", "a")
    original_create = profile_store.create_voice_profile

    async def failing_create(*args, **kwargs):
        raise VoxelloError(TTS_PROVIDER_UNAVAILABLE, "down")

    monkeypatch.setattr(profile_store, "create_voice_profile", failing_create)
    assert main(["voice", "record", "marco", "-r", "sad", "-y", "--no-keep"]) == 1
    err = capsys.readouterr().err
    clip = isolated_env / "voices" / "marco-sad.wav"
    assert clip.exists(), "a failed upload never loses the recording"
    assert "tts_provider_unavailable" in err
    assert f"voxello voice clone marco-sad {shlex.quote(str(clip))} --ref-text" in err
    monkeypatch.setattr(profile_store, "create_voice_profile", original_create)


def test_voice_record_reports_a_missing_recorder(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, monkeypatch, capsys
):
    import voxello.recording.ffmpeg as ffmpeg
    from voxello.errors import RECORDER_UNAVAILABLE

    def unavailable(**_kw):
        raise VoxelloError(RECORDER_UNAVAILABLE, "install ffmpeg")

    monkeypatch.setattr(ffmpeg, "detect_recorder", unavailable)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert main(["voice", "record", "marco", "-y"]) == 1
    assert "install ffmpeg" in capsys.readouterr().err


ALL_REGISTERS = ["neutral", "whisper", "sad", "angry", "happy"]
TAKE = ("", "", "a")  # Enter to record, Enter to stop, accept


def test_voice_record_all_clones_every_register_with_one_consent(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    from voxello.recording.registers import available_registers

    prompts = answer(monkeypatch, "y", *TAKE * 5)
    assert main(["voice", "record", "francesca", "--all"]) == 0
    scripts = available_registers("it")
    expected = {
        "francesca": scripts["neutral"].text,
        **{f"francesca-{r}": scripts[r].text for r in ALL_REGISTERS[1:]},
    }
    assert {pid: text for pid, (_, _, text) in profile_store.profiles.items()} == expected
    assert sum("permission" in prompt for prompt in prompts) == 1, "one consent for the session"
    out = capsys.readouterr().out
    assert "[1/5] neutral" in out and "[5/5] happy" in out
    for register in ALL_REGISTERS[1:]:
        assert re.search(rf"{register}\s+clone:francesca-{register}\s+created", out)


def test_voice_record_all_records_only_the_missing_registers_when_asked(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    profile_store.profiles["francesca"] = (b"", "old.wav", "old")
    profile_store.profiles["francesca-sad"] = (b"", "old.wav", "old")
    prompts = answer(monkeypatch, "n", *TAKE * 3)
    assert main(["voice", "record", "francesca", "--all", "-y"]) == 0
    assert "francesca, francesca-sad" in prompts[0]
    assert profile_store.profiles["francesca"][2] == "old"
    assert profile_store.profiles["francesca-sad"][2] == "old"
    assert len(profile_store.profiles) == 5
    out = capsys.readouterr().out
    assert "[1/3] whisper" in out
    assert re.search(r"sad\s+clone:francesca-sad\s+already cloned", out)


def test_voice_record_all_with_every_register_cloned_has_nothing_to_do(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    for pid in ["francesca", *(f"francesca-{r}" for r in ALL_REGISTERS[1:])]:
        profile_store.profiles[pid] = (b"", "old.wav", "old")
    answer(monkeypatch, "n")
    assert main(["voice", "record", "francesca", "--all", "-y"]) == 0
    assert "--overwrite" in capsys.readouterr().out
    assert recorder.started == 0


def test_voice_record_all_skip_and_quit(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    answer(monkeypatch, "s", *TAKE, "q")
    assert main(["voice", "record", "francesca", "--all", "-y"]) == 0
    assert list(profile_store.profiles) == ["francesca-whisper"]
    out = capsys.readouterr().out
    assert re.search(r"neutral\s+clone:francesca\s+skipped", out)
    assert re.search(r"happy\s+clone:francesca-happy\s+not recorded", out)


def test_voice_record_all_quit_at_once_records_nothing(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    answer(monkeypatch, "q")
    assert main(["voice", "record", "francesca", "--all", "-y"]) == 1
    assert "Nothing recorded" in capsys.readouterr().err


def test_voice_record_all_goes_on_after_a_failed_upload(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, monkeypatch, capsys
):
    from voxello.errors import TTS_PROVIDER_ERROR

    original_create = profile_store.create_voice_profile

    async def flaky_create(profile_id, *args, **kwargs):
        if profile_id == "francesca-whisper":
            raise VoxelloError(TTS_PROVIDER_ERROR, "boom")
        return await original_create(profile_id, *args, **kwargs)

    monkeypatch.setattr(profile_store, "create_voice_profile", flaky_create)
    answer(monkeypatch, *TAKE * 5)
    assert main(["voice", "record", "francesca", "--all", "-y"]) == 1
    assert len(profile_store.profiles) == 4
    captured = capsys.readouterr()
    assert "voxello voice clone francesca-whisper" in captured.err
    assert re.search(r"whisper\s+clone:francesca-whisper\s+upload failed", captured.out)
    assert (isolated_env / "voices" / "francesca-whisper.wav").exists()


def test_voice_record_all_rejects_text_register_and_unknown_languages(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, recorder, capsys
):
    assert main(["voice", "record", "x", "--all", "--text", "Ciao."]) == 1
    assert "invalid_parameter" in capsys.readouterr().err
    assert main(["voice", "record", "x", "--all", "-l", "fr"]) == 1
    assert "--register" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["voice", "record", "x", "--all", "--register", "sad"])
    assert recorder.started == 0


def test_blocking_player_waits_for_the_backend(monkeypatch, tmp_path: Path, capsys):
    import voxello.cli as cli
    import voxello.playback.detect as detect
    from voxello.playback.backends import Afplay

    runs: list[list[str]] = []
    monkeypatch.setattr(detect, "detect_backend", lambda _preferred: Afplay())
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **_kw: runs.append(argv))
    cli._blocking_player(Settings())(tmp_path / "a.wav")
    assert runs == [["afplay", "-v", "0.80", str(tmp_path / "a.wav")]]
    monkeypatch.setattr(detect, "detect_backend", lambda _preferred: None)
    cli._blocking_player(Settings())(tmp_path / "a.wav")
    assert "no audio player found" in capsys.readouterr().err


def test_doctor_recorder_line(monkeypatch):
    import voxello.recording.ffmpeg as ffmpeg
    from voxello.cli import describe_recorder
    from voxello.errors import RECORDER_UNAVAILABLE

    linux = ffmpeg.FfmpegRecorder("/usr/bin/ffmpeg", ["-f", "pulse", "-i", "default"], "linux")
    monkeypatch.setattr(ffmpeg, "detect_recorder", lambda **_kw: linux)
    assert describe_recorder() == "Recorder: ffmpeg (pulse default)"

    def unavailable(**_kw):
        raise VoxelloError(RECORDER_UNAVAILABLE, "needs ffmpeg")

    monkeypatch.setattr(ffmpeg, "detect_recorder", unavailable)
    assert describe_recorder() == "Recorder: unavailable (needs ffmpeg)"


def test_voice_list_shows_designs_even_when_the_server_is_down(
    isolated_env: Path, profile_store: FakeVoiceProfileStore, monkeypatch, capsys
):
    from voxello.errors import TTS_PROVIDER_UNAVAILABLE

    cfg = isolated_env / "config.yaml"
    cfg.write_text('speech:\n  voice_designs:\n    narrator: "Female, High Pitch"\n')
    monkeypatch.setenv("VOXELLO_CONFIG", str(cfg))
    profile_store.fail_with = VoxelloError(TTS_PROVIDER_UNAVAILABLE, "down")
    assert main(["voice", "list"]) == 1
    captured = capsys.readouterr()
    assert "narrator" in captured.out and "female, high pitch" in captured.out
    assert "tts_provider_unavailable" in captured.err
    profile_store.fail_with = None
    assert main(["voice", "list"]) == 0
    assert "No cloned voices" in capsys.readouterr().out


async def test_doctor_reports_designs_clones_and_vocabulary_drift(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    from voxello.core.voice_design import VOICE_DESIGN_ATTRIBUTES

    doctor_settings.speech.voice_designs = {"narrator": "female, high pitch"}
    doctor_settings.speech.voices_by_language = {"en": "narrator", "it": "clone:marco"}
    doctor_provider.voices = [
        VoiceInfo("auto", kind="auto"),
        VoiceInfo("clone:marco", kind="clone"),
    ]
    attributes = {k: sorted(v) for k, v in VOICE_DESIGN_ATTRIBUTES.items()}
    doctor_provider.attributes = attributes
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    lines = capsys.readouterr().out.splitlines()
    assert '  voice_designs: narrator="female, high pitch"' in lines
    assert "  cloned voices: 1 (clone:marco)" in lines
    assert "    voices_by_language[en]=narrator: voice design (config)" in lines
    assert "    voices_by_language[it]=clone:marco: found" in lines
    assert "  voice design: vocabulary matches the server" in lines

    attributes = {**attributes, "style": ["whisper", "shout"], "gender": ["female"]}
    doctor_provider.attributes = attributes
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    out = capsys.readouterr().out
    assert "WARNING the server accepts style 'shout'" in out
    assert "WARNING the server no longer lists gender 'male'" in out


async def test_doctor_survives_an_unreadable_design_vocabulary(
    doctor_settings: Settings,
    doctor_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    async def broken() -> None:
        raise VoxelloError(TTS_PROVIDER_ERROR, "boom")

    monkeypatch.setattr(doctor_provider, "design_attributes", broken)
    assert await cmd_doctor(doctor_settings, None, provider=doctor_provider) == 0
    assert "  voice design: could not read the vocabulary (tts_provider_error)" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize("command", ["update", "delete"])
def test_voice_changes_evict_cached_audio_while_the_cache_is_disabled(
    isolated_env: Path,
    profile_store: FakeVoiceProfileStore,
    reference: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    command: str,
):
    """Re-enabling the cache must not replay a changed or deleted cloned voice."""
    from voxello.storage.cache import AudioCache

    assert main(["voice", "clone", "marco", str(reference), "-y"]) == 0
    seed_cache_entry(isolated_env / "cache", "clone:marco")
    seed_cache_entry(isolated_env / "cache", "nova")
    capsys.readouterr()

    monkeypatch.setenv("VOXELLO_CACHE__ENABLED", "false")
    args = ["voice", "update", "marco", "--ref-text", "Ciao"]
    if command == "delete":
        args = ["voice", "delete", "marco", "--yes"]
    assert main(args) == 0
    assert "(1 cached phrase dropped)" in capsys.readouterr().out
    cache = AudioCache(isolated_env / "cache", max_entries=20, max_age_days=30)
    assert [entry.voice for entry in cache.entries()] == ["nova"]


class FakePopen:
    """Records the detached child the Stop hook would start; never runs anything."""

    started: ClassVar[list[FakePopen]] = []

    def __init__(self, argv, **kwargs):
        self.argv, self.kwargs = argv, kwargs
        self.written = b""
        self.stdin = self
        FakePopen.started.append(self)

    def write(self, data: bytes) -> None:
        self.written += data

    def close(self) -> None:
        pass


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch):
    FakePopen.started = []
    monkeypatch.setattr("voxello.cli.subprocess.Popen", FakePopen)
    return FakePopen.started


def stop_payload(transcript: Path | None, **extra) -> dict:
    return {
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "transcript_path": str(transcript) if transcript else None,
        **extra,
    }


def test_hook_stop_blocks_a_long_turn_without_a_summary(
    isolated_env: Path, hook_stdin, monkeypatch: pytest.MonkeyPatch, popen, capsys
):
    from voxello.hooks import SUMMARY_REQUEST

    write_config(
        isolated_env / "config.yaml",
        monkeypatch,
        "integrations:\n  summaries:\n    mode: always\n    enforce: true\n"
        "    min_turn_seconds: 0\n",
    )
    transcript = isolated_env / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "go"},
            }
        )
        + "\n"
    )
    hook_stdin(stop_payload(transcript))
    assert main(["hook", "stop", "--agent", "claude-code"]) == 0
    assert json.loads(capsys.readouterr().out) == {"decision": "block", "reason": SUMMARY_REQUEST}
    assert popen == []


def test_hook_stop_prints_an_empty_object_when_nothing_to_do(
    isolated_env: Path, hook_stdin, popen, capsys
):
    """Codex wants JSON on stdout when a Stop hook exits 0; `{}` is valid for both agents."""
    hook_stdin(stop_payload(None))
    assert main(["hook", "stop"]) == 0  # default agent: claude-code
    assert capsys.readouterr().out.strip() == "{}"
    hook_stdin("garbage")
    assert main(["hook", "stop", "--agent", "claude-code"]) == 0
    assert capsys.readouterr().out.strip() == "{}"
    assert popen == []


def test_hook_stop_codex_speaks_the_phrase_in_a_detached_child(
    isolated_env: Path, hook_stdin, popen, capsys
):
    hook_stdin(stop_payload(None))
    assert main(["--config", str(isolated_env / "c.yaml"), "hook", "stop", "--agent", "codex"]) == 0
    assert capsys.readouterr().out.strip() == "{}"
    (child,) = popen
    # -P: the hook runs in the project, whose directory must not shadow the voxello package.
    assert child.argv[1:4] == ["-P", "-m", "voxello"]
    assert child.kwargs["cwd"] == str(Path.home())
    assert child.argv[4:] == [
        "--config",
        str(isolated_env / "c.yaml"),
        "hook",
        "notification",
        "--priority",
        "normal",
    ]
    assert child.kwargs["start_new_session"] is True
    assert json.loads(child.written) == {"notification_type": "codex_turn_complete"}


def test_codex_turn_complete_phrase_is_in_every_message_file():
    from voxello.install import hook_languages, hook_phrases

    for language in hook_languages():
        assert hook_phrases(language)["codex_turn_complete"].startswith("Codex")


async def test_doctor_shows_what_the_agent_integrations_say_and_with_which_voice(
    doctor_settings: Settings, doctor_provider: FakeProvider, capsys: pytest.CaptureFixture[str]
):
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    lines = capsys.readouterr().out.splitlines()
    assert "  phrases: mode=cached voice=default" in lines
    assert "  summaries: mode=on_request voice=default" in lines

    doctor_settings.integrations = IntegrationsSettings(
        phrases={"mode": "live", "voice": "nope", "language": "en", "channels": ["desktop"]},
        summaries={"mode": "always", "enforce": True, "instructions": "female"},
    )
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    lines = capsys.readouterr().out.splitlines()
    assert "  phrases: mode=live voice=nope language=en channels=desktop" in lines
    assert (
        '  summaries: mode=always (enforced by the Stop hook, turns >= 60 s) voice=design:"female"'
        in lines
    )
    assert "    integrations.phrases.voice=nope: WARNING not in the server's voice list" in lines


def test_detached_child_gets_an_absolute_config_path(
    isolated_env: Path, hook_stdin, monkeypatch: pytest.MonkeyPatch, popen
):
    monkeypatch.chdir(isolated_env)
    hook_stdin(stop_payload(None))
    assert main(["--config", "c.yaml", "hook", "stop", "--agent", "codex"]) == 0
    assert popen[0].argv[4] == "--config"
    config = Path(popen[0].argv[5])
    assert config.is_absolute() and config.resolve() == (isolated_env / "c.yaml").resolve()


def test_hook_channels_default_and_detached_child_without_config(
    isolated_env: Path, hook_stdin, monkeypatch: pytest.MonkeyPatch, popen
):
    from voxello.cli import hook_channels

    monkeypatch.delenv("VOXELLO_HOOK_CHANNELS", raising=False)
    assert hook_channels(Settings(), None) == ["voice", "desktop"]
    hook_stdin(stop_payload(None))
    assert main(["hook", "stop", "--agent", "codex"]) == 0
    assert popen[0].argv[4:] == ["hook", "notification", "--priority", "normal"]


def test_hooks_use_the_project_file_of_the_event_cwd(
    isolated_env: Path, fake_service, hook_stdin, monkeypatch: pytest.MonkeyPatch, popen
):
    """Two projects, two voices: the hook picks the project from the event's `cwd`."""
    from voxello.config import PROJECT_CONFIG_NAME

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("VOXELLO_PROJECT_DIR", raising=False)
    monkeypatch.setenv("VOXELLO_HOOK_CHANNELS", "file")
    voices = {"alfa": "clone:marco", "beta": "clone:anna"}
    for name, voice in voices.items():
        root = isolated_env / name
        (root / ".git").mkdir(parents=True)
        (root / PROJECT_CONFIG_NAME).write_text(
            f"integrations:\n  phrases: {{voice: '{voice}'}}\n", encoding="utf-8"
        )
    for name in voices:
        hook_stdin({"notification_type": "idle_prompt", "cwd": str(isolated_env / name / "src")})
        assert main(["hook", "notification"]) == 0
    assert [call[1] for call in fake_service.calls] == ["clone:marco", "clone:anna"]

    # The detached Codex phrase keeps the event's cwd, so it gets the same project voice.
    hook_stdin(stop_payload(None, cwd=str(isolated_env / "beta")))
    assert main(["hook", "stop", "--agent", "codex"]) == 0
    assert json.loads(popen[0].written) == {
        "notification_type": "codex_turn_complete",
        "cwd": str(isolated_env / "beta"),
    }


async def test_doctor_reports_the_project_file_and_ignored_keys(
    doctor_settings: Settings,
    doctor_provider: FakeProvider,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    from voxello.config import PROJECT_CONFIG_NAME

    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = tmp_path / "alfa"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("VOXELLO_PROJECT_DIR", str(root))
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    assert f"Project config: none (searched from {root})" in capsys.readouterr().out

    (root / PROJECT_CONFIG_NAME).write_text(
        "notifications: {title: Alfa}\ntts: {provider: voicestudio}\n", encoding="utf-8"
    )
    await cmd_doctor(doctor_settings, None, provider=doctor_provider)
    out = capsys.readouterr().out
    assert f"Project config: {root / PROJECT_CONFIG_NAME}" in out
    assert "WARNING ignored (not allowed in a project file): tts" in out
