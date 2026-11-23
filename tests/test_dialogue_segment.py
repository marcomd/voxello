"""Deterministic segmentation of a dialogue source (roadmap 8.3)."""

from __future__ import annotations

import re

import pytest

from voxello.dialogue.segment import Segment, default_tts_text, segment_text


def texts(segments: list[Segment]) -> list[tuple[str, str, str]]:
    return [(s.id, s.kind, s.text) for s in segments]


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


# --- quote styles ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "«Nevica?» gli chiese, senza alzarsi.",
            [
                ("p001.1", "speech", "«Nevica?»"),
                ("p001.2", "narration", "gli chiese, senza alzarsi."),
            ],
            id="guillemets-question-then-attribution",
        ),
        pytest.param(
            "“Nevica?” gli domandò.",
            [("p001.1", "speech", "“Nevica?”"), ("p001.2", "narration", "gli domandò.")],
            id="curly-quotes",
        ),
        pytest.param(
            '"Nevica?" gli domandò.',
            [("p001.1", "speech", '"Nevica?"'), ("p001.2", "narration", "gli domandò.")],
            id="straight-quotes",
        ),
        pytest.param(
            "«Porto fuori il cane. È già tardi.» disse Bruno.",
            [
                ("p001.1", "speech", "«Porto fuori il cane. È già tardi.»"),
                ("p001.2", "narration", "disse Bruno."),
            ],
            id="attribution-without-comma",
        ),
        pytest.param(
            "«Sono nuovi», disse lei, girandosi.",
            [
                ("p001.1", "speech", "«Sono nuovi»,"),
                ("p001.2", "narration", "disse lei, girandosi."),
            ],
            id="comma-goes-with-the-quote",
        ),
        pytest.param(
            "«Se passi in centro restituiscili», disse, indicando i libri. «Scadono domani.»",
            [
                ("p001.1", "speech", "«Se passi in centro restituiscili»,"),
                ("p001.2", "narration", "disse, indicando i libri."),
                ("p001.3", "speech", "«Scadono domani.»"),
            ],
            id="second-quote-after-sentence-end",
        ),
        pytest.param(
            "«Ci vediamo», aggiunse lei, «verso le otto.»",
            [
                ("p001.1", "speech", "«Ci vediamo»,"),
                ("p001.2", "narration", "aggiunse lei,"),
                ("p001.3", "speech", "«verso le otto.»"),
            ],
            id="inciso",
        ),
        pytest.param(
            "Lei chiuse il libro. «Lo hai già letto?»",
            [
                ("p001.1", "narration", "Lei chiuse il libro."),
                ("p001.2", "speech", "«Lo hai già letto?»"),
            ],
            id="narration-then-quote",
        ),
        pytest.param(
            "«Pronto.» «Arrivo.» rispose lei.",
            [
                ("p001.1", "speech", "«Pronto.»"),
                ("p001.2", "speech", "«Arrivo.»"),
                ("p001.3", "narration", "rispose lei."),
            ],
            id="two-quotes-in-a-row",
        ),
        pytest.param(
            "Ripeté soltanto: «Domani.»",
            [
                ("p001.1", "narration", "Ripeté soltanto:"),
                ("p001.2", "speech", "«Domani.»"),
            ],
            id="after-colon",
        ),
        pytest.param(
            "La chiamava «tesoro» davanti a tutti.",
            [("p001.1", "narration", "La chiamava «tesoro» davanti a tutti.")],
            id="mid-sentence-quote-stays-in-narration",
        ),
        pytest.param(
            "«Ha detto “mai più”, capisci?» chiese lei.",
            [
                ("p001.1", "speech", "«Ha detto “mai più”, capisci?»"),
                ("p001.2", "narration", "chiese lei."),
            ],
            id="nested-quotes-belong-to-the-outer-one",
        ),
        pytest.param(
            "L’aspettava sempre. Quell'anno no.",
            [("p001.1", "narration", "L’aspettava sempre. Quell'anno no.")],
            id="apostrophes-are-not-quotes",
        ),
    ],
)
def test_quotes(source: str, expected: list[tuple[str, str, str]]) -> None:
    assert texts(segment_text(source).segments) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "— Nevica? — domandò lei. — Non ancora.",
            [
                ("p001.1", "speech", "— Nevica? —"),
                ("p001.2", "narration", "domandò lei."),
                ("p001.3", "speech", "— Non ancora."),
            ],
            id="em-dash",
        ),
        pytest.param(
            "– Nevica?",
            [("p001.1", "speech", "– Nevica?")],
            id="en-dash-whole-paragraph",
        ),
        pytest.param(
            "—Nevica? —domandò lei.",
            [("p001.1", "speech", "—Nevica? —"), ("p001.2", "narration", "domandò lei.")],
            id="dash-without-spaces",
        ),
        pytest.param(
            "Capitolo 2 — In cucina.",
            [("p001.1", "narration", "Capitolo 2 — In cucina.")],
            id="dash-inside-narration-is-not-dialogue",
        ),
    ],
)
def test_dash_dialogue(source: str, expected: list[tuple[str, str, str]]) -> None:
    assert texts(segment_text(source).segments) == expected


def test_unclosed_quote_runs_to_the_paragraph_end_with_a_warning() -> None:
    result = segment_text("«Primo paragrafo di un discorso lungo.\n\n«Secondo.»")
    assert texts(result.segments) == [
        ("p001.1", "speech", "«Primo paragrafo di un discorso lungo."),
        ("p002.1", "speech", "«Secondo.»"),
    ]
    assert [w.segment_id for w in result.warnings] == ["p001.1"]
    assert "not closed" in result.warnings[0].message


