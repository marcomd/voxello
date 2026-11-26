"""Source synchronization through the CLI: identity, pronunciation and recovery."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from tests.conftest import FakeProvider, make_wav
from voxello.cli import main
from voxello.dialogue import commands
from voxello.dialogue.project import Project
from voxello.dialogue.render import render
from voxello.errors import VoxelloError

SOURCE = "La giacca era color antracite.\n\nLa finestra era aperta.\n"
CUSTOM = "La giacca era color antracíte"
TITLES = "## Capitolo 1, Il tempo per noi.\n\n#### Scena 1, Il sabato senza sveglia.\n\n"


def files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def invoke(project: Project, *args: str) -> int:
    return main(
        [
            "--config",
            str(project.root.parent / "missing.yaml"),
            "dialogue",
            "sync",
            "-p",
            str(project.root),
            *args,
        ]
    )


@pytest.fixture
async def prepared(tmp_path: Path) -> tuple[Project, Path]:
    source = tmp_path / "chapter.txt"
    source.write_text(SOURCE, encoding="utf-8")
    project = Project.init(source, tmp_path / "chapter")
    project.cast_path.write_text(
        yaml.safe_dump(
            {
                "narrator": "narratore",
                "language": "it",
                "characters": {
                    "narratore": {
                        "voice": "clone:narratore",
                        "registers": {"whisper": "clone:narratore-whisper"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    project.annotate({"p001.1": {"tts_text": CUSTOM, "speed": 0.95, "register": "whisper"}})
    await render(project, FakeProvider())
    state = project.load_state()
    extra = project.store_take(
        state,
        "p001.1",
        project.lines()[0].request.with_seed(29),
        make_wav(300),
    )
    state.lines["p001.1"].selected = extra.fingerprint
    for entry in state.lines.values():
        entry.approved = entry.takes[-1].fingerprint
    state.review.position = "p001.1"
    project.save_state(state)
    return project, source


async def test_two_titles_keep_pronunciation_takes_selection_approval_and_qa(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    old_script, old_state = project.load_script(), project.load_state()
    old_audio = files(project.takes_dir)
    cast = project.cast_path.read_bytes()
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert project.check().ok
    assert project.cast_path.read_bytes() == cast
    for old, new in (("p001.1", "p003.1"), ("p002.1", "p004.1")):
        assert project.load_script().segment(new).model_dump(exclude={"id"}) == (
            old_script.segment(old).model_dump(exclude={"id"})
        )
        assert project.load_state().lines[new] == old_state.lines[old]
        for name, audio in old_audio.items():
            if name.startswith(old + "/"):
                assert (project.takes_dir / name.replace(old, new, 1)).read_bytes() == audio
    assert project.load_state().review.position == "p003.1"
    provider = FakeProvider()
    result = await render(project, provider)
    assert (result.rendered, result.reused) == (2, 2)
    assert all(CUSTOM != call[0] for call in provider.calls)


def test_reordering_paragraphs_swaps_ids_without_overwriting_audio(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    before = project.load_state()
    source.write_text(
        "La finestra era aperta.\n\nLa giacca era color antracite.\n", encoding="utf-8"
    )
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert project.load_state().lines["p002.1"] == before.lines["p001.1"]
    assert project.load_state().lines["p001.1"] == before.lines["p002.1"]
    assert all(project.pick(project.load_state(), line).approved for line in project.lines())


def test_changed_sentence_warns_and_archives_custom_text_and_audio(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(SOURCE.replace("La giacca era", "La sciarpa era"), encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    out = capsys.readouterr().out
    assert CUSTOM in out
    assert "not transferred" in out
    assert project.load_script().segment("p001.1").tts_text is None
    assert "p001.1" not in project.load_state().lines
    archive = next((project.root / "sync-backups").iterdir())
    for name, data in before.items():
        assert (archive / "before" / name).read_bytes() == data
    assert (archive / "report.json").is_file()


@pytest.mark.parametrize("answer", ["n", ""])
def test_refusal_changes_no_project_file(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: answer)
    assert invoke(project, str(source)) == 0
    assert files(project.root) == before


def test_preview_does_not_prompt_or_write(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: pytest.fail("preview prompted"))
    assert invoke(project, str(source), "--dry-run") == 0
    out = capsys.readouterr().out
    assert "p001.1 -> p003.1" in out
    assert "approved" in out
    assert files(project.root) == before


def test_internal_source_already_changed_uses_old_script(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, _ = prepared
    old = project.load_state().lines["p001.1"]
    project.source_path.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(project.source_path)) == 0
    assert project.check().ok
    assert project.load_state().lines["p003.1"] == old
    assert "old source bytes" in capsys.readouterr().out


def test_duplicate_paragraphs_are_not_matched_arbitrarily(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, source = prepared
    source.write_text(SOURCE + "\nLa giacca era color antracite.\n", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    out = capsys.readouterr().out
    assert "ambiguous" in out
    assert "p001.1" not in project.load_state().lines
    assert "p003.1" not in project.load_state().lines
    assert project.load_script().segment("p001.1").tts_text is None
    assert project.load_script().segment("p003.1").tts_text is None


def test_error_mid_apply_restores_original_project(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    replace = os.replace
    failed = False

    def fail_once(src: str | Path, dst: str | Path) -> None:
        nonlocal failed
        if Path(dst) == project.script_path and not failed:
            failed = True
            raise OSError("disk failure")
        replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_once)
    assert invoke(project, str(source)) == 1
    assert "storage_error" in capsys.readouterr().err
    assert failed
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data
    assert not (project.root / ".sync-transaction.json").exists()
    assert Project.open(project.root).check().ok


def test_abrupt_stop_blocks_commands_until_explicit_recovery(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    replace = os.replace

    def abrupt_stop(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == project.script_path:
            raise SystemExit(99)  # emulate process death, bypassing the normal rollback
        replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", abrupt_stop)
        with pytest.raises(SystemExit):
            invoke(project, str(source))
    with pytest.raises(VoxelloError, match="recover"):
        Project.open(project.root)
    assert invoke(project, "--recover") == 0
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data
    assert Project.open(project.root).check().ok


def test_no_changes_preserves_files_and_does_not_prompt(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    before = files(project.root)
    monkeypatch.setattr(commands, "ask", lambda _: pytest.fail("no-op prompted"))
    assert invoke(project, str(source)) == 0
    assert files(project.root) == before


def test_source_change_while_confirming_rejects_stale_plan(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")

    def confirm(_: str) -> str:
        source.write_text("Another edit.", encoding="utf-8")
        return "y"

    monkeypatch.setattr(commands, "ask", confirm)
    assert invoke(project, str(source)) == 1
    assert files(project.root) == before


def test_report_records_original_text_and_custom_pronunciation(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    source.write_text("Solo una frase nuova.", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    archive = next((project.root / "sync-backups").iterdir())
    report = json.loads((archive / "report.json").read_text())
    assert report["unmatched_old"][0]["tts_text"] == CUSTOM
    assert report["unmatched_old"][0]["text"] == SOURCE.split("\n")[0]


def test_damaged_backup_is_detected_before_any_active_file_changes(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    copy = shutil.copy2

    def corrupt_copy(src, dst, **kwargs):  # type: ignore[no-untyped-def]
        result = copy(src, dst, **kwargs)
        if Path(dst).parent.name == "before" and Path(dst).name == "source.txt":
            Path(dst).write_text("corrupt backup", encoding="utf-8")
        return result

    monkeypatch.setattr(shutil, "copy2", corrupt_copy)
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 1
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data
    assert not (project.root / ".sync-transaction.json").exists()


@pytest.mark.parametrize(
    "new_text", ["Una frase. Un'altra frase.", "Una frase.\n\nUn'altra frase."]
)
def test_split_or_merged_segments_do_not_inherit_whole_sentence_customizations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    new_text: str,
) -> None:
    source = tmp_path / "input.txt"
    old = "Una frase.\n\nUn'altra frase." if "\n" not in new_text else "Una frase. Un'altra frase."
    source.write_text(old, encoding="utf-8")
    project = Project.init(source, tmp_path / "project")
    project.annotate({"p001.1": {"tts_text": "Pronuncia personalizzata"}})
    source.write_text(new_text, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert all(s.tts_text is None for s in project.load_script().segments)


def test_unique_paragraph_context_preserves_repeated_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("«Sì.» disse Anna.\n\n«Sì.» disse Bruno.", encoding="utf-8")
    project = Project.init(source, tmp_path / "project")
    project.annotate(
        {
            "p001.1": {"tts_text": "Sì", "speaker": "anna"},
            "p002.1": {"tts_text": "[sigh] Sì.", "speaker": "bruno"},
        }
    )
    source.write_text(TITLES + "«Sì.» disse Bruno.\n\n«Sì.» disse Anna.", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert project.load_script().segment("p003.1").tts_text == "[sigh] Sì."
    assert project.load_script().segment("p004.1").speaker == "anna"


def test_a_unique_segment_survives_a_change_elsewhere_in_its_paragraph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("«Sì.» disse Anna.", encoding="utf-8")
    project = Project.init(source, tmp_path / "project")
    project.annotate({"p001.1": {"tts_text": "Sì", "speaker": "anna"}})
    source.write_text("«Sì.» rispose Anna.", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert project.load_script().segment("p001.1").tts_text == "Sì"
    assert project.load_script().segment("p001.2").tts_text is None


def test_deletion_archives_orphan_audio_and_resets_deleted_review_position(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    orphan = project.takes_dir / "p999.1" / "unrecorded.wav"
    orphan.parent.mkdir()
    orphan.write_bytes(b"orphan audio")
    source.write_text("La finestra era aperta.", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert project.load_state().review.position is None
    archive = next((project.root / "sync-backups").iterdir())
    assert (archive / "before/takes/p999.1/unrecorded.wav").read_bytes() == b"orphan audio"
    assert not (project.takes_dir / "p999.1").exists()


@pytest.mark.parametrize("stale", ["missing-audio", "voice", "unapproved"])
def test_preview_uses_existing_audio_validity_rules(
    prepared: tuple[Project, Path],
    stale: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, source = prepared
    if stale == "missing-audio":
        shutil.rmtree(project.takes_dir / "p001.1")
    elif stale == "voice":
        project.cast_path.write_text(
            project.cast_path.read_text().replace("clone:narratore-whisper", "clone:other"),
            encoding="utf-8",
        )
    else:
        state = project.load_state()
        state.lines["p001.1"].approved = None
        project.save_state(state)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    before = files(project.root)
    assert invoke(project, str(source), "--dry-run") == 0
    expected = "review needed" if stale == "unapproved" else "render needed"
    assert f"p001.1 -> p003.1: {expected}" in capsys.readouterr().out
    assert files(project.root) == before


@pytest.mark.parametrize("raw", [b"\xff", None])
def test_invalid_source_leaves_project_unchanged(
    prepared: tuple[Project, Path],
    raw: bytes | None,
) -> None:
    project, source = prepared
    before = files(project.root)
    if raw is None:
        source.unlink()
    else:
        source.write_bytes(raw)
    assert invoke(project, str(source), "--dry-run") == 1
    assert files(project.root) == before


@pytest.mark.parametrize("damage", ["duplicate-id", "unsafe-id", "state", "script"])
def test_damaged_project_is_not_silently_repaired(
    prepared: tuple[Project, Path],
    damage: str,
) -> None:
    project, source = prepared
    if damage == "state":
        project.state_path.write_text("not json", encoding="utf-8")
    elif damage == "script":
        project.script_path.write_text("segments: [", encoding="utf-8")
    else:
        script = project.load_script()
        script.segments[-1].id = script.segments[0].id if damage == "duplicate-id" else "../bad"
        project.save_script(script)
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    assert invoke(project, str(source), "--dry-run") == 1
    assert files(project.root) == before


def test_breaks_skips_and_dropped_attributions_remain_unspoken(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, source = prepared
    project.annotate({"p001.1": {"skip": True}, "p002.1": {"attribution": True}})
    cast = yaml.safe_load(project.cast_path.read_text())
    cast["attributions"] = "drop"
    project.cast_path.write_text(yaml.safe_dump(cast), encoding="utf-8")
    source.write_text(TITLES + SOURCE + "\n---\n", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert "p001.1 -> p003.1: not spoken" in capsys.readouterr().out
    assert len(project.lines()) == 2


def test_first_sync_works_before_cast_annotations_and_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("«Ciao.»", encoding="utf-8")
    project = Project.init(source, tmp_path / "project", max_chars=8)
    source.write_text("## Titolo\n\n«Ciao.»\n\n«Arrivo.»", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert project.load_script().max_chars == 8
    assert project.load_state().lines == {}
    assert not project.cast_path.exists()


def test_empty_source_archives_everything(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    source.write_text("", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    assert invoke(project, str(source)) == 0
    assert project.load_script().segments == []
    assert project.load_state().lines == {}
    assert project.check().ok
