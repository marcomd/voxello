# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Follow [AGENTS.md](AGENTS.md) and [CONTRIBUTING.md](CONTRIBUTING.md) for shared engineering
policy, TDD and required checks. Keep that policy there rather than duplicating it here.

## What this is

Voxello is an MCP server (stdio) that gives AI agents a voice on the user's machine: `speak`, `notify`,
`stop_speaking`, `get_status`. It sends text to an OpenAI-compatible TTS endpoint (VoiceStudio on port
3900 or headless `omnivoice-server` on port 8880, both running OmniVoice), plays the WAV with a local
player subprocess (`afplay`/`mpv`/`paplay`/`aplay`/`ffplay`/PowerShell), and shows desktop notifications.
With omnivoice-server it also clones voices (server-side profiles, managed from the CLI only) and
designs them from attributes (`instructions`).
The full design lives in `docs/voxello-specification.md`; code comments cite its section numbers
(for example "spec section 15" for queue semantics, "section 23" for error codes).

## Commands

```bash
uv sync --all-groups                              # install with dev group
uv run pytest                                     # unit tests (integration excluded by default via addopts)
uv run pytest --cov --cov-report=term-missing --cov-report=html  # branches; combined floor 90%
uv run pytest tests/test_speak.py                 # one file
uv run pytest tests/test_queue.py -k interrupt    # one test by keyword
uv run pytest -m integration tests/integration    # real afplay; real TTS if VOXELLO_VOICESTUDIO_URL is set
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src                                # pyright only covers src/, not tests/
uv run voxello doctor [--synth] [-l en]           # check config, TTS server, voices per language, player, notifier
uv run voxello speak "text" [--save] [--cache] [-l en]
uv run voxello notify "text" --channels voice,desktop [--no-cache] [-l en]
echo '{"notification_type":"idle_prompt"}' | uv run voxello hook notification -l it   # what the Claude Code hook runs
echo '{"stop_hook_active":false}' | uv run voxello hook stop --agent codex   # what the Stop hook runs
uv run voxello cache list|clear                   # audio cache: hash, size, chars, voice, last use
uv run voxello cache warm --hook-phrases --language it|en   # or: cache warm FILE, cache warm -
uv run voxello speak "text" -l en -i "female, young adult, high pitch"   # voice design
uv run voxello voice list|clone NAME FILE [--ref-text T] [--yes]|update NAME|delete NAME
uv run voxello voice record NAME [--register whisper] [--text T] [--no-keep]  # guided, needs ffmpeg
uv run voxello serve                              # MCP server over stdio (what agents run)
uv run voxello config init|path|show
uv run voxello install claude [--yes] [--stop-hook] [--claude-dir DIR]   # skill + hook into ~/.claude, offer settings.json entries
uv run voxello install codex [--yes] [--codex-dir DIR]     # skill + hook into ~/.codex, offer the hooks.json Stop entry
uv build                                          # sdist + wheel; version comes from src/voxello/__init__.py
```

Toolchain: Python 3.12+ managed by uv (`.python-version`). Ruff line length 100; lint set includes
`S` (bandit), `ASYNC`, `B`, `RUF`. `pytest-asyncio` runs in `asyncio_mode = "auto"`, so async tests
need no decorator.

## Architecture

Layers, top to bottom, each depending only on the ones below:

- `mcp/server.py` builds the `MCPServer` with a lifespan that creates and starts a `VoxelloService`;
  `mcp/tools.py` registers the four tools. Tools are thin: they call the service (always with
  `role="summary"`, since MCP text is written by the agent) and convert `VoxelloError` into
  `ToolError`. `build_instructions(settings)` is what agents see: `INSTRUCTIONS` plus the
  always-summarize paragraph when `integrations.summaries.mode` is `always`; keep it in sync
  with tool descriptions if behaviour changes.
