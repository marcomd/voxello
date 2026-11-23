"""script.yaml / cast.yaml models, annotation merge and `check` (roadmap 8.3)."""

from __future__ import annotations

from typing import Any

import pytest

from voxello.core.models import VoiceChoice
from voxello.dialogue.cast import Cast, resolve_line_voice
from voxello.dialogue.script import (
    ALLOWED_TAGS,
    Script,
    apply_annotations,
    check,
    effective_tts_text,
    new_script,
    select_range,
    speaker_of,
)
from voxello.errors import INVALID_INSTRUCTIONS, INVALID_SCRIPT, VoxelloError

SOURCE = """Anna aprì la finestra.

«Porto fuori il cane.» disse Bruno.

«Fino al parco?» domandò Anna.

---

#### Scena 2
"""


def cast_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "language": "it",
        "narrator": "narratore",
        "characters": {
            "narratore": {"voice": "onyx"},
            "bruno": {
                "voice": "clone:bruno",
                "speed": 0.95,
                "registers": {
                    "whisper": "clone:bruno-whisper",
                    "sad": {"instructions": "Male, Low Pitch"},
                },
            },
            "anna": {"instructions": "female, young adult"},
        },
    }
    data.update(overrides)
    return data


@pytest.fixture
def script() -> Script:
    return new_script(SOURCE)


@pytest.fixture
def cast() -> Cast:
    return Cast.model_validate(cast_data())


def annotated(script: Script) -> Script:
    return apply_annotations(
        script,
        {"p002.1": {"speaker": "bruno"}, "p002.2": {"attribution": True}},
    ).script


def by_id(script: Script, seg_id: str):
    return next(s for s in script.segments if s.id == seg_id)


# --- new_script -----------------------------------------------------------------------------


def test_new_script_records_segments_hash_and_threshold(script: Script) -> None:
    assert [s.id for s in script.segments] == [
        "p001.1",
        "p002.1",
        "p002.2",
        "p003.1",
        "p003.2",
        "p004.1",
        "p005.1",
    ]
    assert script.max_chars == 300
    assert len(script.source_sha256) == 64
    assert by_id(script, "p002.1").speaker is None


def test_script_yaml_round_trip_omits_defaults(script: Script) -> None:
    script = annotated(script)
    text = script.to_yaml()
    assert "register" not in text  # defaults stay out of the file
    assert "speaker: bruno" in text
    assert "«Porto fuori il cane.»" in text  # unicode kept readable
    assert Script.from_yaml(text) == script


def test_from_yaml_rejects_unknown_fields() -> None:
    with pytest.raises(VoxelloError) as err:
        Script.from_yaml("version: 1\nsource_sha256: x\nmax_chars: 300\nsegments: []\nfoo: 1\n")
    assert err.value.code == INVALID_SCRIPT
    with pytest.raises(VoxelloError):
        Script.from_yaml("- not a mapping")


# --- annotations ----------------------------------------------------------------------------


def test_annotations_merge_by_id_and_null_resets(script: Script) -> None:
    result = apply_annotations(
        script,
        {
            "p002.1": {"speaker": "bruno", "register": "whisper", "speed": 0.9},
            "p002.2": {"attribution": True},
        },
    )
    assert result.changed == ["p002.1", "p002.2"]
    seg = by_id(result.script, "p002.1")
    assert (seg.speaker, seg.register_, seg.speed) == ("bruno", "whisper", 0.9)
    reset = apply_annotations(result.script, {"p002.1": {"register": None, "speed": None}})
    seg = by_id(reset.script, "p002.1")
    assert (seg.speaker, seg.register_, seg.speed) == ("bruno", None, None)
    assert by_id(script, "p002.1").speaker is None  # the input is not mutated


def test_annotation_with_identical_text_is_accepted(script: Script) -> None:
    text = by_id(script, "p002.2").text
    result = apply_annotations(script, {"p002.2": {"text": text, "attribution": True}})
    assert result.changed == ["p002.2"]


def test_unchanged_annotation_is_not_reported(script: Script) -> None:
    assert apply_annotations(script, {"p002.2": {"attribution": False}}).changed == []


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"p099.1": {"speaker": "bruno"}}, "p099.1: unknown segment id"),
        ({"p002.1": {"text": "«Altro.»"}}, "p002.1: `text` cannot change"),
        ({"p002.1": {"kind": "narration"}}, "p002.1: `kind` cannot be annotated"),
        ({"p002.1": {"colour": "red"}}, "p002.1: unknown field `colour`"),
        ({"p002.1": {"speed": 9}}, "p002.1: speed"),
        ({"p002.1": "bruno"}, "p002.1: expected a mapping"),
        (["p002.1"], "a mapping of segment ids"),
    ],
)
def test_bad_annotations_are_rejected_whole(script: Script, data: object, fragment: str) -> None:
    with pytest.raises(VoxelloError) as err:
        apply_annotations(script, data)
    assert err.value.code == INVALID_SCRIPT
    assert fragment in err.value.message


