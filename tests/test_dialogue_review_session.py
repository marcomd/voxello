"""`dialogue review` sessions (roadmap 8.5): scripted keys, FakeProvider and FakePlayer."""

from __future__ import annotations

import array
import io
import json
import math
import wave
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import FakePlayer, FakeProvider
from voxello.dialogue.project import Project
from voxello.dialogue.review_session import ReviewSession, ReviewSummary
from voxello.errors import INVALID_SCRIPT, PLAYBACK_ERROR, TTS_PROVIDER_ERROR, VoxelloError
from voxello.tts.base import SynthesisOptions, SynthesisResult

SOURCE = """Francesca cercò i suoi occhi e poi guardò fuori dalla finestra.

«Metto il telefono in carica nello studio.»

«Va bene, ma torna presto che ti aspetto.»
"""

CAST: dict[str, Any] = {
    "language": "it",
    "narrator": "narratore",
    "generation": {"seed": 1, "num_step": 16},
    "pauses_ms": {"same_speaker": 100, "speaker_change": 200, "paragraph": 300, "break": 400},
    "characters": {
        "narratore": {"voice": "onyx"},
        "marco": {"voice": "clone:marco", "registers": {"whisper": "clone:marco-whisper"}},
        "francesca": {"voice": "clone:francesca"},
    },
}

LINES = ["p001.1", "p002.1", "p003.1"]
RATE = 24000


def tone_wav(ms: int, lead_ms: int = 100) -> bytes:
    frames = RATE * ms // 1000
    lead = RATE * lead_ms // 1000
    samples = [0] * lead + [
        round(8000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(frames)
    ]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(array.array("h", samples).tobytes())
    return buffer.getvalue()


class ToneProvider(FakeProvider):
    """Audible takes at 14 characters per second; `slow` lines last twice as long."""

    def __init__(self) -> None:
        super().__init__()
        self.slow: set[str] = set()

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        instructions: str | None = None,
        *,
        options: SynthesisOptions | None = None,
    ) -> SynthesisResult:
        await super().synthesize(text, voice, language, instructions, options=options)
        chars = sum(c.isalnum() for c in text)
        ms = round(chars / 14 * 1000) * (2 if text in self.slow else 1)
        return SynthesisResult(tone_wav(ms), "audio/wav", self.name, voice or "x")


class ScriptedTerminal:
    """Keys from a list; records what was said and, at each key, what state.json held."""

    def __init__(self, project: Project, keys: list[str], edits: list[str | None] | None = None):
        self.project = project
        self.keys = list(keys)
        self.edits = list(edits or [])
        self.edited: list[str] = []
        self.out: list[str] = []
        self.states: list[dict[str, Any]] = []
        self.player: FakePlayer | None = None
        self.playing_at_key: list[bool] = []

    def say(self, text: str) -> None:
        self.out.append(text)

    async def key(self) -> str:
        path = self.project.state_path
        self.states.append(json.loads(path.read_text()) if path.exists() else {})
        if self.player is not None:
            self.playing_at_key.append(self.player.current is not None)
        if not self.keys:
            raise AssertionError("the session asked for more keys than scripted")
        return self.keys.pop(0)

    def edit(self, text: str) -> str | None:
        self.edited.append(text)
        return self.edits.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.out)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "chapter.txt"
    source.write_text(SOURCE, encoding="utf-8")
    project = Project.init(source, tmp_path / "chapter")
    project.annotate({"p002.1": {"speaker": "marco"}, "p003.1": {"speaker": "francesca"}})
    project.cast_path.write_text(yaml.safe_dump(CAST), encoding="utf-8")
    return project


@pytest.fixture
def provider() -> ToneProvider:
    return ToneProvider()


async def review(
    project: Project,
    provider: FakeProvider,
    keys: list[str],
    *,
    edits: list[str | None] | None = None,
    player: FakePlayer | None = None,
    no_player: bool = False,
    **options: Any,
) -> tuple[ReviewSummary, ScriptedTerminal, FakePlayer]:
    terminal = ScriptedTerminal(project, keys, edits)
    fake = player or FakePlayer()
    terminal.player = fake
    session = ReviewSession(
        project,
        provider,
        None if no_player else fake,
        terminal,
        workdir=project.root / "work",
    )
    summary = await session.run(**options)
    return summary, terminal, fake


