# 0002 — Agents write their own spoken summaries; hooks only enforce and announce

Status: accepted, 2026-11-17

## Context

Users wanted to choose what the Claude Code and Codex integrations say: the fixed hook
sentences (cached, as until now), messages generated live, or a summary of the work at the end
of a task, each with a cloned or designed voice of its own.

Voxello's invariant is that it speaks exactly the text it receives and never rewrites or
summarizes it. Both agents give a Stop hook the final assistant message
(`last_assistant_message`), so a hook could read that message aloud, but it is written for
the screen: long, with code, paths and lists.

## Options

1. A Stop hook reads the last assistant message aloud, truncated.
2. Voxello summarizes the last message (an LLM call inside Voxello).
3. The agent writes a spoken summary and sends it through the MCP `notify` tool; a Stop hook
   only checks that it happened.

## Decision

Option 3.

- `integrations.summaries.mode: always` adds a paragraph to the MCP server instructions, which
  every client receives; that is what asks Claude Code, Codex or any other client for the
  summary, with no client-specific code. `on_request` keeps the previous behaviour (the
  `voice-notify` skill, triggered by the user).
- `enforce: true` adds a deterministic backstop: `voxello hook stop` reads the turn from the
  transcript and, when a turn of at least `min_turn_seconds` ended without a Voxello
  `notify`/`speak` call, returns a block decision whose reason asks for the summary. It never
  blocks when `stop_hook_active` is set, and it fails open when the transcript is missing or
  unreadable (the Codex rollout format is not a stable interface).
- Voices are chosen per kind of message (`integrations.phrases` / `.summaries`), resolved by
  `resolve_voice` with a `role`, so the cache key, the provider call and `doctor --synth` agree.
- Text arriving through MCP is not cached by default (`role="summary"`): summaries do not
  repeat, and caching them would only evict the hook sentences.
- Voices can differ per project through a `.voxello.yaml` layered over the user config, so
  concurrent agents in different repositories are recognizable by ear. A project file may set
  only voice and notification keys (`config.PROJECT_KEYS`): a cloned repository must not be
  able to point the TTS client at another server, which would receive the spoken text.
- Codex has no Notification event, so its Stop hook also announces the end of a turn with a
  fixed, cached sentence when no summary was spoken. It plays in a detached process so the
  synchronous hook returns at once.

## Consequences

- Voxello stays a renderer: no LLM dependency, no rewritten text.
- The summary costs the agent one tool call per task; with `enforce`, a forgotten summary costs
  one more model turn.
- The "already spoke" check depends on transcript shapes; an unknown shape disables
  enforcement for that turn instead of blocking the agent.
- `summaries.mode` reaches the agent at MCP server start, so changing it needs an agent restart.

## Validation and migration

Defaults (`phrases.mode: cached`, `summaries.mode: on_request`, `enforce: false`) reproduce the
previous behaviour, except that MCP text is no longer cached unless the agent asks. The policy
is unit-tested (`tests/test_stop_hook.py`, `tests/test_cli.py`, `tests/test_install.py`); the
live end-to-end checks are in the README.
