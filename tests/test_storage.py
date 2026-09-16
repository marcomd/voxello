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
    path = store.write(make_wav(100))
    assert path.parent == store.directory
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
    assert not new.exists()
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
