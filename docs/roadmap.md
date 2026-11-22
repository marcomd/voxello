# Voxello roadmap

Status as of 2026-11-21 (version 0.3.0). The MVP described in the specification (phases 1-3, section 38) is
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

## Milestone 2 — Audio cache for static phrases (done 2026-09-17)

Goal: fixed phrases (Claude Code hook, repeated notifications) are synthesized only once.
Before this milestone every call regenerated the WAV, stored it in `TempStore` and deleted it
after `temp_retention_minutes`.

Delivered: `storage/cache.py` (`AudioCache`, SHA-256 key over `CacheKeyParts`, `<hash>.wav` plus
a `<hash>.json` sidecar with voice label, provider and text length, LRU on file mtime touched on
hit, eviction by count and age after every store), `cache:` settings with a validator that keeps
the cache directory distinct from the temp dir, `cache: bool | None` on `speak`/`notify` (service,
MCP tools, CLI `--cache/--no-cache`) with the policy below, `cached` in `SpeechResult` and
`cache_hits` in `get_status`, `doctor` cache line, `voxello cache list|clear|warm` (`warm` reads a
file, stdin or `--hook-phrases --language it|en`), the hook passes `--cache`, and the hook
sentences live in `assets/claude/messages/{it,en}.yaml` (the bash script keeps a copy until 3.3;
a test keeps them equal). Deviations from the text below: the LRU signal is the WAV mtime rather
than atime; the language in the key is still the global `tts.voicestudio.language` until 3.1,
whose per-request value simply replaces that argument of `CacheKeyParts`.

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

## Milestone 3 — Per-request language selection (done 2026-09-17)

Goal: choose `it` or `en` per call and get consistent phrases and pronunciation.

Before this milestone the language was global only (`tts.voicestudio.language`, default `it`)
and was sent to the server as a pronunciation hint. It was exposed neither by the MCP tools, nor
by the CLI, nor by the hook, which picked the phrase per language but left the TTS in Italian
even when it spoke English.

Delivered: `language` on `TTSProvider.synthesize`, `VoxelloService.speak`/`notify`, the MCP
tools (with `INSTRUCTIONS` asking agents to pass it) and the CLI (`--language/-l` on `speak`,
`notify`, `cache warm`); validation `^[a-z]{2}$` after strip/lowercase with the `invalid_language`
code; `language` in `RequestRecord`, `SpeechResult` and `StatusReport`; `speech.default_language`
(alias `VOXELLO_LANGUAGE`) with `tts.voicestudio.language` kept as a deprecated fallback that
logs a warning; `speech.voices_by_language` with the precedence below, resolved in the service so
provider and cache key see the same voice; `doctor` lines for both. Deviation for 3.3: the hook
script does not parse YAML (it has no parser and `install` does not copy the message files);
instead it execs the new `voxello hook notification --language --channels` command, which reads
the Notification JSON from stdin and picks the sentence with `install.hook_text()` from the
package message files, so adding a language is still just adding a YAML file and the script
needs neither `jq` nor `python3`. The sync test was dropped; the bash tests check the arguments
and stdin passed to a fake `voxello` and stay in the default suite (they only need `bash`), not
under `integration` as 3.4 suggested, so CI keeps running them.

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

- The hook passes `--language "$LANG_CODE"` (it already passes `--cache` since milestone 2).
  The message files `assets/claude/messages/{it,en}.yaml` and
  `voxello cache warm --hook-phrases --language it` already exist; what remains is making the
  bash script read them instead of its hardcoded copies, so adding a language does not require
  touching the script, and dropping the sync test in `tests/test_hook.py`.
- The `voice-notify` skill instructs to pass a `language` consistent with the message text.

### 3.4 Tests

- The payload contains the request language, not the global one.
- `voices_by_language` respects the precedence.
- Invalid language → `invalid_language`.
- A hook script test (bash, marked `integration`) that checks the arguments passed to a fake
  `voxello` on the PATH.

