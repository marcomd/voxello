# Voxello

**A local voice and notification layer for AI agents.**

Voxello is a small MCP server that lets Claude Code, Codex, Cursor and other MCP clients
`speak`, `notify`, `stop_speaking` and `get_status`. It sends text to a local TTS server
(VoiceStudio, or the headless `omnivoice-server`, both running OmniVoice), plays the audio on
your speaker, shows desktop notifications, and cleans up after itself. The agent decides *what* to say; Voxello
handles *how* it is delivered.

```
Agent ──MCP──▶ Voxello ──HTTP──▶ VoiceStudio / omnivoice-server ──▶ WAV ──▶ afplay / mpv / PowerShell
                  └──▶ desktop notification, saved file
```

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- A TTS server on this machine or on an intranet host: [VoiceStudio](https://github.com/debpalash/VoiceStudio)
  (port 3900, API runs while the app is open) or [omnivoice-server](https://github.com/maemreyo/omnivoice-server)
  (port 8880, headless)
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

Minimal intranet setup with `omnivoice-server`:

```yaml
tts:
  voicestudio:
    base_url: http://192.168.1.144:8880
    # voice: alloy          # omit for the server default; presets: alloy, ash, ballad, cedar, coral, echo, fable, marin, nova, onyx, sage, shimmer, verse
    language: it
```

With VoiceStudio on another host:

```yaml
tts:
  voicestudio:
    base_url: http://voicestudio.lan:3900
    api_key: "the key configured as OMNIVOICE_API_KEY on the VoiceStudio host"
    engine: omnivoice       # or voxcpm2, cosyvoice, mlx-audio, kittentts, moss-tts-nano
    # voice: <profile id>
    language: it
```

Leave `voice` unset to use the server's default: VoiceStudio and omnivoice-server disagree on
its name (`default` vs `auto`) and each rejects the other's.

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

Check the connection with `claude mcp get voxello`; it should report `Connected`. A user-scoped
entry is preferable to a project `.mcp.json` because Claude Code warns when the same server is
defined in two scopes with different commands.

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

## Claude Code skill and hook

Two small Claude Code additions ship in `.claude/` and turn Voxello into the "tell me when you
are done" workflow.

**`voice-notify` skill** (`.claude/skills/voice-notify/SKILL.md`). When you ask Claude Code to
notify you by voice ("avvisami a voce quando hai finito", "notify me by voice", "leggimelo"), the
skill activates and Claude ends the task with one `notify` call carrying a one or two sentence
spoken summary in your language, on the voice and desktop channels, with high priority when the
task failed or is blocked. Install it for every project by copying the file:

```bash
mkdir -p ~/.claude/skills/voice-notify
cp .claude/skills/voice-notify/SKILL.md ~/.claude/skills/voice-notify/
```

It can also be invoked explicitly with `/voice-notify`. The skill relies on the model to make the
final call; the hook below is the deterministic half.

**Notification hook** (`.claude/hooks/voxello-notification-hook.sh`). Claude Code fires a
`Notification` event when it waits on a permission prompt or goes idle. The hook turns the event
into a short spoken sentence ("Claude Code chiede un permesso per continuare.") and delivers it
through `voxello notify`, so you hear it when you have walked away from the terminal. Add it to
`~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      {
        "matcher": "permission_prompt|idle_prompt|agent_needs_input|elicitation_dialog",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/voxello/.claude/hooks/voxello-notification-hook.sh",
            "async": true,
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The script speaks Italian by default; set `VOXELLO_HOOK_LANG=en` for English and
`VOXELLO_HOOK_CHANNELS=desktop` to keep it silent. Review or disable it later with `/hooks`.

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

- `tts_provider_unavailable`: the TTS server is down or `base_url` is wrong. VoiceStudio's API only
  runs while the desktop app is open; `omnivoice-server` runs headless.
- `tts_provider_unauthorized`: the host is remote and needs `api_key` (`OMNIVOICE_API_KEY` on the server).
- `voice_not_found`: the voice id does not exist on this server; `voxello doctor` lists the valid ids.
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

Design notes: `docs/voxello-specification.md` (the specification), `docs/voicestudio-api.md`
(VoiceStudio HTTP contract) and `docs/omnivoice-server-api.md` (omnivoice-server setup and contract).

## Licensing

Voxello is Apache-2.0. VoiceStudio is AGPL-3.0 and omnivoice-server is MIT; both are only called
over HTTP. OmniVoice's model weights are CC-BY-NC (non-commercial); with VoiceStudio you can choose
another engine through `tts.voicestudio.engine` where that matters.
