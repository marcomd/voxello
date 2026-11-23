"""`voxello dialogue ...` commands (roadmap 8.3-8.4)."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest
import yaml

from voxello.cli import main
from voxello.dialogue import commands

from .conftest import FakeProvider

SOURCE = """Anna aprì la finestra.

«Porto fuori il cane.» disse Bruno.

«Fino al parco?» domandò Anna.
"""

CAST = {
    "language": "it",
    "narrator": "narratore",
    "characters": {
        "narratore": {"voice": "onyx"},
        "bruno": {"voice": "clone:bruno"},
        "anna": {"voice": "clone:anna"},
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
    assert annotate(cfg, project, {"p002.1": {"speaker": "bruno"}}) == 0
    assert "Annotated 1 segment: p002.1" in capsys.readouterr().out
    assert run(cfg, project, "segments", "--from", "p002.1", "--count", "2") == 0
    assert capsys.readouterr().out.splitlines() == [
        "p002.1 speech {speaker: bruno} «Porto fuori il cane.»",
        "p002.2 narration disse Bruno.",
    ]


def test_segments_pending_lists_unassigned_speech(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    annotate(cfg, project, {"p002.1": {"speaker": "bruno"}})
    capsys.readouterr()
    assert run(cfg, project, "segments", "--pending") == 0
    assert capsys.readouterr().out.splitlines() == [
        "p003.1 speech «Fino al parco?»",
    ]


def test_annotate_from_stdin_and_rejection(
    project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str], monkeypatch
) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("p002.1: {speaker: bruno}\n"))
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

    annotate(cfg, project, {"p002.1": {"speaker": "bruno"}, "p003.1": {"speaker": "anna"}})
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
            "p002.1": {"speaker": "bruno", "register": "whisper"},
            "p002.2": {"attribution": True},
            "p003.1": {"speaker": "anna", "tts_text": "[sigh] Fino al parco?"},
            "p003.2": {"skip": True},
        },
    )
    (project / "cast.yaml").write_text(yaml.safe_dump(CAST), encoding="utf-8")
    capsys.readouterr()
    assert run(cfg, project, "show") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split(None, 2) == ["p001.1", "NARRATORE", "Anna aprì la finestra."]
    assert "BRUNO (whisper)" in lines[1]
    assert "(attribution)" in lines[2]
    assert lines[3].endswith("[sigh] Fino al parco?")
    assert lines[4] == "          text: «Fino al parco?»"
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
    annotate(cfg, project, {"p002.1": {"speaker": "bruno"}})
    capsys.readouterr()
    assert run(cfg, project, "stats") == 0
    out = capsys.readouterr().out
    assert "bruno" in out
    assert "(narration)" in out
    assert "unassigned speech: 1 line" in out


def test_render_uses_the_provider_and_reports_progress(
    project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotate(cfg, project, {"p002.1": {"speaker": "bruno"}, "p003.1": {"speaker": "anna"}})
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


# --- review (roadmap 8.5) ---------------------------------------------------------------------


class Keys:
    """A review terminal for the CLI tests: scripted keys, collected output."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.out: list[str] = []

    def __enter__(self) -> Keys:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def say(self, text: str) -> None:
        self.out.append(text)

    async def key(self) -> str:
        key = self.keys.pop(0)
        if key == "SIGINT":
            raise KeyboardInterrupt
        return key

    def edit(self, text: str) -> str | None:  # pragma: no cover - not used by these tests
        return None


