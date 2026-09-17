# PR Create

Create a GitHub pull request for the current branch using `gh`, assign the repository taxonomy, and
represent any linked issue accurately. Never use em dashes in written content. Prefer regular
dashes, commas, or rewrite the sentence.

## Step 1: Gather information

Run these commands to understand the current state:

```bash
git branch --show-current
git status --short
git log main..HEAD --oneline
git diff --stat main...HEAD
git rev-parse --abbrev-ref @{u} 2>/dev/null || echo "NOT_PUSHED"
```

Read `.github/issue-labels.json` and `CONTRIBUTING.md` before preparing the PR. Inspect the full
diff, including new files. If the user has authorized committing, finish and validate the scoped
changes, create a descriptive branch if currently on `main`, and commit before opening the PR.
Otherwise, explain the uncommitted work and ask before committing it. Do not include unrelated
changes or private/ignored files, and never force-add them. Preserve the user's Git identity and
add any requested agent attribution as a `Co-authored-by` trailer; do not claim a cryptographic
signature unless the commit was actually signed.

Use the quality-check commands in `README.md#development`. Record actual outcomes and distinguish
local checks from GitHub CI results. Hardware and live-TTS tests remain opt-in.

## Step 2: Resolve a linked issue

Treat an issue as linked only when there is an explicit signal: the user supplied its number or URL,
the branch or commit history names it unambiguously, or an existing PR already reports it through
`closingIssuesReferences`. Do not infer a link from a merely similar title.

Read every linked GitHub issue before drafting:

```bash
gh issue view N --json number,title,state,body,labels,url
```

Decide the relationship from the delivered diff, not from the branch name alone:

- Use `Closes #N` when merging this PR will fully satisfy the issue's remaining scope and acceptance
  criteria.
- Use `Related to #N` when the PR is partial, preparatory, exploratory, or only shares context.
- For a non-GitHub task link, include the URL without a GitHub closing keyword.
- If the relationship is genuinely unclear and would determine automatic closure, ask the user.

## Step 3: Select PR labels

Every PR must have exactly one `type:*`, exactly one `priority:*`, and one to three `area:*` labels
from `.github/issue-labels.json`.

If an issue is linked, use its type, priority, and areas as the starting point, then compare them
with the actual diff:

- Keep the issue labels when they still describe the delivered work.
- Replace them with more accurate catalog labels when implementation or investigation changed the
  work. The final diff is authoritative.
- Supporting tests and documentation do not by themselves justify extra types or areas.
- Do not blindly copy `status:*` labels. They describe issue lifecycle and belong on a PR only when
  they accurately describe the PR itself.
- Select areas by the behavior or infrastructure changed: synthesis, playback, notification
  delivery, storage, MCP, CLI, configuration, integrations, testing, CI or documentation.
  Use `area:core` for request orchestration and lifecycle changes, not merely because a file
  lives under `src/voxello/core/`.

If there is no linked issue, classify the PR directly from the catalog. Use `priority:high` only for
a blocker or severe impact, `priority:low` for optional or safely deferrable work, and
`priority:medium` otherwise. This fallback prevents an otherwise complete PR from being left
unclassified.

The catalog is a JSON array of objects with `name`, `color` (six hex digits without `#`) and
`description`, matching GitHub's label fields. Check the repository labels with
`gh label list --limit 100 --json name,color,description`. When PR creation is authorized, create
any missing labels selected for that PR with `gh label create NAME --color COLOR --description
DESCRIPTION`, using the exact catalog values. Do not overwrite or delete existing labels as part
of PR creation. The file alone does not synchronize labels on GitHub.

## Step 4: Build the PR title and body

Describe the final delivered change in the title, using the branch/commits as context. Use this format:

```text
CATEGORY | Short description
```

Use `.github/pull_request_template.md` as the starting point and keep its five headings:

- `## Describe the context`: the concrete problem and any verified issue relationship.
- `## Describe the change`: resulting behavior and the main changes.
- `## Describe why we need the change`: user or engineering benefit.
- `## How to review`: review entry points, relevant tradeoffs and remaining limitations.
- `## How to test`: checks actually run, results, and checks not run. For a bug fix, include
  the reproducing regression case and its outcome here.

Choose `CATEGORY` from `FIX`, `FEAT`, `REFACTOR`, `TEST`, `DOCS` or `CHORE` based on the primary
change. Remove template comments, keep the description proportional to the change, and use
`Not applicable` with a brief reason if a section does not apply. Update the title/body when
scope changes so reviewers see the final implementation rather than conversation history.

Do not invent reproduction steps, test results, or issue coverage that were not supplied or
verified.

## Step 5: Push and create

Confirm the working tree is clean and the branch is not `main`. Check `git diff --check main...HEAD`
and the files included in the commits. If the branch has no upstream, run
`git push -u origin HEAD`. If already pushed, run `git push` only when needed. Do not force-push
as part of this workflow. Check for an existing PR for the branch before creating a duplicate.

Create the PR against `main`, include the selected labels, and do not create a draft unless the user
explicitly asks for one:

Write the exact body to a temporary Markdown file outside the repository and use `--body-file`
to preserve newlines and avoid shell expansion of Markdown or code examples.

```bash
gh pr create \
  --base main \
  --head BRANCH_NAME \
  --title "TITLE_HERE" \
  --body-file /tmp/voxello-pr-body.md \
  --label "type:TYPE,priority:PRIORITY,area:AREA"
```

Do not merge the PR or change repository settings as part of creation.

## Step 6: Verify and report

Read the PR back after creation:

```bash
gh pr view PR_URL --json url,title,body,labels,closingIssuesReferences,baseRefName,headRefName,isDraft,state
```

Verify the base is `main`, the body contains the intended issue relationship, and the label set has
one type, one priority, and one to three areas. Correct a mismatch once, then read it back again.
Stop and report the remaining mismatch instead of repeatedly editing live state.

Check `gh pr checks PR_URL` for CI. Pending checks are not successful checks. Fix failures within
the authorized scope, push the correction and verify again; report unrelated blockers accurately.

Report the PR URL, selected labels, linked issue relationship or the absence of one, and a short
summary of what was included.

## Rules

- Always target `main`.
- Use the PR template exactly as the base structure.
- Do not invent testing steps that were not actually performed.
