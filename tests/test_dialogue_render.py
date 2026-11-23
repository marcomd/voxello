"""Takes, fingerprints, state.json and `dialogue render` (roadmap 8.4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import FakeProvider
from voxello.dialogue.project import LineDefaults, Project
from voxello.dialogue.render import RenderEvent, render
from voxello.dialogue.takes import LineRequest, fingerprint
from voxello.errors import (
    INVALID_SCRIPT,
    STORAGE_ERROR,
    TTS_PROVIDER_UNAVAILABLE,
    VoxelloError,
)
from voxello.tts.base import SynthesisOptions

SOURCE = """Anna aprì la finestra.

«Porto fuori il cane.» disse Bruno.

«Fino al parco?» domandò Anna.

---

#### Scena 2 · Bruno
"""

CAST: dict[str, Any] = {
    "language": "it",
    "narrator": "narratore",
    "generation": {"seed": 7, "num_step": 16},
    "characters": {
        "narratore": {"voice": "onyx"},
        "bruno": {"voice": "clone:bruno", "registers": {"whisper": "clone:bruno-whisper"}},
        "anna": {"instructions": "female, young adult"},
    },
}

ANNOTATIONS = {
    "p002.1": {"speaker": "bruno", "register": "whisper"},
    "p002.2": {"attribution": True},
    "p003.1": {"speaker": "anna", "speed": 0.9},
    "p005.1": {"tts_text": "Scena 2"},
}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    source = tmp_path / "chapter1.txt"
    source.write_text(SOURCE, encoding="utf-8")
    project = Project.init(source, tmp_path / "chapter1")
    project.annotate(ANNOTATIONS)
    project.cast_path.write_text(yaml.safe_dump(CAST), encoding="utf-8")
    return project


def request(**overrides: Any) -> LineRequest:
    values: dict[str, Any] = {
        "tts_text": "Ciao.",
        "voice": "clone:bruno",
        "instructions": None,
        "language": "it",
        "speed": None,
        "num_step": 16,
        "seed": 1,
        "guidance_scale": None,
    }
    values.update(overrides)
    return LineRequest(**values)


# --- fingerprint ----------------------------------------------------------------------------


def test_fingerprint_is_stable_and_covers_every_input() -> None:
    base = fingerprint(request())
    assert base == fingerprint(request())
    assert len(base) == 64
    variants = [
        {"tts_text": "Ciao!"},
        {"voice": "clone:bruno-whisper"},
        {"voice": None, "instructions": "male"},
        {"language": "en"},
        {"speed": 0.9},
        {"num_step": 32},
        {"seed": 2},
        {"guidance_scale": 3.0},
    ]
    assert len({fingerprint(request(**v)) for v in variants} | {base}) == len(variants) + 1


def test_request_options() -> None:
    assert request(speed=0.9).options() == SynthesisOptions(seed=1, speed=0.9, num_step=16)


# --- project --------------------------------------------------------------------------------


def test_init_copies_the_source_and_writes_the_script(tmp_path: Path) -> None:
    source = tmp_path / "c.txt"
    source.write_bytes("Uno.\r\n\r\n«Due.»\r\n".encode())
    project = Project.init(source, tmp_path / "c", max_chars=200)
    assert project.source_path.read_bytes() == source.read_bytes()
    script = project.load_script()
    assert [s.id for s in script.segments] == ["p001.1", "p002.1"]
    assert script.max_chars == 200
    assert project.check().errors[0].message == "speech has no speaker"


def test_init_refuses_an_existing_project(project: Project, tmp_path: Path) -> None:
    with pytest.raises(VoxelloError) as err:
        Project.init(tmp_path / "chapter1.txt", project.root)
    assert err.value.code == INVALID_SCRIPT


def test_init_refuses_a_missing_or_undecodable_source(tmp_path: Path) -> None:
    with pytest.raises(VoxelloError):
        Project.init(tmp_path / "missing.txt", tmp_path / "x")
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(VoxelloError) as err:
        Project.init(bad, tmp_path / "y")
    assert "UTF-8" in err.value.message


def test_open_requires_a_project(tmp_path: Path) -> None:
    with pytest.raises(VoxelloError) as err:
        Project.open(tmp_path)
    assert "dialogue init" in err.value.message


def test_annotate_persists(project: Project) -> None:
    reopened = Project.open(project.root)
    assert reopened.load_script().segment("p002.1").register_ == "whisper"


def test_load_cast_missing_and_invalid(project: Project) -> None:
    project.cast_path.unlink()
    assert project.load_cast() is None
    project.cast_path.write_text("narrator: [", encoding="utf-8")
    with pytest.raises(VoxelloError) as err:
        project.load_cast()
    assert err.value.code == INVALID_SCRIPT


# --- line requests --------------------------------------------------------------------------


def test_line_requests_resolve_voices_and_values(project: Project) -> None:
    lines = {
        line.segment_id: line.request
        for line in project.lines(LineDefaults(speed=1.1, guidance_scale=2.0))
    }
    assert lines["p002.1"].voice == "clone:bruno-whisper"
    assert lines["p002.1"].tts_text == "Porto fuori il cane."
    assert lines["p002.1"].speed == 1.1  # cast leaves speed to config.yaml
    assert lines["p003.1"].instructions == "female, young adult"
    assert lines["p003.1"].voice is None
    assert lines["p003.1"].speed == 0.9
    assert lines["p005.1"].tts_text == "Scena 2"
    assert {r.seed for r in lines.values()} == {7}
    assert {r.guidance_scale for r in lines.values()} == {2.0}
    assert "p004.1" not in lines  # the break is silent


def test_drop_attributions_skips_flagged_narration(project: Project) -> None:
    cast = dict(CAST, attributions="drop")
    project.cast_path.write_text(yaml.safe_dump(cast), encoding="utf-8")
    assert "p002.2" not in {line.segment_id for line in project.lines()}


def test_lines_refuse_an_invalid_project(project: Project) -> None:
    project.annotate({"p002.1": {"speaker": None}})
    with pytest.raises(VoxelloError) as err:
        project.lines()
    assert err.value.code == INVALID_SCRIPT
    assert "p002.1: speech has no speaker" in err.value.message


def test_lines_need_a_cast(project: Project) -> None:
    project.cast_path.unlink()
    with pytest.raises(VoxelloError) as err:
        project.lines()
    assert "cast.yaml" in err.value.message


# --- render ---------------------------------------------------------------------------------


async def test_render_synthesizes_every_spoken_line_once(project: Project) -> None:
    provider = FakeProvider()
    events: list[RenderEvent] = []
    summary = await render(project, provider, progress=events.append)
    assert summary.rendered == 6
    assert summary.reused == 0
    assert [c[0] for c in provider.calls][:2] == [
        "Anna aprì la finestra.",
        "Porto fuori il cane.",
    ]
    assert provider.options[0] == SynthesisOptions(seed=7, speed=None, num_step=16)
    assert provider.instructions[3] == "female, young adult"
    assert [e.segment_id for e in events] == [
        "p001.1",
        "p002.1",
        "p002.2",
        "p003.1",
        "p003.2",
        "p005.1",
    ]
    state = json.loads(project.state_path.read_text(encoding="utf-8"))
    take = state["lines"]["p002.1"]["takes"][0]
    assert take["seed"] == 7
    assert take["duration_ms"] == 500
    assert state["lines"]["p002.1"]["approved"] is None
    assert project.take_path("p002.1", take["fingerprint"]).is_file()
    assert not list(project.root.rglob("*.tmp"))


async def test_second_render_calls_the_provider_zero_times(project: Project) -> None:
    await render(project, FakeProvider())
    provider = FakeProvider()
    summary = await render(project, provider)
    assert provider.calls == []
    assert (summary.rendered, summary.reused) == (0, 6)


async def test_editing_one_line_rerenders_it_and_clears_its_approval(project: Project) -> None:
    await render(project, FakeProvider())
    state = project.load_state()
    for line in state.lines.values():
        line.approved = line.takes[0].fingerprint
    project.save_state(state)
    old = state.lines["p002.1"].takes[0].fingerprint

    project.annotate({"p002.1": {"tts_text": "[sigh] Porto fuori il cane."}})
    provider = FakeProvider()
    summary = await render(project, provider)

    assert [c[0] for c in provider.calls] == ["[sigh] Porto fuori il cane."]
    assert summary.rendered == 1
    state = project.load_state()
    assert state.lines["p002.1"].approved is None
    assert state.lines["p002.1"].takes[0].fingerprint == old  # older take kept
    assert len(state.lines["p002.1"].takes) == 2
    assert state.lines["p001.1"].approved is not None


async def test_a_take_with_another_seed_still_counts(project: Project) -> None:
    """A take chosen in review (another seed) stays current while its inputs do not change."""
    lines = {line.segment_id: line for line in project.lines()}
    line = lines["p001.1"]
    other = line.request.with_seed(42)
    state = project.load_state()
    project.store_take(state, "p001.1", other, b"RIFF")
    state.lines["p001.1"].approved = fingerprint(other)
    project.save_state(state)
    provider = FakeProvider()
    await render(project, provider, lines="p001.1")
    assert provider.calls == []
    assert project.load_state().lines["p001.1"].approved == fingerprint(other)


async def test_missing_take_file_is_rendered_again(project: Project) -> None:
    await render(project, FakeProvider())
    state = project.load_state()
    project.take_path("p001.1", state.lines["p001.1"].takes[0].fingerprint).unlink()
    provider = FakeProvider()
    await render(project, provider)
    assert len(provider.calls) == 1
    assert len(project.load_state().lines["p001.1"].takes) == 1  # same fingerprint, replaced


async def test_render_a_range_and_force(project: Project) -> None:
    provider = FakeProvider()
    summary = await render(project, provider, lines="p002.1-p002.2")
    assert summary.rendered == 2
    provider = FakeProvider()
    summary = await render(project, provider, lines="p002.1", force=True)
    assert (summary.rendered, len(provider.calls)) == (1, 1)
    assert len(project.load_state().lines["p002.1"].takes) == 1


async def test_provider_error_names_the_line_and_keeps_earlier_takes(project: Project) -> None:
    await render(project, FakeProvider(), lines="p001.1")
    project.annotate({"p002.1": {"tts_text": "Altro."}, "p001.1": {"tts_text": "Altro."}})
    provider = FakeProvider()
    provider.fail_with = VoxelloError(TTS_PROVIDER_UNAVAILABLE, "down")
    with pytest.raises(VoxelloError) as err:
        await render(project, provider)
    assert err.value.code == TTS_PROVIDER_UNAVAILABLE
    assert err.value.message.startswith("p001.1: ")
    assert err.value.details["segment_id"] == "p001.1"
    assert len(project.load_state().lines["p001.1"].takes) == 1


async def test_cancellation_leaves_no_partial_take(project: Project) -> None:
    provider = FakeProvider()
    provider.delay = 10
    task = asyncio.create_task(render(project, provider))
    async with asyncio.timeout(1):
        while not provider.calls:
            await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not list(project.takes_dir.rglob("*.wav"))
    assert not list(project.root.rglob("*.tmp"))


async def test_resume_after_interruption(project: Project) -> None:
    provider = FakeProvider()
    calls = 0

    def stop_after_two(event: RenderEvent) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        await render(project, provider, progress=stop_after_two)
    provider = FakeProvider()
    summary = await render(project, provider)
    assert (summary.rendered, summary.reused) == (4, 2)


def test_corrupt_state_is_reported(project: Project) -> None:
    project.state_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(VoxelloError) as err:
        project.load_state()
    assert err.value.code == STORAGE_ERROR
    assert "state.json" in err.value.message


def test_failed_write_leaves_neither_file_nor_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from voxello.dialogue import project as project_module

    def broken_replace(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(project_module.os, "replace", broken_replace)
    target = tmp_path / "takes" / "p001.1" / "abc.wav"
    with pytest.raises(OSError, match="disk full"):
        project_module.write_atomic(target, b"RIFF")
    assert list(target.parent.iterdir()) == []