## Milestone 4 — Quality and robustness (done 2026-09-18)

Delivered: MCP-level tests for `notify` (`partial`, `failed`, file channel) next to the existing
service-level ones; `tts.voicestudio.connect_timeout_seconds`, `retries` and
`retry_backoff_seconds` with a retry loop in the provider (connection errors and HTTP 502/503/504
only, never 4xx or read timeouts, `attempts` in the error details) and two tests pinning the
`_synth_lock` behaviour (a cache hit never waits); `doctor` groups voices by language, warns
about configured voices missing from the server list and gains `--synth`/`--language` for a timed
sample phrase (its provider is injectable, so the healthy branch is finally tested); `pwsh` as a
playback-only Windows fallback, a `windows-latest` unit-test leg in CI and the manual procedure
in `docs/windows-testing.md`; `release.yml` runs `ci.yml` as a reusable workflow on the tagged
commit before building. Deviations: the Windows item is verified only for command construction
and with fakes in CI; the checklist still has to be executed on a real Windows machine and its
results table filled in. The missing-voice check is a warning rather than a failure because
omnivoice-server accepts ids its list may not expose.

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

## Milestone 6 — Cloned and designed voices (done 2026-11-17)

Goal: create a voice once and reuse it everywhere, and describe a voice in words.

Delivered: `core/voice_design.py` (OmniVoice vocabulary, canonical form, `invalid_instructions`);
`instructions` on `TTSProvider.synthesize`, the service, the MCP `speak`/`notify` tools and the
CLI; `speech.voice_designs` resolved by `resolve_voice` into a `VoiceChoice` (voice or
instructions, never both); `instructions` in `CacheKeyParts`, left out of the hash when unset so
older keys survive; the provider's `/v1/voices/profiles` operations and `core/voices.py`
(`VoiceLibrary`: names, reference audio, 3-10 s warning, cache eviction per voice);
`voxello voice list|clone|update|delete` with a consent prompt; `doctor` lines for designs,
clones and vocabulary drift; an opt-in integration test that clones a server-synthesized
recording. Decisions in `docs/decisions/0001-voice-profiles-on-server.md`: profiles stay on the
server and cloning is CLI-only. Deviations: the cache sidecar needed no `instructions` field
because the voice label already records `design:<attributes>`; VoiceStudio (`instruct`, no
profile API) is not supported.

## Milestone 7 — Agent notifications you can choose (done 2026-11-17)

Goal: pick, for Claude Code and Codex, between cached fixed sentences, live messages and
agent-written summaries, each with its own cloned or designed voice.

Delivered: `integrations.phrases` (`cached` | `live` | `off`, voice or instructions, language,
channels) and `integrations.summaries` (`on_request` | `always`, voice or instructions,
`enforce`, `min_turn_seconds`) in `config.py`; `VoiceRole` and the role step in
`resolve_voice`; MCP calls carry `role="summary"` and are not cached by default;
`build_instructions(settings)` adds the always-summarize paragraph; `install.hook_delivery`
applies the phrase mode; `voxello/hooks.py` and `voxello hook stop` (block decision, Codex
end-of-turn sentence in a detached child, fail open on unreadable transcripts); the hook
script's `stop [agent]` mode; `voxello install claude --stop-hook` and `voxello install codex`;
`doctor` lines; per-project `.voxello.yaml` (allow-listed keys) so each project can have its own
voices. Decisions in `docs/decisions/0002-agent-summaries.md`. Limitations: the Codex
rollout format is not a stable interface, so the "already spoke" check for Codex is tolerant
and fails open; the Codex paths were verified against the documentation and a local
`codex-cli 0.169`, not in a live Codex session.

## Milestone 8 — Multi-character dialogue (in progress)

Goal: turn a text (a page, a chapter) into an audio dialogue with a narrator and one voice per
character, every line reviewed by ear and regenerable alone or in the context of the scene.
Personal use. Design and spike results in `docs/decisions/0003-dialogue-production.md`.