def state(project: Project) -> dict[str, Any]:
    return json.loads(project.state_path.read_text())


def played(player: FakePlayer) -> list[str]:
    return [
        h.path.parent.name if h.path.parent.name != "work" else "context" for h in player.handles
    ]


# --- the basic loop --------------------------------------------------------------------------


async def test_approving_every_line_renders_on_the_fly_and_finishes(
    project: Project, provider: ToneProvider
) -> None:
    summary, terminal, player = await review(project, provider, ["enter", "enter", "enter"])
    assert len(provider.calls) == 3
    assert played(player) == LINES
    data = state(project)
    for sid in LINES:
        line = data["lines"][sid]
        assert line["approved"] == line["takes"][0]["fingerprint"]
    assert data["review"]["position"] is None  # finished: the next review starts at a pending line
    assert summary == ReviewSummary(approved=3, total=3, flagged=0, position=None)
    assert "[1/3] p001.1  NARRATORE  take 1/1 · seed 1" in terminal.text


async def test_every_action_is_saved_before_the_next_prompt(
    project: Project, provider: ToneProvider
) -> None:
    _, terminal, _ = await review(project, provider, ["enter", "r", "q"])
    first, second, third = terminal.states
    assert first["review"]["position"] == "p001.1"
    assert second["review"]["position"] == "p002.1"
    assert second["lines"]["p001.1"]["approved"] is not None
    assert len(third["lines"]["p002.1"]["takes"]) == 2


async def test_a_key_stops_the_playing_take(project: Project, provider: ToneProvider) -> None:
    _, terminal, player = await review(project, provider, ["n", "q"])
    assert terminal.playing_at_key == [True, True]
    assert all(handle.terminated for handle in player.handles)


async def test_quitting_and_restarting_resumes_where_review_stopped(
    project: Project, provider: ToneProvider
) -> None:
    summary, _, _ = await review(project, provider, ["enter", "n", "q"])
    assert summary.position == "p003.1"
    assert summary.approved == 1
    _, terminal, player = await review(project, provider, ["q"])
    assert played(player) == ["p003.1"]
    assert "p003.1" in terminal.out[0]


async def test_ctrl_c_quits_keeping_the_position(project: Project, provider: ToneProvider) -> None:
    summary, _, _ = await review(project, provider, ["n", "ctrl-c"])
    assert summary.interrupted
    assert summary.position == "p002.1"
    assert state(project)["review"]["position"] == "p002.1"


async def test_a_second_review_does_not_render_again(
    project: Project, provider: ToneProvider
) -> None:
    await review(project, provider, ["n", "n", "q"])
    provider.calls.clear()
    await review(project, provider, ["b", "b", "q"])
    assert provider.calls == []


async def test_from_starts_at_a_line_and_rejects_unknown_ids(
    project: Project, provider: ToneProvider
) -> None:
    _, _, player = await review(project, provider, ["q"], start="p003.1")
    assert played(player) == ["p003.1"]
    with pytest.raises(VoxelloError) as excinfo:
        await review(project, provider, ["q"], start="p009.1")
    assert excinfo.value.code == INVALID_SCRIPT


async def test_nothing_to_review(project: Project, provider: ToneProvider) -> None:
    await review(project, provider, ["enter", "enter", "enter"])
    summary, terminal, _ = await review(project, provider, [], pending=True)
    assert terminal.out == ["Nothing to review: every line is approved."]
    assert summary.approved == 3


# --- takes -----------------------------------------------------------------------------------


