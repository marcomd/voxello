from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

from voxello.errors import STORAGE_ERROR, VoxelloError
from voxello.storage.files import LEGACY_MARGIN_SECONDS, OutputStore, TempStore
from voxello.storage.wav import is_wav, wav_duration_ms, write_silence

from .conftest import make_wav


def test_temp_store_permissions_and_names(tmp_path: Path):
    store = TempStore(tmp_path / "tmp", retention_minutes=10, cleanup_on_start=True)
    store.prepare()
    if sys.platform != "win32":
        assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(store.namespace_dir.stat().st_mode) == 0o700
    path = store.write(make_wav(100))
    assert path.parent == store.namespace_dir
    assert path.name.startswith("vox_") and path.suffix == ".wav"
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    other = store.write(make_wav(100))
    assert other.name != path.name


def test_temp_store_sweep_and_cleanup_on_start(tmp_path: Path):
    store = TempStore(tmp_path / "tmp", retention_minutes=10, cleanup_on_start=False)
    store.prepare()
    old = store.write(b"x")
    new = store.write(b"y")
    past = time.time() - 11 * 60
    os.utime(old, (past, past))
    assert store.sweep() == 1
    assert not old.exists() and new.exists()

    (store.directory / "unrelated.txt").write_text("keep")
    fresh = TempStore(store.directory, retention_minutes=10, cleanup_on_start=True)
    fresh.prepare()
    assert new.exists(), "another store's files are not swept at startup"
    assert (store.directory / "unrelated.txt").exists()


def test_temp_store_discard_only_own_files(tmp_path: Path):
    store = TempStore(tmp_path / "tmp", 10, True)
    store.prepare()
    foreign = tmp_path / "foreign.wav"
    foreign.write_bytes(b"x")
    store.discard(foreign)
    assert foreign.exists()
    own = store.write(b"y")
    store.discard(own)
    assert not own.exists()
    store.discard(own)  # idempotent


def test_output_store_saves_and_refuses_traversal(tmp_path: Path):
    out = OutputStore(tmp_path / "out")
    src = tmp_path / "src.wav"
    src.write_bytes(make_wav(50))
    saved = out.save_audio(src, "vox_ABC123")
    assert saved.parent == (tmp_path / "out").resolve()
    assert saved.name.endswith("_vox_ABC123.wav")
    txt = out.save_text("ciao", "vox_ABC123")
    assert txt.read_text() == "ciao"
    with pytest.raises(VoxelloError) as exc:
        out.save_audio(src, "../evil")
    assert exc.value.code == STORAGE_ERROR


def test_wav_helpers(tmp_path: Path):
    data = make_wav(1234)
    assert is_wav(data)
    assert wav_duration_ms(data) == 1234
    assert not is_wav(b"ID3junk")
    assert wav_duration_ms(b"not a wav") is None
    path = write_silence(tmp_path / "s.wav", 250)
    assert wav_duration_ms(path) == 250


def _dead_pid() -> int:
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_two_stores_share_a_directory_without_interfering(tmp_path: Path):
    directory = tmp_path / "tmp"
    store_a = TempStore(directory, retention_minutes=10, cleanup_on_start=True)
    store_a.prepare()
    keep = store_a.write(make_wav(50))

    store_b = TempStore(directory, retention_minutes=10, cleanup_on_start=True)
    store_b.prepare()

    assert keep.exists(), "startup cleanup of store B must not remove store A's files"
    assert keep.parent == store_a.namespace_dir
    assert not store_b.owns(keep)
    assert store_a.owns(keep)


def test_sweep_only_removes_files_in_own_namespace(tmp_path: Path):
    directory = tmp_path / "tmp"
    store_a = TempStore(directory, retention_minutes=10, cleanup_on_start=False)
    store_a.prepare()
    old = store_a.write(make_wav(50))
    past = time.time() - 11 * 60
    os.utime(old, (past, past))

    store_b = TempStore(directory, retention_minutes=10, cleanup_on_start=False)
    store_b.prepare()
    assert store_b.sweep() == 0
    assert old.exists()

    assert store_a.sweep() == 1
    assert not old.exists()


