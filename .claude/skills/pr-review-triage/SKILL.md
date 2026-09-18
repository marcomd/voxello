---
name: pr-review-triage
description: Verify and respond to existing Voxello PR review feedback, fix confirmed defects with TDD, and commit scoped changes. Use when asked to triage, answer, or fix reviewer comments. Pass --autofix to also push and run a bounded review loop.
allowed-tools: Bash, Read, Edit, Write
---

# PR Review Triage

Shared instructions live in `docs/skills/pr-review-triage.md`.

Read that file from the repository root and follow it.

Usage: `/pr-review-triage [PR_NUMBER_OR_URL] [--autofix]`.
Use a supplied PR number or URL instead of resolving one from the current branch.
`--autofix` enables Step 7 (push, wait for review, repeat within the limits).
Without it, stop at the local commit in Step 6. Explicit user limits take precedence.
