"""`dialogue status`: where a project stands and the next command (roadmap 8.7)."""

from __future__ import annotations

import os
import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import FakeProvider
from voxello.dialogue.export import export
from voxello.dialogue.project import Project
from voxello.dialogue.render import render
from voxello.dialogue.status import format_status, project_status

SOURCE = """Anna aprì la finestra.

«Porto fuori il cane.» disse Bruno.

«Fino al parco?»
"""

CAST: dict[str, Any] = {
    "language": "it",
    "narrator": "narratore",
    "characters": {
        "narratore": {"voice": "onyx"},
        "bruno": {"voice": "clone:bruno"},
        "anna": {"voice": "clone:anna"},
    },
}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "chapter.txt"
    source.write_text(SOURCE, encoding="utf-8")
    return Project.init(source, tmp_path / "chapter")


def annotated(project: Project) -> Project:
    project.annotate({"p002.1": {"speaker": "bruno"}, "p003.1": {"speaker": "anna"}})
    return project


def with_cast(project: Project) -> Project:
    project.cast_path.write_text(yaml.safe_dump(CAST, sort_keys=False), encoding="utf-8")
    return project


def approve_all(project: Project) -> None:
    state = project.load_state()
    for line in state.lines.values():
        line.approved = line.takes[-1].fingerprint
    project.save_state(state)


def next_commands(project: Project) -> list[str]:
    return [" ".join(step.command) for step in project_status(project).next]


def test_a_new_project_needs_speakers(project: Project) -> None:
    status = project_status(project)
    assert (status.segments, status.speech, status.unassigned) == (4, 2, 2)
    assert status.characters is None
    assert status.errors == []  # counted in `unassigned`
    assert next_commands(project) == ["dialogue segments --pending"]
    assert "2 speech segments without a speaker" in status.next[0].why


def test_then_the_cast(project: Project) -> None:
    annotated(project)
    assert next_commands(project) == ["dialogue cast --write"]


def test_then_check_errors(project: Project) -> None:
    annotated(project)
    project.cast_path.write_text(
        yaml.safe_dump({**CAST, "characters": {"narratore": {"voice": "onyx"}}}), encoding="utf-8"
    )
    status = project_status(project)
    assert next_commands(project) == ["dialogue check"]
    assert any("speaker 'bruno' is not in cast.yaml" in error for error in status.errors)


async def test_then_render_review_and_export(project: Project) -> None:
    with_cast(annotated(project))
    status = project_status(project)
    assert status.characters == ["narratore", "bruno", "anna"]
    assert (status.lines, status.rendered, status.approved) == (4, 0, 0)
    assert next_commands(project) == ["dialogue render"]

    await render(project, FakeProvider(), lines="p001.1")
    assert project_status(project).rendered == 1
    assert next_commands(project) == ["dialogue render"]

    await render(project, FakeProvider())
    steps = project_status(project).next
    assert [" ".join(s.command) for s in steps] == ["dialogue review --pending"]
    assert steps[0].interactive

    approve_all(project)
    steps = project_status(project).next
    assert [" ".join(s.command) for s in steps] == ["dialogue play", "dialogue export --srt"]
    assert [s.interactive for s in steps] == [True, False]

    export(project)
    status = project_status(project)
    assert status.exported
    assert status.next == []
    # a later change to the project makes the export old again
    state = project.state_path
    later = project.root.joinpath("final.wav").stat().st_mtime + 10
    os.utime(state, (later, later))
    assert not project_status(project).exported


async def test_format_shows_the_numbers_and_the_next_command(project: Project) -> None:
    with_cast(annotated(project))
    await render(project, FakeProvider())
    text = "\n".join(format_status(project_status(project), project.root))
    assert "Segments   4 (2 speech, every one with a speaker)" in text
    assert "Cast       3 characters: narratore, bruno, anna" in text
    assert "Check      OK" in text
    assert "Takes      4/4 lines rendered, 0 approved" in text
    next_line = next(line for line in text.splitlines() if line.startswith("Next"))
    assert shlex.split(next_line.removeprefix("Next")) == [
        "voxello",
        "dialogue",
        "review",
        "-p",
        str(project.root),
        "--pending",
    ]
    assert "your own terminal" in text


def test_format_before_the_cast(project: Project) -> None:
    text = "\n".join(format_status(project_status(project), project.root))
    assert "Segments   4 (2 speech, 2 without a speaker)" in text
    assert "Cast       missing" in text
    assert "Takes" not in text


def test_format_when_done(project: Project, tmp_path: Path) -> None:
    done = replace(project_status(project), next=[], exported=True)
    text = "\n".join(format_status(done, project.root))
    assert "Done: final.wav is up to date" in text


def test_format_lists_check_errors(project: Project) -> None:
    annotated(project)
    project.cast_path.write_text(
        yaml.safe_dump({**CAST, "characters": {"narratore": {"voice": "onyx"}}}), encoding="utf-8"
    )
    text = "\n".join(format_status(project_status(project), project.root))
    assert "Check      2 errors" in text
    assert "  p002.1: speaker 'bruno' is not in cast.yaml" in text
