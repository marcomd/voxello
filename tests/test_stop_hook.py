"""Stop hook policy (``voxello.hooks``): summaries on demand and the Codex end-of-turn phrase.

Transcripts are synthetic, shaped like the lines Claude Code and Codex write: only the
fields the policy reads are present.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from voxello import hooks
from voxello.config import Settings

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def iso(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat().replace("+00:00", "Z")


def claude_prompt(seconds_ago: float, content: object = "fix the tests") -> str:
    return json.dumps(
        {
            "type": "user",
            "timestamp": iso(seconds_ago),
            "message": {"role": "user", "content": content},
        }
    )


def claude_tool(seconds_ago: float, name: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": iso(seconds_ago),
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name}]},
        }
    )


def claude_tool_result(seconds_ago: float) -> str:
    return json.dumps(
        {
            "type": "user",
            "timestamp": iso(seconds_ago),
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
        }
    )


def claude_meta(seconds_ago: float) -> str:
    return json.dumps(
        {
            "type": "user",
            "isMeta": True,
            "timestamp": iso(seconds_ago),
            "message": {"role": "user", "content": "<system reminder>"},
        }
    )


def codex_prompt(seconds_ago: float) -> str:
    return json.dumps(
        {
            "timestamp": iso(seconds_ago),
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": []},
        }
    )


def codex_call(seconds_ago: float, name: str) -> str:
    return json.dumps(
        {
            "timestamp": iso(seconds_ago),
            "type": "response_item",
            "payload": {"type": "function_call", "name": name, "arguments": "{}"},
        }
    )


def codex_mcp_event(seconds_ago: float, server: str, tool: str) -> str:
    return json.dumps(
        {
            "timestamp": iso(seconds_ago),
            "type": "event_msg",
            "payload": {
                "type": "mcp_tool_call_end",
                "invocation": {"server": server, "tool": tool},
            },
        }
    )


def settings(mode: str = "always", enforce: bool = True, phrases: str = "cached") -> Settings:
    return Settings(
        integrations={
            "summaries": {"mode": mode, "enforce": enforce, "min_turn_seconds": 60},
            "phrases": {"mode": phrases},
        }
    )


def decide(lines, *, agent="claude-code", payload=None, config=None):
    return hooks.stop_decision(
        payload or {"hook_event_name": "Stop", "stop_hook_active": False},
        config or settings(),
        agent,
        now=NOW,
        lines=lines,
    )


# -- transcript reading -------------------------------------------------------------------


def test_claude_turn_starts_at_the_last_typed_prompt():
    lines = [
        claude_prompt(600),
        claude_tool(590, "mcp__voxello__notify"),
        claude_prompt(120, [{"type": "text", "text": "and now the docs"}]),
        claude_meta(110),
        claude_tool(100, "Bash"),
        claude_tool_result(99),
    ]
    turn = hooks.read_turn(lines, "claude-code")
    assert turn == hooks.Turn(NOW - timedelta(seconds=120), spoke=False)


@pytest.mark.parametrize("name", ["mcp__voxello__notify", "mcp__voxello__speak"])
def test_claude_turn_sees_a_voxello_speech_call(name: str):
    lines = [claude_prompt(120), claude_tool(30, name), claude_tool_result(29)]
    assert hooks.read_turn(lines, "claude-code") == hooks.Turn(
        NOW - timedelta(seconds=120), spoke=True
    )


@pytest.mark.parametrize(
    "call",
    [
        codex_call(30, "mcp__voxello__notify"),
        codex_mcp_event(30, "voxello", "notify"),
    ],
)
def test_codex_turn_sees_a_voxello_speech_call(call: str):
    lines = [codex_prompt(300), codex_call(200, "shell"), call]
    assert hooks.read_turn(lines, "codex") == hooks.Turn(NOW - timedelta(seconds=300), spoke=True)


def test_read_turn_tolerates_garbage_and_needs_a_prompt():
    assert hooks.read_turn(["not json", "[]", "{}", claude_tool(5, "Bash")], "claude-code") is None
    lines = ["{", claude_prompt(10, ""), json.dumps({"type": "user", "message": {"content": "x"}})]
    assert hooks.read_turn(lines, "claude-code") == hooks.Turn(None, spoke=False)
    assert hooks.read_turn([codex_prompt(5), json.dumps({"payload": 1})], "codex") is not None


def test_is_voxello_speech_tool():
    assert hooks.is_voxello_speech_tool("mcp__voxello__notify")
    assert hooks.is_voxello_speech_tool("voxello.speak")
    assert not hooks.is_voxello_speech_tool("mcp__voxello__get_status")
    assert not hooks.is_voxello_speech_tool("mcp__slack__notify")


def test_transcript_lines_reads_the_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "t.jsonl"
    path.write_text("first\nsecond\nthird\n", encoding="utf-8")
    assert hooks.transcript_lines(str(path)) == ["first", "second", "third"]
    monkeypatch.setattr(hooks, "MAX_TRANSCRIPT_BYTES", 9)
    assert hooks.transcript_lines(str(path)) == ["third"], "the cut line is dropped"
    assert hooks.transcript_lines(str(tmp_path / "missing.jsonl")) is None
    assert hooks.transcript_lines(None) is None


# -- decision -----------------------------------------------------------------------------


def test_long_turn_without_a_summary_is_blocked_once():
    decision = decide([claude_prompt(300), claude_tool(200, "Bash")])
    assert decision == hooks.Block(hooks.SUMMARY_REQUEST)
    again = decide(
        [claude_prompt(300)], payload={"hook_event_name": "Stop", "stop_hook_active": True}
    )
    assert isinstance(again, hooks.Nothing), "never loops"


def test_turn_with_a_summary_or_short_is_left_alone():
    spoke = [claude_prompt(300), claude_tool(10, "mcp__voxello__notify")]
    assert isinstance(decide(spoke), hooks.Nothing)
    assert isinstance(decide([claude_prompt(20)]), hooks.Nothing), "too short for a summary"


@pytest.mark.parametrize("lines", [None, [], [claude_prompt(300, "")[:-1]]])
def test_unreadable_transcript_fails_open(lines):
    assert isinstance(decide(lines), hooks.Nothing)


def test_unknown_turn_start_fails_open():
    no_timestamp = json.dumps({"type": "user", "message": {"role": "user", "content": "go"}})
    assert isinstance(decide([no_timestamp]), hooks.Nothing)


@pytest.mark.parametrize("config", [settings(mode="on_request"), settings(enforce=False)])
def test_no_enforcement_means_no_block(config: Settings):
    assert isinstance(decide([claude_prompt(300)], config=config), hooks.Nothing)


def test_codex_speaks_the_turn_complete_phrase_when_it_did_not_summarize():
    lines = [codex_prompt(300), codex_call(200, "shell")]
    config = settings(mode="on_request")
    assert decide(lines, agent="codex", config=config) == hooks.SpeakPhrase("codex_turn_complete")
    # No transcript at all (transcript_path is nullable for Codex): still the phrase.
    assert decide(None, agent="codex", config=config) == hooks.SpeakPhrase("codex_turn_complete")
    # A summary was spoken, or phrases are off: silence.
    spoke = [*lines, codex_mcp_event(10, "voxello", "notify")]
    assert isinstance(decide(spoke, agent="codex", config=config), hooks.Nothing)
    off = settings(mode="on_request", phrases="off")
    assert isinstance(decide(lines, agent="codex", config=off), hooks.Nothing)


def test_codex_long_turn_is_asked_for_the_summary_before_any_phrase():
    lines = [codex_prompt(300)]
    assert decide(lines, agent="codex") == hooks.Block(hooks.SUMMARY_REQUEST)
    # Too short to ask for a summary: the end-of-turn phrase still plays.
    assert decide([codex_prompt(5)], agent="codex") == hooks.SpeakPhrase("codex_turn_complete")


def test_claude_code_has_no_turn_complete_phrase():
    """Claude Code has its own Notification event (idle_prompt) for that."""
    assert isinstance(decide([claude_prompt(5)], config=settings(enforce=False)), hooks.Nothing)


def test_codex_reader_variants():
    user_event = json.dumps(
        {"timestamp": iso(90), "type": "event_msg", "payload": {"type": "user_message"}}
    )
    reasoning = json.dumps(
        {"timestamp": iso(80), "type": "response_item", "payload": {"type": "reasoning"}}
    )
    other_event = json.dumps(
        {"timestamp": iso(70), "type": "event_msg", "payload": {"type": "token_count"}}
    )
    bad_time = json.dumps(
        {"timestamp": "yesterday", "type": "response_item", "payload": {"type": "function_call"}}
    )
    turn = hooks.read_turn([user_event, reasoning, other_event, bad_time], "codex")
    assert turn == hooks.Turn(NOW - timedelta(seconds=90), spoke=False)
