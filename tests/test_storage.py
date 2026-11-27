from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

from voxello.errors import STORAGE_ERROR, VoxelloError
from voxello.storage.files import OutputStore, TempStore
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

    proc = subprocess.Popen(["true"])
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

    live = directory / "99999-live"
    live.mkdir()
    (live / "owner").write_text(str(os.getpid()))
    os.utime(live, (time.time() - 3600, time.time() - 3600))

    dead_pid = _dead_pid()
    dead = directory / "99999-dead"
    dead.mkdir()
    (dead / "owner").write_text(str(dead_pid))
    (dead / "vox_stale.wav").write_bytes(b"x")
    os.utime(dead, (time.time() - 3600, time.time() - 3600))

    missing = directory / "99999-missing"
    missing.mkdir()
    os.utime(missing, (time.time() - 3600, time.time() - 3600))

    fresh_dead = directory / "99999-fresh"
    fresh_dead.mkdir()
    (fresh_dead / "owner").write_text(str(dead_pid))

    store = TempStore(directory, retention_minutes=10, cleanup_on_start=True)
    store.prepare()

    assert live.exists(), "a live owner must keep its namespace"
    assert not dead.exists(), "a dead owner's namespace is reclaimed"
    assert not missing.exists(), "a namespace with no owner file is reclaimed"
    assert fresh_dead.exists(), "a fresh namespace is left alone"
    assert store.namespace_dir.exists()


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
