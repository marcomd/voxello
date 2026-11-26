---
name: voice-dialogue
description: Turn a text (a scene, a short story, a book chapter) into a multi-voice audio dialogue with Voxello - a narrator plus one cloned voice per character - and guide the user step by step through annotation, cast, rendering, review and export. Use when the user asks to generate, narrate, voice or produce audio from a text file with characters, e.g. "aiutami a generare il dialogo in tmp/example2.txt", "trasforma questo capitolo in un audio a più voci", "fammi l'audiolibro di questo testo", "dimmi cosa devo scrivere passo per passo per il dialogo", "turn this chapter into an audio drama", "voice this scene with different characters".
---

# Multi-voice dialogue with Voxello

You do the language work (who speaks, how, which tag); Voxello does everything else
deterministically from the command line. Voxello never rewrites the text: it splits the source
into numbered segments, you label them by id, and `voxello dialogue check` proves the segments
still rebuild the source word for word.

Talk to the user in their language. Keep them informed with short updates; never paste whole
YAML files or segment lists into the conversation unless they ask.

## Ground rules

- **Commands.** Use `voxello` if it is on the PATH (`voxello --version`); inside the Voxello
  checkout without an installed CLI, prefix every command with `uv run`.
- **Resume first.** If the project directory already exists, run
  `voxello dialogue status -p DIR` and continue from what `Next` says. Run `status` again
  whenever you are unsure what comes next.
- **Source changes.** For an initialized project whose source changed, preview with
  `voxello dialogue sync SOURCE -p DIR --dry-run` (SOURCE may be the already-edited internal
  `source.txt`). Show ambiguous matches and pronunciation customizations that will not
  transfer. Give the user `voxello dialogue sync SOURCE -p DIR` to confirm in their terminal;
  never reinitialize or manually renumber takes. Sync preserves certain matches and archives
  the rest under `sync-backups/`. If interrupted recovery is required, give the user
  `voxello dialogue sync --recover -p DIR`. Then resume from `status` and the new segment IDs.
- **The user's terminal.** Three commands need a person at the keyboard, the microphone or the
  speakers: `voxello dialogue review`, `voxello dialogue play` and `voxello voice record`. Never
  run them yourself. Give the user the exact command; in Claude Code they can type it with a
  leading `!` (for example `! voxello dialogue review -p tmp/ch1`), otherwise in their own shell.
- **Consent.** Voices are cloned only from the CLI, only after the speaker agreed. Never clone a
  voice yourself and never pass `--yes` to `voxello voice clone` for the user.
- **Words are sacred.** Never change, add, drop or reorder words of a line, in `tts_text` or
  anywhere else. You may only add an allowed tag at the start and adjust punctuation.
- **Personal use.** OmniVoice weights are CC-BY-NC and the source text is often copyrighted;
  mention it once if the user talks about publishing the result.

## 1. Create the project

```bash
voxello dialogue init SOURCE.txt [DIR]        # DIR defaults to SOURCE without .txt
```

It prints how many segments and paragraphs it found and any warning (an unclosed quote runs to
the end of its paragraph: tell the user if that looks wrong in the source).

## 2. Annotate the speakers

Read the segments in chunks of about 80 and annotate each chunk before reading the next:

```bash
voxello dialogue segments -p DIR --count 80
voxello dialogue segments -p DIR --from p041.1 --count 80
voxello dialogue annotate --help               # the fields, the allowed tags, an example
```

Each line of `segments` is `id kind [current annotations] text`. Write the annotations for the
chunk and merge them through stdin (all or nothing: on an error nothing changes, fix the reported
ids and send the chunk again):

```bash
voxello dialogue annotate -p DIR - <<'EOF'
p002.1: {speaker: francesca}
p002.2: {attribution: true}
p003.1: {speaker: marco}
p008.1: {speaker: francesca, register: whisper}
p017.1: {speaker: marco, tts_text: "[sigh] L'ultima."}
EOF
```

How to annotate:

- **speaker**: every `speech` segment needs one. Use short lowercase names (`marco`,
  `francesca`, `oste`), the same across chapters of a book, matching the voice profiles the user
  has when possible (`voxello voice list` shows them as `clone:<name>`). Narration and headings
  need no speaker: the narrator reads them. Infer the speaker from the attribution ("disse
  lei"), from turn-taking in an exchange, and from content. When an attribution is genuinely
  ambiguous, ask the user instead of guessing, quoting the line.
- **attribution: true** on narration segments that only say who speaks and how ("disse
  Bruno.", "gli domandò, senza muoversi."). Not on narration that also tells something else.
  The cast decides later whether they are read (`attributions: keep`) or left out (`drop`).
- **register**: only when the text asks for it ("sussurrò", "a bassa voce" for `whisper`;
  clear sadness, anger, joy for `sad`, `angry`, `happy`). Leave it unset for ordinary lines:
  the main voice is the most natural. Whisper transfers well; sad less; angry and happy are
  weak, so for them also strengthen the punctuation in `tts_text` (a `!`) when the text allows.
- **tts_text** with a tag, sparingly and only at the start of the line:
  `[sigh]` (a sigh: tiredness, resignation, relief), `[laughter]`, `[dissatisfaction-hnn]`
  (annoyed "hmm"), `[surprise-ah]`, `[surprise-oh]`, `[question-ah]`, `[question-oh]`,
  `[confirmation-en]` (an assenting "mm-hm"). Use one when the text describes that sound or
  reaction right around the line. No other tag exists; `check` rejects unknown ones.
- **speed** (0.85-1.15) only for a line the text marks as slow or hurried.
- **skip: true** for text that must never be read (a point-of-view marker under a scene title,
  a footnote number).

When every chunk is done:

```bash
voxello dialogue segments -p DIR --pending      # must print nothing
voxello dialogue check -p DIR                   # works before the cast exists
voxello dialogue stats -p DIR                   # lines and characters per speaker
voxello dialogue show -p DIR --count 40         # screenplay view, to spot mistakes
```

## 3. The cast

Draft it from the speakers and the voices on the server:

```bash
voxello dialogue cast -p DIR                    # prints the draft; -l en for an English text
```

Every character gets `clone:<name>`; a profile `<name>-<register>` on the server becomes a
register. Lines on stderr list the missing voices and registers with the command to record them.

Then interview the user, briefly, using the `stats` numbers:

1. Confirm the characters and the narrator's name (`--narrator NAME`, default `narratore` for
   Italian, `narrator` otherwise). Merge duplicates you introduced (re-annotate if needed).
2. For each character without a voice, offer, in this order:
   - an existing clone (another `clone:<id>` from `voxello voice list`);
   - a guided recording now, about two minutes in their own terminal:
     `voxello voice record NAME` (add `--all` to record every register in one session); see
     `docs/recording-voices.md` in the Voxello repository for the steps;
   - a clone from an existing recording of that person, with their consent:
     `voxello voice clone NAME FILE --prepare` (the user runs it and confirms);
   - as a last resort a voice design, `{instructions: "female, young adult, low pitch"}`,
     warning that designed voices are unstable outside Chinese and English.
3. For registers used by your annotation but not recorded, say how many lines fall back to the
   main voice and offer `voxello voice record NAME --register whisper` (or `--all`).
4. Ask whether attributions should be read (`keep`, the default) or dropped.

Write it, then edit `cast.yaml` for the choices that differ from the draft (another clone, a
design, `attributions: drop`, a character `speed`):

```bash
voxello dialogue cast -p DIR --write            # --force replaces an existing cast.yaml
voxello dialogue check -p DIR                   # must say OK
```

If the user records voices after the cast was written, nothing needs editing when the profile
names match (`clone:anna`, `clone:anna-whisper`); for new registers run
`voxello dialogue cast -p DIR` again and copy the new `registers` entries.

## 4. Render

```bash
voxello dialogue render -p DIR
```

About 2-3 seconds per line (tell the user the estimate from the number of lines). It is
resumable: if it stops, run it again and it continues; lines already rendered are not
synthesized twice. A `voice_not_found` error names the line: its voice profile does not exist
yet (step 3).

## 5. Hand over to the user

Review, play and export are the user's part. Give them these commands for their own terminal:

```bash
voxello dialogue review -p DIR
voxello dialogue play -p DIR
voxello dialogue export -p DIR --srt
```

Explain the review keys in one sentence: Enter approves and goes on, `r` makes another take,
`1`-`9` picks a take, `p` replays, `c` plays with the neighbouring lines, `e` edits `tts_text`,
`g` changes register, `+`/`-` change speed, `q` quits (everything is saved; it resumes where it
stopped). `play` reads the whole scene with the pauses; Ctrl-C there offers to review the line.
`export` writes `final.wav`, a timeline and subtitles once every line is approved
(`--allow-unapproved` exports anyway, `--no-level` keeps each take's loudness).

If the user comes back with notes on specific lines ("p012.1 sounds angry, it should be
whispered"), apply them with `voxello dialogue annotate`, run `check` and `render`, and send them
back to `review -p DIR --pending`.
