# Review a narrated text or dialogue

Run these commands from the checkout with `uv run`; an installed Voxello also works without
that prefix. Rendering needs the configured TTS server. Review needs your own interactive
terminal and an audio player. The QA flags are signal and speaking-rate hints, not a check
that every word was pronounced correctly.

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
  speaker: francesca
p003.1:
  speaker: marco
```

```bash
uv run voxello dialogue annotate annotations.yaml -p tmp/example2-review
uv run voxello dialogue segments -p tmp/example2-review --pending
```

Repeat until no speech lacks a speaker. Write `cast.yaml` as above, adding an entry under
`characters` for each speaker and their voice ID. To hear everything with the narrator alone,
assign speech to `narratore` too. Then run `check`, `show` and `stats` to verify the script,
followed by `render` and `review`. Rendering skips current takes and can resume after an
interruption; review can also render missing takes as you reach them.

Each line's audio is kept in the project directory. Whole-chapter playback and export are
planned in roadmap 8.6; this workflow currently produces and approves individual takes.
