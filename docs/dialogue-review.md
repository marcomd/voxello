# Review a narrated text or dialogue

Run these commands from the checkout with `uv run`; an installed Voxello also works without
that prefix. Rendering needs the configured TTS server. Review needs your own interactive
terminal and an audio player. The QA flags are signal and speaking-rate hints, not a check
that every word was pronounced correctly.

## Let your agent drive it

`voxello install claude` and `voxello install codex` install the `voice-dialogue` skill. Ask
your agent in plain words, for example:

> Aiutami a generare il dialogo in tmp/example2.txt, dimmi cosa devo fare passo per passo.

The agent creates the project, reads the segments in chunks and annotates speakers,
registers, tags and attributions, drafts `cast.yaml` with `voxello dialogue cast`, asks you
about the voices that are missing (an existing clone, a recording now, a design as a last
resort), checks the project and renders it. Then it hands you the commands that need you at
the keyboard: `voxello voice record` for missing voices, `review`, `play` and `export`. In
Claude Code you can run them in the same session with a leading `!`.

At any point, `voxello dialogue status -p DIR` prints where the project stands and the next
command, so you (or a new agent session) can resume without remembering the steps.

## A short test with a cloned narrator

List the available profiles with `uv run voxello voice list`. The example below assumes the
narrator is `clone:narratore`; replace that value with your profile's actual voice ID.

Create a short source in the ignored `tmp/` directory and a new project:

```bash
mkdir -p tmp
cat > tmp/review-smoke.txt <<'EOF'
La luce entrava dalla finestra. Sul tavolo c'era un libro aperto.

Il narratore riprese a leggere, con calma, fino alla fine della pagina.
EOF
uv run voxello dialogue init tmp/review-smoke.txt tmp/review-smoke
cat > tmp/review-smoke/cast.yaml <<'EOF'
version: 1
language: it
narrator: narratore
characters:
  narratore:
    voice: clone:narratore
EOF
uv run voxello dialogue check -p tmp/review-smoke
uv run voxello dialogue render -p tmp/review-smoke
uv run voxello dialogue review -p tmp/review-smoke
```

Use a fresh destination if you already initialized this project. Both paragraphs are narration,
so this test needs only the narrator profile and no speaker annotations.

During review:

1. Press `r` to generate another take, then `1` to select the first one. Older takes remain
   under `takes/`.
2. Press `p` to replay, then `c` to hear the line with its available neighbors and cast pauses.
3. Press Enter to approve and advance. Press `+` or `-` to change the second line's speed;
   the line is rendered again, keeping the chosen seed. `e` opens `$VISUAL` or `$EDITOR`
   to change `tts_text`, while the source `text` remains unchanged.
4. Press `q`, run the same review command, and verify it resumes at the second line with
   the earlier approval and take selection preserved in `state.json`.
5. Approve the second line, then run
   `uv run voxello dialogue review -p tmp/review-smoke --pending`: nothing remains to review.

`--flagged` puts lines with QA warnings first; it still includes the other lines. `--from ID`
starts at a specific segment. Registers (`g`) are available when the speaker has a `registers`
mapping in the cast; see [recording voices](recording-voices.md) to prepare those profiles.
The narrator works the same way on narration and headings: render reads them with its main
voice, and `g` re-renders one line with a register, `0` goes back to the main voice.

```yaml
characters:
  narratore:
    voice: clone:narratore
    registers:
      whisper: clone:narratore-whisper   # voxello voice record narratore --register whisper
```

`voxello dialogue cast` adds these entries by itself when the `narratore-<register>` profiles
exist on the server.

## Change the source during review

Use `sync` to update an initialized project; `init` still creates projects and refuses to
replace existing ones. Finish or quit an active review/render before synchronizing.

```bash
uv run voxello dialogue sync revised-chapter.txt -p tmp/chapter --dry-run
uv run voxello dialogue sync revised-chapter.txt -p tmp/chapter
```

The preview lists old-to-new IDs, new/unmatched and old/unmatched segments, ambiguous
duplicates, whether each line needs annotation/check, render or review, and the recovery
copy location. Apply requires an affirmative answer (`y` or `s`); Enter, EOF or Ctrl-C at
the prompt cancels. `--dry-run` never prompts or writes project files. No changes means no
backup or rewriting. There is no `--yes` option. The existing `max_chars` is retained.

Matching uses exact source text **and segment kind**, first in unique intact paragraphs,
then in unique remaining segments. It does not use fuzzy similarity or old positional IDs
to guess associations. Intact unique paragraphs can preserve repeated phrases within them;
unresolved duplicate paragraphs/phrases, edited text and segment splits/merges receive no
automatic transfer. Even a punctuation change in the source can therefore need new work.

Review pronunciation edits live in `script.yaml` as `tts_text`, separately from source
`text`. Removing final stops or adding a pronunciation accent such as `antracíte` is
preserved exactly for a matched segment, along with speaker, register, speed, attribution,
skip, every take, QA, selection and approval metadata. Whether an approval is usable still
depends on its audio existing and its fingerprint matching current effective inputs with
that take's own seed. Changed cast/configuration does not make stale takes valid.

For unmatched old segments the preview shows custom `tts_text` explicitly as **not
transferred**, and retains it in the recovery copy. New speech may need speaker annotations
before `check`/render succeeds. The old review position follows its matched segment, or
resets to the first pending line when its segment is no longer associated.

