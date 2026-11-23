# 0003 — Multi-character dialogue: an agent annotates, Voxello renders deterministically

Status: accepted, 2026-11-21; implemented in roadmap 8.1-8.7 (2026-11-23)

## Context

The user wants to turn a text, from a few lines to a book chapter, into an audio dialogue: a
narrator and one voice per character, each line reviewed by ear and regenerated when it does
not work, alone or in the context of the scene. Use is personal (the OmniVoice weights are
CC-BY-NC and the source texts are usually copyrighted), review happens in the terminal.

A spike on omnivoice-server with Italian text (2026-11-21, `tmp/spike/`, not committed)
settled what the engine can do:

- `seed` is deterministic: the same text, voice, parameters and seed give byte-identical
  audio, and other seeds give other takes. A take is fully described by those values.
- One line takes about 2-2.8 s to synthesize regardless of length; `num_step` 16, 32 and 48
  were rated the same, so 16 is the default here. `speed` 0.85-1.15, punctuation (`?`, `!`,
  `...`) and very short lines ("Nevica?", "Arrivo.") all sound right.
- OmniVoice has no emotion control. Of the 13 non-verbal tags, eight work in Italian at the
  start of a line (`[sigh]`, `[laughter]`, `[confirmation-en]`, `[question-ah]`,
  `[question-oh]`, `[surprise-ah]`, `[surprise-oh]`, `[dissatisfaction-hnn]`); the others
  were rated 1-3, and tags in the middle of a line are less reliable.
- The reference recording carries the delivery: the same speaker cloned from a whispered,
  sad, irritated or cheerful clip keeps that register (whisper clearly, the others less so;
  ratings covered audio quality and how recognizable the emotion was). A neutral clip
  recorded with guidance and cleaned up cloned better than the existing profile.
- omnivoice-server has `POST /v1/audio/script` for multi-speaker audio, but it takes no seed
  and returns one file, so single lines cannot be reviewed or regenerated.

## Options

1. A separate project that calls omnivoice-server directly.
2. Inside Voxello, with an LLM call made by Voxello (attribution, expressiveness).
3. Inside Voxello as an isolated `dialogue` package driven from the CLI; the coding agent
   (Claude Code, Codex) does the language work through a skill and writes files that Voxello
   validates.
4. Like 3, but exposed as MCP tools.

## Decision

Option 3.

- **Reuse.** The provider (retries, stable errors), the cloned-voice profiles with the consent
  prompt, voice design, the players and the configuration already exist; a separate project
  would duplicate them. The code lives in `src/voxello/dialogue/` and depends only on
  `tts/`, `playback/`, `storage/` and `core/voices.py`; nothing in the MCP server or the
  service changes, so it can be extracted later if it grows.
- **No LLM in Voxello** (same reasoning as 0002). The agent reads the text and writes
  annotations; Voxello never rewrites text.
- **Deterministic segmentation.** Voxello splits the source into numbered segments
  (quoted speech in `«»`, `“”`, `""` or dash dialogue, and narration split at sentence
  boundaries). The agent only labels segment ids: speaker, register, tags, speed, whether a
  narration segment is a pure speech attribution ("disse Bruno"). `dialogue check` proves the
  segments rebuild the source, so no sentence can be lost or paraphrased, and long texts can
  be annotated in chunks.
- **The spoken text is explicit.** Each line keeps the source `text` and a `tts_text`
  (defaults to the text without quote marks) where tags and punctuation changes go; the
  difference is shown in review.
- **Registers are recordings.** Expressiveness comes from one cloned profile per character
  and register (`bruno`, `bruno-whisper`, `bruno-sad`, ...), recorded with a guided
  `voxello voice record` command, plus the allowed tags and `speed`. A missing register falls
  back to the character's main voice, so a cast works before every register is recorded.
- **Takes are reproducible.** Audio is stored per line under a fingerprint of everything that
  shapes it (tts_text, resolved voice or instructions, language, speed, num_step, seed);
  approvals point at a fingerprint, so editing a line invalidates its approval by
  construction. Takes live in the dialogue directory, not in the audio cache.
- **Review is a terminal loop**, then a play-through of the whole scene that shows the line
  being played and can jump back to it, then export.
- **Not MCP tools.** Rendering is long and batch, review needs a person at the keyboard, and
  recording needs the microphone. The skill drives the CLI.

## Consequences

- `TTSProvider.synthesize` gains optional per-request generation options (`seed`, `speed`,
  `num_step`). Existing callers pass none, so the cache keys and `speak`/`notify` behaviour do
  not change.
- Recording needs `ffmpeg` (avfoundation on macOS, PulseAudio/ALSA on Linux); Windows
  recording is deferred. `doctor` reports whether it is available. Rendering, review and
  export need only the standard library: all audio is 24 kHz mono 16-bit WAV from one server,
  so concatenation, silence and gain are `wave` and `array` operations.
- Reference recordings are kept locally next to the profiles they created, so a voice can be
  re-cloned on another server; they are a person's voice and are never sent anywhere but the
  configured TTS server.
- The quality of the result depends on the recordings more than on code; the recording
  command therefore guides the reading (text, tone, countdown, cleanup, playback, retry).
- No automatic speech recognition in the first version: review flags suspicious takes with
  cheap signal checks (duration against the speaker's characters per second, clipping, long
  silences). Whisper-based checks can come later as an optional extra.

## Validation and migration

Nothing existing changes behaviour; the new provider options default to absent. The
segmenter, annotation merge, fingerprints, pause and gain arithmetic and the review commands
are pure and unit-tested with the fake provider and player; an opt-in integration test renders
`tests/integration/fixtures/` text against a live server. The three texts in `tmp/` (not
committed) are the manual acceptance set.
