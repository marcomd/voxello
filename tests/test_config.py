from __future__ import annotations

from pathlib import Path

import pytest

from voxello.config import load_settings, resolve_config_path


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VOXELLO_CONFIG", raising=False)
    settings = load_settings(tmp_path / "missing.yaml")
    assert settings.tts.voicestudio.base_url == "http://localhost:3900"
    assert settings.playback.default_interrupt is True
    assert settings.limits.max_text_chars == 2000
    assert settings.logging.log_text is False


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
    monkeypatch.setenv("VOXELLO_LOG_LEVEL", "debug")
    settings = load_settings(tmp_path / "none.yaml")
    assert settings.tts.voicestudio.base_url == "http://vs.lan:3900"
    assert settings.tts.voicestudio.api_key is not None
    assert settings.tts.voicestudio.api_key.get_secret_value() == "secret"
    assert settings.tts.voicestudio.voice == "marco"
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
