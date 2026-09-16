"""Notification channel abstraction."""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    name: str

    def available(self) -> bool: ...

    async def send(self, title: str, body: str) -> None: ...
