"""The review loop as a pure state machine (roadmap 8.5).

``step`` turns one key into a new position in the queue and a list of effects (approve,
render a take, edit the line, play...). It reads nothing and writes nothing: the session
(``review_session``) executes the effects against the project, the provider and the player,
and the CLI owns the terminal. Each effect is persisted by the session before the next key.

Keys: Enter approves the chosen take and moves on, ``r`` renders a new take with the next seed
not used by the line's current takes, ``1``-``9`` pick a take (the latest nine when there are
more), ``p`` replays, ``c`` plays in context, ``e`` edits ``tts_text``, ``g`` picks a register,
``+``/``-`` change speed by 0.05, ``n``/``b`` (or the arrows) move, ``q`` quits.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace

from voxello.dialogue.qa import Flag
from voxello.tts.base import SPEED_RANGE

SPEED_STEP = 0.05
MAX_PICK = 9
ARROWS = {"\x1b[C": "n", "\x1b[B": "n", "\x1b[D": "b", "\x1b[A": "b"}


@dataclass(frozen=True)
class TakeInfo:
    fingerprint: str
    seed: int
    duration_ms: int | None


@dataclass(frozen=True)
class LineView:
    """What the reviewer sees of one line; ``takes`` are its current takes, oldest first."""

    segment_id: str
    number: int  # 1-based position among the spoken lines
    total: int
    speaker: str
    register: str | None
    text: str
    tts_text: str | None  # only when it differs from what `text` gives
    speed: float | None  # effective speed; None when nothing sets it (1.0)
    base_seed: int  # the seed of a first render
    takes: tuple[TakeInfo, ...] = ()
    chosen: str | None = None  # fingerprint
    approved: str | None = None  # fingerprint, among the current takes only
    registers: tuple[str, ...] = ()  # the speaker's registers in cast.yaml
    flags: tuple[Flag, ...] = ()  # of the chosen take

    def chosen_take(self) -> TakeInfo | None:
        return next((t for t in self.takes if t.fingerprint == self.chosen), None)

    def seed(self) -> int:
        """The seed an edit renders with: the chosen take's, so the pick survives the edit."""
        take = self.chosen_take()
        return take.seed if take is not None else self.base_seed


# --- effects --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Approve:
    segment_id: str
    fingerprint: str


@dataclass(frozen=True)
class Select:
    segment_id: str
    fingerprint: str


@dataclass(frozen=True)
class NewTake:
    segment_id: str
    seed: int


@dataclass(frozen=True)
class Play:
    segment_id: str


@dataclass(frozen=True)
class PlayContext:
    segment_id: str


@dataclass(frozen=True)
class EditText:
    segment_id: str
    seed: int


@dataclass(frozen=True)
class SetSpeed:
    segment_id: str
    speed: float
    seed: int


@dataclass(frozen=True)
class ChooseRegister:
    segment_id: str
    seed: int


@dataclass(frozen=True)
class Notice:
    text: str


@dataclass(frozen=True)
class Quit:
    interrupted: bool = False


Effect = (
    Approve
    | Select
    | NewTake
    | Play
    | PlayContext
    | EditText
    | SetSpeed
    | ChooseRegister
    | Notice
    | Quit
)


@dataclass(frozen=True)
class Review:
    queue: tuple[str, ...]
    index: int = 0

    @property
    def done(self) -> bool:
        return self.index >= len(self.queue)

    @property
    def current(self) -> str | None:
        return None if self.done else self.queue[self.index]


# --- keys -----------------------------------------------------------------------------------


def normalize_key(raw: str) -> str:
    """One key from what the terminal sent: ``enter``, ``ctrl-c``, ``escape``, or a character
    (lowercased; the arrows are ``n``/``b``, ``=`` is ``+`` so it needs no Shift)."""
    if raw in ARROWS:
        return ARROWS[raw]
    if not raw:
        return ""
    first = raw[0]
    if first in "\r\n":
        return "enter"
    if first == "\x03":
        return "ctrl-c"
    if first == "\x1b":
        return "escape"
    if first == "=":
        return "+"
    return first.lower()


