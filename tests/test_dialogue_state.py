"""state.json additions for review (roadmap 8.5): chosen take, position, stored measures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import make_wav
from voxello.dialogue import qa
from voxello.dialogue.project import Project
from voxello.dialogue.takes import LineRequest, LineState, State, Take

SOURCE = """Francesca cercò i suoi occhi.

«Metto il telefono in carica.»
"""

CAST: dict[str, Any] = {
    "language": "it",
    "narrator": "narratore",
    "characters": {
        "narratore": {"voice": "onyx"},
        "marco": {"voice": "clone:marco", "registers": {"whisper": "clone:marco-whisper"}},
    },
}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "chapter.txt"
    source.write_text(SOURCE, encoding="utf-8")
    project = Project.init(source, tmp_path / "chapter")
    project.annotate({"p002.1": {"speaker": "marco"}})
    project.cast_path.write_text(yaml.safe_dump(CAST), encoding="utf-8")
    return project


def request(seed: int = 1) -> LineRequest:
    return LineRequest("Ciao.", "clone:marco", None, "it", None, 16, seed, None)


def test_an_older_state_file_loads_with_the_new_fields_empty(project: Project) -> None:
    project.state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "lines": {
                    "p001.1": {
                        "approved": None,
                        "takes": [{"fingerprint": "ab", "seed": 1, "created": "now"}],
                    }
                },
            }
        )
    )
    state = project.load_state()
    assert state.review.position is None
    assert state.lines["p001.1"].selected is None
    assert state.lines["p001.1"].takes[0].qa is None


def test_store_take_measures_the_audio(project: Project) -> None:
    state = State()
    take = project.store_take(state, "p001.1", request(), make_wav(800))
    assert take.qa is not None
    assert take.qa.duration_ms == 800
    project.save_state(state)
    assert project.load_state().lines["p001.1"].takes[0].qa == take.qa


def test_ensure_measures_fills_missing_or_outdated_measures(project: Project) -> None:
    state = State()
    take = project.store_take(state, "p001.1", request(), make_wav(300))
    take.qa = None
    assert project.ensure_measures("p001.1", take) is True
    assert take.qa is not None and take.qa.duration_ms == 300
    assert project.ensure_measures("p001.1", take) is False
    take.qa = take.qa.model_copy(update={"version": qa.QA_VERSION - 1})
    assert project.ensure_measures("p001.1", take) is True
    assert take.qa.version == qa.QA_VERSION


def test_ensure_measures_of_a_missing_file_is_a_no_op(project: Project) -> None:
    take = Take(fingerprint="f" * 64, seed=1, created="now")
    assert project.ensure_measures("p001.1", take) is False
    assert take.qa is None


def test_chosen_take_is_the_selected_one_or_the_latest() -> None:
    first = Take(fingerprint="a", seed=1, created="now")
    second = Take(fingerprint="b", seed=2, created="now")
    line = LineState(takes=[first, second])
    assert line.chosen([first, second]) is second
    line.selected = "a"
    assert line.chosen([first, second]) is first
    assert line.chosen([second]) is second  # the selected take is no longer current
    assert line.chosen([]) is None


def test_edit_line_saves_valid_changes(project: Project) -> None:
    assert project.edit_line("p002.1", {"register": "whisper", "speed": 0.95}) == []
    seg = project.load_script().segment("p002.1")
    assert seg.register_ == "whisper"
    assert seg.speed == 0.95


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"tts_text": "[shout] Metto il telefono."}, "tag [shout] is not allowed"),
        ({"tts_text": "   "}, "tts_text is empty"),
        ({"speed": 9.0}, "speed"),
        ({"text": "changed"}, "`text` cannot change"),
    ],
)
def test_edit_line_rejects_changes_that_break_the_line(
    project: Project, fields: dict[str, Any], message: str
) -> None:
    before = project.script_path.read_bytes()
    problems = project.edit_line("p002.1", fields)
    assert any(message in problem for problem in problems), problems
    assert project.script_path.read_bytes() == before
