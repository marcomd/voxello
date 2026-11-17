"""Voice-design instructions: validation and canonical form (OmniVoice docs/voice-design.md)."""

from __future__ import annotations

import pytest

from voxello.core.voice_design import (
    FULLWIDTH_COMMA,
    MAX_INSTRUCTIONS_CHARS,
    VOICE_DESIGN_ATTRIBUTES,
    normalize_instructions,
)
from voxello.errors import INVALID_INSTRUCTIONS, VoxelloError


def test_canonical_form_is_lowercase_comma_space_in_given_order():
    assert (
        normalize_instructions("  Female ,Young   Adult,HIGH PITCH ")
        == "female, young adult, high pitch"
    )


def test_single_attribute_is_valid():
    assert normalize_instructions("whisper") == "whisper"


def test_full_width_comma_and_chinese_keywords():
    raw = FULLWIDTH_COMMA.join(["女", "青年", "高音调", "四川话"])
    assert normalize_instructions(raw) == "女, 青年, 高音调, 四川话"


def test_english_accent_is_accepted():
    assert normalize_instructions("male, british accent") == "male, british accent"


@pytest.mark.parametrize("raw", ["", "   ", ",", "female,,high pitch"])
def test_empty_or_blank_items_are_rejected(raw):
    with pytest.raises(VoxelloError) as exc:
        normalize_instructions(raw)
    assert exc.value.code == INVALID_INSTRUCTIONS


def test_unknown_attribute_lists_the_vocabulary():
    with pytest.raises(VoxelloError) as exc:
        normalize_instructions("female, italian accent")
    assert exc.value.code == INVALID_INSTRUCTIONS
    assert "italian accent" in exc.value.message
    assert "british accent" in exc.value.message


def test_two_attributes_of_the_same_category_are_rejected():
    with pytest.raises(VoxelloError) as exc:
        normalize_instructions("male, female")
    assert exc.value.code == INVALID_INSTRUCTIONS
    assert "gender" in exc.value.message


def test_english_and_chinese_synonyms_count_as_the_same_category():
    with pytest.raises(VoxelloError) as exc:
        normalize_instructions("female, 女")
    assert "gender" in exc.value.message


def test_too_long_is_rejected():
    with pytest.raises(VoxelloError) as exc:
        normalize_instructions("female, " * (MAX_INSTRUCTIONS_CHARS // 4))
    assert exc.value.code == INVALID_INSTRUCTIONS


def test_vocabulary_matches_the_server_categories():
    # omnivoice-server exposes these keys as `design_attributes` in GET /v1/voices.
    assert {"gender", "age", "pitch", "style", "accent_en", "dialect_zh"} <= set(
        VOICE_DESIGN_ATTRIBUTES
    )
    assert "very high pitch" in VOICE_DESIGN_ATTRIBUTES["pitch"]
