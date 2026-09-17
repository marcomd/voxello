# Voxello roadmap

Status as of 2026-09-17. The MVP described in the specification (phases 1-3, section 38) is
implemented: `speak`, `stop_speaking`, `get_status`, `notify`, queue with interrupt, VoiceStudio /
omnivoice-server provider, desktop notifications, CLI, Claude Code skill and hook. This roadmap
lists what is missing to make Voxello an everyday tool, in priority order.

Conventions: each item names the files involved, the acceptance criteria and the expected tests.
Milestones are sequential, but items within a milestone are independent.

## Milestone 1 — Use from any project (done 2026-09-17)

Goal: from any directory, without a Voxello checkout at hand,
`voxello speak "Build completed. All tests pass."` works.

Before this milestone it only worked with `uv run --directory /path/to/voxello ...` or after
`uv tool install --from /path/to/voxello voxello`. Running `uv run voxello` inside another
project looks for `voxello` among that project's dependencies and fails.

Delivered: dynamic version from `__init__.py`, `.github/workflows/release.yml` (tag → build →
GitHub release → PyPI trusted publishing) and `ci.yml`, `doctor` prints executable, interpreter,
install mode and config source, the hook prefers `voxello` on the PATH, `voxello install claude`
copies the skill and hook from `src/voxello/assets/claude/` and offers to register the hook.
Still open: the first tagged release and the one-time PyPI trusted-publisher setup.

### 1.1 Global tool installation, documented and verified — done

- Make `uv tool install voxello` (from PyPI or from git) the main path in the README, with
  `uv run --directory` as the alternative for people developing Voxello itself.
- Publish the package: on PyPI or, as a first step, installable from git
  (`uv tool install git+https://github.com/marcomd/voxello`). Add a release workflow
  (tag → build → publish) and keep `version` in `pyproject.toml` and `__init__.py` aligned.
- Verify that the installed command finds the user configuration (`platformdirs`) and that
  `voxello doctor` prints where it read the config and the binary from.
- Acceptance: in an empty directory, `voxello speak "..."` and `uvx voxello speak "..."`
  speak without extra arguments.

### 1.2 Hook and skill independent of the checkout — done

- The hook (`.claude/hooks/voxello-notification-hook.sh`) currently runs
  `uv run --directory "$REPO" voxello notify`. It must prefer a `voxello` on the PATH and use
  `VOXELLO_REPO` only as a fallback; log to stderr when neither is available.
- Add `voxello install claude` (or `voxello setup`) that copies the skill into
  `~/.claude/skills/voice-notify/` and proposes the hook JSON snippet for
  `~/.claude/settings.json`, without modifying the file unless confirmed.
- Acceptance: hook and skill work with Voxello installed only as a tool.

### 1.3 `uv run voxello` inside a third-party project — done

- Document usage as a development dependency (`uv add --dev voxello`) for people who want
  exactly the `uv run voxello speak ...` form inside their own `pyproject.toml`.
- No code: README only, plus one line in `doctor` explaining the difference between the two modes.

## Milestone 2 — Audio cache for static phrases

Goal: fixed phrases (Claude Code hook, repeated notifications) are synthesized only once.
Today every call regenerates the WAV, stores it in `TempStore` and deletes it after
`temp_retention_minutes`.

### 2.1 `AudioCache` in `storage/`

- New module `storage/cache.py` with `AudioCache(directory, max_entries, max_age_days)`.
- Key: SHA-256 hash of (normalized text, effective voice, engine, language, speed, num_step,
  guidance_scale, provider base_url). Changing server or voice invalidates the cache without
  touching existing entries. File name = hash, never the text (spec section 18).
- Default directory `user_cache_dir("voxello")/audio`, separate from `tmp/` so the
  `TempStore` sweeper never touches it. The `PlaybackManager` must never delete cache files:
  `_on_playback_outcome` discards only if `temp_store.owns(path)`.
- LRU eviction on file count and age; touch atime on hit (or a JSON index file if atime is
  unreliable on the filesystem).
- Configuration:

  ```yaml
  cache:
    enabled: true
    directory: ~            # default: OS cache dir
    max_entries: 200
    max_age_days: 90
    min_text_chars: 1
    max_text_chars: 300     # short phrases only: long answers do not repeat
  ```

### 2.2 Service integration

- In `VoxelloService.speak`: after validation and before `_synth_lock`, look up the cache;
  on hit skip synthesis (state `GENERATING` → `QUEUED` immediately) and mark the record
  `cached=True`. On miss, after synthesis copy the WAV into the cache if `cache_policy`
  allows it.
- New parameter `cache: bool | None` on `speak` and `notify` (MCP tools and CLI
  `--cache/--no-cache`). Default `None` = use the policy. Default policy: cache enabled for
  `mode="notification"` and for the CLI `notify`; disabled for `mode="verbatim"` unless the
  caller asks for it. The hook passes `--cache` explicitly.
- `SpeechResult` and `get_status` expose `cached` and `cache_hits`; `doctor` shows cache size
  and entry count.