- `core/service.py` (`VoxelloService`) is the orchestrator and holds all business rules: text
  validation and length limits, request ID and `RequestRecord` state machine (`core/models.py`),
  serialising synthesis behind `_synth_lock`, temp/output storage, and mapping `notify` priorities
  to interrupt behaviour (`high`/`critical` interrupt, `low` never, `normal` uses config default).
  `notify` is implemented on top of `speak` with `mode="notification"`; the `file` channel saves
  both audio and text via `OutputStore`. Its result is `delivered`/`partial`/`failed` per channel.
  `speak`/`notify` take `cache: bool | None`: `_cache_allowed` applies the policy (notifications
  cached by default, verbatim only on request, never outside `cache.min/max_text_chars`), the
  lookup runs before `_synth_lock` so a hit never waits on the TTS server, and a miss stores the
  WAV after synthesis (a store failure is logged, never raised). Hits play the cache file in
  place and count in `StatusReport.cache_hits`. `speak`/`notify` also take `language: str | None`
  (roadmap 3.1): `_validate_language` strips, lowercases and checks `^[a-z]{2}$`
  (`invalid_language`), falling back to `speech.default_language`; `_resolve_voice` fixes the
  effective voice once (request `voice` > `speech.voices_by_language[language]` >
  `tts.voicestudio.voice` > `None` for the server default) so the provider call and the cache
  key see the same voice and language. `SpeechResult.language` reports the effective code.
  `resolve_voice` returns a `VoiceChoice(voice, instructions)` (`core/models.py`): explicit
  `instructions` (validated by `core/voice_design.normalize_instructions`) bypass configured
  voices and are an `invalid_parameter` error together with `voice`; a resolved name found in
  `speech.voice_designs` becomes those instructions. Never send both to the server: a profile
  wins over instructions there. `speak`/`notify` also take `role: VoiceRole | None`
  (`"phrase"` for hook sentences, `"summary"` for agent text): the voice or instructions of
  `integrations.phrases`/`.summaries` come right after the request's own, before
  `voices_by_language`. `role="summary"` with `cache=None` is never cached.
- `core/voices.py` (`VoiceLibrary`) is the policy behind `voxello voice`: profile-id and
  reference-audio checks, the 3-10 s warning, and `AudioCache.evict_voice({id, "clone:id"})`
  after every change. It talks to a `VoiceProfileStore` (`tts/base.py`), implemented by the
  VoiceStudio provider over omnivoice-server's `/v1/voices/profiles`. Cloning is deliberately
  not an MCP tool (consent, spec section 26; `docs/decisions/0001-voice-profiles-on-server.md`).
- `storage/cache.py` (`AudioCache`) keys entries by SHA-256 of `CacheKeyParts` (normalized text,
  effective voice, instructions (hashed only when set, so older keys stay valid), engine,
  language, speed, num_step, guidance_scale, base_url); files are
  `<hash>.wav` plus a `<hash>.json` sidecar (voice label, provider, text length, never the text).
  LRU uses the WAV mtime, touched on hit; `evict()` runs after every store. It lives in
  `user_cache_dir/audio`, a sibling of `TempStore`'s `tmp/`, and `Settings` rejects the two
  directories being equal because `TempStore.owns()` is what keeps the service from deleting cache
  files.
- `playback/manager.py` (`PlaybackManager`) is a single worker loop over an `asyncio` queue with
  interrupt, per-request cancel and stop-all semantics; it reports outcomes back to the service via a
  callback so records reach terminal states and temp files get discarded. `playback/detect.py`
  picks a `PlayerBackend` for the OS; `subprocess_player.py` wraps the subprocess into a
  `PlaybackHandle`.
- `tts/voicestudio.py` is the only provider. It speaks `POST /v1/audio/speech` and probes
  alternative paths for voices/engines because VoiceStudio and omnivoice-server differ
  (`VOICES_PATHS`, `ENGINES_PATHS`). HTTP failures are translated into stable error codes.
  `instructions` is sent only when set, and the configured voice is not added then.
  `synthesize(..., options=SynthesisOptions(seed, speed, num_step))` overrides the configured
  generation values for one request (roadmap 8.1); the service never passes it, so cache keys
  are unaffected. An unknown `clone:<id>` voice is a 404 mapped to `voice_not_found`; rejected
  attributes are `invalid_instructions`. Profile requests (`create/update/delete/list_voice_profiles`) are
  never retried; a bare FastAPI 404/405 means `voice_cloning_unsupported` (e.g. VoiceStudio).
  `_post_speech` retries `tts.voicestudio.retries` times (default 1) with exponential backoff
  from `retry_backoff_seconds` on connection-phase failures (`RETRIABLE_EXCEPTIONS`) and HTTP
  502/503/504 (`RETRIABLE_STATUSES`); 4xx and read timeouts are never retried because a slow
  server would double the time `_synth_lock` is held. `details["attempts"]` is set on the error.
