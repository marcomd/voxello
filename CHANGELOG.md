# Changelog

All notable changes to Voxello are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/): while the major version is 0, a minor bump may
change the MCP tool signatures or the configuration layout.

## [Unreleased]

### Added

- `voxello voice record NAME [--register R | --all]` records a reference clip with guidance
  (tone hint, sentence to read, countdown, record until Enter, cleanup with ffmpeg, playback,
  redo) and clones it as `NAME` or `NAME-REGISTER` with the read sentence as transcript, after
  the same permission prompt as `voice clone`. `--all` records every built-in register in one
  session with one permission prompt, clones each clip as soon as it is accepted, lets you
  skip a register, offers to record only the registers not cloned yet, and ends with a
  summary. Step-by-step guide in `docs/recording-voices.md`. Built-in registers `neutral`, `whisper`, `sad`, `angry`
  and `happy` with Italian and English sentences; `--text` for your own. Clips are kept in the
  new `storage.recordings_dir` unless `--no-keep`. macOS and Linux; needs `ffmpeg`.
  `voxello doctor` prints a `Recorder:` line. New error codes `recorder_unavailable` and
  `recording_failed`.
- The TTS provider accepts per-request `seed`, `speed` and `num_step`
  (`SynthesisOptions`): the same seed reproduces the same audio. `speak` and `notify` do not
  use them yet, so their requests and cache keys are unchanged.

## [0.3.0] - 2026-11-21

Per-request language selection (roadmap milestone 3), quality and robustness work (roadmap
milestone 4), cloned and designed voices (roadmap milestone 6), and configurable agent
notifications for Claude Code and Codex (roadmap milestone 7).

### Added