def test_cleanup_on_start_removes_abandoned_namespaces_only(tmp_path: Path):
    directory = tmp_path / "tmp"
    directory.mkdir(parents=True)

    live = directory / f"voxello-temp-{os.getpid()}-live"
    live.mkdir()
    (live / "owner").write_text(str(os.getpid()))
    os.utime(live, (time.time() - 3600, time.time() - 3600))

    dead_pid = _dead_pid()
    dead = directory / f"voxello-temp-{dead_pid}-dead"
    dead.mkdir()
    (dead / "owner").write_text(str(dead_pid))
    (dead / "vox_stale.wav").write_bytes(b"x")
    os.utime(dead, (time.time() - 3600, time.time() - 3600))

    missing = directory / "voxello-temp-99999-missing"
    missing.mkdir()
    os.utime(missing, (time.time() - 3600, time.time() - 3600))

    mismatch = directory / f"voxello-temp-{dead_pid}-mismatch"
    mismatch.mkdir()
    (mismatch / "owner").write_text(str(os.getpid()))
    os.utime(mismatch, (1, 1))

    fresh_dead = directory / f"voxello-temp-{dead_pid}-fresh"
    fresh_dead.mkdir()
    (fresh_dead / "owner").write_text(str(dead_pid))

    store = TempStore(directory, retention_minutes=10, cleanup_on_start=True)
    store.prepare()

    assert live.exists(), "a live owner must keep its namespace"
    assert not dead.exists(), "a provably dead owner's namespace is reclaimed on every platform"
    assert missing.exists(), "an unknown owner is not proof of abandonment"
    assert mismatch.exists(), "a mismatched owner is not proof of abandonment"
    assert fresh_dead.exists(), "a fresh namespace is left alone"
    assert store.namespace_dir.exists()


def _age(path: Path, seconds: float) -> None:
    past = time.time() - seconds
    os.utime(path, (past, past))


def test_startup_preserves_unrelated_directories(tmp_path: Path):
    directory = tmp_path / "tmp"
    directory.mkdir()
    unrelated = directory / "documents"
    unrelated.mkdir()
    (unrelated / "important").write_text("keep")
    os.utime(unrelated, (1, 1))
    TempStore(directory, 10, True).prepare()
    assert (unrelated / "important").read_text() == "keep"


def test_startup_reclaims_legacy_audio_older_than_retention(tmp_path: Path):
    # Older versions wrote loose vox_* files and swept any of them past the retention
    # period, so one that is older than that (plus a margin) is not in use by them.
    directory = tmp_path / "tmp"
    directory.mkdir()
    retention = 10 * 60
    stale = directory / "vox_stale.wav"
    stale.write_bytes(b"audio")
    _age(stale, retention + LEGACY_MARGIN_SECONDS + 5)
    recent = directory / "vox_recent.wav"
    recent.write_bytes(b"audio")
    _age(recent, retention + 1)
    other = directory / "notes.txt"
    other.write_text("keep")
    _age(other, 10 * retention)
    legacy_dir = directory / "vox_folder"
    legacy_dir.mkdir()
    _age(legacy_dir, 10 * retention)

    TempStore(directory, retention_minutes=10, cleanup_on_start=True).prepare()

    assert not stale.exists()
    assert recent.exists(), "an older instance may still own audio within its retention"
    assert other.exists() and legacy_dir.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_startup_never_follows_legacy_symlinks(tmp_path: Path):
    directory = tmp_path / "tmp"
    directory.mkdir()
    target = tmp_path / "elsewhere.wav"
    target.write_bytes(b"keep")
    link = directory / "vox_link.wav"
    link.symlink_to(target)
    os.utime(link, (1, 1), follow_symlinks=False)
    TempStore(directory, 10, True).prepare()
    assert link.is_symlink() and target.read_bytes() == b"keep"