Workflow the user sees, driven by the `dialogue` skill:

```
voxello dialogue init chapter1.txt          # project dir, deterministic segments
  (agent annotates segments in chunks)      # voxello dialogue annotate FILE, dialogue check
  (agent interviews the user on the cast)   # writes cast.yaml; offers voxello voice record
voxello voice record marco --register whisper   # guided recording + consent + clone
voxello dialogue render                     # one take per line, resumable
voxello dialogue review                     # terminal loop: approve, regenerate, edit
voxello dialogue play --from 12             # whole scene, shows the line being played
voxello dialogue export --srt               # final.wav, timeline.json, final.srt
```

A dialogue project is a directory:

```
chapter1/
  source.txt     # copy of the input, never modified
  cast.yaml      # written by the agent with the user: characters, voices, registers, pauses
  script.yaml    # segments: id, kind, text, speaker, register, tts_text, speed, attribution, skip
  state.json     # machine-owned: takes per line and the approved fingerprint
  takes/<id>/<fingerprint[:16]>.wav
  render/        # final.wav, timeline.json, final.srt
```

```yaml
# cast.yaml
language: it
narrator: narratore
attributions: keep          # keep | drop narration segments flagged as "disse Marco"
generation: {num_step: 16, seed: 1}
pauses_ms: {same_speaker: 250, speaker_change: 450, paragraph: 700, break: 1500}
characters:
  narratore: {voice: clone:narratore}
  marco:
    voice: clone:marco                       # main voice, also the fallback for registers
    registers: {whisper: clone:marco-whisper, sad: clone:marco-sad}
  francesca: {voice: clone:francesca}        # registers can be recorded later
```

A character (and a register, written as a bare voice or `{instructions: ...}`) has exactly
one of `voice` (server voice, `clone:<id>` or a `speech.voice_designs` name) or
`instructions`; `speed` per character is optional. `generation.speed: null` leaves speed to
`config.yaml`.

### 8.1 Per-request generation options — done

`tts/base.py`: a `SynthesisOptions` dataclass (`seed`, `speed`, `num_step`, all optional) and an
`options` keyword on `TTSProvider.synthesize`; `tts/voicestudio.py` `_payload` lets them
override the configured values. `FakeProvider` records them.

Acceptance: `speak`/`notify` payloads and cache keys are unchanged; the options reach the
request body only when set. Tests: `tests/test_voicestudio.py` payload cases with
`httpx2.MockTransport`; a regression test that a service call sends no `seed`.

### 8.2 Guided recording: `voxello voice record` — done

`voxello voice record NAME [--register R] [--language it] [--text T] [--device D] [--no-keep]`.
New `recording/` package: a `Recorder` protocol, an ffmpeg backend (avfoundation on macOS,
`pulse`/`alsa` on Linux; Windows raises `recorder_unavailable`), and `prepare_reference()`
(high-pass, trim leading and trailing silence, loudness normalization, 24 kHz mono 16-bit).
Reading texts and tone hints per register and language in
`src/voxello/assets/dialogue/registers/{it,en}.yaml` (registers `neutral`, `whisper`, `sad`,
`angry`, `happy`; custom registers need `--text`). The flow: show tone hint and text, countdown,
record until Enter (bounded at 15 s), clean, check 3-10 s, play back, accept / redo / quit,
then the existing consent prompt and `VoiceLibrary.clone()` of `NAME` (register `neutral`) or
`NAME-REGISTER` with the read text as `ref_text`. `--all` records every built-in register in one session (one
consent, skip per register, only the missing ones on request, summary at the end); the
step-by-step guide is `docs/recording-voices.md`. The cleaned clip is kept under
`user_data_dir/voices/` unless `--no-keep`, for re-cloning on another server. `doctor` reports
the recorder backend.

