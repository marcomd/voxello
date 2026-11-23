"""The review state machine (roadmap 8.5): pure functions, scripted keys."""

from __future__ import annotations

from dataclasses import replace

import pytest

from voxello.dialogue.qa import Flag
from voxello.dialogue.review import (
    Approve,
    ChooseRegister,
    EditText,
    LineView,
    NewTake,
    Notice,
    Play,
    PlayContext,
    Quit,
    Review,
    Select,
    SetSpeed,
    TakeInfo,
    build_queue,
    format_line,
    help_line,
    normalize_key,
    register_from_key,
    start_index,
    step,
)

TAKES = (TakeInfo("aaa", 1, 1200), TakeInfo("bbb", 2, 1400), TakeInfo("ccc", 5, 1300))


def view(**overrides: object) -> LineView:
    values: dict[str, object] = {
        "segment_id": "p002.1",
        "number": 2,
        "total": 3,
        "speaker": "bruno",
        "register": None,
        "text": "«Usciamo adesso?»",
        "tts_text": None,
        "speed": None,
        "base_seed": 1,
        "takes": TAKES,
        "chosen": "bbb",
        "approved": None,
        "registers": ("whisper", "sad"),
        "flags": (),
    }
    values.update(overrides)
    return LineView(**values)  # type: ignore[arg-type]


REVIEW = Review(("p001.1", "p002.1", "p003.1"), 1)


# --- keys -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("\r", "enter"),
        ("\n", "enter"),
        ("\x03", "ctrl-c"),
        ("\x1b[C", "n"),
        ("\x1b[B", "n"),
        ("\x1b[D", "b"),
        ("\x1b[A", "b"),
        ("\x1b", "escape"),
        ("R", "r"),
        ("+", "+"),
        ("=", "+"),
        ("rq", "r"),
        ("", ""),
    ],
)
def test_normalize_key(raw: str, key: str) -> None:
    assert normalize_key(raw) == key


# --- step -----------------------------------------------------------------------------------


def test_enter_approves_the_chosen_take_and_moves_on() -> None:
    review, effects = step(REVIEW, view(), "enter")
    assert effects == [Approve("p002.1", "bbb")]
    assert review.current == "p003.1"


def test_enter_on_the_last_line_finishes() -> None:
    review, _ = step(Review(REVIEW.queue, 2), view(segment_id="p003.1"), "enter")
    assert review.done
    assert review.current is None


def test_enter_without_a_take_does_nothing() -> None:
    review, effects = step(REVIEW, view(takes=(), chosen=None), "enter")
    assert review == REVIEW
    assert effects == [Notice("No take to approve: r renders one.")]


def test_r_renders_with_the_next_unused_seed() -> None:
    _, effects = step(REVIEW, view(), "r")
    assert effects == [NewTake("p002.1", 6)]
    _, effects = step(REVIEW, view(takes=(), chosen=None), "r")
    assert effects == [NewTake("p002.1", 1)]


def test_number_keys_pick_a_take() -> None:
    review, effects = step(REVIEW, view(), "1")
    assert review == REVIEW
    assert effects == [Select("p002.1", "aaa")]
    _, effects = step(REVIEW, view(), "4")
    assert effects == [Notice("There is no take 4.")]


def test_number_keys_reach_the_latest_nine_takes() -> None:
    many = tuple(TakeInfo(f"t{n}", n, None) for n in range(1, 13))
    _, effects = step(REVIEW, view(takes=many, chosen="t12"), "1")
    assert effects == [Select("p002.1", "t4")]
    assert "1-9 take 4-12" in help_line(view(takes=many, chosen="t12"))


def test_play_and_context() -> None:
    assert step(REVIEW, view(), "p")[1] == [Play("p002.1")]
    assert step(REVIEW, view(), "c")[1] == [PlayContext("p002.1")]
    assert step(REVIEW, view(takes=(), chosen=None), "p")[1] == [Notice("No take to play.")]


def test_edits_keep_the_seed_of_the_chosen_take() -> None:
    assert step(REVIEW, view(), "e")[1] == [EditText("p002.1", 2)]
    assert step(REVIEW, view(), "g")[1] == [ChooseRegister("p002.1", 2)]
    assert step(REVIEW, view(takes=(), chosen=None), "e")[1] == [EditText("p002.1", 1)]


def test_g_without_registers_says_so() -> None:
    _, effects = step(REVIEW, view(registers=()), "g")
    assert effects == [Notice("bruno has no registers in cast.yaml.")]


@pytest.mark.parametrize(
    ("speed", "key", "expected"),
    [(None, "+", 1.05), (None, "-", 0.95), (0.95, "+", 1.0), (1.1, "-", 1.05)],
)
def test_speed_steps_by_005(speed: float | None, key: str, expected: float) -> None:
    _, effects = step(REVIEW, view(speed=speed), key)
    assert effects == [SetSpeed("p002.1", expected, 2)]