If you already edited the internal source, this works too:

```bash
uv run voxello dialogue sync tmp/chapter/source.txt -p tmp/chapter --dry-run
uv run voxello dialogue sync tmp/chapter/source.txt -p tmp/chapter
```

The old logical source comes from `script.yaml`. If the old source hash no longer matches
`source.txt`, sync warns that the exact old source bytes cannot be recovered. The backup
contains the internal source as it stands before sync and the previous script with its
original segments and annotations. A take stores a fingerprint, not a readable historical
copy of `tts_text`: earlier pronunciation variants already absent from the script cannot be
reconstructed from that fingerprint.

Each confirmed change retains `sync-backups/<operation>/before/` containing the previous
`source.txt`, `script.yaml`, `cast.yaml`, `state.json` and entire `takes/` tree when present.
`report.json` records mappings, unmatched old/new segments, custom text and the preview's
line statuses. Files are ordinary local files; you can inspect old annotations/audio there.
Preparation and displaced files may also remain in the operation directory. Nothing is
automatically pruned, and preparation can require several times the original audio size in
free disk space. Existing exports and unrelated project files are preserved; exports must
be regenerated for the new text.

Sync rechecks source, script, cast, state and audio contents after confirmation. If they
changed, run the preview again. CLI commands use an OS lock stored in a persistent sibling
file `.PROJECT.voxello.lock`; the project's parent must be writable. A busy project is
rejected, rather than updated while another command is using it. Direct Python callers
that run concurrently must use the same `project_lock` context.

An error while replacing project files triggers rollback. If persisting the commit marker
fails, recovery uses its recorded phase to restore the old files or retain the complete new
project. If the process dies or rollback fails, a
`.sync-transaction.json` marker blocks ordinary commands (including init) from consuming
an incomplete project. Recover with:

```bash
uv run voxello dialogue sync --recover -p tmp/chapter
```

Recovery previews its action and asks for confirmation. An interrupted apply restores
the previous managed files; a completely committed sync with pending cleanup validates and
keeps the new files. Recovery can be retried if interrupted and never consumes the backup.
Do not delete the marker or backups to bypass recovery. Damaged backups or journals require
manual inspection. The mechanism targets local filesystems and process interruption; it
does not promise recovery from disk corruption or every power-loss/filesystem failure
(directory fsync is unavailable through Python's standard library on Windows).

After sync, run `voxello dialogue status -p DIR`, annotate if needed, render missing lines,
review with `--pending`, and export again. Sync itself calls no TTS server and plays no audio.

## A chapter or an existing sample

Start from a UTF-8 text file, such as a chapter you provide or a local `tmp/example2.txt`:

```bash
uv run voxello dialogue init tmp/example2.txt tmp/example2-review
uv run voxello dialogue segments -p tmp/example2-review
```

Assign each `speech` segment a speaker with `dialogue annotate`, using the actual segment IDs:

```yaml
# annotations.yaml, example only: match the IDs and speakers to your source
p002.1:
  speaker: anna
p003.1:
  speaker: bruno
```

```bash
uv run voxello dialogue annotate annotations.yaml -p tmp/example2-review
uv run voxello dialogue segments -p tmp/example2-review --pending
```

Repeat until no speech lacks a speaker (`voxello dialogue annotate --help` lists every field
and the allowed tags). Then draft the cast from the speakers and the voices on your server:

```bash
uv run voxello dialogue cast -p tmp/example2-review            # prints the draft
uv run voxello dialogue cast -p tmp/example2-review --write    # writes cast.yaml
```

Each character gets `clone:<name>` and every profile named `<name>-<register>` becomes a
register; voices that do not exist yet are marked `TODO` with the `voxello voice record`
command that creates them, so the cast works unchanged once they are recorded. Edit
`cast.yaml` for anything else (another clone, a design, `attributions: drop`). To hear
everything with the narrator alone, assign speech to `narratore` too. Then run `check`,
`show` and `stats` to verify the script, followed by `render` and `review`. Rendering skips current takes and can resume after an
interruption; review can also render missing takes as you reach them.

Each line's audio is kept in the project directory.

## Play the scene and export it

```bash
uv run voxello dialogue play -p tmp/example2-review            # or --from p012.1
uv run voxello dialogue export -p tmp/example2-review --srt
```

`play` plays every line with the pauses of `cast.yaml` and prints the line being played.
Lines not approved yet play their chosen take and are marked `(not approved)`; lines without
a take are skipped. Press Ctrl-C to stop: Voxello offers to open review at that line, and
after review you can run `play --from` the same id.

`export` writes `final.wav`, `final.timeline.json` and, with `--srt`, `final.srt` in the
project directory (`-o book/chapter1.wav` writes `chapter1.wav`, `chapter1.timeline.json`
and `chapter1.srt` there instead). It refuses while a line has no current take (run
`render`) or no approval (run `review --pending`); `--allow-unapproved` exports the chosen
takes anyway. Each line's loudness is brought to the same level, which mostly lifts short
lines such as "chiese Francesca." that the model renders quietly; whispered lines are
lifted too, so pass `--no-level` to keep the takes exactly as rendered. Subtitles show the
source words without quote marks or tags, in blocks of at most two rows.