async def test_r_adds_a_take_with_the_next_seed_and_keeps_the_older_ones(
    project: Project, provider: ToneProvider
) -> None:
    await review(project, provider, ["r", "r", "1", "enter", "q"])
    seeds = [o.seed for o in provider.options if o is not None]
    assert seeds == [1, 2, 3, 1]  # p001.1 x3, then p002.1 rendered on the fly
    line = state(project)["lines"]["p001.1"]
    assert [t["seed"] for t in line["takes"]] == [1, 2, 3]
    assert line["approved"] == line["takes"][0]["fingerprint"] == line["selected"]
    for take in line["takes"]:
        assert project.take_path("p001.1", take["fingerprint"]).is_file()


async def test_the_picked_take_survives_a_restart(project: Project, provider: ToneProvider) -> None:
    await review(project, provider, ["r", "1", "q"])
    _, terminal, _ = await review(project, provider, ["q"])
    assert "take 1/2 · seed 1" in terminal.out[0]


async def test_a_failed_render_is_reported_and_retried_only_on_request(
    project: Project, provider: ToneProvider
) -> None:
    provider.fail_with = VoxelloError(TTS_PROVIDER_ERROR, "server down")
    _, terminal, player = await review(project, provider, ["p", "x", "r", "q"])
    assert len(provider.calls) == 2  # the first render, then r; never one per key
    assert terminal.text.count("p001.1: server down") == 2
    assert "no take" in terminal.text
    assert player.handles == []


# --- edits ------------------------------------------------------------------------------------


async def test_e_edits_tts_text_and_renders_with_the_chosen_seed(
    project: Project, provider: ToneProvider
) -> None:
    keys = ["n", "r", "e", "enter", "q"]
    await review(project, provider, keys, edits=["  Metto il telefono\n in carica.  "])
    seg = project.load_script().segment("p002.1")
    assert seg.text == "«Metto il telefono in carica nello studio.»"
    assert seg.tts_text == "Metto il telefono in carica."
    # p001.1, p002.1 seed 1, r (seed 2), the edited line, then p003.1 after Enter
    assert provider.calls[3][0] == "Metto il telefono in carica."
    options = provider.options[3]
    assert options is not None and options.seed == 2
    line = state(project)["lines"]["p002.1"]
    assert len(line["takes"]) == 3  # older takes stay in state.json and on disk
    assert line["approved"] == line["takes"][-1]["fingerprint"]


async def test_e_back_to_the_text_clears_tts_text(project: Project, provider: ToneProvider) -> None:
    project.annotate({"p002.1": {"tts_text": "Metto il telefono."}})
    await review(
        project, provider, ["n", "e", "q"], edits=["Metto il telefono in carica nello studio."]
    )
    assert project.load_script().segment("p002.1").tts_text is None


@pytest.mark.parametrize("edited", [None, "", "Metto il telefono in carica nello studio."])
async def test_e_without_a_change(
    project: Project, provider: ToneProvider, edited: str | None
) -> None:
    before = project.script_path.read_bytes()
    _, terminal, _ = await review(project, provider, ["n", "e", "q"], edits=[edited])
    assert project.script_path.read_bytes() == before
    assert "tts_text unchanged." in terminal.out
    assert len(provider.calls) == 2


async def test_e_with_a_bad_tag_changes_nothing(project: Project, provider: ToneProvider) -> None:
    before = project.script_path.read_bytes()
    _, terminal, _ = await review(project, provider, ["e", "q"], edits=["[shout] Ciao a tutti."])
    assert project.script_path.read_bytes() == before
    assert "tag [shout] is not allowed" in terminal.text
    assert len(provider.calls) == 1


async def test_g_changes_the_register(project: Project, provider: ToneProvider) -> None:
    _, terminal, _ = await review(project, provider, ["n", "g", "1", "g", "x", "q"])
    assert project.load_script().segment("p002.1").register_ == "whisper"
    assert provider.calls[-1][1] == "clone:marco-whisper"
    assert "0 main voice · 1 whisper" in terminal.text
    assert "Register unchanged." in terminal.out
    assert "MARCO (whisper)" in terminal.text


async def test_plus_and_minus_change_speed(project: Project, provider: ToneProvider) -> None:
    await review(project, provider, ["+", "+", "-", "q"])
    assert project.load_script().segment("p001.1").speed == 1.05
    speeds = [o.speed for o in provider.options if o is not None]
    assert speeds == [None, 1.05, 1.1]  # 1.05 again is a take that already exists


