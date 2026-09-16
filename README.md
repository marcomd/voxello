# Voxello

**A local voice and notification layer for AI agents.**

Voxello is a small MCP server that lets Claude Code, Codex, Cursor and other MCP clients
`speak`, `notify`, `stop_speaking` and `get_status`. It sends text to a local TTS provider
(VoiceStudio, running OmniVoice or another engine), plays the audio on your speaker, shows
desktop notifications, and cleans up after itself. The agent decides *what* to say; Voxello
handles *how* it is delivered.

```
Agent ──MCP──▶ Voxello ──HTTP──▶ VoiceStudio ──▶ WAV ──▶ afplay / mpv / PowerShell
                  └──▶ desktop notification, saved file
```

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- [VoiceStudio](https://github.com/debpalash/VoiceStudio) open on this machine or on an intranet host
- An audio player: `afplay` (macOS, built in), `mpv`/`paplay`/`aplay`/`ffplay` (Linux), PowerShell (Windows)

## Install

From this repository:

```bash
git clone <repo-url> voxello && cd voxello
uv sync
uv run voxello doctor
```

As a tool available on your PATH:

```bash
uv tool install --from /path/to/voxello voxello   # or: uvx --from /path/to/voxello voxello
```

## Configure

```bash
uv run voxello config init        # writes ~/Library/Application Support/voxello/config.yaml (macOS)
uv run voxello config path        # shows where the file lives on this OS
```

Minimal intranet setup:

```yaml
tts:
  voicestudio:
    base_url: http://voicestudio.lan:3900
    api_key: "the key configured as OMNIVOICE_API_KEY on the VoiceStudio host"
    engine: omnivoice
    voice: default
    language: it
```

Environment variables override the file: `VOXELLO_VOICESTUDIO_URL`, `VOXELLO_VOICESTUDIO_API_KEY`,
`VOXELLO_DEFAULT_VOICE`, `VOXELLO_LOG_LEVEL`, or any nested key as `VOXELLO_TTS__VOICESTUDIO__ENGINE`.
`VOXELLO_CONFIG` points to an alternative config file. See `config.example.yaml` for every option.

## Try it without an agent

```bash
uv run voxello doctor                                  # provider reachable? player? notifier?
uv run voxello speak "Build completata. Tutti i test passano."
uv run voxello speak "Salvami" --save                  # also writes ~/Voxello/<timestamp>_<id>.wav
uv run voxello notify "Refactoring completato." --channels voice,desktop
```

## Register with your agent

**Claude Code** (user scope, available in every project):

```bash
claude mcp add --scope user --transport stdio voxello -- uv run --directory /path/to/voxello voxello serve
```

This repository also ships a project-scoped `.mcp.json`, so opening the repo in Claude Code
registers the server automatically.

**Codex CLI** (`~/.codex/config.toml`):

```toml
[mcp_servers.voxello]
command = "uv"
args = ["run", "--directory", "/path/to/voxello", "voxello", "serve"]
```

**Cursor** (`~/.cursor/mcp.json`):

```json
{ "mcpServers": { "voxello": { "command": "uv", "args": ["run", "--directory", "/path/to/voxello", "voxello", "serve"] } } }
```

Then ask the agent, for example: *"Refactor the auth module, run the tests, and tell me by
voice when you are done."*

## Tools

| Tool | Purpose | Key parameters |
|---|---|---|
| `speak` | synthesize and play text | `text`, `voice`, `interrupt` (default true), `save`, `play`, `mode`, `client_id` |
| `stop_speaking` | stop playback and clear the queue | optional `request_id` |
| `get_status` | state, current request, queue, provider and player health | none |
| `notify` | route a short message to `voice`, `desktop` and/or `file` | `message`, `channels`, `priority`, `title` |

`speak` returns metadata (`request_id`, `status`, `duration_ms`, `saved_path`), never audio.
Errors come back as tool errors with a stable code: `tts_provider_unavailable`,
`tts_provider_unauthorized`, `voice_not_found`, `text_too_long`, `queue_full`,
`playback_unavailable`, `notification_unavailable`.

## How it behaves

- **Interrupt by default.** A new `speak` stops what is playing and clears the queue. Pass
  `interrupt: false` to queue instead (FIFO, `playback.max_queue_size` items).
- **Short by design.** Text is capped at `limits.max_text_chars` (2000). Voice is for summaries
  and status; the textual answer carries the details.
- **Private by default.** Temporary audio lives in the OS cache directory with 0700 permissions,
  gets random names, and is deleted after playback and after `storage.temp_retention_minutes`.
  Logs record text length, not text, unless `logging.log_text: true`.
- **stdout is the protocol.** Logs go to stderr (and to `logging.file` if set).

## Troubleshooting

- `tts_provider_unavailable`: VoiceStudio is not open, or `base_url` is wrong. Its API only runs
  while the desktop app is running.
- `tts_provider_unauthorized`: the host is remote and needs `api_key` (VoiceStudio's `OMNIVOICE_API_KEY`).
- No desktop notification on macOS: the terminal app hosting the agent needs notification
  permission in System Settings. Installing `terminal-notifier` (`brew install terminal-notifier`)
  is picked up automatically as an alternative.
- No sound on Linux: install `mpv` or make sure `paplay`/`aplay` are on PATH; `doctor` shows what
  was detected. Force one with `playback.backend`.

## Development

```bash
uv sync --all-groups
uv run pytest                      # unit tests (fake provider and player)
uv run pytest -m integration tests/integration   # real afplay; real VoiceStudio if VOXELLO_VOICESTUDIO_URL is set
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
```

Design notes: `docs/voxello-specification.md` (the specification) and `docs/voicestudio-api.md`
(the VoiceStudio HTTP contract Voxello relies on).

## Licensing

Voxello is Apache-2.0. VoiceStudio is AGPL-3.0 and is only called over HTTP. The default
OmniVoice engine's model weights are CC-BY-NC (non-commercial); choose another engine through
`tts.voicestudio.engine` where that matters.
