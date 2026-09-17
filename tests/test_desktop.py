"""Native notification boundaries, exercised without sending real notifications."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from voxello.errors import NOTIFICATION_UNAVAILABLE, VoxelloError
from voxello.notifications.desktop import DesktopNotifier, detect_notifier_command


@pytest.mark.parametrize(
    ("platform", "available", "expected"),
    [
        ("darwin", {"terminal-notifier", "osascript"}, "terminal-notifier"),
        ("darwin", {"osascript"}, "osascript"),
        ("darwin", set(), None),
        ("linux", {"notify-send"}, "notify-send"),
        ("linux", set(), None),
        ("win32", {"powershell"}, "powershell"),
        ("win32", set(), None),
        ("unknown", {"notify-send"}, None),
    ],
)
def test_detection(platform, available, expected, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: name if name in available else None)
    assert detect_notifier_command(platform) == expected


@pytest.fixture
def process(monkeypatch):
    process = Mock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"", b""))
    process.wait = AsyncMock(return_value=0)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    return process, spawn


@pytest.mark.parametrize("command", ["terminal-notifier", "osascript", "notify-send", "powershell"])
async def test_user_text_is_passed_as_data(command, process):
    _, spawn = process
    title, body = "Title 'quoted'", 'Body $(touch /tmp/unwanted); "quoted"\nnext line'
    await DesktopNotifier(command).send(title, body)
    args, kwargs = spawn.call_args
    assert args[0] == command
    assert kwargs["stdout"] == asyncio.subprocess.DEVNULL
    if command == "powershell":
        assert kwargs["env"]["VOXELLO_NOTIFY_TITLE"] == title
        assert kwargs["env"]["VOXELLO_NOTIFY_BODY"] == body
        assert title not in args[-1] and body not in args[-1]
    else:
        assert title in args and body in args
        assert kwargs["env"] is None


async def test_unavailable_notifier_does_not_spawn(monkeypatch, process):
    _, spawn = process
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(VoxelloError) as exc:
        await DesktopNotifier().send("title", "body")
    assert exc.value.code == NOTIFICATION_UNAVAILABLE
    spawn.assert_not_awaited()


@pytest.mark.parametrize("command", ["unknown", "notify-send"])
async def test_invalid_command_or_spawn_failure_is_domain_error(command, process):
    _, spawn = process
    spawn.side_effect = OSError("missing executable")
    with pytest.raises(VoxelloError) as exc:
        await DesktopNotifier(command).send("title", "body")
    assert exc.value.code == NOTIFICATION_UNAVAILABLE


async def test_nonzero_exit_is_domain_error(process):
    proc, _ = process
    proc.returncode = 1
    proc.communicate.return_value = (b"", b"notification service unavailable")
    with pytest.raises(VoxelloError) as exc:
        await DesktopNotifier("notify-send").send("title", "body")
    assert exc.value.code == NOTIFICATION_UNAVAILABLE


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
@pytest.mark.parametrize("already_exited", [False, True])
async def test_interrupted_notification_reaps_process(process, failure, already_exited):
    proc, _ = process
    proc.returncode = None
    proc.communicate.side_effect = [failure, (b"", b"")]
    if already_exited:
        proc.kill.side_effect = ProcessLookupError
    expected = VoxelloError if failure is TimeoutError else asyncio.CancelledError
    with pytest.raises(expected) as exc:
        await DesktopNotifier("notify-send").send("title", "body")
    if failure is TimeoutError:
        assert exc.value.code == NOTIFICATION_UNAVAILABLE
    proc.kill.assert_called_once_with()
    assert proc.communicate.await_count == 2
