from __future__ import annotations

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
    assert "PROBLEMS FOUND" in out
