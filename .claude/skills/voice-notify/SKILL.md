---
name: voice-notify
description: Announce the outcome of the current task by voice through Voxello when the work is done or blocked. Use when the user asks to be notified, told, alerted or updated by voice, audio or speech, or asks to have something read aloud, e.g. "avvisami a voce", "dimmelo a voce quando hai finito", "fammi sapere a voce", "notify me by voice", "tell me out loud when you're done", "leggimelo", "read it to me".
allowed-tools: mcp__voxello__notify mcp__voxello__speak mcp__voxello__get_status
---

# Voice notification through Voxello

The user asked to hear about this task rather than watch the terminal. Do the task exactly as you
normally would, then deliver a spoken notification through the Voxello MCP server.

## When to notify

Call `mcp__voxello__notify` exactly once, as the last tool call of the turn, in these cases:

- the task is complete (success or partial success);
- the task cannot continue and you need the user (a decision, a permission, a failure you cannot fix);
- the task failed.

Do not notify for quick exchanges that end within a few seconds unless the user asked to be told
by voice anyway. If the user asked to have something *read aloud* ("leggimelo", "read it to me"),
use `mcp__voxello__speak` instead, with a spoken version that fits in about twenty seconds; the
full text stays in your written answer.

## How to write the message

- One or two sentences, at most about 250 characters. It will be spoken, so it must sound natural.
- Write in the language the user wrote in (Italian if they wrote in Italian).
- Say what was done and the outcome first, then anything the user must do next.
- No code, file paths, URLs, identifiers, hashes, or long lists. Say counts in words when short
  ("tutti i test passano", "due test falliscono", "tre file modificati").
- Details belong in the written answer; the voice message may end with "i dettagli sono nella
  risposta" or the equivalent.

## Parameters

```json
{
  "message": "<the spoken message>",
  "channels": ["voice", "desktop"],
  "priority": "normal",
  "title": "Claude Code",
  "client_id": "claude-code"
}
```

- `priority`: `high` when the task failed or is blocked waiting for the user; `normal` otherwise.
  High priority interrupts any speech currently playing.
- `channels`: keep both; the desktop notification shows the same text for when the user has
  audio muted.

## After the call

- If `notify` returns a tool error (for example `tts_provider_unavailable`), say so in one line of
  your written answer and do not retry more than once.
- Do not repeat the spoken message verbatim in the written answer; the written answer stays as
  detailed as usual.

## Examples of good messages

- "Refactoring del modulo auth completato. Tutti i centoventisei test passano. I dettagli sono nella risposta."
- "Ho finito la migrazione, ma due test di integrazione falliscono e non riesco a correggerli da solo. Serve una tua decisione."
- "Analysis done: three blockers found, the most critical one is the payment integration. Details are in the reply."