def test_odd_straight_quotes_warn() -> None:
    result = segment_text('"Vieni" disse. "Subito')
    assert texts(result.segments)[-1] == ("p001.3", "speech", '"Subito')
    assert [w.segment_id for w in result.warnings] == ["p001.3"]


# --- paragraphs, headings, breaks -----------------------------------------------------------


def test_paragraphs_headings_and_breaks() -> None:
    source = "Primo.\n\n---\n\n#### Scena 2 — In cucina · Bruno\n\n\n\nUltimo.  \n"
    assert texts(segment_text(source).segments) == [
        ("p001.1", "narration", "Primo."),
        ("p002.1", "break", "---"),
        ("p003.1", "heading", "#### Scena 2 — In cucina · Bruno"),
        ("p004.1", "narration", "Ultimo."),
    ]


def test_lines_inside_a_paragraph_are_kept() -> None:
    result = segment_text("Una riga\nche continua.")
    assert texts(result.segments) == [("p001.1", "narration", "Una riga\nche continua.")]


def test_windows_line_endings_and_bom() -> None:
    result = segment_text("﻿Primo.\r\n\r\nSecondo.\r\n")
    assert texts(result.segments) == [
        ("p001.1", "narration", "Primo."),
        ("p002.1", "narration", "Secondo."),
    ]


def test_id_width_grows_past_999_paragraphs() -> None:
    result = segment_text("\n\n".join(["Frase."] * 1000))
    assert result.segments[0].id == "p0001.1"
    assert result.segments[-1].id == "p1000.1"


def test_empty_source() -> None:
    assert segment_text("  \n\n ").segments == []


# --- narration length -----------------------------------------------------------------------


def sentence(n: int, length: int) -> str:
    body = f"Frase {n} " + "parola " * 60
    return body[: length - 1].rstrip() + "."


def test_short_narration_paragraph_is_one_segment() -> None:
    text = " ".join(sentence(i, 90) for i in range(3))
    assert len(segment_text(text).segments) == 1


def test_long_paragraph_splits_at_sentences_in_balanced_parts() -> None:
    parts = [sentence(0, 20), sentence(1, 110), sentence(2, 170), sentence(3, 40)]
    result = segment_text(" ".join(parts), max_chars=300)
    got = [s.text for s in result.segments]
    assert got == [f"{parts[0]} {parts[1]}", f"{parts[2]} {parts[3]}"]
    assert result.warnings == []


def test_split_respects_max_chars_with_more_parts() -> None:
    parts = [sentence(i, 95) for i in range(7)]
    result = segment_text(" ".join(parts), max_chars=300)
    assert all(len(s.text) <= 300 for s in result.segments)
    assert len(result.segments) == 3


def test_a_single_long_sentence_is_never_cut_but_warns_past_500() -> None:
    long = "Una " + "parola " * 90 + "fine."
    result = segment_text(long, max_chars=300)
    assert [s.text for s in result.segments] == [long]
    assert [w.segment_id for w in result.warnings] == ["p001.1"]


def test_long_speech_splits_too() -> None:
    parts = [sentence(i, 120) for i in range(3)]
    result = segment_text("«" + " ".join(parts) + "»", max_chars=300)
    assert [s.kind for s in result.segments] == ["speech", "speech"]
    assert result.segments[0].text.startswith("«")
    assert result.segments[-1].text.endswith("»")


@pytest.mark.parametrize(
    ("source", "count"),
    [
        ("Il sig. Rossi arrivò. Poi partì.", 2),
        ("Aspettò... Poi partì.", 2),
        ("Aspettò… poi partì.", 1),
        ("Disse ecc. e basta. Poi partì.", 2),
    ],
)
def test_sentence_boundaries(source: str, count: int) -> None:
    # max_chars small enough that every sentence becomes its own segment
    result = segment_text(source, max_chars=10)
    assert len(result.segments) == count


# --- invariants -----------------------------------------------------------------------------

FIXTURE = """Anna aprì la finestra. Entrava aria fresca.

«Porto fuori il cane un momento. È già tardi.» disse Bruno.

«Fino al parco?» domandò Anna.

«Se passi in centro restituiscili», disse, indicando i libri. «Scadono domani.»

— Nevica? — domandò lei. — Non ancora.

---

#### Scena 2 — In cucina · Bruno

La chiamava «tesoro» davanti a tutti; “sempre”, diceva. Lei chiuse il libro. «Tu?»

«Pronto.» «Arrivo.» rispose lei.
"""


def test_segments_rebuild_the_source_modulo_whitespace() -> None:
    result = segment_text(FIXTURE)
    assert squash("".join(s.text for s in result.segments)) == squash(FIXTURE)


def test_segmentation_is_deterministic_and_prefix_stable() -> None:
    whole = segment_text(FIXTURE).segments
    prefix = segment_text(FIXTURE.split("---")[0]).segments
    assert whole[: len(prefix)] == prefix


# --- default tts_text -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("speech", "«Sono nuovi»,", "Sono nuovi,"),
        ("speech", "«Nevica?»", "Nevica?"),
        ("speech", "“Nevica?”", "Nevica?"),
        ("speech", '"Nevica?"', "Nevica?"),
        ("speech", "— Nevica? —", "Nevica?"),
        ("speech", "— Non ancora.", "Non ancora."),
        ("speech", "«Inizio di un discorso", "Inizio di un discorso"),
        ("speech", "«Ha detto “mai più”.»", "Ha detto “mai più”."),
        ("narration", "disse lei.", "disse lei."),
        ("heading", "#### Scena 2 — In cucina", "Scena 2 — In cucina"),
        ("break", "---", ""),
    ],
)
def test_default_tts_text(kind: str, text: str, expected: str) -> None:
    assert default_tts_text(kind, text) == expected  # type: ignore[arg-type]