def test_one_bad_entry_rejects_the_whole_file(script: Script) -> None:
    with pytest.raises(VoxelloError) as err:
        apply_annotations(script, {"p002.1": {"speaker": "bruno"}, "p099.1": {}})
    assert "p099.1" in err.value.message


# --- effective values -----------------------------------------------------------------------


def test_effective_tts_text_and_speaker(script: Script, cast: Cast) -> None:
    script = annotated(script)
    assert effective_tts_text(by_id(script, "p002.1")) == "Porto fuori il cane."
    assert effective_tts_text(by_id(script, "p005.1")) == "Scena 2"
    assert speaker_of(by_id(script, "p002.1"), cast) == "bruno"
    assert speaker_of(by_id(script, "p001.1"), cast) == "narratore"
    assert speaker_of(by_id(script, "p005.1"), cast) == "narratore"
    assert speaker_of(by_id(script, "p004.1"), cast) is None  # breaks are silent
    assert speaker_of(by_id(script, "p003.1"), cast) is None  # unassigned speech


# --- voices ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("speaker", "register", "expected"),
    [
        ("bruno", None, VoiceChoice("clone:bruno", None)),
        ("bruno", "whisper", VoiceChoice("clone:bruno-whisper", None)),
        ("bruno", "angry", VoiceChoice("clone:bruno", None)),  # missing register: main voice
        ("bruno", "sad", VoiceChoice(None, "male, low pitch")),
        ("anna", None, VoiceChoice(None, "female, young adult")),
        ("narratore", None, VoiceChoice(None, "male, elderly")),  # a design name from config
    ],
)
def test_resolve_line_voice(speaker: str, register: str | None, expected: VoiceChoice) -> None:
    data = cast_data()
    data["characters"]["narratore"] = {"voice": "old-man"}
    cast = Cast.model_validate(data)
    designs = {"old-man": "male, elderly"}
    assert resolve_line_voice(cast, speaker, register, designs) == expected


def test_resolve_line_voice_rejects_bad_instructions() -> None:
    data = cast_data()
    data["characters"]["anna"] = {"instructions": "female, purple"}
    cast = Cast.model_validate(data)
    with pytest.raises(VoxelloError) as err:
        resolve_line_voice(cast, "anna", None, {})
    assert err.value.code == INVALID_INSTRUCTIONS


# --- cast validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"narrator": "nessuno"}, "narrator 'nessuno' is not a character"),
        ({"characters": {"narratore": {}}}, "exactly one of `voice` or `instructions`"),
        (
            {"characters": {"narratore": {"voice": "x", "instructions": "male"}}},
            "exactly one of `voice` or `instructions`",
        ),
        ({"characters": {"Narratore!": {"voice": "x"}}, "narrator": "Narratore!"}, "name"),
        ({"language": "ita"}, "language"),
        ({"attributions": "maybe"}, "attributions"),
        ({"generation": {"seed": -1}}, "seed"),
    ],
)
def test_cast_validation(change: dict[str, Any], fragment: str) -> None:
    with pytest.raises(VoxelloError) as err:
        Cast.from_yaml_data(cast_data(**change))
    assert err.value.code == INVALID_SCRIPT
    assert fragment in err.value.message


def test_cast_defaults() -> None:
    cast = Cast.from_yaml_data(cast_data())
    assert cast.attributions == "keep"
    assert (cast.generation.seed, cast.generation.num_step, cast.generation.speed) == (1, 16, None)
    assert cast.pauses_ms.model_dump(by_alias=True) == {
        "same_speaker": 250,
        "speaker_change": 450,
        "paragraph": 700,
        "break": 1500,
    }


# --- check ----------------------------------------------------------------------------------


def messages(issues: list[Any]) -> list[str]:
    return [f"{i.segment_id}: {i.message}" if i.segment_id else i.message for i in issues]


def test_check_passes_on_a_complete_project(script: Script, cast: Cast) -> None:
    script = apply_annotations(
        annotated(script),
        {"p003.1": {"speaker": "anna", "tts_text": "[question-ah] Fino al parco?"}},
    ).script
    report = check(script, SOURCE, cast, {})
    assert report.errors == []
    assert report.warnings == []
    assert report.ok