def test_no_startup_cleanup_when_disabled(tmp_path: Path):
    directory = tmp_path / "tmp"
    directory.mkdir()
    legacy = directory / "vox_legacy.wav"
    legacy.write_bytes(b"audio")
    _age(legacy, 10**6)
    dead_pid = _dead_pid()
    dead = directory / f"voxello-temp-{dead_pid}-dead"
    dead.mkdir()
    (dead / "owner").write_text(str(dead_pid))
    _age(dead, 3600)
    TempStore(directory, 10, cleanup_on_start=False).prepare()
    assert legacy.exists() and dead.exists()


class FakeProcessQuery:
    """Stands in for OpenProcess/GetExitCodeProcess/CloseHandle."""

    def __init__(self, *, handle: int | None = 7, error: int = 0, exit_code: int | None = 259):
        self.handle = handle
        self.error = error
        self.code = exit_code
        self.closed: list[int] = []

    def open(self, pid: int) -> int | None:
        return self.handle

    def last_error(self) -> int:
        return self.error

    def exit_code(self, handle: int) -> int | None:
        return self.code

    def close(self, handle: int) -> None:
        self.closed.append(handle)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (FakeProcessQuery(exit_code=259), True),  # STILL_ACTIVE
        (FakeProcessQuery(exit_code=0), False),  # exited, handle kept alive elsewhere
        (FakeProcessQuery(exit_code=None), None),  # GetExitCodeProcess failed
        (FakeProcessQuery(handle=None, error=87), False),  # ERROR_INVALID_PARAMETER: no such pid
        (FakeProcessQuery(handle=None, error=5), True),  # ERROR_ACCESS_DENIED: exists
        (FakeProcessQuery(handle=None, error=1), None),  # anything else is unknown
    ],
)
def test_windows_liveness_queries_the_process(query, expected):
    from voxello.storage import files

    assert files._windows_pid_alive(123, query) is expected
    if query.handle is not None:
        assert query.closed == [query.handle], "the process handle is always closed"


def test_windows_pid_check_never_sends_a_signal(monkeypatch):
    from voxello.storage import files

    query = FakeProcessQuery(handle=None, error=87)
    monkeypatch.setattr(files.sys, "platform", "win32")
    monkeypatch.setattr(files, "_windows_process_query", lambda: query)
    monkeypatch.setattr(files.os, "kill", lambda *_: pytest.fail("must not signal a process"))
    assert files._pid_alive(123) is False


def test_windows_pid_check_without_process_api_keeps_owner(monkeypatch):
    from voxello.storage import files

    monkeypatch.setattr(files.sys, "platform", "win32")
    monkeypatch.setattr(files, "_windows_process_query", lambda: None)
    monkeypatch.setattr(files.os, "kill", lambda *_: pytest.fail("must not signal a process"))
    assert files._pid_alive(123) is None


def test_dispose_removes_only_own_namespace(tmp_path: Path):
    directory = tmp_path / "tmp"
    store_a = TempStore(directory, retention_minutes=10, cleanup_on_start=False)
    store_a.prepare()
    keep = store_a.write(make_wav(50))

    store_b = TempStore(directory, retention_minutes=10, cleanup_on_start=False)
    store_b.prepare()
    store_b.dispose()

    assert keep.exists()
    assert not store_b.namespace_dir.exists()
    store_a.dispose()
    assert not store_a.namespace_dir.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX liveness check")
@pytest.mark.parametrize(
    ("error", "expected"),
    [(ProcessLookupError, False), (PermissionError, True), (OSError, None), (None, True)],
)
def test_posix_liveness_maps_kill_results(monkeypatch, error, expected):
    from voxello.storage import files

    def kill(pid: int, sig: int) -> None:
        assert sig == 0
        if error is not None:
            raise error()

    monkeypatch.setattr(files.os, "kill", kill)
    assert files._pid_alive(123) is expected
