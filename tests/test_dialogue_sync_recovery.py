"""Failure, recovery and cross-process ownership of source synchronization."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxello.cli import main
from voxello.dialogue import commands, sync_storage
from voxello.dialogue import project_lock as lock_module
from voxello.dialogue.project import Project
from voxello.dialogue.project_lock import project_lock
from voxello.errors import VoxelloError

from .test_dialogue_sync import SOURCE, TITLES, files, invoke
from .test_dialogue_sync import prepared as prepared


@pytest.fixture
def pending(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Project, dict[str, bytes]]:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    replace = os.replace

    def stop(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == project.script_path:
            raise SystemExit(99)
        replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", stop)
        with pytest.raises(SystemExit):
            invoke(project, str(source))
    return project, before


def test_recovery_can_be_declined_without_any_writes(
    pending: tuple[Project, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = pending
    before = files(project.root)
    monkeypatch.setattr(commands, "ask", lambda _: "n")
    assert invoke(project, "--recover") == 0
    assert files(project.root) == before
    with pytest.raises(VoxelloError, match="recover"):
        Project.open(project.root)


def test_init_cannot_overwrite_a_project_with_an_interrupted_sync(
    pending: tuple[Project, dict[str, bytes]],
) -> None:
    project, _ = pending
    assert not project.script_path.exists()
    before = files(project.root)
    source = project.root.parent / "chapter.txt"
    with pytest.raises(VoxelloError, match="recover"):
        Project.init(source, project.root)
    assert files(project.root) == before


def test_failed_recovery_can_be_retried(
    pending: tuple[Project, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, before = pending
    replace = os.replace

    def fail_restore(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == project.source_path:
            raise OSError("disk unavailable")
        replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_restore)
        assert invoke(project, "--recover") == 1
    assert (project.root / sync_storage.JOURNAL).is_file()
    assert invoke(project, "--recover") == 0
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data


def test_apply_and_rollback_failure_leave_a_recoverable_project(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    replace = os.replace

    def fail_script(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == project.script_path:
            raise OSError("disk unavailable")
        replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_script)
        assert invoke(project, str(source)) == 1
    assert "rollback failed" in capsys.readouterr().err
    assert invoke(project, "--recover") == 0
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data


@pytest.mark.parametrize("published", [False, True])
def test_commit_marker_failure_keeps_project_recoverable(
    prepared: tuple[Project, Path], monkeypatch: pytest.MonkeyPatch, published: bool
) -> None:
    project, source = prepared
    original = project.source_path.read_bytes()
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    write = sync_storage._durable_write

    def fail_committed_marker(path: Path, data: bytes) -> None:
        if path == project.root / sync_storage.JOURNAL and json.loads(data)["phase"] == "committed":
            if published:
                sync_storage.write_atomic(path, data)
            raise OSError("journal persistence failed")
        write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(sync_storage, "_durable_write", fail_committed_marker)
        assert invoke(project, str(source)) == 1
    assert invoke(project, "--recover") == 0
    assert project.source_path.read_bytes() == (source.read_bytes() if published else original)
    assert Project.open(project.root).check().ok


def test_commit_cleanup_failure_keeps_new_project_and_finishes_on_recovery(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    unlink = Path.unlink

    def fail_cleanup(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == project.root / sync_storage.JOURNAL:
            raise OSError("cleanup denied")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_cleanup)
        assert invoke(project, str(source)) == 1
    expected = sync_storage.snapshot(project.root)
    assert project.load_script().segment("p003.1").tts_text is not None
    assert invoke(project, "--recover") == 0
    assert sync_storage.snapshot(project.root) == expected
    assert Project.open(project.root).check().ok


def test_recovery_refuses_a_damaged_backup(pending: tuple[Project, dict[str, bytes]]) -> None:
    project, _ = pending
    archive = next((project.root / "sync-backups").iterdir())
    (archive / "before/source.txt").write_text("damaged", encoding="utf-8")
    before = files(project.root)
    assert invoke(project, "--recover") == 1
    assert files(project.root) == before


@pytest.mark.parametrize("damage", ["invalid-json", "unsafe-path", "missing-backup"])
def test_recovery_rejects_bad_journals_and_missing_backups(
    pending: tuple[Project, dict[str, bytes]],
    damage: str,
) -> None:
    project, _ = pending
    marker = project.root / sync_storage.JOURNAL
    data = json.loads(marker.read_bytes())
    if damage == "invalid-json":
        marker.write_text("[", encoding="utf-8")
    elif damage == "unsafe-path":
        data["archive"] = "../../other-project"
        marker.write_text(json.dumps(data), encoding="utf-8")
    else:
        archive = project.root / "sync-backups" / data["archive"]
        archive.rename(archive.with_name("retained-copy"))
    before = files(project.root)
    assert invoke(project, "--recover") == 1
    assert files(project.root) == before


def test_recovery_rechecks_the_journal_after_confirmation(
    pending: tuple[Project, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = pending
    marker = project.root / sync_storage.JOURNAL

    def confirm(_: str) -> str:
        marker.write_bytes(marker.read_bytes() + b"\n")
        return "y"

    monkeypatch.setattr(commands, "ask", confirm)
    assert invoke(project, "--recover") == 1
    assert marker.is_file()


def test_preparation_error_leaves_active_files_untouched(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")

    def full_disk(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", full_disk)
    assert invoke(project, str(source)) == 1
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data
    assert not (project.root / sync_storage.JOURNAL).exists()


def test_corrupted_prepared_audio_is_rejected_before_apply(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    copy = sync_storage._copy

    # Hook the project's copy helper, not shutil.copyfile: on Windows shutil.copy2 (and
    # copytree through it) uses CopyFile2 and never calls copyfile.
    def corrupt_audio(source: Path, target: Path) -> None:
        copy(source, target)
        if "after" not in target.parts:
            return
        for path in [target, *target.rglob("*")] if target.is_dir() else [target]:
            if path.suffix == ".wav":
                path.write_bytes(b"corrupt audio")

    monkeypatch.setattr(sync_storage, "_copy", corrupt_audio)
    assert invoke(project, str(source)) == 1
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data
    assert not (project.root / sync_storage.JOURNAL).exists()


def test_rollback_restores_the_already_edited_internal_source_exactly(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = prepared
    project.source_path.write_bytes((TITLES + SOURCE).replace("\n", "\r\n").encode())
    before = files(project.root)
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    replace = os.replace
    failed = False

    def fail_once(src: str | Path, dst: str | Path) -> None:
        nonlocal failed
        if Path(dst) == project.state_path and not failed:
            failed = True
            raise OSError("state replacement failed")
        replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_once)
    assert invoke(project, str(project.source_path)) == 1
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data
    assert not (project.root / sync_storage.JOURNAL).exists()


def test_rollback_preserves_absence_of_uninitialized_state_and_takes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.txt"
    source.write_text("Una frase.", encoding="utf-8")
    project = Project.init(source, tmp_path / "project")
    before = files(project.root)
    source.write_text(TITLES + "Una frase.", encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    replace = os.replace

    def fail_takes(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == project.takes_dir:
            raise OSError("takes replacement failed")
        replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_takes)
    assert invoke(project, str(source)) == 1
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data
    assert not project.state_path.exists()
    assert not project.takes_dir.exists()
    assert not (project.root / sync_storage.JOURNAL).exists()


def test_keyboard_interrupt_during_apply_rolls_back(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    before = files(project.root)
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    replace = os.replace
    interrupted = False

    def interrupt(src: str | Path, dst: str | Path) -> None:
        nonlocal interrupted
        if Path(dst) == project.script_path and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        replace(src, dst)

    monkeypatch.setattr(os, "replace", interrupt)
    assert invoke(project, str(source)) == 130
    for name, data in before.items():
        assert (project.root / name).read_bytes() == data
    assert not (project.root / sync_storage.JOURNAL).exists()


def test_another_process_cannot_run_a_command_while_project_is_locked(
    prepared: tuple[Project, Path],
) -> None:
    project, source = prepared
    with project_lock(project.root):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "voxello",
                "--config",
                str(source.parent / "missing.yaml"),
                "dialogue",
                "sync",
                str(source),
                "-p",
                str(project.root),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert result.returncode == 1
    assert "busy" in result.stderr
    # The lock is released when its owner exits the context.
    assert invoke(project, str(source), "--dry-run") == 0


def test_other_dialogue_commands_hold_the_same_project_lock(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = prepared

    def inspect_lock(_project: Project, _args: object) -> int:
        with pytest.raises(VoxelloError, match="busy"):
            with project_lock(project.root):
                pytest.fail("command did not own the lock")
        return 0

    monkeypatch.setattr(commands, "cmd_segments", inspect_lock)
    assert main(["dialogue", "segments", "-p", str(project.root)]) == 0


def test_windows_lock_protocol_and_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    calls: list[tuple[int, int]] = []
    api = SimpleNamespace(
        LK_NBLCK=1, LK_UNLCK=2, locking=lambda _fd, mode, count: calls.append((mode, count))
    )
    monkeypatch.setattr(lock_module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(lock_module, "importlib", SimpleNamespace(import_module=lambda _: api))
    with project_lock(root):
        assert calls == [(1, 1)]
    assert calls == [(1, 1), (2, 1)]
    assert files(root) == {}


def test_windows_sync_uses_writable_handles_for_flushing(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, source = prepared
    source.write_text(TITLES + SOURCE, encoding="utf-8")
    monkeypatch.setattr(commands, "ask", lambda _: "y")
    writable: dict[int, bool] = {}
    original_open = Path.open

    def track_open(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        handle = original_open(path, *args, **kwargs)
        writable[handle.fileno()] = handle.writable()
        return handle

    def windows_flush(fd: int) -> None:
        if not writable[fd]:
            raise OSError("FlushFileBuffers requires write access")

    monkeypatch.setattr(Path, "open", track_open)
    monkeypatch.setattr(sync_storage, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(os, "fsync", windows_flush)
    assert invoke(project, str(source)) == 0
    assert all(project.pick(project.load_state(), line).approved for line in project.lines()[2:])


@pytest.mark.parametrize("args", [[], ["--recover", "source.txt"], ["--recover", "--dry-run"]])
def test_sync_arguments_are_validated(prepared: tuple[Project, Path], args: list[str]) -> None:
    project, _ = prepared
    before = files(project.root)
    assert invoke(project, *args) == 1
    assert files(project.root) == before


def test_recover_without_pending_work_does_not_prompt(
    prepared: tuple[Project, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _ = prepared
    before = files(project.root)
    monkeypatch.setattr(commands, "ask", lambda _: pytest.fail("unexpected confirmation"))
    assert invoke(project, "--recover") == 0
    assert files(project.root) == before
