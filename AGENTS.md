# Repository engineering instructions

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing code. It is the shared development
policy for humans and coding agents. [CLAUDE.md](CLAUDE.md) describes the current architecture
and product invariants. See the [README](README.md#development) for setup and quality checks.

- For behavior changes and bug fixes, use red–green–refactor: describe the observable behavior,
  run a focused test that fails for the intended reason, implement the smallest coherent change,
  then refactor with passing tests. Characterize existing behavior before changing it.
- Test outcomes and public contracts. Prefer the existing fake provider/player/notifier and
  isolated storage; mock HTTP and subprocess boundaries. Use events and bounded waits for new
  concurrency tests. Keep real audio, desktop notifications and live TTS opt-in.
- Preserve stable error codes, MCP schemas, exact spoken text, stderr-only library logging,
  privacy defaults and separation of temporary, cached and saved audio.
- Keep CLI/MCP adapters thin. Put policy in the service or small pure functions; use existing
  protocols for external dependencies. Add abstractions when a concrete use case needs them.
- Own async tasks, subprocesses and files explicitly. Cancellation must clean up resources and
  propagate; terminal outcomes must release their temporary audio.
- Run Ruff, Pyright and the full unit suite with branch coverage before handing off code changes.
  The combined coverage floor is 90%; do not lower it or add exclusions to hide missing tests.
  For focused tests during TDD, omit coverage so the whole-project floor does not interfere.
- Keep changes reviewable. Record meaningful architectural tradeoffs in `docs/`; update public
  documentation and `CHANGELOG.md` when behavior changes. Report checks and remaining limitations.
