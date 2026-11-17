"""Voice-design instructions for OmniVoice (``instructions`` on ``POST /v1/audio/speech``).

The vocabulary comes from the upstream documentation
(https://github.com/k2-fsa/OmniVoice/blob/master/docs/voice-design.md) and matches the
``design_attributes`` that omnivoice-server publishes in ``GET /v1/voices``; ``voxello doctor``
warns when the two drift apart. Rules from the same document: attributes are comma separated
(half-width ``,`` or full-width U+FF0C), case-insensitive, any subset may be given, and at most one
attribute per category. Accents only apply to English text and dialects only to Chinese text;
that is the model's behaviour, not something Voxello checks, because the text language is a
hint the caller may omit.

Validating locally gives agents an immediate, actionable error listing the allowed values
instead of an opaque HTTP 422, and the canonical form keeps cache keys stable across trivial
spelling variants.
"""

from __future__ import annotations

from voxello.errors import INVALID_INSTRUCTIONS, VoxelloError

MAX_INSTRUCTIONS_CHARS = 256
FULLWIDTH_COMMA = "\uff0c"  # the separator the upstream docs use for Chinese attributes

# Same keys and values as omnivoice-server's `design_attributes`.
VOICE_DESIGN_ATTRIBUTES: dict[str, frozenset[str]] = {
    "gender": frozenset({"male", "female"}),
    "age": frozenset({"child", "teenager", "young adult", "middle-aged", "elderly"}),
    "pitch": frozenset(
        {"very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch"}
    ),
    "style": frozenset({"whisper"}),
    "accent_en": frozenset(
        {
            "american accent",
            "british accent",
            "australian accent",
            "canadian accent",
            "indian accent",
            "chinese accent",
            "korean accent",
            "japanese accent",
            "portuguese accent",
            "russian accent",
        }
    ),
    "dialect_zh": frozenset(
        {
            "河南话",
            "陕西话",
            "四川话",
            "贵州话",
            "云南话",
            "桂林话",
            "济南话",
            "石家庄话",
            "甘肃话",
            "宁夏话",
            "青岛话",
            "东北话",
        }
    ),
}

# Chinese spellings of the English attributes; the model normalises either language.
CHINESE_SYNONYMS: dict[str, frozenset[str]] = {
    "gender": frozenset({"男", "女"}),
    "age": frozenset({"儿童", "少年", "青年", "中年", "老年"}),
    "pitch": frozenset({"极低音调", "低音调", "中音调", "高音调", "极高音调"}),
    "style": frozenset({"耳语"}),
}

_CATEGORY_OF: dict[str, str] = {
    attribute: category
    for table in (VOICE_DESIGN_ATTRIBUTES, CHINESE_SYNONYMS)
    for category, attributes in table.items()
    for attribute in attributes
}


def describe_vocabulary() -> str:
    """One line per category with the English attributes, for error messages and help."""
    return "; ".join(
        f"{category}: {', '.join(sorted(values))}"
        for category, values in VOICE_DESIGN_ATTRIBUTES.items()
        if category != "dialect_zh"
    )


def normalize_instructions(raw: str) -> str:
    """Validate voice-design instructions and return their canonical form.

    The canonical form is lowercase, with single spaces inside attributes, joined by ``", "``
    in the order given. Raises ``invalid_instructions`` for blank items, unknown attributes,
    two attributes of the same category or text longer than ``MAX_INSTRUCTIONS_CHARS``.
    """
    if len(raw) > MAX_INSTRUCTIONS_CHARS:
        raise VoxelloError(
            INVALID_INSTRUCTIONS,
            f"Voice-design instructions are limited to {MAX_INSTRUCTIONS_CHARS} characters.",
        )
    parts = raw.replace(FULLWIDTH_COMMA, ",").split(",")
    items = [" ".join(part.split()).lower() for part in parts]
    if not raw.strip() or any(not item for item in items):
        raise VoxelloError(
            INVALID_INSTRUCTIONS,
            "Voice-design instructions must be comma-separated attributes, "
            f"e.g. 'female, young adult, high pitch'. Allowed: {describe_vocabulary()}.",
        )
    seen: dict[str, str] = {}
    for item in items:
        category = _CATEGORY_OF.get(item)
        if category is None:
            raise VoxelloError(
                INVALID_INSTRUCTIONS,
                f"Unknown voice-design attribute '{item}'. Allowed: {describe_vocabulary()}.",
            )
        if category in seen:
            raise VoxelloError(
                INVALID_INSTRUCTIONS,
                f"Only one {category} attribute is allowed, got '{seen[category]}' and '{item}'.",
            )
        seen[category] = item
    return ", ".join(items)
