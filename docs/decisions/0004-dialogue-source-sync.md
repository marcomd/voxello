# 0004 — Conservative source sync with explicit recovery

Status: accepted.

## Problem

Dialogue IDs encode paragraph position. Inserting two headings can renumber every line,
while users have already invested in pronunciation edits, seed selection and approval.
The internal source may already have been overwritten, leaving only script.yaml as the old
logical source. Independent atomic file writes cannot coordinate script, state and audio.

## Decision

Add a dedicated CLI-only `sync SOURCE -p DIR`, with a read-only `--dry-run` preview and an
affirmative prompt before applying. Keep init's ordinary creation/refusal contract. Share
UTF-8 input reading and effective LineRequest construction with Project; reuse segmentation,
script/state models, checks and fingerprint rules. No model calls, ASR or new dependencies.

Match unique exact paragraph signatures (ordered kind/text segments), then unique remaining
exact segments. Retain positional identity only when the entire segment sequence is
unchanged. Similarity never authorizes transfer. An edit/split/merge or unresolved duplicate
archives its old data and starts new unassociated segments. This loses some automatic reuse
in exchange for avoiding false assignments. Manual association/merging is deferred.

Copy all annotations, especially tts_text, and complete LineState for associated lines. Audio
files move through a prepared tree, never direct ID-to-ID renames that can collide in swaps.
Preserve the saved review position through the same mapping. Approval metadata remains
subject to the existing current-fingerprint and audio-presence rules. Older audio does not
contain a recoverable text record beyond its fingerprint; this change does not invent one.

Take and state contents, cast, script and internal source are hashed for stale-plan checks.
Keep full managed-file copies under sync-backups/<operation>/before, including orphan audio,
and record mappings/customizations in report.json. Verify backup and candidate contents before
changing active files. Preserve unrelated files and cast in place; mark existing exports as
needing regeneration in the summary.

All existing-project CLI commands use the same nonblocking OS project lock. The persistent
lock inode lives beside the resolved project directory, avoiding changes inside the project
on a preview/refusal. It is never unlinked, avoiding lock-inode races, and the OS releases it
on process death. This requires a writable parent and cooperating callers. Init acquires it
for an already-existing directory and refuses pending sync recovery.

After preparation, persist a journal before any active-file replacement. During application,
retain displaced files too. Normal failures roll back from verified independent copies;
process death leaves a marker that prevents ordinary CLI use of a partial project.
`sync --recover -p DIR` asks before restoring an interrupted apply; a committed transaction
only needs validated cleanup. Recovery copies rather than consumes its source so it remains
repeatable. File fsync and POSIX directory fsync support durability, without a universal
power-loss/storage guarantee. Journal corruption is a visible block, never guessed away.

## Alternatives and consequences

- Reinitializing loses production work. Extending init conflates creation and revision.
- Fuzzy/positional pairing can attach the wrong voice or pronunciation to repeated text.
- A whole-directory swap requires multiple portable renames and disrupts unrelated files.
- A generation-pointer format would require migrating every existing reader and project.
- Per-file atomic replacement alone can expose mixed generations after interruption.

The journal/lock approach keeps current project schemas and paths, with more local disk use
and a small recovery path. Backups are not pruned automatically. Advisory locks do not
coordinate older Voxello versions or editors that bypass them; snapshots detect ordinary
changes before commit, but users must stop other producers and avoid editing during apply.

## Validation

Test insertion, movement, duplication, modifications, splits/merges, custom pronunciation,
multiple takes/seeds, approval/selection/QA, missing audio and changed voice inputs. Check
preview/refusal byte invariance and internal-source edits. Inject backup, staging, apply,
rollback, commit-cleanup and recovery errors; test cross-process exclusion and Windows lock
calls with a fake. Keep existing init and render/review contracts green, then run the full
suite with branch coverage, Ruff and Pyright.
