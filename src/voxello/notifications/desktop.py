"""Desktop notifications through native OS tools, no extra dependencies.

macOS: ``osascript`` (Apple's signed binary, so notifications work even from an
unsigned Python). Linux: ``notify-send``. Windows: a PowerShell WinRT toast.
Title and body are passed as arguments or environment variables, never
interpolated into a script string.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys

from voxello.errors import NOTIFICATION_UNAVAILABLE, VoxelloError

log = logging.getLogger(__name__)

_WINDOWS_TOAST = r"""  # noqa: E501
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $template.GetElementsByTagName("text")
$nodes.Item(0).AppendChild($template.CreateTextNode($env:VOXELLO_NOTIFY_TITLE)) | Out-Null
$nodes.Item(1).AppendChild($template.CreateTextNode($env:VOXELLO_NOTIFY_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Voxello").Show($toast)
"""


def detect_notifier_command(platform: str | None = None) -> str | None:
    plat = platform or sys.platform
    if plat == "darwin":
        if shutil.which("terminal-notifier"):
            return "terminal-notifier"
        return "osascript" if shutil.which("osascript") else None
    if plat.startswith("linux"):
        return "notify-send" if shutil.which("notify-send") else None
    if plat == "win32":
        return "powershell" if shutil.which("powershell") else None
    return None


class DesktopNotifier:
    name = "desktop"

    def __init__(self, command: str | None = None) -> None:
        self.command = command if command is not None else detect_notifier_command()

    def available(self) -> bool:
        return self.command is not None

    def _build(self, title: str, body: str) -> tuple[list[str], dict[str, str] | None]:
        if self.command == "terminal-notifier":
            return [self.command, "-title", title, "-message", body, "-group", "voxello"], None
        if self.command == "osascript":
            # Arguments reach AppleScript via argv, so no quoting/escaping is involved.
            return [
                self.command,
                "-e",
                "on run argv",
                "-e",
                "display notification (item 1 of argv) with title (item 2 of argv)",
                "-e",
                "end run",
                body,
                title,
            ], None
        if self.command == "notify-send":
            return [self.command, "--app-name=Voxello", title, body], None
        if self.command == "powershell":
            env = dict(os.environ)
            env["VOXELLO_NOTIFY_TITLE"] = title
            env["VOXELLO_NOTIFY_BODY"] = body
            return [self.command, "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_TOAST], env
        raise VoxelloError(NOTIFICATION_UNAVAILABLE, "No desktop notification tool was detected.")

    async def send(self, title: str, body: str) -> None:
        if not self.available():
            raise VoxelloError(
                NOTIFICATION_UNAVAILABLE, "No desktop notification tool was detected."
            )
        command, env = self._build(title, body)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
        except (OSError, TimeoutError) as exc:
            raise VoxelloError(
                NOTIFICATION_UNAVAILABLE, f"Desktop notification failed ({type(exc).__name__})."
            ) from exc
        if process.returncode != 0:
            detail = (stderr or b"").decode(errors="replace").strip()[:200]
            log.warning(
                "Desktop notifier %s exited %s: %s", self.command, process.returncode, detail
            )
            raise VoxelloError(
                NOTIFICATION_UNAVAILABLE,
                f"Desktop notification command '{self.command}' failed.",
            )
