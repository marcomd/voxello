"""Subprocess lifecycle and platform selection without an installed player."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from voxello.errors import PLAYBACK_ERROR, VoxelloError
from voxello.playback.backends import ALL_BACKENDS
from voxello.playback.detect import detect_backend
from voxello.playback.subprocess_player import SubprocessHandle, SubprocessPlayer


@pytest.mark.parametrize(
    ("platform", "available", "expected"),
    [
        ("darwin", {"afplay", "mpv"}, "afplay"),
        ("linux", {"mpv", "paplay"}, "mpv"),
        ("linux", {"aplay"}, "aplay"),
        ("win32", {"powershell", "mpv"}, "powershell"),
        ("unknown", {"ffplay"}, "ffplay"),
        ("linux", set(), None),
    ],
)
def test_detection(platform, available, expected, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: name if name in available else None)
    backend = detect_backend(platform=platform)
    assert (backend.name if backend else None) == expected


@pytest.mark.parametrize("preferred", ["mpv", "missing", "afplay"])
def test_explicit_backend_does_not_fall_back(preferred, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: name if name == "mpv" else None)
    backend = detect_backend(preferred, platform="linux")
    assert (backend.name if backend else None) == ("mpv" if preferred == "mpv" else None)


@pytest.mark.parametrize(
    ("name", "volume_args"),
    [
        ("afplay", ["-v", "0.50"]),
        ("mpv", ["--volume=50"]),
        ("paplay", ["--volume=32768"]),
        ("aplay", ["-q"]),
        ("ffplay", ["-volume", "50"]),
    ],
)
async def test_player_passes_path_as_single_argument(name, volume_args, monkeypatch):
    process = Mock(wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    path = Path("audio with spaces; $data.wav")
    handle = await SubprocessPlayer(ALL_BACKENDS[name], 0.5).play(path)
    args, kwargs = spawn.call_args
    assert args[0] == name
    assert args[-1] == str(path)
    assert all(arg in args for arg in volume_args)
    assert kwargs == dict.fromkeys(("stdin", "stdout", "stderr"), asyncio.subprocess.DEVNULL)
    assert await handle.wait() == 0


def test_powershell_path_escapes_single_quotes():
    command = ALL_BACKENDS["powershell"].build_command(Path("it's audio.wav"), 0.5)
    assert command[:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    assert "'it''s audio.wav'" in command[-1]


@pytest.mark.parametrize("failure", [OSError, ValueError])
async def test_spawn_failure_is_domain_error(failure, monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(side_effect=failure))
    with pytest.raises(VoxelloError) as exc:
        await SubprocessPlayer(ALL_BACKENDS["mpv"], 1).play(Path("test.wav"))
    assert exc.value.code == PLAYBACK_ERROR


async def test_terminate_finished_process_is_noop():
    process = Mock(returncode=0)
    await SubprocessHandle(process).terminate()
    process.terminate.assert_not_called()


async def test_terminate_waits_for_process():
    process = Mock(returncode=None, wait=AsyncMock(return_value=0))
    await SubprocessHandle(process).terminate()
    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once_with()
    process.kill.assert_not_called()


@pytest.mark.parametrize("exits_during_kill", [False, True])
async def test_terminate_escalates_to_kill_on_timeout(exits_during_kill):
    process = Mock(returncode=None, wait=AsyncMock(side_effect=[TimeoutError, 0]))
    if exits_during_kill:
        process.kill.side_effect = ProcessLookupError
    await SubprocessHandle(process).terminate()
    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.await_count == (1 if exits_during_kill else 2)


async def test_process_exit_during_terminate_is_harmless():
    process = Mock(returncode=None, terminate=Mock(side_effect=ProcessLookupError))
    await SubprocessHandle(process).terminate()
    process.kill.assert_not_called()