Acceptance: the command refuses without a TTY; nothing is uploaded without consent; a failed
upload keeps the clip and says how to retry with `voxello voice clone`. New error codes
`recorder_unavailable`, `recording_failed`. Tests: ffmpeg argument lists per OS, the prompt
loop with scripted input and a fake recorder, duration warnings, profile naming; an opt-in
integration test that records nothing but runs `prepare_reference` on a fixture through the
real ffmpeg.

### 8.3 Segmentation and annotation — done

`dialogue/segment.py` (pure): paragraphs split on blank lines; quoted speech in `«»`, `“”`,
`""` and dash dialogue (`—`/`–` at paragraph start, closing dash before an attribution);
narration split at sentence boundaries into segments of at most about 300 characters. Ids are
stable (`p003.2`). `dialogue/script.py`: the `script.yaml` and `cast.yaml` models (Pydantic),
annotation merge, and `check`: the segments rebuild the source modulo whitespace, every quote
has a speaker in the cast, registers and voices resolve, tags are in the allowed list and at
the start of `tts_text` (warning otherwise), `tts_text` is not empty. Commands: `dialogue init`,
`dialogue segments [--from ID] [--count N]` (prints a chunk for the agent), `dialogue annotate
FILE` (partial updates by id, rejects unknown ids and any change to `text`), `dialogue check`,
`dialogue show` (screenplay-like view), `dialogue stats` (lines and characters per speaker,
for the cast interview). New error code `invalid_script`.

Acceptance: the three texts in `tmp/` segment as a person would read them, including
`«...», disse lei, «...»` and a question followed by an attribution. Tests: table-driven
segmenter cases per quote style, the rebuild invariant as a property over the fixtures, merge
and validation errors with ids in the messages.

As built (details settled before implementation):

- Segment kinds `narration`, `speech`, `heading` (a paragraph starting with `#`, read by the
  narrator without the marks) and `break` (`---`, `***`, `* * *`: a `pauses_ms.break` silence).
- A quote opens speech only at a paragraph start, after a sentence end (also after a closing
  quote: `«Bellissimo.» «Vero.»`), after `:`, or after `,` once a quote has closed in the
  paragraph (the inciso); `la chiamava «tesoro»` stays narration. Only the outermost quotes
  count; punctuation right after a closing quote stays with the speech (`«Sono
  piccolissimi»,`); apostrophes are never quotes. An unclosed quote runs to the paragraph end
  with a warning (the multi-paragraph convention).
- Narration and long speech are grouped by whole sentences into the fewest segments of at
  most `max_chars` (300, `init --max-chars`, stored in `script.yaml`), balanced in length; a
  single sentence is never cut (warning from 500 characters). Ids `p003.2`, the paragraph
  number zero-padded to 3 digits (4 past 999 paragraphs).
- Tags are written in `tts_text` only (no separate field): unknown tags are errors, allowed
  tags not at the line start are warnings. A `skip` flag silences a segment (a scene title's
  point-of-view marker, a note). `script.yaml` omits default values; `register` is an alias
  of the Python field `register_`.
- `check` also verifies `source_sha256` and works without `cast.yaml` (structure only), so
  annotation can start before the cast interview. `dialogue segments --pending` lists speech
  without a speaker. Five texts in `tmp/` are the manual acceptance set (all rebuild, no
  warnings).

### 8.4 Render and takes — done

`dialogue/takes.py`: the fingerprint (SHA-256 of tts_text, resolved voice or instructions,
language, speed, num_step, seed), the take store under `takes/` and `state.json` written
atomically. `dialogue render [--lines A-B] [--force]` synthesizes every line without a take
for its current fingerprint, sequentially, with progress on stderr; an interrupted render
resumes. Voice resolution: line register → `characters.X.registers` → `characters.X.voice`;
design names and `instructions` go through `core/voice_design.normalize_instructions`, never
together with a voice. `attributions: drop` skips flagged narration.