def test_speed_stops_at_the_bounds() -> None:
    _, effects = step(REVIEW, view(speed=4.0), "+")
    assert effects == [Notice("Speed is already 4.00, the limit.")]
    _, effects = step(REVIEW, view(speed=0.25), "-")
    assert effects == [Notice("Speed is already 0.25, the limit.")]


def test_moving() -> None:
    assert step(REVIEW, view(), "n")[0].current == "p003.1"
    assert step(REVIEW, view(), "b")[0].current == "p001.1"
    first = Review(REVIEW.queue, 0)
    assert step(first, view(), "b") == (first, [Notice("This is the first line.")])
    last = Review(REVIEW.queue, 2)
    assert step(last, view(), "n") == (
        last,
        [Notice("This is the last line: Enter approves it, q quits.")],
    )


def test_quit_and_interrupt() -> None:
    assert step(REVIEW, view(), "q")[1] == [Quit()]
    assert step(REVIEW, view(), "ctrl-c")[1] == [Quit(interrupted=True)]


def test_unknown_key_shows_the_help() -> None:
    review, effects = step(REVIEW, view(), "x")
    assert review == REVIEW
    assert effects == [Notice(help_line(view()))]


def test_register_from_key() -> None:
    registers = ("whisper", "sad")
    assert register_from_key(registers, "0") == (True, None)
    assert register_from_key(registers, "2") == (True, "sad")
    assert register_from_key(registers, "3") == (False, None)
    assert register_from_key(registers, "escape") == (False, None)


# --- queue ----------------------------------------------------------------------------------

IDS = ("p001.1", "p002.1", "p003.1", "p004.1")


def test_queue_filters() -> None:
    approved = {"p001.1", "p003.1"}
    flagged = {"p003.1", "p004.1"}
    assert build_queue(IDS, approved=approved, flagged=flagged) == IDS
    assert build_queue(IDS, approved=approved, flagged=flagged, pending=True) == (
        "p002.1",
        "p004.1",
    )
    assert build_queue(IDS, approved=approved, flagged=flagged, flagged_first=True) == (
        "p003.1",
        "p004.1",
        "p001.1",
        "p002.1",
    )
    assert build_queue(
        IDS, approved=approved, flagged=flagged, pending=True, flagged_first=True
    ) == ("p004.1", "p002.1")


def test_start_index() -> None:
    queue = ("p002.1", "p004.1")
    assert start_index(IDS, IDS, start="p003.1", position=None, approved=set()) == 2
    assert start_index(queue, IDS, start="p003.1", position=None, approved=set()) == 1
    assert start_index(queue, IDS, start="p004.2", position=None, approved=set()) == 0
    assert start_index(IDS, IDS, start=None, position="p002.1", approved=set()) == 1
    # no position (or one outside the queue): the first line not approved yet
    assert start_index(IDS, IDS, start=None, position=None, approved={"p001.1"}) == 1
    assert start_index(queue, IDS, start=None, position="p001.1", approved={"p002.1"}) == 1
    assert start_index(IDS, IDS, start=None, position=None, approved=set(IDS)) == 0


# --- screen ---------------------------------------------------------------------------------


def test_format_line_minimal() -> None:
    lines = format_line(view(takes=(TAKES[0],), chosen="aaa", registers=()))
    assert lines == [
        "[2/3] p002.1  BRUNO  take 1/1 · seed 1 · 1.2 s · not approved",
        "  text      «Usciamo adesso?»",
    ]


def test_format_line_full() -> None:
    lines = format_line(
        view(
            register="whisper",
            tts_text="[question-ah] Usciamo adesso?",
            speed=0.95,
            approved="bbb",
            flags=(Flag("slow", "slow 9.1 c/s (bruno ~14.2)"), Flag("x", "trailing silence")),
        )
    )
    assert lines == [
        "[2/3] p002.1  BRUNO (whisper)  take 2/3 · seed 2 · 1.4 s · approved",
        "  text      «Usciamo adesso?»",
        "  tts_text  [question-ah] Usciamo adesso?",
        "  speed     0.95",
        "  ! slow 9.1 c/s (bruno ~14.2) · trailing silence",
    ]


def test_format_line_other_take_approved_and_no_take() -> None:
    header = format_line(view(approved="aaa"))[0]
    assert header.endswith("take 2/3 · seed 2 · 1.4 s · take 1 approved")
    unknown = replace(TAKES[1], duration_ms=None)
    assert "seed 2 · not approved" in format_line(view(takes=(unknown,)))[0]
    assert format_line(view(takes=(), chosen=None))[0].endswith("no take")


def test_help_line() -> None:
    assert help_line(view()) == (
        "Enter approve · r new take · 1-3 take · p play · c context · e edit · g register "
        "· +/- speed · n/b next/back · q quit"
    )
    assert "g register" not in help_line(view(registers=()))
