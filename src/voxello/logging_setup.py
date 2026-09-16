"""Logging configuration.

Voxello speaks MCP over stdout, so logs go to stderr and, optionally, a rotating file.
Nothing here ever writes to stdout.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "info", file: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    # Replace handlers so repeated calls (tests, CLI) do not duplicate output.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(stderr_handler)

    if file is not None:
        file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(file_handler)

    # Third-party chatter is rarely useful at info level.
    for noisy in ("httpx2", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))


def describe_text(text: str, log_text: bool) -> str:
    """Return what may be logged about a spoken text (spec section 27)."""
    if log_text:
        return repr(text)
    return f"<{len(text)} chars>"
