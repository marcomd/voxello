"""Deterministic segmentation of a dialogue source (roadmap 8.3, decision 0003).

The source is split into paragraphs on blank lines; each paragraph into quoted speech and
narration; long runs into groups of whole sentences. Every segment's ``text`` is a verbatim
slice of the source, so the segments rebuild it modulo whitespace (``dialogue check`` proves
it). No language model is involved: the agent only labels the ids this module produces.

Italian conventions:

- speech in ``«»``, ``“”`` or ``""`` starts at the beginning of a paragraph, after the end of
  a sentence (``.!?…``, possibly closed by a previous quote), after ``:``, or after ``,`` once a
  quote has closed in the same paragraph (``«...», disse lei, «...»``). Other quotes, such as
  ``la chiamava «tesoro»``, stay inside the narration;
- dash dialogue: a paragraph that starts with ``—`` or ``–``; a dash preceded by whitespace
  closes the speech before an attribution, and the next one reopens it;
- punctuation right after a closing quote belongs to the speech (``«Sono piccolissimi»,``);
- apostrophes (``'``, ``’``) are never quotes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

SegmentKind = Literal["narration", "speech", "heading", "break"]

DEFAULT_MAX_CHARS = 300
LONG_SENTENCE_CHARS = 500  # a single sentence this long is kept whole but reported

QUOTE_PAIRS = {"«": "»", "“": "”", '"': '"'}
CLOSING_QUOTES = frozenset('»”"')
DASHES = frozenset("—–")
SENTENCE_END = frozenset(".!?…")
TRAILING_PUNCT = frozenset(",.;:!?…")

PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n")
BREAK_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,}|(?:\* ?){3,}|(?:- ){2,}-)$")
# End of a sentence: terminal punctuation, optional closing quotes, whitespace, then something
# that may start a sentence; ``_starts_sentence`` then requires an upper-case letter, an opening
# quote or a dash.
SENTENCE_BREAK_RE = re.compile(r"[.!?…]+[»”\")]*(\s+)(?=\S)")
SENTENCE_OPENERS = frozenset('«“"—–(')
ABBREVIATIONS = frozenset(
    {
        "sig",
        "sigg",
        "sig.ra",
        "sig.na",
        "dott",
        "dott.ssa",
        "prof",
        "prof.ssa",
        "avv",
        "ing",
        "arch",
        "geom",
        "rag",
        "on",
        "sen",
        "mons",
        "don",
        "p.es",
        "ecc",
        "pag",
        "pagg",
        "cap",
        "vol",
        "n",
        "nr",
        "st",
        "mr",
        "mrs",
        "ms",
        "dr",
    }
)


@dataclass(frozen=True)
class Segment:
    id: str
    kind: SegmentKind
    text: str


@dataclass(frozen=True)
class SegmentWarning:
    segment_id: str
    message: str


@dataclass
class SegmentationResult:
    segments: list[Segment] = field(default_factory=list)
    warnings: list[SegmentWarning] = field(default_factory=list)


@dataclass(frozen=True)
class _Piece:
    kind: SegmentKind
    text: str
    warning: str | None = None


def normalize_source(source: str) -> str:
    """Line endings to ``\\n`` and no byte-order mark; the text itself is untouched."""
    return source.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def paragraphs(source: str) -> list[str]:
    """Non-empty paragraphs, stripped, in order."""
    parts = PARAGRAPH_SPLIT_RE.split(normalize_source(source))
    return [p.strip() for p in parts if p.strip()]


def segment_text(source: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> SegmentationResult:
    """Split ``source`` into numbered segments (``p003.2``: paragraph 3, segment 2)."""
    paras = paragraphs(source)
    width = max(3, len(str(len(paras))))
    result = SegmentationResult()
    for number, paragraph in enumerate(paras, start=1):
        pieces = _split_paragraph(paragraph, max_chars)
        for index, piece in enumerate(pieces, start=1):
            seg_id = f"p{number:0{width}d}.{index}"
            result.segments.append(Segment(seg_id, piece.kind, piece.text))
            if piece.warning is not None:
                result.warnings.append(SegmentWarning(seg_id, piece.warning))
    return result


def _split_paragraph(paragraph: str, max_chars: int) -> list[_Piece]:
    if BREAK_RE.match(paragraph):
        return [_Piece("break", paragraph)]
    if paragraph.startswith("#"):
        return [_Piece("heading", paragraph)]
    runs = _dash_runs(paragraph) if paragraph[0] in DASHES else _quote_runs(paragraph)
    pieces: list[_Piece] = []
    for run in runs:
        pieces.extend(_group_sentences(run, max_chars))
    return pieces


def _dash_runs(paragraph: str) -> list[_Piece]:
    """Alternate speech and narration at every dash preceded by whitespace."""
    runs: list[_Piece] = []
    start = 0
    speaking = True
    i = 1
    while i <= len(paragraph):
        at_dash = i < len(paragraph) and paragraph[i] in DASHES and paragraph[i - 1].isspace()
        if i == len(paragraph) or at_dash:
            if speaking:
                end = i + 1 if at_dash else i  # the closing dash stays with the speech
                _append(runs, "speech", paragraph[start:end])
                start = end
            else:
                _append(runs, "narration", paragraph[start:i])
                start = i
            speaking = not speaking
        i += 1
    return runs


def _quote_runs(paragraph: str) -> list[_Piece]:
    runs: list[_Piece] = []
    narration_start = 0
    quote_closed = False
    i = 0
    while i < len(paragraph):
        char = paragraph[i]
        if char not in QUOTE_PAIRS:
            i += 1
            continue
        close = _find_close(paragraph, i)
        if not _opens_speech(paragraph, i, quote_closed):
            i = (close + 1) if close is not None else i + 1  # an inline quote: skip it
            continue
        _append(runs, "narration", paragraph[narration_start:i])
        if close is None:
            _append(runs, "speech", paragraph[i:], "the quote is not closed in its paragraph")
            return runs
        end = close + 1
        while end < len(paragraph) and paragraph[end] in TRAILING_PUNCT:
            end += 1
        _append(runs, "speech", paragraph[i:end])
        quote_closed = True
        narration_start = i = end
    _append(runs, "narration", paragraph[narration_start:])
    return runs


def _find_close(paragraph: str, open_at: int) -> int | None:
    """Index of the quote closing the one at ``open_at``, honouring nested ``«»``/``“”``."""
    opener = paragraph[open_at]
    closer = QUOTE_PAIRS[opener]
    if opener == closer:
        found = paragraph.find(closer, open_at + 1)
        return None if found < 0 else found
    depth = 0
    for i in range(open_at, len(paragraph)):
        if paragraph[i] == opener:
            depth += 1
        elif paragraph[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


def _opens_speech(paragraph: str, at: int, quote_closed: bool) -> bool:
    before = paragraph[:at].rstrip()
    if not before:
        return True
    last = before[-1]
    if last in SENTENCE_END or last == ":":
        return True
    if last in CLOSING_QUOTES and len(before) > 1 and before[-2] in SENTENCE_END:
        return True
    return last == "," and quote_closed


def _append(runs: list[_Piece], kind: SegmentKind, text: str, warning: str | None = None) -> None:
    text = text.strip()
    if text:
        runs.append(_Piece(kind, text, warning))


# --- sentences ------------------------------------------------------------------------------


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each sentence of ``text``; whitespace between them is excluded."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in SENTENCE_BREAK_RE.finditer(text):
        following = text[match.end(1)]
        if not (following.isupper() or following in SENTENCE_OPENERS):
            continue
        if _ends_with_abbreviation(text[start : match.start(1)]):
            continue
        spans.append((start, match.start(1)))
        start = match.end(1)
    spans.append((start, len(text)))
    return spans


def _ends_with_abbreviation(sentence: str) -> bool:
    if not sentence.endswith("."):
        return False
    words = sentence[:-1].split()
    if not words:
        return False
    word = words[-1].lstrip('«“"(—–').lower()
    return word in ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def _group_sentences(piece: _Piece, max_chars: int) -> list[_Piece]:
    if len(piece.text) <= max_chars:
        return [piece]
    spans = sentence_spans(piece.text)
    bounds = _balanced_groups(spans, max_chars)
    groups: list[_Piece] = []
    for first, last in bounds:
        text = piece.text[spans[first][0] : spans[last][1]]
        warning = None
        if first == last and len(text) >= LONG_SENTENCE_CHARS:
            warning = f"a single sentence of {len(text)} characters; consider a shorter one"
        groups.append(_Piece(piece.kind, text, warning))
    if piece.warning is not None:  # an unclosed quote: report it on the last part
        last_piece = groups[-1]
        groups[-1] = _Piece(last_piece.kind, last_piece.text, piece.warning)
    return groups


def _balanced_groups(spans: list[tuple[int, int]], max_chars: int) -> list[tuple[int, int]]:
    """Group consecutive sentences into the fewest groups of at most ``max_chars``, then even
    out their lengths. A sentence longer than the limit forms a group of its own."""

    def length(first: int, last: int) -> int:
        return spans[last][1] - spans[first][0]

    def fits(first: int, last: int) -> bool:
        return first == last or length(first, last) <= max_chars

    n = len(spans)
    unreachable = n + 1
    # fewest[i]: fewest groups covering sentences i..n-1
    fewest = [unreachable] * n + [0]
    for i in range(n - 1, -1, -1):
        for j in range(i, n):
            if not fits(i, j):
                break
            fewest[i] = min(fewest[i], fewest[j + 1] + 1)
    # longest[i]: smallest possible longest group for i..n-1 using exactly fewest[i] groups
    longest = [0] * (n + 1)
    choice = list(range(n))
    for i in range(n - 1, -1, -1):
        longest[i] = spans[-1][1] + 1
        for j in range(i, n):
            if not fits(i, j):
                break
            if fewest[j + 1] + 1 != fewest[i]:
                continue
            worst = max(length(i, j), longest[j + 1])
            if worst < longest[i]:
                longest[i] = worst
                choice[i] = j
    groups: list[tuple[int, int]] = []
    i = 0
    while i < n:
        groups.append((i, choice[i]))
        i = choice[i] + 1
    return groups


# --- spoken text ----------------------------------------------------------------------------


def default_tts_text(kind: SegmentKind, text: str) -> str:
    """What is spoken when the annotation sets no ``tts_text``.

    Speech loses its outer quote marks or dashes (the punctuation after a closing quote stays);
    a heading loses its ``#`` marks; a break is never spoken.
    """
    if kind == "break":
        return ""
    if kind == "heading":
        return text.lstrip("#").strip()
    if kind == "narration" or not text:
        return text
    if text[0] in DASHES:
        body = text[1:].strip()
        if body and body[-1] in DASHES:
            body = body[:-1]
        return body.strip()
    if text[0] in QUOTE_PAIRS:
        closer = QUOTE_PAIRS[text[0]]
        body = text[1:]
        cut = body.rfind(closer)
        if cut >= 0 and all(c in TRAILING_PUNCT for c in body[cut + 1 :]):
            body = body[:cut] + body[cut + 1 :]
        return body.strip()
    return text
