# Repository engineering instructions

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing code. It is the shared development
policy for humans and coding agents. See the [README](README.md#development) for setup and
quality checks, and [CLAUDE.md](CLAUDE.md) for a detailed architecture reference.

- For behavior changes and bug fixes, use red–green–refactor: describe the observable behavior,
  run a focused test that fails for the intended reason, implement the smallest coherent change,
  then refactor with passing tests. Characterize existing behavior before changing it.
- Test outcomes and public contracts. Prefer the existing fake provider/player/notifier and
  isolated storage; mock HTTP and subprocess boundaries. Use events and bounded waits for new
  concurrency tests. Keep real audio, desktop notifications and live TTS opt-in.
- Preserve stable error codes, MCP schemas, exact spoken text, stderr-only library logging,
  privacy defaults and separation of temporary, cached and saved audio.
- Keep CLI/MCP adapters thin. Put policy in the service or small pure functions; use existing
  protocols for external dependencies. Add abstractions when a concrete use case needs them.
- Own async tasks, subprocesses and files explicitly. Cancellation must clean up resources and
  propagate; terminal outcomes must release their temporary audio.
- Run Ruff, Pyright and the full unit suite with branch coverage before handing off code changes.
  The combined coverage floor is 90%; do not lower it or add exclusions to hide missing tests.
  For focused tests during TDD, omit coverage so the whole-project floor does not interfere.
- Keep changes reviewable. Record meaningful architectural tradeoffs in `docs/`; update public
  documentation and `CHANGELOG.md` when behavior changes. Report checks and remaining limitations.

## Project map

Voxello is a Python 3.12+ MCP server and CLI for speech, notifications, voice profiles and
multi-voice dialogue production. It calls an OpenAI-compatible TTS endpoint over HTTP and uses
local player and notification backends. The detailed specification is in
`docs/voxello-specification.md`.

- `core/service.py` owns request validation, orchestration, synthesis serialization, caching
  policy and delivery; `mcp/` and `cli.py` adapt external interfaces to the underlying services.
- `tts/` implements the provider protocol and VoiceStudio/omnivoice-server HTTP integration;
  `core/voices.py` manages voice profiles. `playback/` owns playback and cancellation;
  `notifications/` selects desktop backends.
- `storage/` separates temporary request audio, cached audio and saved output. Cache keys include
  audio-shaping inputs and must remain compatible with existing entries.
- `dialogue/` implements CLI-only production: segmentation and annotation, cast, rendering,
  review, playback and export. `project.py` owns project files; `sync.py`, `sync_storage.py` and
  `project_lock.py` handle conservative source synchronization, backups and recovery. Text is
  a verbatim source slice, while `tts_text` holds pronunciation edits. Current takes and
  approvals depend on fingerprints and available audio.
- `recording/` handles guided voice recording and cleanup. `config.py` loads settings;
  `hooks.py` implements agent hook policy. `errors.py` defines stable error codes.
- `tests/conftest.py` supplies fake providers, players and notifiers and isolated settings;
  tests requiring real audio or live TTS belong under `tests/integration/`.

Preserve MCP stdout for the protocol and send library logging to stderr or its configured file.
Do not log spoken text by default. Speak the exact requested text; do not rewrite it. Leave the
voice unset for the server default, never send a voice and voice-design instructions together,
and clone voices only from the CLI after consent. Playback and temporary-file cleanup must never
delete cached audio. The package ships the voice skills and hook from `src/voxello/assets/claude/`;
their `.claude/` checkout mirrors must remain byte-identical as checked by `tests/test_install.py`.

## Shared agent workflows

Repository workflow instructions are shared under `docs/skills/`. The Claude and Codex skill
entry points for PR creation and review triage delegate to those guides; change the shared guide
when changing workflow logic. The packaged `voice-dialogue` and `voice-notify` skills are product
assets with their own installation and mirror requirements described in `CLAUDE.md`.

For ordinary checks use the commands in the README. Dialogue projects can be inspected with
`uv run voxello dialogue status -p DIR`; source changes can be previewed with
`uv run voxello dialogue sync SOURCE -p DIR --dry-run`, then applied with confirmation or
recovered with `uv run voxello dialogue sync --recover -p DIR`.