- CLI: `voxello cache list|clear|warm`. `warm` reads a list of phrases (a text file or the
  hook's predefined phrases for a language) and pre-synthesizes them, so the first alert does
  not pay the TTS latency.
- Logging: record hit/miss with the text length, not the text.

### 2.3 Tests

- `tests/test_cache.py`: stable key, changing voice/language yields different keys, eviction,
  a hit does not call `FakeProvider.synthesize` (call counter), a cached file is deleted
  neither at the end of playback nor by the sweeper.
- Test the `notify` path with cache enabled and a failing `FakeProvider`: a hit must work even
  with the TTS unreachable (useful for alerts when the server is down).

## Milestone 3 — Per-request language selection

Goal: choose `it` or `en` per call and get consistent phrases and pronunciation.

Today the language is global only (`tts.voicestudio.language`, default `it`) and is sent to
the server as a pronunciation hint. It is exposed neither by the MCP tools, nor by the CLI, nor
by the hook, which picks the phrase per language but leaves the TTS in Italian even when it
speaks English.

### 3.1 `language` as a request parameter

- `TTSProvider.synthesize(text, voice, language=None)`; `VoiceStudioProvider._payload` uses
  `language or settings.language`.
- `VoxelloService.speak` and `notify` accept `language: str | None`; ISO 639-1 validation
  (`^[a-z]{2}$`) with a new `invalid_language` code in `errors.py`. The record and
  `SpeechResult` report the effective language.
- MCP tools `speak` and `notify`: optional `language` parameter with a description that
  invites the agent to pass the language of the text. Update `INSTRUCTIONS` in `mcp/server.py`.
- CLI: `--language/-l` on `speak` and `notify`; flat alias `VOXELLO_LANGUAGE`.
- Move the default from `tts.voicestudio.language` to `speech.default_language`, keeping the
  old path as an alias with a deprecation warning.

### 3.2 Voice per language

- OmniVoice is multilingual, but some cloned voices sound better in a single language. Add an
  optional map:

  ```yaml
  speech:
    default_language: it
    voices_by_language:
      it: italian_voice
      en: english_voice
  ```

  Precedence: explicit `voice` → `voices_by_language[language]` → global `voice` → server
  default. This fits the per-agent routing of the spec (section 32).

### 3.3 Hook and skill

- The hook passes `--language "$LANG_CODE"` and `--cache`. The predefined phrases move out of
  the bash script into message files (`messages/it.yaml`, `messages/en.yaml`) inside the
  package, so `voxello cache warm --hook-phrases --language it` can pre-generate them and
  adding a language does not require touching the script.
- The `voice-notify` skill instructs to pass a `language` consistent with the message text.

### 3.4 Tests

- The payload contains the request language, not the global one.
- `voices_by_language` respects the precedence.
- Invalid language → `invalid_language`.
- A hook script test (bash, marked `integration`) that checks the arguments passed to a fake
  `voxello` on the PATH.

## Milestone 4 — Quality and robustness

- **End-to-end tests for the `notify` tool**: cover the `partial` result when the desktop
  channel fails and the voice channel works.
- **Provider timeout and retry**: a single retry on connection errors, none on 4xx;
  configurable backoff. Today a slow TTS holds `_synth_lock` for the whole request.
- **More complete `doctor`**: check cache, language, voices available per language, synthesis
  time of a sample phrase.
- **Windows**: manual test of the PowerShell backend and notifier; today only macOS is
  verified with real audio.
- **CI**: GitHub Actions with ruff, pyright, pytest on macOS and Linux; release job tied to
  milestone 1.

## Milestone 5 — From the specification, beyond the MVP

In estimated order of usefulness, all at the same low priority until milestones 1-3 are closed.

- **Per-agent voice routing** (spec 32): `voices.agents.<client_id>`; together with
  `voices_by_language` it becomes a three-level precedence.
- **Webhook channel** in `notify` (spec 33): JSON POST to a configured URL, for Slack or
  internal automations.
- **Alternative providers** (spec 38, phase 4): a second `TTSProvider` (for example macOS
  `say` as an offline fallback when the server is unreachable and the cache has no entry).
  Useful precisely for hook alerts.
- **Streaming TTS** (spec 34): start playback before synthesis ends, if the server supports
  it; reduces perceived latency for uncached phrases.
- **Audio ducking / presence** (spec 34): out of scope until there is demand.
- **STT, hotkeys, remote output**: spec phase 5, not planned.

## Recommended order

| Step | Item | Reason |
|------|------|--------|
| 1 | 3.1 | Small, unblocks correct cache and hook (language becomes part of the cache key) |
| 2 | 2.1 + 2.2 | Reduces server load for repeated alerts |
| 3 | 3.3 | Hook uses language and cache; phrases in message files |
| 4 | 1.1 + 1.2 | Global tool installation, hook independent of the checkout |
| 5 | 3.2, 2.2 `warm`, 1.3 | Polish |
| 6 | Milestone 4 | CI and robustness before publishing |
| 7 | Milestone 5 | On demand |

Items 3.1 and 2.x should be done before publishing (1.1) because they change the MCP tool
signatures and the configuration layout: better to do that before external installations
exist that would need migrating.