def step(review: Review, view: LineView, key: str) -> tuple[Review, list[Effect]]:
    sid = view.segment_id
    chosen = view.chosen_take()
    if key == "enter":
        if chosen is None:
            return review, [Notice("No take to approve: r renders one.")]
        return replace(review, index=review.index + 1), [Approve(sid, chosen.fingerprint)]
    if key == "r":
        seed = max((t.seed for t in view.takes), default=view.base_seed - 1) + 1
        return review, [NewTake(sid, seed)]
    if key.isdigit() and key != "0":
        offset = max(0, len(view.takes) - MAX_PICK)
        index = offset + int(key) - 1
        if index >= len(view.takes):
            return review, [Notice(f"There is no take {int(key) + offset}.")]
        return review, [Select(sid, view.takes[index].fingerprint)]
    if key in ("p", "c"):
        if chosen is None:
            return review, [Notice("No take to play.")]
        return review, [Play(sid) if key == "p" else PlayContext(sid)]
    if key == "e":
        return review, [EditText(sid, view.seed())]
    if key == "g":
        if not view.registers:
            return review, [Notice(f"{view.speaker} has no registers in cast.yaml.")]
        return review, [ChooseRegister(sid, view.seed())]
    if key in ("+", "-"):
        return review, [_speed(view, 1 if key == "+" else -1)]
    if key == "n":
        if review.index + 1 >= len(review.queue):
            return review, [Notice("This is the last line: Enter approves it, q quits.")]
        return replace(review, index=review.index + 1), []
    if key == "b":
        if review.index == 0:
            return review, [Notice("This is the first line.")]
        return replace(review, index=review.index - 1), []
    if key == "q":
        return review, [Quit()]
    if key == "ctrl-c":
        return review, [Quit(interrupted=True)]
    return review, [Notice(help_line(view))]


def _speed(view: LineView, direction: int) -> Effect:
    current = view.speed if view.speed is not None else 1.0
    low, high = SPEED_RANGE
    speed = min(high, max(low, round(current + direction * SPEED_STEP, 2)))
    if speed == round(current, 2):
        return Notice(f"Speed is already {current:.2f}, the limit.")
    return SetSpeed(view.segment_id, speed, view.seed())


def register_from_key(registers: Sequence[str], key: str) -> tuple[bool, str | None]:
    """``(True, register)`` for a valid choice (``0`` is the main voice), else ``(False, None)``."""
    if key == "0":
        return True, None
    if key.isdigit() and 1 <= int(key) <= len(registers):
        return True, registers[int(key) - 1]
    return False, None


# --- queue ----------------------------------------------------------------------------------


def build_queue(
    ids: Sequence[str],
    *,
    approved: Collection[str],
    flagged: Collection[str],
    pending: bool = False,
    flagged_first: bool = False,
) -> tuple[str, ...]:
    """The lines to review in script order; ``--pending`` drops approved ones, ``--flagged``
    moves flagged ones to the front."""
    chosen = [i for i in ids if not (pending and i in approved)]
    if flagged_first:
        chosen = [i for i in chosen if i in flagged] + [i for i in chosen if i not in flagged]
    return tuple(chosen)


def start_index(
    queue: Sequence[str],
    order: Sequence[str],
    *,
    start: str | None,
    position: str | None,
    approved: Collection[str],
) -> int:
    """``--from`` (or the first queued line after it), else the saved position, else the first
    line not approved yet, else the first line."""
    if start is not None:
        rank = {seg_id: n for n, seg_id in enumerate(order)}
        target = rank.get(start, len(order))
        return next((n for n, i in enumerate(queue) if rank[i] >= target), 0)
    if position in queue:
        return queue.index(position)
    return next((n for n, i in enumerate(queue) if i not in approved), 0)


# --- screen ---------------------------------------------------------------------------------


def format_line(view: LineView) -> list[str]:
    label = view.speaker.upper() + (f" ({view.register})" if view.register else "")
    header = f"[{view.number}/{view.total}] {view.segment_id}  {label}  "
    chosen = view.chosen_take()
    if chosen is None:
        header += "no take"
    else:
        position = view.takes.index(chosen) + 1
        parts = [f"take {position}/{len(view.takes)}", f"seed {chosen.seed}"]
        if chosen.duration_ms is not None:
            parts.append(f"{chosen.duration_ms / 1000:.1f} s")
        if view.approved == chosen.fingerprint:
            parts.append("approved")
        elif view.approved is not None:
            number = next(n for n, t in enumerate(view.takes, 1) if t.fingerprint == view.approved)
            parts.append(f"take {number} approved")
        else:
            parts.append("not approved")
        header += " · ".join(parts)
    lines = [header, f"  text      {_one_line(view.text)}"]
    if view.tts_text is not None:
        lines.append(f"  tts_text  {_one_line(view.tts_text)}")
    if view.speed is not None:
        lines.append(f"  speed     {view.speed:.2f}")
    if view.flags:
        lines.append("  ! " + " · ".join(flag.message for flag in view.flags))
    return lines


def help_line(view: LineView) -> str:
    count = len(view.takes)
    parts = ["Enter approve", "r new take"]
    if count == 1:
        parts.append("1 take")
    elif 1 < count <= MAX_PICK:
        parts.append(f"1-{count} take")
    elif count > MAX_PICK:
        parts.append(f"1-9 take {count - MAX_PICK + 1}-{count}")
    parts += ["p play", "c context", "e edit"]
    if view.registers:
        parts.append("g register")
    parts += ["+/- speed", "n/b next/back", "q quit"]
    return " · ".join(parts)


def _one_line(text: str) -> str:
    return " ".join(text.split())
