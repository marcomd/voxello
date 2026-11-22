"""`voxello dialogue ...` commands (roadmap 8.3-8.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from voxello.cli import main
from voxello.dialogue import commands

from .conftest import FakeProvider

SOURCE = """Francesca cercò i suoi occhi.

«Metto il telefono in carica.» disse Marco.

«Nello studio?» domandò Francesca.
"""

CAST = {
    "language": "it",
    "narrator": "narratore",
    "characters": {
        "narratore": {"voice": "onyx"},
        "marco": {"voice": "clone:marco"},
        "francesca": {"voice": "clone:francesca"},
    },
}


@pytest.fixture
def cfg(tmp_path: Path) -> list[str]:
    return ["--config", str(tmp_path / "missing-config.yaml")]


@pytest.fixture
def project(tmp_path: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]) -> Path:
    source = tmp_path / "chapter1.txt"
    source.write_text(SOURCE, encoding="utf-8")
    assert main([*cfg, "dialogue", "init", str(source)]) == 0
    capsys.readouterr()
    return tmp_path / "chapter1"


def run(cfg: list[str], project: Path, *args: str) -> int:
    return main([*cfg, "dialogue", args[0], "-p", str(project), *args[1:]])


def annotate(cfg: list[str], project: Path, data: object) -> int:
    path = project.parent / "ann.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return run(cfg, project, "annotate", str(path))


def test_init_creates_the_project_next_to_the_source(
    tmp_path: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "c.txt"
    source.write_text("«Uno.» disse.\n\n«Due", encoding="utf-8")
    assert main([*cfg, "dialogue", "init", str(source)]) == 0
    captured = capsys.readouterr()
    assert (tmp_path / "c" / "script.yaml").is_file()
    assert "3 segments in 2 paragraphs" in captured.out
    assert "warning: p002.1: the quote is not closed in its paragraph" in captured.err


def test_init_into_a_named_directory_with_threshold(
    tmp_path: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "c.txt"
    source.write_text("Uno. Due.", encoding="utf-8")
    target = tmp_path / "elsewhere"
    assert main([*cfg, "dialogue", "init", str(source), str(target), "--max-chars", "4"]) == 0
    assert yaml.safe_load((target / "script.yaml").read_text())["max_chars"] == 4


def test_init_refuses_twice(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*cfg, "dialogue", "init", str(project.parent / "chapter1.txt")]) == 1
    assert "already holds a dialogue project" in capsys.readouterr().err


def test_commands_outside_a_project_fail(
    tmp_path: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(cfg, tmp_path, "check") == 1
    assert "invalid_script" in capsys.readouterr().err


def test_segments_prints_a_chunk_with_annotations(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert annotate(cfg, project, {"p002.1": {"speaker": "marco"}}) == 0
    assert "Annotated 1 segment: p002.1" in capsys.readouterr().out
    assert run(cfg, project, "segments", "--from", "p002.1", "--count", "2") == 0
    assert capsys.readouterr().out.splitlines() == [
        "p002.1 speech {speaker: marco} «Metto il telefono in carica.»",
        "p002.2 narration disse Marco.",
    ]


def test_segments_pending_lists_unassigned_speech(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    annotate(cfg, project, {"p002.1": {"speaker": "marco"}})
    capsys.readouterr()
    assert run(cfg, project, "segments", "--pending") == 0
    assert capsys.readouterr().out.splitlines() == [
        "p003.1 speech «Nello studio?»",
    ]


def test_annotate_from_stdin_and_rejection(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("p002.1: {speaker: marco}\n"))
    assert run(cfg, project, "annotate", "-") == 0
    assert annotate(cfg, project, {"p002.1": {"text": "altro"}}) == 1
    assert "p002.1: `text` cannot change" in capsys.readouterr().err
    (project.parent / "bad.yaml").write_text("p002.1: [", encoding="utf-8")
    assert run(cfg, project, "annotate", str(project.parent / "bad.yaml")) == 1
    assert run(cfg, project, "annotate", str(project.parent / "none.yaml")) == 1


def test_annotate_reports_no_change(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert annotate(cfg, project, {"p002.2": {"attribution": False}}) == 0
    assert "No segment changed" in capsys.readouterr().out


def test_check_reports_errors_then_passes(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(cfg, project, "check") == 1
    out = capsys.readouterr().out
    assert "error: p002.1: speech has no speaker" in out
    assert "warning: cast.yaml is missing" in out

    annotate(cfg, project, {"p002.1": {"speaker": "marco"}, "p003.1": {"speaker": "francesca"}})
    (project / "cast.yaml").write_text(yaml.safe_dump(CAST), encoding="utf-8")
    capsys.readouterr()
    assert run(cfg, project, "check") == 0
    out = capsys.readouterr().out
    assert "OK: 5 segments rebuild source.txt; 5 lines to speak." in out


def test_show_is_a_screenplay(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    annotate(
        cfg,
        project,
        {
            "p002.1": {"speaker": "marco", "register": "whisper"},
            "p002.2": {"attribution": True},
            "p003.1": {"speaker": "francesca", "tts_text": "[sigh] Nello studio?"},
            "p003.2": {"skip": True},
        },
    )
    (project / "cast.yaml").write_text(yaml.safe_dump(CAST), encoding="utf-8")
    capsys.readouterr()
    assert run(cfg, project, "show") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split(None, 2) == ["p001.1", "NARRATORE", "Francesca cercò i suoi occhi."]
    assert "MARCO (whisper)" in lines[1]
    assert "(attribution)" in lines[2]
    assert lines[3].endswith("[sigh] Nello studio?")
    assert lines[4] == "          text: «Nello studio?»"
    assert "(skip)" in lines[5]


def test_show_without_cast_marks_unassigned(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(cfg, project, "show", "--count", "2") == 0
    lines = capsys.readouterr().out.splitlines()
    assert "(narration)" in lines[0]
    assert "???" in lines[1]


def test_stats_counts_lines_and_characters(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    annotate(cfg, project, {"p002.1": {"speaker": "marco"}})
    capsys.readouterr()
    assert run(cfg, project, "stats") == 0
    out = capsys.readouterr().out
    assert "marco" in out
    assert "(narration)" in out
    assert "unassigned speech: 1 line" in out


def test_render_uses_the_provider_and_reports_progress(
    project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotate(cfg, project, {"p002.1": {"speaker": "marco"}, "p003.1": {"speaker": "francesca"}})
    (project / "cast.yaml").write_text(yaml.safe_dump(CAST), encoding="utf-8")
    provider = FakeProvider()
    monkeypatch.setattr(commands, "make_provider", lambda _settings: provider)
    capsys.readouterr()
    assert run(cfg, project, "render") == 0
    captured = capsys.readouterr()
    assert len(provider.calls) == 5
    assert "[1/5] p001.1 rendered (0.5 s)" in captured.err
    assert "Rendered 5 lines, 0 already current." in captured.out
    state = json.loads((project / "state.json").read_text())
    assert set(state["lines"]) == {"p001.1", "p002.1", "p002.2", "p003.1", "p003.2"}

    assert run(cfg, project, "render", "--lines", "p002.1") == 0
    assert "[1/1] p002.1 current" in capsys.readouterr().err


def test_render_refuses_an_invalid_project(
    project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commands, "make_provider", lambda _settings: FakeProvider())
    assert run(cfg, project, "render") == 1
    assert "cast.yaml" in capsys.readouterr().err