@pytest.fixture
def review_project(
    project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    from .conftest import FakePlayer

    annotate(cfg, project, {"p002.1": {"speaker": "bruno"}, "p003.1": {"speaker": "anna"}})
    (project / "cast.yaml").write_text(yaml.safe_dump(CAST), encoding="utf-8")
    monkeypatch.setattr(commands, "make_provider", lambda _settings: FakeProvider())
    monkeypatch.setattr(commands, "make_player", lambda _settings: FakePlayer())
    monkeypatch.setattr(commands, "stdin_is_tty", lambda: True)
    capsys.readouterr()
    return project


def test_review_needs_a_terminal(
    project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(cfg, project, "review") == 1
    assert "interactive terminal" in capsys.readouterr().err


def test_review_runs_a_session_and_prints_a_summary(
    review_project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys = Keys(["enter", "q"])
    monkeypatch.setattr(commands, "make_review_terminal", lambda _work: keys)
    assert run(cfg, review_project, "review") == 0
    out = capsys.readouterr().out
    assert "Approved 1/5 lines" in out
    resume = out.split("Resume with: ", 1)[1].strip()
    assert shlex.split(resume) == ["voxello", "dialogue", "review", "-p", str(review_project)]
    assert any(line.startswith("[1/5] p001.1") for line in keys.out)


def test_review_flags_reach_the_session(
    review_project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys = Keys(["q"])
    monkeypatch.setattr(commands, "make_review_terminal", lambda _work: keys)
    assert run(cfg, review_project, "review", "--from", "p003.1", "--pending", "--flagged") == 0
    # FakeProvider takes are silent, so every line is flagged; --from picks p003.1 among them
    assert keys.out[1].startswith("[4/5] p003.1")


def test_review_finished(
    review_project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keys = Keys(["enter"] * 5)
    monkeypatch.setattr(commands, "make_review_terminal", lambda _work: keys)
    assert run(cfg, review_project, "review") == 0
    assert "Review finished: approved 5/5 lines" in capsys.readouterr().out


@pytest.mark.parametrize("key", ["ctrl-c", "SIGINT"])
def test_review_interrupted(
    review_project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    key: str,
) -> None:
    keys = Keys(["n", key])
    monkeypatch.setattr(commands, "make_review_terminal", lambda _work: keys)
    assert run(cfg, review_project, "review") == 130
    assert "Review saved at p002.1" in capsys.readouterr().out
    state = json.loads((review_project / "state.json").read_text())
    assert state["review"]["position"] == "p002.1"


# --- the console terminal ------------------------------------------------------------------


@pytest.fixture
def pty_terminal(tmp_path: Path):  # type: ignore[no-untyped-def]
    if sys.platform == "win32":
        pytest.skip("POSIX pseudo-terminals are unavailable on Windows")
    import os
    import pty

    master, slave = pty.openpty()
    terminal = commands.ConsoleReviewTerminal(tmp_path, fd=slave)
    try:
        with terminal:
            yield terminal, master
    finally:
        os.close(master)
        os.close(slave)


async def test_console_terminal_reads_single_keys(pty_terminal) -> None:  # type: ignore[no-untyped-def]
    import os

    terminal, master = pty_terminal
    os.write(master, b"r")
    assert await terminal.key() == "r"
    os.write(master, b"\x1b[C")
    assert await terminal.key() == "n"


def test_console_terminal_edits_with_the_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shlex
    import sys

    script = tmp_path / "editor.py"
    script.write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "p.write_text(p.read_text(encoding='utf-8').upper(), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VISUAL", f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")
    terminal = commands.ConsoleReviewTerminal(tmp_path, fd=None)
    assert terminal.edit("ciao è") == "CIAO È\n"
    monkeypatch.setenv("VISUAL", f"{shlex.quote(sys.executable)} -c 'raise SystemExit(3)'")
    assert terminal.edit("ciao") is None
    monkeypatch.setenv("VISUAL", "/nonexistent/editor")
    assert terminal.edit("ciao") is None


@pytest.mark.parametrize(
    ("env", "platform", "expected"),
    [
        ({"VISUAL": "code -w", "EDITOR": "nano"}, "darwin", ["code", "-w"]),
        ({"EDITOR": "nano"}, "linux", ["nano"]),
        ({}, "darwin", ["vi"]),
        ({}, "win32", ["notepad"]),
    ],
)
def test_editor_command(env: dict[str, str], platform: str, expected: list[str]) -> None:
    assert commands.editor_command(env, platform) == expected


async def test_windows_keys_poll_msvcrt() -> None:
    class FakeMsvcrt:
        def __init__(self, chars: str) -> None:
            self.chars = list(chars)
            self.polls = 0

        def kbhit(self) -> bool:
            self.polls += 1
            return self.polls > 1

        def getwch(self) -> str:
            return self.chars.pop(0)

    assert await commands.windows_key(FakeMsvcrt("q"), interval=0) == "q"
    assert await commands.windows_key(FakeMsvcrt("\xe0M"), interval=0) == "n"
    assert await commands.windows_key(FakeMsvcrt("\r"), interval=0) == "enter"


def test_make_player_uses_the_detected_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from voxello.config import Settings
    from voxello.playback import detect
    from voxello.playback.backends import ALL_BACKENDS

    monkeypatch.setattr(detect, "detect_backend", lambda _preferred: None)
    assert commands.make_player(Settings()) is None
    monkeypatch.setattr(detect, "detect_backend", lambda _preferred: ALL_BACKENDS["afplay"])
    player = commands.make_player(Settings())
    assert player is not None and player.name == "afplay"


def test_make_review_terminal_reads_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Stdin:
        def fileno(self) -> int:
            return 7

    monkeypatch.setattr("sys.stdin", Stdin())
    terminal = commands.make_review_terminal(tmp_path)
    assert isinstance(terminal, commands.ConsoleReviewTerminal)
    assert terminal.fd == (None if sys.platform == "win32" else 7)


def test_console_terminal_restores_cbreak_after_the_editor(
    pty_terminal,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import termios

    terminal, _master = pty_terminal
    line_mode = termios.ICANON | termios.ECHO
    assert termios.tcgetattr(terminal.fd)[3] & line_mode == 0
    monkeypatch.setenv("VISUAL", "true")
    assert terminal.edit("ciao") == "ciao\n"
    # back in cbreak (macOS may add PENDIN to the local flags, so only these bits are compared)
    assert termios.tcgetattr(terminal.fd)[3] & line_mode == 0
    terminal.say("hello")
    assert capsys.readouterr().out == "hello\n"


async def test_console_terminal_waits_for_a_key(pty_terminal) -> None:  # type: ignore[no-untyped-def]
    import asyncio
    import os

    terminal, master = pty_terminal
    loop = asyncio.get_running_loop()
    loop.call_later(0.1, os.write, master, b"\r")
    assert await asyncio.wait_for(terminal.key(), 2) == "enter"


# --- play and export (roadmap 8.6) -------------------------------------------------------------


class InstantPlayer:
    """Every take ends at once; ``interrupt_at`` raises Ctrl-C when that take starts."""

    name = "instant"

    def __init__(self, interrupt_at: str | None = None) -> None:
        self.interrupt_at = interrupt_at
        self.paths: list[Path] = []

    async def play(self, path: Path):  # type: ignore[no-untyped-def]
        from .conftest import FakeHandle

        if path.parent.name == self.interrupt_at:
            raise KeyboardInterrupt
        self.paths.append(path)
        handle = FakeHandle(path)
        handle.finish()
        return handle


@pytest.fixture
def rendered_project(
    review_project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> Path:
    assert run(cfg, review_project, "render") == 0
    # pauses are not part of a take; without them `play` does not sleep in these tests
    cast = {
        **CAST,
        "pauses_ms": dict.fromkeys(("same_speaker", "speaker_change", "paragraph", "break"), 0),
    }
    (review_project / "cast.yaml").write_text(yaml.safe_dump(cast), encoding="utf-8")
    capsys.readouterr()
    return review_project


def test_play_prints_each_line_and_a_summary(
    rendered_project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    player = InstantPlayer()
    monkeypatch.setattr(commands, "make_player", lambda _settings: player)
    import shutil

    shutil.rmtree(rendered_project / "takes" / "p003.2")
    assert run(cfg, rendered_project, "play", "--from", "p002.1") == 0
    out = capsys.readouterr().out
    assert "[2/5] p002.1  BRUNO  Porto fuori il cane.  (not approved)" in out
    assert "[5/5] p003.2  no take: render it first" in out
    assert "Played 3 lines (3 not approved, 1 without a take)." in out
    assert [p.parent.name for p in player.paths] == ["p002.1", "p002.2", "p003.1"]


def test_play_interrupted_offers_review_at_that_line(
    rendered_project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .conftest import FakePlayer

    players = [InstantPlayer("p002.2"), FakePlayer()]  # play, then review
    monkeypatch.setattr(commands, "make_player", lambda _settings: players.pop(0))
    monkeypatch.setattr(commands, "ask", lambda _prompt: "y")
    keys = Keys(["q"])
    monkeypatch.setattr(commands, "make_review_terminal", lambda _work: keys)
    assert run(cfg, rendered_project, "play") == 0
    assert "Stopped at p002.2." in capsys.readouterr().out
    assert keys.out[0].startswith("[3/5] p002.2")


def test_play_interrupted_without_review(
    rendered_project: Path,
    cfg: list[str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commands, "make_player", lambda _settings: InstantPlayer("p002.2"))
    monkeypatch.setattr(commands, "ask", lambda _prompt: "")
    assert run(cfg, rendered_project, "play") == 130
    resume = capsys.readouterr().out.split("Review it with: ", 1)[1].strip()
    assert shlex.split(resume) == [
        "voxello",
        "dialogue",
        "review",
        "-p",
        str(rendered_project),
        "--from",
        "p002.2",
    ]


def test_play_interrupted_before_the_first_line(
    rendered_project: Path, cfg: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def interrupted(_self: object, _start: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(commands.PlayThrough, "run", interrupted)
    assert run(cfg, rendered_project, "play") == 130


def test_ask_treats_end_of_input_as_no(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert commands.ask("? ") == ""
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert commands.ask("? ") == "y"


def test_export_refuses_unapproved_lines(
    rendered_project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(cfg, rendered_project, "export") == 1
    err = capsys.readouterr().err
    assert "dialogue_not_ready" in err
    assert "5 lines have no approved take" in err
    assert not (rendered_project / "final.wav").exists()


def test_export_writes_the_files(
    rendered_project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    output = tmp_path / "book.wav"
    args = ["export", "-o", str(output), "--srt", "--allow-unapproved", "--no-level"]
    assert run(cfg, rendered_project, *args) == 0
    captured = capsys.readouterr()
    assert "warning: 5 lines not approved" in captured.err
    assert f"Exported 5 lines (2.5 s) to {output}" in captured.out  # 5 takes of 0.5 s
    assert f"Timeline: {tmp_path / 'book.timeline.json'}" in captured.out
    assert f"Subtitles: {tmp_path / 'book.srt'}" in captured.out
    assert output.is_file()


def test_export_approved_lines_without_subtitles(
    rendered_project: Path, cfg: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    state = json.loads((rendered_project / "state.json").read_text())
    for line in state["lines"].values():
        line["approved"] = line["takes"][-1]["fingerprint"]
    (rendered_project / "state.json").write_text(json.dumps(state))
    assert run(cfg, rendered_project, "export") == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Subtitles" not in captured.out
    assert (rendered_project / "final.wav").is_file()
