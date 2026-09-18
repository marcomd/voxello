# Developing Voxello

Voxello is a small async Python application. Favor readable code, explicit dependencies and
observable behavior. These instructions apply to maintainers and coding agents alike.

For requirements, installation and usage, see the [README](README.md). Commands for setup,
tests, coverage and quality checks live in [Development](README.md#development); the release
workflow and CI details live in [Releasing](README.md#releasing). Keep operational instructions
there rather than duplicating them here.

## TDD and regression testing

1. List behavior and edge cases: success, invalid inputs, dependency failure, cancellation and
   resource cleanup where applicable. Pick the smallest useful scenario.
2. Write a focused test against a public boundary; verify it fails because the behavior is absent
   or broken, rather than because the test setup is wrong.
3. Implement enough code to satisfy the behavior. Run the focused tests.
4. Refactor names, duplication and responsibilities with green tests, then run the full checks.
   Preserve external contracts unless the change explicitly revises them.

Bug fixes need a reproducing regression test. Existing uncovered behavior can receive
characterization tests that pass immediately; do not present them as test-first development.
Documentation, formatting and other reversible low-impact changes need no artificial tests.
Exploratory prototypes may precede tests, but production behavior must be specified and tested
before completion. This workflow follows the test-first and refactoring cycle described by
[Martin Fowler](https://martinfowler.com/bliki/TestDrivenDevelopment.html).

## Test design and coverage

- Assert observable results, stable errors and resource cleanup. Avoid tests that mirror private
  implementation or assert incidental internal call sequences.
- Reuse the project's fake provider, player and notifier, and isolate files with `tmp_path`.
  Set configuration explicitly when testing precedence; avoid depending on user settings.
- Patch external boundaries: `httpx2.MockTransport`, subprocess creation, platform detection and
  clocks. Parameterize equivalent cases; use a fresh fixture for mutable state.
- In new async tests use `asyncio.Event` and bounded waits. Existing `settle()` is a convenience,
  not proof that all interleavings are safe.
- Keep MCP in-memory round trips and CLI contract tests. Unit tests should not require live TTS,
  real sound or desktop notifications.
- Measure the entire package, including unimported modules, with branch tracing. Inspect module
  gaps and distinguish statement, branch and combined coverage when reporting results. Refer to
  the [README](README.md#development) for the current threshold and reporting commands.
- Prioritize meaningful uncovered paths, especially lifecycle and errors. Review new branches in
  a diff even if the aggregate gate passes. Do not chase 100% with trivial assertions or hide
  gaps with exclusions. Any exclusion needs a specific explanation and review.

The existing `src/` layout and separate tests follow
[pytest's integration guidance](https://docs.pytest.org/en/stable/explanation/goodpractices.html).
See [Coverage.py's branch measurement](https://coverage.readthedocs.io/en/latest/branch.html)
for how coverage is calculated.

## Python and architecture practices

- Keep syntax compatible with the supported Python versions documented in the README, and use
  standard-library facilities where sufficient. Follow the project's formatting and lint rules.
  Prefer simple functions and composition to deep inheritance or speculative frameworks.
  Readability and explicitness follow
  [PEP 20](https://peps.python.org/pep-0020/).
- Type public interfaces and nontrivial helpers. Use `Protocol` for replaceable dependencies,
  dataclasses for internal values and Pydantic at validation/serialization boundaries. Narrow
  untrusted data; localize and justify `Any` and `type: ignore`.
- Keep configuration loading, HTTP clients, OS detection and process creation near composition
  boundaries. Domain decisions should be testable with injected dependencies and pure inputs.
  Extract responsibilities when they change independently; preserve the small modular application.
- Catch specific exceptions and translate infrastructure failures to `VoxelloError` with exception
  chaining at adapter boundaries. Preserve stable error codes. Distinguish best-effort cache
  failures from required output failures; do not silently catch programming errors.
- Give tasks and subprocesses a lifecycle owner. Use `try/finally` or context managers for cleanup;
  propagate `asyncio.CancelledError` afterward. Keep references to background tasks and await them
  on shutdown. Bound external waits and avoid blocking work in the event loop. Follow
  [Python's cancellation guidance](https://docs.python.org/3.12/library/asyncio-task.html#task-cancellation).
- Use argument lists for subprocesses and pass user text as data. Keep secrets and spoken text
  out of ordinary logs, and stdout reserved for MCP. Test these boundaries explicitly.
- Distinguish files owned by a request, service instance and shared cache. Eviction or startup
  cleanup must not remove another active request's input. Review atomicity across processes as
  well as within one event loop.

## Definition of done and review

A behavioral change is ready when its regression/contract tests and required checks pass,
interface changes are documented, and failures/cancellation leave resources in a defined state.
Review privacy, concurrency and dependency direction as well as style. Report checks and which
platforms/integrations were not exercised.

Update the lockfile with dependency changes. Update public documentation and the changelog when
behavior changes; keep setup, commands and release instructions in the README.

For a meaningful architecture decision, add a short document under `docs/decisions/` describing
the problem, options, decision, consequences and validation/migration strategy. Create that directory
with the first decision.

## Pull requests

- Keep each PR focused on a coherent outcome and small enough to review. Separate unrelated
  cleanup; explain any necessary changes to public behavior, dependencies or configuration.
- Target `main` from a descriptive branch. Use `CATEGORY | Short description` for the title,
  with `FIX`, `FEAT`, `REFACTOR`, `TEST`, `DOCS` or `CHORE` describing the primary change.
- Start from the [PR template](.github/pull_request_template.md) and keep its five sections.
  Explain the problem, resulting behavior, motivation, review entry points and validation.
  Remove placeholder comments and keep the body proportional to the change.
- Include checks actually run and their results. Distinguish local validation from CI, identify
  untested integrations/platforms, and include a reproducing regression case for bug fixes.
  Use the commands in the [README](README.md#development) and resolve relevant CI failures before merge.
- Link an issue only with explicit evidence. Use `Closes #N` only when the PR completes its
  remaining scope; use `Related to #N` for partial work. Read the issue before claiming completion.
- Select exactly one `type:*`, one `priority:*` and one to three `area:*` labels from the
  [label catalog](.github/issue-labels.json), based on the delivered change. Supporting tests or
  documentation alone do not justify extra areas. Lifecycle labels must reflect the current state.
- Review the diff before committing, keep private files and generated reports out of commits,
  and write clear commit messages. Preserve authorship and include requested co-author attribution.
- Update the PR description when scope changes. Address review feedback explicitly and verify
  checks on the latest commit; do not treat pending CI as a successful run.

The [PR creation guide](docs/skills/pr-create.md) documents the `gh` workflow, label selection and
post-creation verification. The repository's [Claude skill](.claude/skills/pr-create/SKILL.md)
delegates to that shared guide.

The [PR review triage guide](docs/skills/pr-review-triage.md) covers verifying and responding to
existing feedback, regression fixes and the opt-in bounded autofix loop. The repository's
[triage skill](.claude/skills/pr-review-triage/SKILL.md) delegates to it; `status:to-approve` marks a
completed review cycle waiting for human approval.
