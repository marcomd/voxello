"""Stop hook policy for agent integrations (Claude Code, Codex).

``voxello hook stop --agent <name>`` runs when an agent ends its turn. It decides, from the
Stop event the agent pipes on stdin and the turn's transcript, between three outcomes:

- ask the agent to deliver the spoken summary it skipped (``Block``), when
  ``integrations.summaries`` is ``always`` with ``enforce``;
- speak the fixed "turn complete" sentence (``SpeakPhrase``), for agents without a
  Notification event of their own (Codex);
- do nothing.

The functions here are pure (payload, settings, transcript lines, clock in; decision out)
so the policy is tested without an agent. Anything unexpected in the transcript makes the
hook fail open: it never blocks an agent because it could not read the turn.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from voxello.config import Settings

log = logging.getLogger(__name__)

Agent = Literal["claude-code", "codex"]
AGENTS: tuple[Agent, ...] = ("claude-code", "codex")

# Message-file key of the sentence spoken when a Codex turn ends without a summary.
TURN_COMPLETE_KEYS: dict[Agent, str] = {"codex": "codex_turn_complete"}

# Fed back to the agent as the reason it may not stop yet. English, like the agents' own
# instructions; the summary itself is written in the user's language.
SUMMARY_REQUEST = (
    "The user asked to hear the outcome of every task. Before ending, call the Voxello "
    "`notify` tool once with a spoken summary of one or two sentences in the user's language "
    "(what was done, the outcome, anything the user must do), passing `language` and "
    "`cache: false`. Then stop without repeating your answer."
)

# Bounded read: a transcript is append-only and can grow large; the current turn is at the end.
MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Block:
    reason: str


@dataclass(frozen=True)
class SpeakPhrase:
    key: str


@dataclass(frozen=True)
class Nothing:
    why: str


StopDecision = Block | SpeakPhrase | Nothing


@dataclass(frozen=True)
class TurnEvent:
    kind: Literal["prompt", "tool"]
    timestamp: datetime | None
    name: str = ""


@dataclass(frozen=True)
class Turn:
    """What the transcript says about the turn that just ended."""

    started: datetime | None
    spoke: bool


def is_voxello_speech_tool(name: str) -> bool:
    """``mcp__voxello__notify`` (Claude Code), ``voxello.notify`` or similar (Codex)."""
    lowered = name.lower()
    return "voxello" in lowered and (lowered.endswith("notify") or lowered.endswith("speak"))


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def claude_events(entry: Mapping[str, Any]) -> list[TurnEvent]:
    """Claude Code transcript line -> events.

    A prompt is a ``user`` entry the user typed: not ``isMeta`` and not a tool result. Tool
    calls are ``tool_use`` blocks of ``assistant`` entries.
    """
    message = entry.get("message")
    if not isinstance(message, Mapping):
        return []
    when = _timestamp(entry.get("timestamp"))
    content = message.get("content")
    if entry.get("type") == "user" and not entry.get("isMeta"):
        if isinstance(content, str) and content.strip():
            return [TurnEvent("prompt", when)]
        if isinstance(content, list) and not any(
            isinstance(b, Mapping) and b.get("type") == "tool_result" for b in content
        ):
            return [TurnEvent("prompt", when)]
        return []
    if entry.get("type") == "assistant" and isinstance(content, list):
        return [
            TurnEvent("tool", when, str(b.get("name", "")))
            for b in content
            if isinstance(b, Mapping) and b.get("type") == "tool_use"
        ]
    return []


def codex_events(entry: Mapping[str, Any]) -> list[TurnEvent]:
    """Codex rollout line -> events.

    Rollout lines are ``{"timestamp", "type", "payload"}``. A prompt is a ``response_item``
    user ``message`` (or an ``event_msg`` ``user_message``); tool calls are
    ``function_call`` / ``custom_tool_call`` items, and MCP calls also appear as
    ``mcp_tool_call_*`` events naming the server and the tool.
    """
    payload = entry.get("payload")
    if not isinstance(payload, Mapping):
        return []
    when = _timestamp(entry.get("timestamp"))
    kind = payload.get("type")
    if entry.get("type") == "response_item":
        if kind == "message" and payload.get("role") == "user":
            return [TurnEvent("prompt", when)]
        if kind in ("function_call", "custom_tool_call", "mcp_tool_call"):
            return [TurnEvent("tool", when, str(payload.get("name", "")))]
        return []
    if entry.get("type") == "event_msg":
        if kind == "user_message":
            return [TurnEvent("prompt", when)]
        if isinstance(kind, str) and kind.startswith("mcp_tool_call"):
            invocation = payload.get("invocation")
            if isinstance(invocation, Mapping):
                name = f"{invocation.get('server', '')}.{invocation.get('tool', '')}"
                return [TurnEvent("tool", when, name)]
    return []


EVENT_READERS: dict[Agent, Callable[[Mapping[str, Any]], list[TurnEvent]]] = {
    "claude-code": claude_events,
    "codex": codex_events,
}


def read_turn(lines: Iterable[str], agent: Agent) -> Turn | None:
    """The last turn of a transcript: when its prompt arrived and whether Voxello spoke.

    ``None`` when no prompt is found, so callers fail open.
    """
    reader = EVENT_READERS[agent]
    started: datetime | None = None
    seen_prompt = False
    spoke = False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, Mapping):
            continue
        for event in reader(entry):
            if event.kind == "prompt":
                seen_prompt, started, spoke = True, event.timestamp, False
            elif seen_prompt and is_voxello_speech_tool(event.name):
                spoke = True
    return Turn(started, spoke) if seen_prompt else None


def transcript_lines(path: str | None) -> list[str] | None:
    """The tail of the transcript at ``path``; ``None`` when it cannot be read."""
    if not path:
        return None
    try:
        with Path(path).expanduser().open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - MAX_TRANSCRIPT_BYTES))
            data = fh.read()
    except OSError as exc:
        log.warning("stop hook: cannot read the transcript (%s)", exc.__class__.__name__)
        return None
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > MAX_TRANSCRIPT_BYTES else lines  # drop a cut first line


def stop_decision(
    payload: Mapping[str, Any],
    settings: Settings,
    agent: Agent,
    *,
    now: datetime,
    lines: list[str] | None,
) -> StopDecision:
    """What the Stop hook does for this event (see the module docstring).

    ``lines`` is the transcript (``None`` when unavailable). ``now`` must be timezone-aware.
    """
    if payload.get("stop_hook_active") is True:
        return Nothing("the agent is already continuing because of a stop hook")
    turn = read_turn(lines, agent) if lines is not None else None
    if turn is not None and turn.spoke:
        return Nothing("the agent already spoke this turn")

    summaries = settings.integrations.summaries
    if summaries.mode == "always" and summaries.enforce:
        if turn is None or turn.started is None:
            log.info("stop hook: turn unknown in the transcript, not asking for a summary")
        elif (now - turn.started).total_seconds() >= summaries.min_turn_seconds:
            return Block(SUMMARY_REQUEST)

    key = TURN_COMPLETE_KEYS.get(agent)
    if key is not None and settings.integrations.phrases.mode != "off":
        return SpeakPhrase(key)
    return Nothing("nothing to say")
