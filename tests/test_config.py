from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from voxello.config import EXAMPLE_CONFIG, Settings, load_settings, resolve_config_path

REPO = Path(__file__).resolve().parents[1]


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VOXELLO_CONFIG", raising=False)
    settings = load_settings(tmp_path / "missing.yaml")
    assert settings.tts.voicestudio.base_url == "http://localhost:3900"
    assert settings.playback.default_interrupt is True
    assert settings.limits.max_text_chars == 2000
    assert settings.logging.log_text is False
    assert settings.cache.enabled is True
    assert settings.cache.max_entries == 200
    assert settings.cache.max_age_days == 90
    assert settings.cache.max_text_chars == 300
    assert settings.cache.resolved_directory().name == "audio"
    assert settings.cache.resolved_directory().parent == settings.storage.resolved_temp_dir().parent
    assert settings.speech.default_language == "it"
    assert settings.speech.voices_by_language == {}
    assert settings.tts.voicestudio.language is None


def test_speech_settings_validation(tmp_path: Path):
    settings = Settings(speech={"default_language": "en", "voices_by_language": {"it": "marco"}})
    assert settings.speech.default_language == "en"
    assert settings.speech.voices_by_language == {"it": "marco"}
    with pytest.raises(ValidationError, match="default_language"):
        Settings(speech={"default_language": "italiano"})
    with pytest.raises(ValidationError, match="ISO 639-1"):
        Settings(speech={"voices_by_language": {"it-IT": "marco"}})
    with pytest.raises(ValidationError, match="must name a voice"):
        Settings(speech={"voices_by_language": {"it": " "}})


def test_deprecated_language_key_migrates_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Roadmap 3.1: tts.voicestudio.language still works as the default, but warns."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("tts:\n  voicestudio:\n    language: en\n")
    with caplog.at_level("WARNING", logger="voxello.config"):
        settings = load_settings(cfg)
    assert settings.speech.default_language == "en"
    assert settings.tts.voicestudio.language == "en"
    assert any("deprecated" in r.message for r in caplog.records)

    caplog.clear()
    cfg.write_text("tts:\n  voicestudio:\n    language: en\nspeech:\n  default_language: it\n")
    with caplog.at_level("WARNING", logger="voxello.config"):
        settings = load_settings(cfg)
    assert settings.speech.default_language == "it", "the new key wins over the deprecated one"
    assert any("ignored" in r.message for r in caplog.records)

    caplog.clear()
    cfg.write_text("speech:\n  default_language: en\n")
    with caplog.at_level("WARNING", logger="voxello.config"):
        load_settings(cfg)
    assert not caplog.records, "no warning without the deprecated key"


def test_cache_directory_must_differ_from_temp_dir(tmp_path: Path):
    with pytest.raises(ValidationError, match="differ"):
        Settings(storage={"temp_dir": tmp_path / "same"}, cache={"directory": tmp_path / "same"})
    with pytest.raises(ValidationError, match="min_text_chars"):
        Settings(cache={"min_text_chars": 10, "max_text_chars": 5})
    settings = Settings(
        storage={"temp_dir": tmp_path / "tmp"}, cache={"directory": tmp_path / "audio"}
    )
    assert settings.cache.resolved_directory() == tmp_path / "audio"


def test_example_config_matches_repo_file():
    """`voxello config init` writes EXAMPLE_CONFIG; config.example.yaml documents the same."""
    assert (REPO / "config.example.yaml").read_text(encoding="utf-8") == EXAMPLE_CONFIG
    assert "cache:" in EXAMPLE_CONFIG
    assert "speech:" in EXAMPLE_CONFIG and "    language:" not in EXAMPLE_CONFIG


def test_yaml_then_env_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "tts:\n  voicestudio:\n    base_url: http://from-yaml:3900/\n    engine: voxcpm2\n"
        "playback:\n  volume: 0.5\nlogging:\n  level: WARNING\n"
    )
    settings = load_settings(cfg)
    assert settings.tts.voicestudio.base_url == "http://from-yaml:3900"
    assert settings.tts.voicestudio.engine == "voxcpm2"
    assert settings.playback.volume == 0.5
    assert settings.logging.level == "warning"

    monkeypatch.setenv("VOXELLO_TTS__VOICESTUDIO__BASE_URL", "http://nested-env:3900")
    monkeypatch.setenv("VOXELLO_PLAYBACK__VOLUME", "0.9")
    settings = load_settings(cfg)
    assert settings.tts.voicestudio.base_url == "http://nested-env:3900"
    assert settings.playback.volume == 0.9


def test_flat_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOXELLO_VOICESTUDIO_URL", "http://vs.lan:3900")
    monkeypatch.setenv("VOXELLO_VOICESTUDIO_API_KEY", "secret")
    monkeypatch.setenv("VOXELLO_DEFAULT_VOICE", "marco")
    monkeypatch.setenv("VOXELLO_LANGUAGE", "en")
    monkeypatch.setenv("VOXELLO_LOG_LEVEL", "debug")
    settings = load_settings(tmp_path / "none.yaml")
    assert settings.tts.voicestudio.base_url == "http://vs.lan:3900"
    assert settings.tts.voicestudio.api_key is not None
    assert settings.tts.voicestudio.api_key.get_secret_value() == "secret"
    assert settings.tts.voicestudio.voice == "marco"
    assert settings.speech.default_language == "en"
    assert settings.logging.level == "debug"


def test_config_path_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assert resolve_config_path(tmp_path / "x.yaml") == tmp_path / "x.yaml"
    monkeypatch.setenv("VOXELLO_CONFIG", str(tmp_path / "env.yaml"))
    assert resolve_config_path() == tmp_path / "env.yaml"
    monkeypatch.delenv("VOXELLO_CONFIG")
    assert resolve_config_path().name == "config.yaml"


def test_invalid_yaml_shape(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- just\n- a list\n")
    with pytest.raises(ValueError):
        load_settings(cfg)
