# Changelog

All notable changes to Voxello are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/): while the major version is 0, a minor bump may
change the MCP tool signatures or the configuration layout.

## [Unreleased]

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

[Unreleased]: https://github.com/marcomd/voxello/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/marcomd/voxello/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/marcomd/voxello/releases/tag/v0.1.0