- `integrations` in `config.yaml` chooses what the agent integrations say and with which voice.
  `integrations.phrases` (hook sentences): `mode` `cached` (default, as before), `live` (the
  event's own message, synthesized every time) or `off`, plus `voice` or `instructions`,
  `language` and `channels`. `integrations.summaries` (text the agent writes): `mode`
  `on_request` (default) or `always`, `voice` or `instructions`, `enforce` and
  `min_turn_seconds`. The role voice wins over `speech.voices_by_language`; a request's own
  voice still wins over it. `voxello doctor` prints both and checks the configured voices.
- With `summaries.mode: always` the MCP server instructions ask every client to end each
  non-trivial task with one spoken `notify` summary.
- `voxello hook stop --agent claude-code|codex`: reads a Stop event and, with
  `summaries.enforce`, asks the agent once for the spoken summary it skipped (only for turns of
  at least `min_turn_seconds`, never when `stop_hook_active`, never when the transcript cannot
  be read). For Codex it otherwise speaks the new `codex_turn_complete` sentence in a detached
  process. `voxello install claude --stop-hook` registers it for Claude Code.
- `voxello install codex`: copies the `voice-notify` skill and the hook script into
  `$CODEX_HOME` (`~/.codex`), offers the Stop entry for `hooks.json` and prints the
  `config.toml` MCP block without editing it.
- `voxello hook notification --mode cached|live|off` overrides `integrations.phrases.mode`.
- Per-project overrides: a `.voxello.yaml` in the project (searched up to the repository root,
  never in the home directory) is layered over `config.yaml` for the hooks, the MCP server and
  the CLI, so each project can have its own voices and desktop title. Only `integrations`,
  `speech.default_language`, `speech.voices_by_language`, `speech.voice_designs` and
  `notifications.title` are taken; other keys are ignored with a warning. Hooks search from the
  event's `cwd`, the MCP server from `$CLAUDE_PROJECT_DIR` or its working directory;
  `VOXELLO_PROJECT_DIR` overrides both. `voxello doctor` prints the project file in use.

- Voice design: `instructions` on the `speak` and `notify` tools, `--instructions/-i` on
  `speak`, `notify` and `cache warm`. Attributes (`female, young adult, high pitch`) are checked
  against the OmniVoice vocabulary, normalized, sent to omnivoice-server and reported back in
  `SpeechResult.instructions`. Unknown or conflicting attributes fail with `invalid_instructions`.
- `speech.voice_designs`: named designs usable wherever a voice is accepted, including
  `voices_by_language` and `tts.voicestudio.voice`.
- Cloned voices: `voxello voice clone NAME FILE [--ref-text] [--overwrite] [--yes]`,
  `voice update`, `voice delete` and `voice list` manage omnivoice-server profiles, used
  afterwards as `--voice clone:NAME`. `clone` asks to confirm the speaker's permission. New error
  codes `voice_exists`, `voice_cloning_unsupported` and `invalid_reference_audio`. Cached phrases
  recorded with a voice are dropped when Voxello changes or deletes it, also while
  `cache.enabled` is false, so re-enabling the cache never replays the old voice.
- The `notify` tool and `voxello notify` accept `voice`.
- `voxello doctor` lists voice designs and cloned voices and warns when the server's voice-design
  vocabulary differs from Voxello's.

- `tts.voicestudio.connect_timeout_seconds` (5), `retries` (1) and `retry_backoff_seconds` (0.5):
  a synthesis request that fails to connect or gets HTTP 502/503/504 is retried with exponential
  backoff; 4xx answers and read timeouts are never retried. The `tts_provider_*` errors report
  `attempts` in their details.
- `voxello doctor` groups the server's voices by language, checks that every configured
  `voice`/`voices_by_language` id is in the server's list (a warning, not a failure) and, with
  `--synth [--language XX]`, synthesizes a sample phrase and prints the latency, size and
  duration of the audio. `list_engines` joined the `TTSProvider` protocol.
- `pwsh` (PowerShell 7) as a Windows playback fallback after `powershell`; toasts still need
  Windows PowerShell 5.1.
- `docs/windows-testing.md`: manual checklist to verify Windows playback, toasts, interrupt and
  the hook on real hardware, with a results table.
- CI runs the unit suite on Windows (Python 3.12, fakes only, no coverage floor) besides the
  Linux and macOS matrix; the release workflow runs the whole CI matrix on the tagged commit
  before building or publishing anything.
- MCP-level end-to-end tests for `notify` (`partial`, `failed`, file channel) and for the
  synthesis lock (a cache hit never waits for a slow synthesis).
- Shared contributor/agent engineering instructions, branch
  coverage reports and a 90% combined coverage gate in CI across Python 3.12–3.14.
- Repository PR template, Voxello-specific PR creation guidance and an issue/PR label catalog.
- Repository PR review triage skill with Voxello's TDD/checks, a bounded opt-in autofix loop and
  a `status:to-approve` label for the human approval handoff.
- `language` parameter (ISO 639-1, e.g. `it`, `en`) on the `speak` and `notify` MCP tools, on
  `VoxelloService.speak`/`notify` and on the CLI (`speak`, `notify`, `cache warm`, all with
  `--language/-l`). The value is validated (`^[a-z]{2}$`, case-insensitive, new error code
  `invalid_language`), sent to the TTS server as the pronunciation hint and reported back as
  `language` in `SpeechResult`; `get_status` reports the default. The server instructions ask
  agents to pass the language the text is written in.
- `speech.default_language` (default `it`, env alias `VOXELLO_LANGUAGE`) for requests that pass
  no language, and `speech.voices_by_language` mapping a voice to each language. Voice
  precedence: request `voice`, then `voices_by_language[language]`, then `tts.voicestudio.voice`,
  then the server default. The effective language and voice are part of the cache key, so the
  same sentence in two languages is two entries.
- `voxello hook notification`: reads a Claude Code `Notification` JSON from stdin, picks the
  sentence for its type from `assets/claude/messages/<language>.yaml` (unknown types speak the
  event's own message, then the `default` sentence, truncated to 250 characters) and delivers it
  with `notify --cache` in that language. `--language`/`--channels` default to
  `VOXELLO_HOOK_LANG`/`VOXELLO_HOOK_CHANNELS`.
- `doctor` prints `default_language`, the `voices_by_language` map and a deprecation line when
  `tts.voicestudio.language` is still set.
- `docs/TUTORIAL.md`: step-by-step guide to installing and running `omnivoice-server` on Windows
  with an NVIDIA GPU and on Apple Silicon, testing it with curl, and pointing Voxello at it.
  Linked from the README.

### Fixed

- An unknown `clone:<name>` voice (HTTP 404 from omnivoice-server) is reported as `voice_not_found`
  instead of a missing-endpoint `tts_provider_error`.
- Desktop notification subprocesses are killed and reaped when delivery times out or is cancelled.

### Changed

- Text that reaches Voxello through the MCP `speak`/`notify` tools is no longer cached unless the
  agent passes `cache: true`: agent summaries do not repeat. `voxello notify` and the hooks keep
  the previous default.
- The hook script no longer passes `--language it --channels voice,desktop` itself: without
  `VOXELLO_HOOK_LANG`/`VOXELLO_HOOK_CHANNELS` the language comes from
  `integrations.phrases.language`, then `speech.default_language` when a message file exists
  for it (else Italian), and the channels from `integrations.phrases.channels`. `cache warm
  --hook-phrases` uses the same language and the phrase voice, so its keys match the hook's.
- The `voice-notify` skill passes `cache: false`, leaves the voice and the desktop title to the
  configuration (the title is now `notifications.title`, "Voxello" by default, instead of
  "Claude Code") and applies to every task when the server instructions say summaries are
  always on.

- The Claude Code hook script no longer contains the spoken sentences nor parses JSON (no `jq`
  or `python3` needed): it only resolves the `voxello` command and execs
  `voxello hook notification --language "$VOXELLO_HOOK_LANG" --channels "$VOXELLO_HOOK_CHANNELS"`.
  Adding a hook language means adding a message file. `voxello install claude --lang` accepts
  every language that has one. Re-run `voxello install claude` to update the installed copy.
- `voxello cache list` and `cache clear` work while `cache.enabled` is false, on the files kept
  from when it was on (with a note on stderr). `cache warm` still refuses.
- `voxello cache warm --language` now also sets the TTS language of the warmed phrases, so the
  English hook sentences are cached under the key the hook will look up.
- The `voice-notify` skill passes `language` consistent with the message text.
- `TTSProvider.synthesize(text, voice=None, language=None)`: the provider gained a `language`
  argument; `VoiceStudioProvider` sends it in the payload and falls back to the deprecated
  setting only when no language is given.

### Deprecated

- `tts.voicestudio.language`: use `speech.default_language`. The old key still works as the
  default (with a warning on stderr) unless the new one is set, in which case it is ignored.

## [0.2.0] - 2026-09-17

Audio cache for static phrases (roadmap milestone 2).

### Added

- `storage/cache.py` with `AudioCache`: synthesized audio for repeated phrases is stored once
  under a SHA-256 of the normalized text and every synthesis parameter (voice, engine, language,
  speed, num_step, guidance_scale, server URL). Entries are a hash-named WAV plus a JSON sidecar
  holding the voice label, provider and text length, never the text. Least-recently-used eviction
  by entry count and age, 0700 directory and 0600 files, atomic writes.
- `cache:` configuration section (`enabled`, `directory`, `max_entries`, `max_age_days`,
  `min_text_chars`, `max_text_chars`). The cache directory defaults to `audio/` next to the
  temporary directory and must differ from it.
- `cache` parameter on the `speak` and `notify` MCP tools and `--cache/--no-cache` on the CLI.
  Unset follows the policy: notifications are cached, verbatim speech is not, and only phrases
  within the configured length bounds qualify. A cache hit skips synthesis entirely, so cached
  alerts keep working while the TTS server is unreachable.
- `cached` in `speak`/`notify` results and `cache_hits` in `get_status`; `doctor` prints the
  cache directory, entry count and size.
- `voxello cache list|clear|warm`. `warm` pre-synthesizes phrases from a file, from stdin (`-`)
  or the Claude Code hook sentences with `--hook-phrases --language it|en`.
- Hook sentences shipped as `assets/claude/messages/it.yaml` and `en.yaml`, read by
  `install.hook_phrases()`.
- `CHANGELOG.md`.

### Changed

- The Claude Code Notification hook passes `--cache`, so each alert sentence is synthesized once.
  Reinstall the `voxello` tool before re-running `voxello install claude`: older builds reject
  the flag.
- Log lines record cache hits and misses with the text length, not the text.

## [0.1.0] - 2026-09-17

First working version: the MVP of the specification (phases 1-3) plus roadmap milestone 1.

### Added

- MCP server over stdio with the `speak`, `stop_speaking`, `get_status` and `notify` tools.
- TTS through an OpenAI-compatible `POST /v1/audio/speech` endpoint, verified against
  VoiceStudio and omnivoice-server (both running OmniVoice); stable error codes for
  unreachable, unauthorized, unknown-voice and provider errors.
- Local playback with `afplay`, `mpv`, `paplay`, `aplay`, `ffplay` or PowerShell, an
  interruptible FIFO queue, per-request cancel and stop-all.
- Desktop notifications through `terminal-notifier`, `osascript`, `notify-send` or PowerShell;
  `notify` reports `delivered`, `partial` or `failed` per channel and maps priority to
  interrupt behaviour.
- Temporary audio with random names, 0700/0600 permissions and a retention sweeper; saved output
  confined to `output.directory`.
- YAML configuration with `VOXELLO_*` environment overrides and the flat aliases from the
  specification.
- CLI: `serve`, `doctor`, `speak`, `notify`, `config init|path|show`, `install claude`.
- Claude Code integration: the `voice-notify` skill and the Notification hook, installed by
  `voxello install claude`, which offers to register the hook in `settings.json`.
- Installable as a uv tool from any project; dynamic version from `voxello.__version__`;
  GitHub Actions CI and a tag-driven release workflow publishing to PyPI.

[Unreleased]: https://github.com/marcomd/voxello/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/marcomd/voxello/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/marcomd/voxello/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/marcomd/voxello/releases/tag/v0.1.0
