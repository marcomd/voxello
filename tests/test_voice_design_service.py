"""Service-level voice design: `instructions`, named designs and their precedence."""

from __future__ import annotations

import pytest

from voxello.config import Settings
from voxello.core.models import VoiceChoice
from voxello.core.service import VoxelloService, resolve_voice
from voxello.errors import INVALID_INSTRUCTIONS, INVALID_PARAMETER, VoxelloError

from .conftest import FakePlayer, FakeProvider, settle

DESIGNS = {"narrator": "female, young adult, high pitch", "whisperer": "male, elderly, whisper"}


def make_settings(**speech) -> Settings:
    return Settings(speech={"voice_designs": DESIGNS, **speech})


# -- resolve_voice (pure) ------------------------------------------------------------


def test_explicit_instructions_are_normalized_and_ignore_configured_voices():
    settings = make_settings(voices_by_language={"it": "clone:marco"})
    settings.tts.voicestudio.voice = "nova"
    assert resolve_voice(settings, None, "it", " Male , LOW pitch") == VoiceChoice(
        None, "male, low pitch"
    )


def test_voice_and_instructions_together_are_rejected():
    with pytest.raises(VoxelloError) as exc:
        resolve_voice(make_settings(), "nova", "it", "female")
    assert exc.value.code == INVALID_PARAMETER
    assert "either" in exc.value.message


def test_invalid_instructions_are_rejected():
    with pytest.raises(VoxelloError) as exc:
        resolve_voice(make_settings(), None, "it", "female, italian accent")
    assert exc.value.code == INVALID_INSTRUCTIONS


@pytest.mark.parametrize(
    ("voice", "by_language", "global_voice", "expected"),
    [
        ("narrator", {}, None, VoiceChoice(None, DESIGNS["narrator"])),
        (None, {"it": "whisperer"}, None, VoiceChoice(None, DESIGNS["whisperer"])),
        (None, {}, "narrator", VoiceChoice(None, DESIGNS["narrator"])),
        ("clone:marco", {"it": "narrator"}, None, VoiceChoice("clone:marco", None)),
        (None, {"it": "clone:marco"}, "narrator", VoiceChoice("clone:marco", None)),
        (None, {}, None, VoiceChoice(None, None)),
        ("design:female", {}, None, VoiceChoice("design:female", None)),
    ],
)
def test_named_designs_resolve_wherever_a_voice_is_accepted(
    voice, by_language, global_voice, expected
):
    settings = make_settings(voices_by_language=by_language)
    settings.tts.voicestudio.voice = global_voice
    assert resolve_voice(settings, voice, "it") == expected


# -- service ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path) -> Settings:  # overrides conftest: same isolation, plus designs
    return Settings(
        storage={"temp_dir": tmp_path / "tmp", "temp_retention_minutes": 1},
        cache={"directory": tmp_path / "cache"},
        output={"directory": tmp_path / "out"},
        playback={"max_queue_size": 3},
        speech={"voice_designs": DESIGNS},
    )


async def test_speak_sends_instructions_and_reports_them(
    service: VoxelloService, provider: FakeProvider, player: FakePlayer
):
    result = await service.speak("Hello", instructions="Female, Whisper", language="en")
    assert provider.calls == [("Hello", None, "en")]
    assert provider.instructions == ["female, whisper"]
    assert result.instructions == "female, whisper"
    assert result.voice == "design:female, whisper"
    await settle()


async def test_speak_with_a_named_design(service: VoxelloService, provider: FakeProvider):
    result = await service.speak("Ciao", voice="narrator", play=False)
    assert provider.calls == [("Ciao", None, "it")]
    assert provider.instructions == [DESIGNS["narrator"]]
    assert result.instructions == DESIGNS["narrator"]


async def test_invalid_instructions_fail_before_synthesis(
    service: VoxelloService, provider: FakeProvider
):
    with pytest.raises(VoxelloError) as exc:
        await service.speak("Ciao", instructions="robot voice")
    assert exc.value.code == INVALID_INSTRUCTIONS
    assert provider.calls == []


async def test_cache_separates_designs_and_hits_the_same_design(
    service: VoxelloService, provider: FakeProvider, player: FakePlayer
):
    await service.speak("Fatto.", cache=True, play=False, instructions="female")
    await service.speak("Fatto.", cache=True, play=False, instructions="male")
    hit = await service.speak("Fatto.", cache=True, play=False, instructions=" FEMALE ")
    assert provider.instructions == ["female", "male"]
    assert hit.cached is True
    assert hit.instructions == "female"
    assert hit.voice == "design:female"


async def test_notify_accepts_voice_and_instructions(
    service: VoxelloService, provider: FakeProvider, player: FakePlayer
):
    await service.notify("Build ok", channels=["voice"], voice="whisperer", cache=False)
    await player.wait_started()
    await service.notify("Build ok", channels=["voice"], instructions="child", cache=False)
    await service.notify("Build ok", channels=["voice"], voice="clone:marco", cache=False)
    assert provider.instructions == [DESIGNS["whisperer"], "child", None]
    assert [voice for _, voice, _ in provider.calls] == [None, None, "clone:marco"]


async def test_notify_reports_rejected_instructions_as_failed_voice(
    service: VoxelloService, provider: FakeProvider
):
    result = await service.notify("Build ok", channels=["voice"], instructions="robot")
    assert result.status == "failed"
    assert result.channels["voice"] == f"error:{INVALID_INSTRUCTIONS}"
    assert provider.calls == []