- `recording/` (roadmap 8.2) is behind `voxello voice record`: `registers.py` loads the
  reading scripts from `assets/dialogue/registers/{it,en}.yaml` (same keys in every file) and
  names profiles (`marco`, `marco-whisper`); `ffmpeg.py` builds the capture (avfoundation,
  pulse/alsa; Windows raises `recorder_unavailable`) and cleanup commands, stopping a capture
  by sending `q` on stdin; `session.py` is the record/listen/redo loop with an injected
  terminal, recorder and player. `cli.cmd_voice_record` is synchronous (the keyboard drives
  it) and runs the profile check and the upload in their own `asyncio.run`; accepted clips
  stay in `storage.recordings_dir`, which `Settings` keeps apart from the temp and cache dirs.
- `notifications/desktop.py` picks `terminal-notifier`, `osascript`, `notify-send` or PowerShell.
  On Windows the toast needs Windows PowerShell 5.1 (`powershell`); `pwsh` is a playback-only
  fallback (`playback/backends.py`) because the WinRT projection is unavailable in PowerShell 7.
  Windows is unit-tested for command construction and runs in CI with fakes only; the real-audio
  procedure is `docs/windows-testing.md`.
- `cli.py` `cmd_doctor` accepts an injected `TTSProvider` for tests; `describe_voices` groups the
  server's voices by language and warns (without failing) when a configured voice is not listed;
  `--synth` times one sample phrase with the voice `core.service.resolve_voice` would pick.
- `hooks.py` is the pure Stop-hook policy behind `voxello hook stop --agent claude-code|codex`:
  `read_turn` finds the last prompt in a Claude Code transcript or Codex rollout and whether a
  Voxello `notify`/`speak` call followed it; `stop_decision` returns `Block` (summaries
  `always` + `enforce`, turn >= `min_turn_seconds`, never with `stop_hook_active`),
  `SpeakPhrase` (Codex end of turn) or `Nothing`, and fails open on unreadable transcripts.
  The CLI prints the decision JSON (`{}` otherwise: Codex requires JSON on exit 0) and speaks
  phrases through a detached `voxello hook notification` child.
- `config.py` uses `pydantic-settings` with a YAML file (path from `platformdirs`, override with
  `VOXELLO_CONFIG`) and `VOXELLO_*` env vars, nested keys with `__`. `load_settings` layers a
  project `.voxello.yaml` (found by `find_project_config` from `project_start_dir`: the
  `VOXELLO_PROJECT_DIR` override, the hook payload `cwd`, `$CLAUDE_PROJECT_DIR`, the cwd; up to
  the `.git` root, never the home dir) between the user file and the env, keeping only
  `PROJECT_KEYS` so a repository cannot redirect `tts.*`; `cli.main` reads the hook payload
  before loading settings for that reason. `speech.default_language`
  (alias `VOXELLO_LANGUAGE`) and `speech.voices_by_language` hold the language defaults;
  `tts.voicestudio.language` is deprecated and migrated into `speech.default_language` by a
  `Settings` validator that logs a warning (never prints).
- `errors.py`: every layer raises `VoxelloError(code, message)`; codes are the stable contract shown
  to agents and printed by the CLI. Add new codes there rather than raising ad hoc exceptions.

Protocols (`TTSProvider`, `AudioPlayer`, `PlaybackHandle`, `Notifier`) live in each package's
`base.py`; `VoxelloService.__init__` accepts injected implementations, which is how tests work.

## Testing conventions

