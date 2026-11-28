# 0005 — Per-instance ownership of temporary audio

Status: accepted.

## Problem

A long-lived MCP server and one-shot CLI/hook commands share the configured temporary
directory. Up to 0.4.0, startup cleanup deleted every `vox_*` file in it, including audio
another running instance had queued for playback. The retention sweeper had the same blind
spot, and a cache hit handed the player the cache file itself, which `cache clear` or eviction
from any process could delete while it was queued.

## Decision

Each service owns a randomly named directory `voxello-temp-<pid>-<nonce>` under the
configured temporary directory, with an `owner` file holding its PID. Writes, `discard`,
the retention sweep and `dispose` (on shutdown) touch only that directory. A cache hit is
copied into it, so playback never depends on the cache file.

Startup cleanup (`storage.cleanup_on_start`) removes another namespace only when its name
matches the pattern, it is not a symlink, it is older than a short grace period, its owner
file matches the PID in its name and the operating system reports that the PID does not
exist. On POSIX that is `os.kill(pid, 0)` raising `ProcessLookupError`. On Windows
`os.kill(pid, 0)` must never be used (signal 0 is `CTRL_C_EVENT`); the check is
`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`: `ERROR_INVALID_PARAMETER` means the PID
does not exist, and an exit code other than `STILL_ACTIVE` means the process has exited.
A reused PID, an unreadable or missing owner, access denied, an exit code of 259 or any
unexpected error leaves the directory intact. Directories that do not match the pattern are
never touched.

Loose `vox_*` files written directly into the directory by older versions are removed at
startup once their mtime is older than `temp_retention_minutes` plus
`LEGACY_MARGIN_SECONDS` (two minutes). Older versions themselves swept every loose `vox_*`
file past the retention period once a minute, so a file that old is not in use by any of
them; younger ones are left until a later start. Symlinks and directories are never removed.

## Consequences

Instances can no longer delete each other's active audio, and abandoned audio is still
reclaimed after a crash on every platform, provided a later instance starts with
`cleanup_on_start`. An older version that runs with a longer retention than the current
configuration (for example through an environment override) could lose a queued legacy
file; the margin covers the sweep interval only. Namespaces whose PID was reused by an
unrelated process stay until that process exits.