def test_check_without_cast_only_checks_structure(script: Script) -> None:
    report = check(script, SOURCE, None, {})
    assert messages(report.errors) == [
        "p002.1: speech has no speaker",
        "p003.1: speech has no speaker",
    ]
    assert "cast.yaml" in messages(report.warnings)[0]


def test_check_detects_a_changed_source(script: Script) -> None:
    report = check(script, SOURCE.replace("finestra", "porta"), None, {})
    assert any("source.txt changed" in m for m in messages(report.errors))


def test_check_proves_the_rebuild(script: Script) -> None:
    broken = script.model_copy(deep=True)
    broken.segments[0].text = "Anna aprì la."  # edited by hand in script.yaml
    report = check(broken, SOURCE, None, {})
    assert "p001.1: the segments do not rebuild source.txt from here" in messages(report.errors)
    dropped = script.model_copy(deep=True)
    del dropped.segments[1]
    report = check(dropped, SOURCE, None, {})
    assert "p002.2: the segments do not rebuild source.txt from here" in messages(report.errors)
    truncated = script.model_copy(deep=True)
    del truncated.segments[-1]
    report = check(truncated, SOURCE, None, {})
    assert "the segments end before source.txt does" in messages(report.errors)


def test_check_reports_duplicate_ids(script: Script) -> None:
    script.segments[1].id = "p001.1"
    assert "p001.1: duplicate id" in messages(check(script, SOURCE, None, {}).errors)


def test_check_cast_rules(script: Script, cast: Cast) -> None:
    script = apply_annotations(
        script,
        {
            "p001.1": {"attribution": False, "tts_text": "  "},
            "p002.1": {"speaker": "giulia"},
            "p002.2": {"register": "whisper", "speaker": "bruno"},
            "p003.1": {"speaker": "bruno", "register": "angry", "attribution": True},
        },
    ).script
    report = check(script, SOURCE, cast, {})
    assert messages(report.errors) == [
        "p001.1: tts_text is empty",
        "p002.1: speaker 'giulia' is not in cast.yaml",
        "p003.1: attribution applies to narration only",
    ]
    assert messages(report.warnings) == [
        "p003.1: register 'angry' is not in the cast for bruno; the main voice is used",
    ]


def test_check_tags(script: Script, cast: Cast) -> None:
    script = apply_annotations(
        annotated(script),
        {
            "p001.1": {"tts_text": "[sigh] [laughter] Anna aprì la finestra."},
            "p002.1": {"tts_text": "Porto [sigh] fuori il cane."},
            "p003.1": {"speaker": "anna", "tts_text": "[cough] Fino al parco? [1]"},
        },
    ).script
    report = check(script, SOURCE, cast, {})
    assert messages(report.errors) == [
        "p003.1: tag [cough] is not allowed; use one of " + ", ".join(sorted(ALLOWED_TAGS)),
    ]
    assert messages(report.warnings) == [
        "p002.1: tag [sigh] is not at the start of the line, where it is most reliable",
    ]


def test_check_reports_unresolvable_voices(script: Script) -> None:
    data = cast_data()
    data["characters"]["anna"] = {"instructions": "female, purple"}
    cast = Cast.model_validate(data)
    script = apply_annotations(annotated(script), {"p003.1": {"speaker": "anna"}}).script
    report = check(script, SOURCE, cast, {})
    assert any(m.startswith("anna: ") for m in messages(report.errors))


def test_check_skipped_lines_need_no_text(script: Script, cast: Cast) -> None:
    script = apply_annotations(
        annotated(script),
        {"p003.1": {"skip": True}, "p005.1": {"tts_text": "", "skip": True}},
    ).script
    assert check(script, SOURCE, cast, {}).errors == []


# --- ranges ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (None, ["p001.1", "p002.1", "p002.2", "p003.1", "p003.2", "p004.1", "p005.1"]),
        ("p002.2", ["p002.2"]),
        ("p002.2-p003.2", ["p002.2", "p003.1", "p003.2"]),
        ("p004.1-", ["p004.1", "p005.1"]),
        ("-p001.1", ["p001.1"]),
    ],
)
def test_select_range(script: Script, spec: str | None, expected: list[str]) -> None:
    assert [s.id for s in select_range(script, spec)] == expected


@pytest.mark.parametrize("spec", ["p009.1", "p003.1-p001.1", "p001.1-p009.9"])
def test_select_range_errors(script: Script, spec: str) -> None:
    with pytest.raises(VoxelloError) as err:
        select_range(script, spec)
    assert err.value.code == INVALID_SCRIPT


def test_select_from_and_count(script: Script) -> None:
    got = select_range(script, None, start="p002.2", count=2)
    assert [s.id for s in got] == ["p002.2", "p003.1"]
