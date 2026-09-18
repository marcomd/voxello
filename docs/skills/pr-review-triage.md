# PR Review Triage

Respond to existing review feedback on a Voxello pull request: verify each claim against the
current code, fix confirmed defects, react and explain the outcome, then commit scoped changes.
This is the response side of review; do not expand it into a search for new findings.

Read [CONTRIBUTING.md](../../CONTRIBUTING.md), [AGENTS.md](../../AGENTS.md) and
[CLAUDE.md](../../CLAUDE.md) before making changes. Setup and quality commands are maintained in
[README.md](../../README.md#development). Run commands from the repository root.

Usage: `/pr-review-triage [PR_NUMBER_OR_URL] [--autofix]`.
Invoking this workflow authorizes per-finding GitHub reactions/replies and local commits.
Default to stopping at the local commit; `--autofix` also authorizes pushing review fixes,
waiting for another review and updating lifecycle labels as described in Step 7. Explicit user
limits, such as read-only triage or no commits, take precedence. Editing this skill is not an
invocation of the workflow.

Never use em dashes in text posted to GitHub. Keep replies short and factual.

## Step 1: Resolve the PR and read all feedback

Inspect the branch and working tree before changing anything:

```bash
git branch --show-current
git status --short
git remote -v
gh pr view --json number,url,state,baseRefName,headRefName,headRefOid,headRepository,headRepositoryOwner,isCrossRepository
```

Use the supplied PR number or URL as the argument to `gh pr view` when present. Otherwise resolve
the current branch's PR. If the base repository is ambiguous, resolve it from the user's PR URL
or the configured upstream repository before proceeding; a fork's same-numbered PR is a different
object. Do not select an arbitrary PR from several candidates.

From the returned PR URL, set `PR_URL`, `BASE_HOST`, `BASE_REPO` (`OWNER/REPO`) and `PR_NUMBER`.
Use `--hostname "$BASE_HOST"` for every API request and `--repo "$BASE_HOST/$BASE_REPO"` for
repository-scoped `gh` commands. Verify that the PR is open, targets `main`, and its head repository
and branch match the checkout before editing. Read feedback on closed or mismatched PRs only;
report the mismatch before attempting fixes.

Fetch all three surfaces, with pagination on each:

```bash
gh api --hostname "$BASE_HOST" "repos/$BASE_REPO/pulls/$PR_NUMBER/comments" --paginate \
  --jq '.[] | {id, html_url, path, line, original_line, commit_id, body, user: .user.login, in_reply_to_id, pull_request_review_id}'
gh api --hostname "$BASE_HOST" "repos/$BASE_REPO/issues/$PR_NUMBER/comments" --paginate \
  --jq '.[] | {id, html_url, created_at, body, user: .user.login}'
gh api --hostname "$BASE_HOST" "repos/$BASE_REPO/pulls/$PR_NUMBER/reviews" --paginate \
  --jq '.[] | {id, html_url, state, commit_id, submitted_at, body, user: .user.login}'
gh api --hostname "$BASE_HOST" user --jq .login
```

Read the discussion as well as the original findings. Distinguish the operator's earlier replies
from new feedback; a reply promising a fix is not evidence that the fix shipped. Verify unresolved
older findings against the current tree. On later rounds, use review IDs and `commit_id` to identify
new feedback, while retaining older findings that still need an answer. Conversation comments
have no review/commit binding: inspect their context instead of assigning them to the latest SHA.

Preserve unrelated local changes. Stage only scoped files/hunks and never stash, reset or commit
someone else's work implicitly. `--autofix` requires a clean tree at entry and before each push.

## Step 2: Verify each claim

For each finding, read the named code and its callers, configuration and tests. Describe a concrete
input, observed result and violated contract. Check the suggested remedy independently; a valid
finding can have an incorrect proposed fix.

Use Voxello's actual boundaries when assessing the claim:

- CLI and MCP adapters delegate policy to `VoxelloService` or small pure functions; external
  dependencies use the existing provider, player and notifier protocols.
- Error codes and MCP schemas are public contracts. Spoken text stays exact, stdout is reserved
  for MCP, and ordinary logs must not expose text or secrets.
- Cancellation propagates after owned tasks, subprocesses and files are cleaned up. Terminal
  requests release temporary audio without deleting cached or saved output.
- Configuration precedence, effective voice/language and cache keys must remain consistent.

Mark the finding confirmed, partly confirmed, already fixed, not reproduced, or incorrect, with
evidence. Do not present inability to reproduce as proof of a false positive. Check `main` before
calling a defect pre-existing; report an out-of-scope follow-up candidate without opening an issue
unless the user asked for one.

## Step 3: Fix confirmed defects using TDD

Follow the red-green-refactor policy in `CONTRIBUTING.md`:

1. Characterize the affected behavior and add a focused regression test against a public boundary.
2. Run it without coverage and confirm failure for the intended defect, not a broken fixture.
3. Make the smallest coherent fix, rerun the focused test, then refactor with green tests.

Reuse `FakeProvider`, `FakePlayer`, `FakeNotifier` and isolated settings/storage from
`tests/conftest.py`. Mock HTTP and subprocess boundaries. Use events and bounded waits for new
concurrency tests. Keep real audio, desktop notifications and live TTS opt-in.

For example, a queue regression can be isolated with
`uv run pytest tests/test_queue.py -k interrupt`. Choose the actual affected tests; do not invent
test names or counts. Documentation-only fixes need no artificial regression test.

For code changes, run Ruff lint and format checks, Pyright and the full unit suite with branch
coverage using the README commands. The combined coverage floor is 90%; do not lower it or add
exclusions to conceal gaps. Run the package build when package configuration/assets change.
If checks fail, retain the work and report the failure; do not commit or push it as verified.

## Step 4: React and reply to each finding

After verification and any fix, post the actual outcome and checks. If a reply precedes a fix,
describe it as planned and update the reply when the outcome is known.

```bash
# Inline comment reaction and threaded reply use different paths.
gh api --hostname "$BASE_HOST" -X POST \
  "repos/$BASE_REPO/pulls/comments/COMMENT_ID/reactions" -f content='+1'
gh api --hostname "$BASE_HOST" -X POST \
  "repos/$BASE_REPO/pulls/$PR_NUMBER/comments/COMMENT_ID/replies" -F body=@/tmp/voxello-review-reply.md

# Top-level conversation comment reaction.
gh api --hostname "$BASE_HOST" -X POST \
  "repos/$BASE_REPO/issues/comments/COMMENT_ID/reactions" -f content='+1'
```

Use `+1` for confirmed/partly confirmed findings and `-1` for a disproved claim. Explain uncertainty
without manufacturing agreement or disagreement. For inline replies, use the original root comment
ID. Review summaries have no equivalent REST reaction or threaded-reply endpoint; respond with a
short PR conversation comment linking the review. For conversation findings, likewise post a
short comment linking the source:

```bash
gh pr comment "$PR_NUMBER" --repo "$BASE_HOST/$BASE_REPO" --body-file /tmp/voxello-review-reply.md
```

Write exact Markdown to a temporary file to avoid shell expansion. Read existing replies/reactions
first and avoid duplicates. Correct a mangled reply in place rather than adding another. Do not
resolve a thread or dismiss a review on the reviewer's behalf.

- **Agreed:** state the defect, the fix and the regression test/result.
- **Partly agreed:** identify the valid portion and explain the different remedy or deferred scope.
- **Disagreed/already fixed:** cite the current file/line or commit and the evidence.
- **Deferred/not reproduced:** state what remains uncertain or unfixed and why.

Every finding gets an outcome, including advisory feedback. Keep the overall triage report in the
conversation with the user; GitHub needs the per-finding responses, not a duplicate long report.

## Step 5: Documentation and closeout

Update public docs and `CHANGELOG.md` under `Unreleased` when behavior changes. Record meaningful
architectural tradeoffs under `docs/decisions/` following `CONTRIBUTING.md`. Update stale explanatory
comments alongside code. Keep engineering knowledge in this repository; no external wiki or vault
is required, and a review pass does not need a ceremonial closeout document.

If the delivered scope changes, update the PR description using the five sections of
`.github/pull_request_template.md` and the guidance in [pr-create.md](pr-create.md).

## Step 6: Commit and report

Review the full scoped diff and run `git diff --check`. Stage only the intended changes and create
one coherent commit for the review pass with a descriptive message matching this repository's
history. Voxello does not use numbered phase commits. Preserve authorship and any requested
co-author attribution. Do not amend or rebase pushed history.

If no changes are needed, do not create an empty commit. By default stop here without pushing.
Report the PR URL, decisions, fixes/deferred findings, checks actually run, commit SHA (if any) and
whether it remains local. Distinguish local validation from CI and name untested platforms or
integrations.

## Step 7: Bounded autofix loop (`--autofix` only)

This is an agent-driven workflow using `git` and `gh`; it requires no repository helper scripts or
scheduler. Select the intended reviewer from the existing feedback or the user's instruction.
Do not assume an automatic reviewer is installed, or that it emits a particular verdict string.
If there is no identifiable reviewer or review trigger, finish the local pass and report that
limitation before starting a loop.

### Scope and limits

Handle confirmed P0/P1 findings first. In this optional loop, fix small in-scope P2 findings for at
most two advisory-only code rounds; a round that also fixes blockers does not spend that budget.
Reply to P3 findings without automatically fixing them. These are loop limits, not a repository
merge policy: explicit reviewer change requests, user instructions and required checks still apply.
Unclear severity or impact must be investigated, never silently treated as approval.

Stop after at most five fix-and-push rounds, on failing checks, a failed API/push operation, changed
remote head, cancellation, or a recurrence contradicting a previous fix at the same location.
A different instance of the same defect class is not automatically a recurrence. Report deferred
advisories and out-of-scope defects; do not open follow-up issues automatically.

Keep a local record outside tracked files at the path returned by
`git rev-parse --git-path "pr-autofix-$PR_NUMBER.json"`. Record the PR URL, reviewer, pushed head,
round/advisory counters, source IDs and reply URLs, decisions, fix commits and remaining findings.
Verify saved state against GitHub on resume. A record of a reply must not hide an unfinished fix.

### Before each push

1. Require an open PR, a clean working tree, passing applicable local checks, and the matching
   head repository/branch. Refuse pushes from `main` or a detached HEAD.
2. Inspect the configured upstream and push destination, including `pushRemote`/`push.default`
   overrides. Fetch the PR head remote and verify its live SHA still equals the last observed PR
   `headRefOid` and is an ancestor of local `HEAD`. Stop on missing upstream, divergence or a
   destination mismatch. Push explicitly to the verified head remote and branch, never by force.
3. Inspect every unpushed commit. Push only work within the authorized PR/review scope; do not
   silently include unrelated commits. If nothing needs pushing, do not manufacture a commit.
4. Wait for any known active review to finish before pushing, with the same 20-minute limit
   and bounded waits used below. Remove `status:to-approve` if present; failure to read/remove
   it is an error, not proof of absence.
5. Push, then read the PR back and verify `headRefOid` equals the pushed local SHA. Record that
   SHA before waiting for review. Stop if another writer changes the remote head.

### Wait for evidence on the pushed head

Poll the three feedback surfaces and `gh pr checks` at bounded intervals (at most 60 seconds per
wait), giving progress updates. Limit each review wait to 20 minutes. Existing unresolved feedback
still matters, but a new review only completes this round if it is by the selected reviewer and
its `commit_id` matches the pushed head. Triage intermediate-commit findings against the current
tree without treating them as a verdict on the final head.

PR-body reactions such as `eyes` or `+1` are hints, not evidence tied to a commit. Do not infer that
silence, an old approval, green CI alone or a new thumb-up means the current head was reviewed.
If the reviewer supplies only an unbound reaction, report that the result needs human verification
and stop without marking it ready. Do not post repeated review requests to provoke a signal.

For a matching completed review, handle new findings through Steps 1-6. If that produces a fix,
push the next round within the limits above. Convergence requires a completed review on the current
head, every finding answered, no unresolved blocker or change request, and passing applicable CI.
An empty review body alone is not a clean verdict: read its inline comments and state too.
Pending, cancelled or missing expected checks are not passing; report them accurately.

### Mark the outcome

Use only labels in [.github/issue-labels.json](../../.github/issue-labels.json). The catalog does
not synchronize GitHub automatically. When an authorized label update needs a missing label,
create it with the exact catalog color/description; do not overwrite existing label definitions.

On convergence, re-read the PR and require the working tree to be clean and local `HEAD`, the
live remote head and reviewed SHA to match, with nothing unpushed. A human pause or an unresolved
`status:blocked`/`status:needs-info` condition prevents this handoff. Then apply:

```bash
gh pr edit "$PR_NUMBER" --repo "$BASE_HOST/$BASE_REPO" --add-label 'status:to-approve'
```

Read the PR back to verify its head and labels. If the head moved, remove the now-stale approval
label and report the race. `status:to-approve` means waiting for human approval; it never approves
or merges the PR. Create no additional closeout commit after labelling.

On an abnormal exit, leave `status:to-approve` absent and report the specific reason and outstanding
findings. Use `status:blocked` only for a named dependency/blocker, or `status:needs-info` when specific
information is missing. Preserve unrelated labels and do not clear human-set blockers just to make
the PR appear ready. No merge, review dismissal, force-push or repository-settings change belongs
to this workflow.
