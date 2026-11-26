"""Recoverable multi-file sync, with copies retained locally under sync-backups/.

A durable marker precedes the first active-file change. Ordinary CLI commands own the same
OS lock and refuse projects with that marker. Applying failures roll back; process death
leaves a marker for explicit recovery. Backups are never consumed during rollback, which
makes recovery repeatable even when recovery itself fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from voxello.dialogue.project import SYNC_JOURNAL, Project, write_atomic
from voxello.dialogue.project_lock import project_lock
from voxello.errors import STORAGE_ERROR, VoxelloError

if TYPE_CHECKING:
    from voxello.dialogue.sync import SyncPlan

MANAGED = ("source.txt", "script.yaml", "state.json", "takes")
SNAPSHOT_NAMES = (*MANAGED, "cast.yaml")
JOURNAL = SYNC_JOURNAL


class Journal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    archive: str = Field(pattern=r"^[a-f0-9]{32}$")
    phase: Literal["applying", "committed"] = "applying"
    original: dict[str, str]
    expected: dict[str, str]


@dataclass(frozen=True)
class Recovery:
    raw: bytes
    journal: Journal
    archive: Path


def _plain(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not (path.is_dir() or path.is_file())):
        raise VoxelloError(STORAGE_ERROR, f"Sync requires ordinary files/directories: {path}")


def snapshot(root: Path) -> dict[str, str]:
    """Hash all managed files, including unrecorded takes; reject links and special files."""
    _plain(root)
    result: dict[str, str] = {}
    for name in SNAPSHOT_NAMES:
        base = root / name
        _plain(base)
        if not base.exists():
            continue
        if (name == "takes") != base.is_dir():
            raise VoxelloError(STORAGE_ERROR, f"Unexpected file type: {base}")
        paths = [base, *sorted(base.rglob("*"))] if base.is_dir() else [base]
        for path in paths:
            _plain(path)
            key = path.relative_to(root).as_posix()
            if path.is_dir():
                result[key + "/"] = "directory"
            else:
                with path.open("rb") as handle:
                    result[key] = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def _copy(source: Path, target: Path) -> None:
    _plain(source)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)


def _sync_dir(path: Path) -> None:
    # Windows does not expose directory fsync through the standard library.
    if sys.platform == "win32":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _flush(path: Path) -> None:
    # Windows FlushFileBuffers requires a handle opened with write access.
    with path.open("r+b" if sys.platform == "win32" else "rb") as handle:
        os.fsync(handle.fileno())


def _durable_write(path: Path, data: bytes) -> None:
    write_atomic(path, data)
    _flush(path)
    _sync_dir(path.parent)


def _flush_tree(root: Path) -> None:
    paths = list(root.rglob("*"))
    for path in paths:
        if path.is_file():
            _flush(path)
    for path in sorted((p for p in paths if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        _sync_dir(path)
    _sync_dir(root)


def _journal_path(project: Project) -> Path:
    return project.root / JOURNAL


def _load_recovery(project: Project) -> Recovery | None:
    marker = _journal_path(project)
    _plain(marker)
    if not marker.exists():
        return None
    raw = marker.read_bytes()
    try:
        journal = Journal.model_validate_json(raw)
    except ValidationError as exc:
        raise VoxelloError(
            STORAGE_ERROR, "Sync journal is damaged; retain the recovery copies."
        ) from exc
    backups = project.root / "sync-backups"
    archive = backups / journal.archive
    for path in (backups, archive):
        _plain(path)
    if not archive.is_dir():
        raise VoxelloError(STORAGE_ERROR, f"Sync recovery copy is missing: {archive}")
    return Recovery(raw, journal, archive)


def recovery_plan(project: Project) -> Recovery | None:
    with project_lock(project.root):
        try:
            return _load_recovery(project)
        except OSError as exc:
            raise VoxelloError(STORAGE_ERROR, f"Cannot inspect sync recovery: {exc}") from exc


def _displace(project: Project, name: str, archive: Path) -> None:
    target = project.root / name
    _plain(target)
    if target.exists():
        displaced = archive / "displaced"
        displaced.mkdir(exist_ok=True)
        os.replace(target, displaced / f"{uuid4().hex}-{name}")
        _sync_dir(displaced)
        _sync_dir(project.root)


def _restore(project: Project, recovery: Recovery) -> None:
    backup = recovery.archive / "before"
    if snapshot(backup) != recovery.journal.original:
        raise VoxelloError(STORAGE_ERROR, "Recovery copy is damaged; project remains blocked.")
    restore = recovery.archive / f"restore-{uuid4().hex}"
    restore.mkdir()
    for name in MANAGED:
        path = backup / name
        if path.exists():
            _copy(path, restore / name)
    _flush_tree(restore)
    for name in MANAGED:
        _displace(project, name, recovery.archive)
        if (restore / name).exists():
            os.replace(restore / name, project.root / name)
        _sync_dir(project.root)
    restored = snapshot(project.root)
    restored.pop("cast.yaml", None)  # cast is never replaced by sync or rollback
    expected = {
        key: value for key, value in recovery.journal.original.items() if key != "cast.yaml"
    }
    if restored != expected:
        raise VoxelloError(STORAGE_ERROR, "Restored files differ from the backup; retry --recover.")
    _journal_path(project).unlink()
    _sync_dir(project.root)


def recover_sync(project: Project, recovery: Recovery) -> None:
    """Restore an interrupted apply, or finish cleanup of an already committed apply."""
    with project_lock(project.root):
        try:
            current = _load_recovery(project)
            if current is None or current.raw != recovery.raw:
                raise VoxelloError(STORAGE_ERROR, "Recovery changed since the preview; retry.")
            if current.journal.phase == "committed":
                if snapshot(project.root) != current.journal.expected:
                    raise VoxelloError(STORAGE_ERROR, "Committed project changed; retain backups.")
                _journal_path(project).unlink()
                _sync_dir(project.root)
            else:
                _restore(project, current)
        except OSError as exc:
            raise VoxelloError(STORAGE_ERROR, f"Recovery failed; retry --recover: {exc}") from exc


def apply_sync(project: Project, plan: SyncPlan) -> Path | None:
    """Apply only the confirmed snapshot; old data remains in the returned archive."""
    with project_lock(project.root):
        Project.open(project.root)
        try:
            if snapshot(project.root) != plan.original or plan.source.read_bytes() != plan.raw:
                raise VoxelloError(
                    STORAGE_ERROR, "Project or source changed; rerun sync to confirm a new plan."
                )
            if not plan.changed:
                return None
            return _apply(project, plan)
        except OSError as exc:
            raise VoxelloError(STORAGE_ERROR, f"Sync failed: {exc}") from exc


def _apply(project: Project, plan: SyncPlan) -> Path:
    backups = project.root / "sync-backups"
    _plain(backups)
    archive = backups / plan.archive_name
    archive.mkdir(parents=True)
    before, after = archive / "before", archive / "after"
    before.mkdir()
    after.mkdir()
    for name in SNAPSHOT_NAMES:
        original = project.root / name
        if original.exists():
            _copy(original, before / name)
    if snapshot(before) != plan.original:
        raise VoxelloError(
            STORAGE_ERROR, "Recovery copy verification failed; active files are unchanged."
        )
    candidate = Project(after)
    write_atomic(candidate.source_path, plan.raw)
    candidate.save_script(plan.script)
    candidate.save_state(plan.state)
    candidate.takes_dir.mkdir()
    if project.cast_path.exists():
        _copy(project.cast_path, candidate.cast_path)
    for old, new in plan.mapping.items():
        original = project.takes_dir / old
        if original.is_dir():
            _copy(original, candidate.takes_dir / new)
    write_atomic(
        archive / "report.json",
        json.dumps(
            plan.report(),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
    )
    expected = snapshot(after)
    expected_audio = {"takes/": "directory"}
    for old, new in plan.mapping.items():
        prefix = f"takes/{old}/"
        expected_audio.update(
            {
                f"takes/{new}/" + key[len(prefix) :]: value
                for key, value in plan.original.items()
                if key.startswith(prefix)
            }
        )
    if (
        {key: value for key, value in expected.items() if key.startswith("takes/")}
        != expected_audio
        or candidate.source_path.read_bytes() != plan.raw
        or candidate.load_script() != plan.script
        or candidate.load_state() != plan.state
        or expected.get("cast.yaml") != plan.original.get("cast.yaml")
    ):
        raise VoxelloError(
            STORAGE_ERROR, "Prepared files failed verification; active files are unchanged."
        )
    _flush_tree(archive)
    _sync_dir(backups)
    if snapshot(project.root) != plan.original or plan.source.read_bytes() != plan.raw:
        raise VoxelloError(
            STORAGE_ERROR, "Project or source changed during preparation; retry sync."
        )
    journal = Journal(archive=plan.archive_name, original=plan.original, expected=expected)
    marker = _journal_path(project)
    _durable_write(marker, journal.model_dump_json().encode())
    try:
        for name in MANAGED:
            _displace(project, name, archive)
            os.replace(after / name, project.root / name)
            _sync_dir(project.root)
        if snapshot(project.root) != expected:
            raise VoxelloError(STORAGE_ERROR, "Synced files differ from the prepared snapshot.")
    except (OSError, VoxelloError, KeyboardInterrupt):
        recovery = Recovery(marker.read_bytes(), journal, archive)
        try:
            _restore(project, recovery)
        except (OSError, VoxelloError) as exc:
            raise VoxelloError(
                STORAGE_ERROR,
                f"Sync and rollback failed; run sync --recover. Backups: {archive}",
            ) from exc
        raise
    # Once publishing the commit marker begins, its on-disk phase may be either value.
    # Leave the complete new snapshot in place so either recovery path remains safe.
    journal.phase = "committed"
    _durable_write(marker, journal.model_dump_json().encode())
    # A cleanup failure after commit is recovered by validating the complete new snapshot.
    marker.unlink()
    _sync_dir(project.root)
    return archive