Acceptance: a second render with no edits calls the provider zero times; editing one
`tts_text` re-renders one line and clears its approval. Tests with `FakeProvider`, including
cancellation mid-render leaving no partial WAV and a provider error reported with the line id.

As built: the fingerprint also covers `guidance_scale` from `config.yaml` (it changes the
audio but is not a per-request option); the server URL is left out so takes survive a server
move. Values resolve line → character → `cast.generation` → `config.yaml` and are always sent
explicitly. `state.json` is `{version, lines: {id: {approved, takes: [{fingerprint, seed,
duration_ms, created}]}}}`; a take is current when its fingerprint matches the line's present
inputs with the take's own seed, so a take picked in review (another seed) stays current. Takes
are named by the first 16 hex digits of the fingerprint; older takes are kept. Render refuses
a project `check` rejects and saves `state.json` after every line. First real run (example2,
22 lines, omnivoice-server on Apple silicon): 25 s.

### 8.5 Review loop — done

`dialogue review [--from ID] [--pending] [--flagged]` (`dialogue/review.py` holds the pure
state machine, `cli.py` the terminal I/O): shows id, speaker, register, `text`, `tts_text`
when different, take n/m and flags, then plays the take. Keys: Enter approve and next, `r`
new take (next seed, older takes kept), `1`-`9` pick a take, `p` replay, `c` play in context
(previous approved line, this take, next line with the configured pauses), `e` edit
`tts_text` in `$EDITOR`, `+`/`-` speed by 0.05, `g` change register, `n`/`b` move, `q` quit.
Flags (`dialogue/qa.py`, standard library only): duration outside the speaker's usual
characters per second, clipping, leading or trailing silence above a threshold, near-silent
take. Flagged lines can be reviewed first.

Acceptance: every action persists before the next prompt; quitting and restarting resumes
where review stopped. Tests: the state machine with scripted keys, `FakePlayer`, QA functions
on synthetic PCM.

As built (details settled before implementation):

- `dialogue/review.py` is pure: `step(review, view, key)` returns the new queue position and
  effects (`Approve`, `Select`, `NewTake`, `Play`, `PlayContext`, `EditText`, `SetSpeed`,
  `ChooseRegister`, `Notice`, `Quit`); `dialogue/review_session.py` executes them with an
  injected terminal, player and provider; `dialogue/commands.py` owns the terminal (single
  keys in cbreak mode on POSIX, `msvcrt` on Windows, `$VISUAL`/`$EDITOR` for `e`). Any key
  stops the playing take first; the arrows move like `n`/`b`. Ctrl-C keeps the position and
  exits with 130.
- `state.json` stays version 1 with three optional additions: `review.position` (the line
  shown last; `null` after a finished review, which then restarts at the first line not
  approved), `lines.X.selected` (the take picked with `1`-`9` or the new one from `r`; else
  the latest current take is the chosen one) and `takes[].qa`, the take's signal measures,
  computed when the take is stored (by `render` too) and once for older takes. Flags are
  recomputed from them at every review because a speaker's usual rate changes with the takes.
- `r` uses the seed after the highest one among the line's current takes. `e`, `g`, `+`/`-`
  go through `Project.edit_line` (`apply_annotations` plus `check` on that line, so an
  unknown tag is refused instead of blocking the project) and render again with the seed of
  the chosen take; older takes stay on disk and become current again if the edit is undone.
  A line without a current take is rendered when review reaches it, once: after a failure
  only `r` retries.
- `c` joins the previous line (its approved take, else its chosen one), the take and the next
  line with `pauses_ms` (`script.pause_between`: break, paragraph, speaker change, same
  speaker) into one temporary WAV (`dialogue/audio.py`, reused by 8.6); takes in another
  format fall back to the take alone.