`tests/conftest.py` provides `FakeProvider` (generates silent WAVs, can be told to fail or delay),
`FakePlayer` (playback never ends until the test calls `finish_current()`), `FakeNotifier`, an
isolated `settings` fixture using `tmp_path`, and a started `service` fixture. Use `settle()` to let
the playback worker run a few loop iterations before asserting on state. Anything touching real audio
or a real TTS server belongs under `tests/integration/` with the `integration` marker.

## Invariants to preserve

- stdout is the MCP protocol; all logging goes to stderr or `logging.file`. Never print in library code.
- Logs record text length, not text, unless `logging.log_text` is true.
- Voxello speaks exactly the text it receives; it never rewrites or summarises.
- Leave `voice` unset for the server default: VoiceStudio and omnivoice-server name it differently
  (`default` vs `auto`) and each rejects the other's.
- Cache files are never deleted by playback, the temp sweeper or shutdown: every discard goes
  through `TempStore.discard`, which ignores paths outside the temp dir. Only `AudioCache.evict`
  and `clear` remove them (plus `evict_voice` when a cloned voice changes). Anything that
  shapes the audio must be part of `CacheKeyParts`; new fields must keep old keys stable, as
  `instructions` does by staying out of the hash when unset.
- A request carries either a voice or voice-design instructions, never both. Clone voices only
  from the CLI, after the speaker's consent prompt.

## Claude Code integration shipped in the package

The canonical skill and hook live in `src/voxello/assets/claude/` and ship in the wheel;
`voxello install claude` (`install.py`) copies them into `~/.claude` (or `$CLAUDE_CONFIG_DIR`) and
only writes the hook entry into `settings.json` with `--yes` or an interactive confirmation.
`.claude/skills/voice-notify/SKILL.md` and `.claude/hooks/voxello-notification-hook.sh` are
byte-identical mirrors for working inside this checkout; `tests/test_install.py` fails if they
drift, so edit the package copy and re-copy. The skill makes Claude end a task with one `notify`
call when the user asks to be told by voice, passing `language` consistent with the message. The
hook script only resolves the CLI (it prefers `voxello` on the PATH, then `~/.local/bin/voxello`,
then `uv run --directory "$VOXELLO_REPO"`, then the checkout it lives in) and execs
`voxello hook notification` with the Notification JSON on stdin, adding `--language`/`--channels`
only when `VOXELLO_HOOK_LANG`/`VOXELLO_HOOK_CHANNELS` are set, so `integrations.phrases` in
config.yaml decides otherwise. With `stop [agent]` it execs `voxello hook stop --agent` and keeps
stdout (the decision). That command (`cli.cmd_hook_notification`) applies
`integrations.phrases.mode` with `install.hook_delivery()` and picks the sentence with
`install.hook_text()` from `src/voxello/assets/claude/messages/{it,en}.yaml`, the single source
of the hook sentences (including `codex_turn_complete`), also read by
`voxello cache warm --hook-phrases` (same language precedence and phrase voice); the script
contains no sentences and needs neither `jq` nor `python3`, so adding a language is adding a YAML
file (`tests/test_hook.py` checks the script stays free of sentences and that every message file
has the same keys). Both are registered at user scope, not in a project `.mcp.json` (Claude Code
warns on duplicate scopes). `voxello install codex` copies the same skill and script into
`$CODEX_HOME` and offers the Stop entry for `hooks.json` (same JSON shape); it never edits
`config.toml`. Design rationale: `docs/decisions/0002-agent-summaries.md`.

## Releasing

Bump `__version__` in `src/voxello/__init__.py` (pyproject reads it via hatch), move the
`Unreleased` section of `CHANGELOG.md` under the new version with today's date, commit, tag
`vX.Y.Z` and push the tag. Every user-visible change gets a line under `Unreleased`.
`.github/workflows/release.yml` first runs `ci.yml` as a reusable workflow on the tagged commit
(nothing is built if a check fails), then checks the tag matches, builds, creates the GitHub
release and publishes to PyPI with trusted publishing (environment `pypi`).

## Licensing note

Voxello is Apache-2.0. VoiceStudio (AGPL-3.0) and omnivoice-server (MIT) are only called over HTTP.
OmniVoice model weights are CC-BY-NC; keep that in mind when documenting commercial use.
