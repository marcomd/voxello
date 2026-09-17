# Voxello

**A local voice and notification layer for AI agents.**

![cover.jpeg](docs/cover.jpeg)

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
  (port 8880, headless). New to this? [docs/TUTORIAL.md](docs/TUTORIAL.md) walks through installing
  `omnivoice-server` on Windows with an NVIDIA GPU and on Apple Silicon, starting it and testing it with curl.
- An audio player: `afplay` (macOS, built in), `mpv`/`paplay`/`aplay`/`ffplay` (Linux), PowerShell (Windows)

## Install

Install Voxello once as a [uv tool](https://docs.astral.sh/uv/guides/tools/); the `voxello` command
then works from any directory and any project:

```bash
uv tool install voxello
voxello doctor
```

Alternatives:

```bash
uvx voxello speak "Hello"                                    # run without installing
uv tool install git+https://github.com/marcomd/voxello       # latest main instead of the PyPI release
uv tool upgrade voxello                                      # later
```

To work on Voxello itself, clone it and use `uv run` inside the checkout (or
`uv run --directory /path/to/voxello voxello ...` from elsewhere):

```bash
git clone https://github.com/marcomd/voxello && cd voxello
uv sync --all-groups
uv run voxello doctor
```

The rest of this README uses the installed `voxello` command; in a checkout prefix `uv run`.

## Configure

```bash
voxello config init        # writes ~/Library/Application Support/voxello/config.yaml (macOS)
voxello config path        # shows where the file lives on this OS
```

Minimal intranet setup with `omnivoice-server`:

```yaml
tts:
  voicestudio:
    base_url: http://192.168.1.144:8880
    # voice: alloy          # omit for the server default; presets: alloy, ash, ballad, cedar, coral, echo, fable, marin, nova, onyx, sage, shimmer, verse
speech:
  default_language: it      # used when a request does not pass `language`
```

With VoiceStudio on another host:

```yaml
tts:
  voicestudio:
    base_url: http://voicestudio.lan:3900
    api_key: "the key configured as OMNIVOICE_API_KEY on the VoiceStudio host"
    engine: omnivoice       # or voxcpm2, cosyvoice, mlx-audio, kittentts, moss-tts-nano
    # voice: <profile id>
speech:
  default_language: it
```

Leave `voice` unset to use the server's default: VoiceStudio and omnivoice-server disagree on
its name (`default` vs `auto`) and each rejects the other's.

The language is chosen per request: agents pass `language` (ISO 639-1, `it`, `en`, ...) with the
text, the CLI takes `--language/-l`, and `speech.default_language` covers calls that pass none.
Cloned voices often sound best in one language, so an optional map picks the voice from the
language when the request names none (an explicit `voice` still wins, then the map, then the
global `voice`, then the server default):

```yaml
speech:
  default_language: it
  voices_by_language:
    it: italian_voice
    en: english_voice
```

`tts.voicestudio.language`, the global setting of earlier versions, is deprecated: it still acts
as the default and `voxello doctor` reminds you to move it to `speech.default_language`.

Phrases that repeat, such as the Claude Code hook alerts, are synthesized once and replayed from
an on-disk audio cache. Notifications are cached by default, `speak` only on request, and only
phrases up to `max_text_chars` qualify:

```yaml
cache:
  enabled: true
  # directory: ~/.cache/voxello/audio   # default: OS cache dir
  max_entries: 200
  max_age_days: 90
  max_text_chars: 300
```

Environment variables override the file: `VOXELLO_VOICESTUDIO_URL`, `VOXELLO_VOICESTUDIO_API_KEY`,
`VOXELLO_DEFAULT_VOICE`, `VOXELLO_LANGUAGE`, `VOXELLO_LOG_LEVEL`, or any nested key as
`VOXELLO_TTS__VOICESTUDIO__ENGINE`.
`VOXELLO_CONFIG` points to an alternative config file. See `config.example.yaml` for every option.
`voxello doctor` prints which config file it read and where it came from (`--config`,
`VOXELLO_CONFIG` or the OS default), plus which executable and Python interpreter are running.

## Try it without an agent

```bash
voxello doctor                                  # provider reachable? player? notifier?
voxello speak "Build completata. Tutti i test passano."
voxello speak "Build finished. All tests pass." --language en   # -l en: pronunciation follows the text
voxello speak "Salvami" --save                  # also writes ~/Voxello/<timestamp>_<id>.wav
voxello notify "Refactoring completato." --channels voice,desktop
voxello notify "Build completata." --no-cache   # notify caches by default; speak needs --cache
voxello cache warm --hook-phrases --language it # pre-synthesize the Claude Code hook sentences
voxello cache warm --hook-phrases -l en         # the English ones, synthesized as English
voxello cache warm phrases.txt                  # one phrase per line ('-' reads stdin)
voxello cache list                              # hash, size, length, voice, last use; never the text
voxello cache clear
```

## Register with your agent

**Claude Code** (user scope, available in every project):

```bash
claude mcp add --scope user --transport stdio voxello -- voxello serve
```

Check the connection with `claude mcp get voxello`; it should report `Connected`. A user-scoped
entry is preferable to a project `.mcp.json` because Claude Code warns when the same server is
defined in two scopes with different commands.

**Codex CLI** (`~/.codex/config.toml`):

```toml
[mcp_servers.voxello]
command = "voxello"
args = ["serve"]
```

**Cursor** (`~/.cursor/mcp.json`):

```json
{ "mcpServers": { "voxello": { "command": "voxello", "args": ["serve"] } } }
```

If the agent's environment does not see `~/.local/bin`, use the absolute path printed by
`voxello doctor` (`Executable:`). From a checkout without a tool install, use
`uv run --directory /path/to/voxello voxello serve` as the command instead.

Then ask the agent, for example: *"Refactor the auth module, run the tests, and tell me by
voice when you are done."*

## Claude Code skill and hook

Two small Claude Code additions ship inside the package and turn Voxello into the "tell me when
you are done" workflow. Install both with one command:

```bash
voxello install claude          # add --yes to register the hook without being asked
```

It copies the `voice-notify` skill to `~/.claude/skills/voice-notify/SKILL.md` and the
Notification hook to `~/.claude/hooks/voxello-notification-hook.sh`, prints the JSON entry the
hook needs in `~/.claude/settings.json`, and writes it only after you confirm (or with `--yes`).
Existing settings are preserved; running it again is harmless. Options: `--claude-dir` (defaults
to `$CLAUDE_CONFIG_DIR` or `~/.claude`), `--lang en` for English hook sentences, `--no-hook`,
`--no-skill`. The same files live in this repository under `.claude/` for people working in the
checkout.

**`voice-notify` skill.** When you ask Claude Code to notify you by voice ("avvisami a voce quando
hai finito", "notify me by voice", "leggimelo"), the skill activates and Claude ends the task with
one `notify` call carrying a one or two sentence spoken summary in your language, on the voice and
desktop channels, with high priority when the task failed or is blocked. It can also be invoked
explicitly with `/voice-notify`. The skill relies on the model to make the final call; the hook
below is the deterministic half.

**Notification hook.** Claude Code fires a `Notification` event when it waits on a permission
prompt or goes idle. The hook script looks for `voxello` on the PATH, then in `~/.local/bin`, then
falls back to `uv run --directory "$VOXELLO_REPO"` if that variable points to a checkout (when none
is available it logs to stderr and exits without speaking) and hands the event to
`voxello hook notification`. That command picks a short sentence for the notification type from
the message files shipped in the package (`voxello/assets/claude/messages/it.yaml`, `en.yaml`),
for example "Claude Code chiede un permesso per continuare.", and delivers it through `notify` in
that language with `--cache`, so each sentence is synthesized once and later alerts play even
while the TTS server is down; run `voxello cache warm --hook-phrases --language it` (or `en`) so
the very first alert is instant. Adding a language is adding a message file. This is the entry
`voxello install claude` proposes for `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      {
        "matcher": "permission_prompt|idle_prompt|agent_needs_input|elicitation_dialog",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/you/.claude/hooks/voxello-notification-hook.sh",
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

## Use inside a project with `uv run`

`uv run voxello` resolves `voxello` against the *current* project, so inside another repository it
fails unless that project depends on Voxello. If you want exactly that form (for example in a
`Makefile` shared by a team), add Voxello as a development dependency:

```bash
uv add --dev voxello
uv run voxello speak "Build completed. All tests pass."
```

For everything else the tool install above is simpler: one `voxello` on the PATH, shared by every
project and by the agents' MCP configuration. `voxello doctor` reports which mode is in use.

## Tools

| Tool            | Purpose                                                               | Key parameters                                                                            |
| --------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `speak`         | synthesize and play text                                              | `text`, `language`, `voice`, `interrupt` (default true), `save`, `play`, `mode`, `cache`, `client_id` |
| `stop_speaking` | stop playback and clear the queue                                     | optional `request_id`                                                                     |
| `get_status`    | state, current request, queue, cache hits, provider and player health | none                                                                                      |
| `notify`        | route a short message to `voice`, `desktop` and/or `file`             | `message`, `language`, `channels`, `priority`, `title`, `cache`                           |

`speak` returns metadata (`request_id`, `status`, `duration_ms`, `saved_path`, `language`,
`cached`), never audio. `language` is the ISO 639-1 code of the text (`it`, `en`); agents are asked
to pass it so pronunciation follows the text, and it falls back to `speech.default_language`.
`cache` is tri-state: unset follows the policy (notifications yes, verbatim speech no), `true`
caches a phrase the agent will repeat, `false` keeps one-off text out of the cache. Errors come
back as tool errors with a stable code: `tts_provider_unavailable`, `tts_provider_unauthorized`,
`voice_not_found`, `invalid_language`, `text_too_long`, `queue_full`, `playback_unavailable`,
`notification_unavailable`.

## How it behaves

- **Interrupt by default.** A new `speak` stops what is playing and clears the queue. Pass
  `interrupt: false` to queue instead (FIFO, `playback.max_queue_size` items).
- **Short by design.** Text is capped at `limits.max_text_chars` (2000). Voice is for summaries
  and status; the textual answer carries the details.
- **Private by default.** Temporary audio lives in the OS cache directory with 0700 permissions,
  gets random names, and is deleted after playback and after `storage.temp_retention_minutes`.
  Logs record text length, not text, unless `logging.log_text: true`.
- **Cached phrases persist, hashed.** Cached audio lives in its own 0700 directory under a
  SHA-256 of the text and synthesis parameters; the text itself is never written there. Only
  notifications (or explicit `cache: true`) up to 300 characters are cached, the least recently
  used entries are dropped beyond `cache.max_entries` or `cache.max_age_days`, and
  `voxello cache clear` empties it. Set `cache.enabled: false` to keep every WAV short-lived.
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
- A notification still plays with the old voice after you changed `voice` or `base_url`: it should
  not, since both are part of the cache key, but `voxello cache clear` removes any doubt.
  `voxello cache list` and `doctor` show what the cache holds.

## Development

```bash
uv sync --all-groups
uv run pytest                      # unit tests (fake provider and player)
uv run pytest -m integration tests/integration   # real afplay; real VoiceStudio if VOXELLO_VOICESTUDIO_URL is set
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
```

Docs: `docs/TUTORIAL.md` (setting up an omnivoice-server host on Windows/NVIDIA or Apple Silicon),
`docs/voxello-specification.md` (the specification), `docs/voicestudio-api.md` (VoiceStudio HTTP
contract), `docs/omnivoice-server-api.md` (omnivoice-server flags and contract) and `docs/roadmap.md`
(what comes next).

### Releasing

The version lives only in `src/voxello/__init__.py` (`pyproject.toml` reads it through hatch).
Bump it, move the `Unreleased` entries of `CHANGELOG.md` under the new version, commit, then tag
and push:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

`.github/workflows/release.yml` checks that the tag matches `__version__`, builds the sdist and
wheel, smoke-tests the wheel, creates a GitHub release with the files and publishes to PyPI through
[trusted publishing](https://docs.pypi.org/trusted-publishers/). One-time setup on pypi.org: add a
GitHub publisher for project `voxello` with owner `marcomd`, repository `voxello`, workflow
`release.yml`, environment `pypi`, and create the `pypi` environment in the GitHub repository
settings. `ci.yml` runs ruff, pyright and pytest on macOS and Linux for every push and pull request.

## Licensing

Voxello is Apache-2.0. VoiceStudio is AGPL-3.0 and omnivoice-server is MIT; both are only called
over HTTP. OmniVoice's model weights are CC-BY-NC (non-commercial); with VoiceStudio you can choose
another engine through `tts.voicestudio.engine` where that matters.