- QA thresholds, tuned on example2 (OmniVoice clones lead with up to 580 ms of silence on
  short lines; about 14 characters per second): silence is a 20 ms window under -45 dBFS;
  leading silence over 700 ms, trailing over 900 ms; clipping is 3 consecutive samples at
  32700 or more; near-silent is RMS under -45 dBFS or under 300 ms of sound. The rate counts
  letters and digits of `tts_text` without tags over the duration minus leading and trailing
  silence, at speed 1.0; lines under 20 characters are not judged. The usual rate is the
  speaker's median over at least 5 lines, else the median of all lines, else 14; flags below
  0.7x or above 1.4x. The example2 takes raise no flags.
- `voice clone NAME FILE --prepare [--start S] [--duration D]` came with it, so a narrator
  can be cloned from a recording made elsewhere: the `voice record` cleanup (`FfmpegCleaner`,
  any platform), a window of the file, the clip kept in `storage.recordings_dir`.

### 8.6 Play-through and export

`dialogue play [--from ID]` plays approved takes (or the latest take, marked) line by line with
the configured pauses, printing the current id; Ctrl-C stops and offers to open review at that
line. `dialogue export [-o PATH] [--srt] [--allow-unapproved]` concatenates with silences,
evens loudness per line (RMS gain with a peak ceiling), checks a single sample format, and
writes `final.wav`, `timeline.json` (id, speaker, start, end) and optionally `final.srt`.

Acceptance: export refuses unapproved or stale lines unless asked; the timeline matches the
WAV sample count. Tests: pause and gain arithmetic, concatenation of fixtures of mixed
lengths, SRT formatting, refusal paths.

### 8.7 Skill, install and documentation

`src/voxello/assets/claude/skills/dialogue/SKILL.md`, installed by `voxello install claude`
and `voxello install codex` next to `voice-notify` (mirror under `.claude/skills/` checked by
`tests/test_install.py`). The skill runs `init`, annotates in chunks of about 80 segments
(speaker, register from the cast, allowed tags only at line start, punctuation in `tts_text`
for registers that transfer weakly such as angry or happy, attribution flags), runs `stats`,
interviews the user on the cast (number of characters, the narrator, for each: an existing
clone, a guided recording now — the user runs `voxello voice record` in their own terminal —,
a design as a last resort with the warning that design is unstable outside Chinese and
English), writes `cast.yaml`, runs `check` and `render`, and hands over to `review`, `play`
and `export`. README section, TUTORIAL chapter, CLAUDE.md architecture lines, CHANGELOG.
Licensing note: personal use; OmniVoice weights are CC-BY-NC and voices are cloned only with
the speaker's consent.

## Recommended order

| Step | Item | Reason |
|------|------|--------|
| 1 | 3.1 | Done: language is a per-request argument and part of the cache key |
| 2 | 2.1 + 2.2 | Done: reduces server load for repeated alerts |
| 3 | 3.3 | Done: hook passes language; sentences come from the message files |
| 4 | 1.1 + 1.2 | Done: global tool installation, hook independent of the checkout |
| 5 | 3.2, 1.3 | Done: `voices_by_language`, `warm`, 1.3 |
| 6 | Milestone 4 | Done: retry, `doctor --synth`, Windows CI leg and checklist, gated release |
| 7 | Milestone 6 | Done: cloned voices on the server, voice design by attributes or by name |
| 7b | Milestone 7 | Done: notification modes and a voice per kind of message, Codex hook |
| 8 | 8.1, 8.2 | Done: per-request seed/speed; guided recording, useful on its own for better clones |
| 9 | 8.3, 8.4 | Done: segments, annotation and reproducible takes; a dialogue renders end to end |
| 10 | 8.5 | Done: review by ear with single keys, QA flags, resumable |
| 11 | 8.6, 8.7 | Export and the skill that drives it |
| 12 | Milestone 5 | On demand |

Items 3.1 and 2.x were done before the first PyPI release (1.1) because they change the MCP
tool signatures and the configuration layout: better to do that before external installations
exist that would need migrating. Next: run `docs/windows-testing.md` on a Windows machine, then
milestone 5 on demand.