# --- playback ---------------------------------------------------------------------------------


def frames(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes()


async def test_c_plays_the_previous_approved_line_this_take_and_the_next(
    project: Project, provider: ToneProvider
) -> None:
    await review(project, provider, ["enter", "n", "q"])  # approve p001, render p002 and p003
    _, _, player = await review(project, provider, ["c", "q"], start="p002.1")
    assert played(player) == ["p002.1", "context"]
    context = player.handles[-1].path
    data = state(project)["lines"]
    parts = []
    for sid in ("p001.1", "p002.1"):
        parts.append(frames(project.take_path(sid, data[sid]["takes"][0]["fingerprint"])))
    rendered = state(project)["lines"]["p003.1"]["takes"][0]["fingerprint"]
    parts.append(frames(project.take_path("p003.1", rendered)))
    assert frames(context) == sum(parts) + RATE * (300 + 300) // 1000


async def test_p_replays(project: Project, provider: ToneProvider) -> None:
    _, _, player = await review(project, provider, ["p", "q"])
    assert played(player) == ["p001.1", "p001.1"]


async def test_without_a_player_the_path_is_shown(project: Project, provider: ToneProvider) -> None:
    _, terminal, _ = await review(project, provider, ["q"], no_player=True)
    assert "(no audio player: listen to " in terminal.text


async def test_a_playback_error_is_reported(project: Project, provider: ToneProvider) -> None:
    class BrokenPlayer(FakePlayer):
        async def play(self, path: Path) -> Any:
            raise VoxelloError(PLAYBACK_ERROR, "no sound device")

    _, terminal, _ = await review(project, provider, ["q"], player=BrokenPlayer())
    assert "no sound device" in terminal.text


# --- QA ---------------------------------------------------------------------------------------


async def test_flags_are_shown_and_flagged_lines_come_first(
    project: Project, provider: ToneProvider
) -> None:
    provider.slow.add("Va bene, ma torna presto che ti aspetto.")
    summary, terminal, _ = await review(project, provider, ["n", "n", "q"])
    assert any(line.startswith("  ! slow") for line in terminal.out)
    assert summary.flagged == 1
    _, _, player = await review(project, provider, ["n", "q"], flagged=True)
    assert played(player) == ["p003.1", "p001.1"]


async def test_measures_are_added_to_older_takes(project: Project, provider: ToneProvider) -> None:
    await review(project, provider, ["n", "n", "q"])
    data = state(project)
    for line in data["lines"].values():
        line["takes"][0]["qa"] = None
    project.state_path.write_text(json.dumps(data))
    await review(project, provider, ["q"])
    assert all(line["takes"][0]["qa"] for line in state(project)["lines"].values())


async def test_pending_skips_approved_lines(project: Project, provider: ToneProvider) -> None:
    await review(project, provider, ["enter", "q"])
    _, _, player = await review(project, provider, ["n", "q"], pending=True)
    assert played(player) == ["p002.1", "p003.1"]


async def test_context_skips_lines_without_takes(project: Project, provider: ToneProvider) -> None:
    _, _, player = await review(project, provider, ["c", "q"])  # p002.1 is not rendered yet
    data = state(project)["lines"]["p001.1"]["takes"][0]["fingerprint"]
    assert frames(player.handles[-1].path) == frames(project.take_path("p001.1", data))


async def test_context_with_mixed_formats_plays_the_take_alone(
    project: Project, provider: ToneProvider
) -> None:
    from tests.conftest import make_wav

    await review(project, provider, ["n", "q"])
    other = state(project)["lines"]["p002.1"]["takes"][0]["fingerprint"]
    project.take_path("p002.1", other).write_bytes(make_wav(500, sample_rate=16000))
    _, terminal, player = await review(project, provider, ["c", "q"], start="p001.1")
    assert "Context unavailable" in terminal.text
    assert played(player) == ["p001.1", "p001.1"]
