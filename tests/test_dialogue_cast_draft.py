"""A draft `cast.yaml` from the annotated script and the server's profiles (roadmap 8.7)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from voxello.dialogue.cast import Cast
from voxello.dialogue.cast_draft import draft_cast
from voxello.dialogue.project import Project

SOURCE = """Anna aprì la finestra.

«Porto fuori il cane.» disse Bruno.

«Fino al parco?» sussurrò Anna.

«Sì.» «Torno presto.»

«Ciao.»

---
"""


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "chapter.txt"
    source.write_text(SOURCE, encoding="utf-8")
    project = Project.init(source, tmp_path / "chapter")
    project.annotate(
        {
            "p002.1": {"speaker": "bruno"},
            "p003.1": {"speaker": "anna", "register": "whisper"},
            "p004.1": {"speaker": "bruno", "register": "happy"},
            "p004.2": {"speaker": "bruno"},
        }
    )
    return project


def test_matches_profiles_by_name_and_register(project: Project) -> None:
    draft = draft_cast(
        project.load_script(),
        language="it",
        narrator="narratore",
        profiles={"narratore", "bruno", "bruno-happy", "bruno-sad", "anna", "other"},
        designs={},
    )
    cast = Cast.from_yaml(draft.text)
    assert cast.language == "it"
    assert cast.narrator == "narratore"
    assert list(cast.characters) == ["narratore", "bruno", "anna"]  # narrator, then by lines
    assert cast.characters["bruno"].voice == "clone:bruno"
    registers = {r: s.voice for r, s in cast.characters["bruno"].registers.items()}
    assert registers == {"happy": "clone:bruno-happy", "sad": "clone:bruno-sad"}
    assert cast.characters["anna"].registers == {}
    assert draft.missing_voices == []
    # anna whispers in one line but has no whisper profile: the main voice reads it
    assert draft.missing_registers == [("anna", "whisper", 1)]
    assert "voxello voice record anna --register whisper" in draft.text
    assert draft.unassigned == 1  # p005.1 has no speaker yet
    assert "1 speech line has no speaker yet" in draft.text


def test_the_narrator_gets_its_registers_even_if_no_line_uses_them(project: Project) -> None:
    draft = draft_cast(
        project.load_script(),
        language="it",
        narrator="narratore",
        profiles={"narratore", "narratore-whisper", "bruno", "anna"},
        designs={},
    )
    narrator = Cast.from_yaml(draft.text).characters["narratore"]
    assert narrator.voice == "clone:narratore"
    assert {r: s.voice for r, s in narrator.registers.items()} == {
        "whisper": "clone:narratore-whisper"
    }


def test_missing_voices_point_at_the_expected_profile(project: Project) -> None:
    draft = draft_cast(
        project.load_script(), language="en", narrator="narrator", profiles=set(), designs={}
    )
    cast = Cast.from_yaml(draft.text)
    assert cast.characters["anna"].voice == "clone:anna"
    assert draft.missing_voices == ["narrator", "bruno", "anna"]
    assert "TODO: no clone:anna on the server" in draft.text
    assert "voxello voice record anna" in draft.text


def test_without_a_server_answer_nothing_is_called_missing(project: Project) -> None:
    draft = draft_cast(
        project.load_script(), language="it", narrator="narratore", profiles=None, designs={}
    )
    cast = Cast.from_yaml(draft.text)
    assert cast.characters["bruno"].voice == "clone:bruno"
    assert draft.missing_voices == []
    assert "cloned voices were not listed" in draft.text


def test_a_voice_design_with_the_character_name_is_used(project: Project) -> None:
    draft = draft_cast(
        project.load_script(),
        language="it",
        narrator="narratore",
        profiles={"bruno", "anna"},
        designs={"narratore": "male, middle-aged, low pitch"},
    )
    cast = Cast.from_yaml(draft.text)
    assert cast.characters["narratore"].voice == "narratore"
    assert "speech.voice_designs" in draft.text
    assert draft.missing_voices == []


def test_the_draft_spells_out_the_defaults(project: Project) -> None:
    text = draft_cast(
        project.load_script(), language="it", narrator="narratore", profiles=set(), designs={}
    ).text
    data = yaml.safe_load(text)
    assert data["attributions"] == "keep"
    assert data["generation"] == {"seed": 1, "num_step": 16}
    assert data["pauses_ms"] == {
        "same_speaker": 250,
        "speaker_change": 450,
        "paragraph": 700,
        "break": 1500,
    }
