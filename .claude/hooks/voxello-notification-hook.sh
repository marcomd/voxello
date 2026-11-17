#!/usr/bin/env bash
# Claude Code / Codex hook -> Voxello.
#
#   voxello-notification-hook.sh             Claude Code Notification event (the default)
#   voxello-notification-hook.sh stop        Claude Code Stop event
#   voxello-notification-hook.sh stop codex  Codex Stop event
#
# Claude Code pipes a JSON object on stdin with at least:
#   {"hook_event_name":"Notification","notification_type":"permission_prompt","message":"..."}
# This script only finds the Voxello CLI and hands that JSON to `voxello hook notification`,
# which picks the spoken sentence for the notification type from the message files shipped
# in the package (voxello/assets/claude/messages/<language>.yaml) and delivers it as
# integrations.phrases in Voxello's config.yaml says: cached (the default: synthesized once,
# `voxello cache warm --hook-phrases` pre-fills them), live or off, with the voice configured
# there. Adding a language means adding a message file; this script does not change. The
# Notification hook never blocks Claude Code (configure it with "async": true).
#
# With `stop` it hands the Stop JSON to `voxello hook stop`, which may ask the agent for the
# spoken summary it skipped (integrations.summaries: mode always, enforce true) and, for Codex,
# speaks the end-of-turn sentence. Its stdout is the decision the agent reads, so it is kept;
# register the Stop hook without "async".
#
# How the CLI is found, in order:
#   1. `voxello` on the PATH (for example after `uv tool install voxello`)
#   2. ~/.local/bin/voxello (uv's tool directory, often missing from a hook's PATH)
#   3. `uv run --directory "$VOXELLO_REPO" voxello` when VOXELLO_REPO points to a checkout
#   4. the checkout this script lives in, when run from .claude/hooks/ inside the repo
# When none applies, the script logs to stderr and exits 1 without speaking.
#
# Environment (each one overrides integrations.phrases in config.yaml):
#   VOXELLO_REPO           Voxello checkout, used only when `voxello` is not on the PATH
#   VOXELLO_HOOK_LANG      language of the spoken sentence, e.g. "it" or "en"
#   VOXELLO_HOOK_CHANNELS  comma-separated channels, e.g. "desktop" to silence the voice
set -u

EVENT="${1:-notification}"
AGENT="${2:-claude-code}"

log() { printf 'voxello-notification-hook: %s\n' "$*" >&2; }

is_voxello_checkout() {
  [ -f "$1/pyproject.toml" ] && grep -q '^name = "voxello"' "$1/pyproject.toml" 2>/dev/null
}

# Resolve the command to run as an array in VOXELLO_CMD.
VOXELLO_CMD=()
if command -v voxello >/dev/null 2>&1; then
  VOXELLO_CMD=(voxello)
elif [ -x "${HOME:-}/.local/bin/voxello" ]; then
  VOXELLO_CMD=("$HOME/.local/bin/voxello")
elif [ -n "${VOXELLO_REPO:-}" ] && is_voxello_checkout "$VOXELLO_REPO"; then
  VOXELLO_CMD=(uv run --directory "$VOXELLO_REPO" voxello)
else
  script_repo="$(cd "$(dirname "$0")/../.." 2>/dev/null && pwd)"
  if [ -n "$script_repo" ] && is_voxello_checkout "$script_repo"; then
    VOXELLO_CMD=(uv run --directory "$script_repo" voxello)
  fi
fi

if [ "${#VOXELLO_CMD[@]}" -eq 0 ]; then
  log "voxello not found on PATH; install it with 'uv tool install voxello' or set VOXELLO_REPO to a checkout"
  exit 1
fi

# stdin (the event JSON) flows through to voxello; stderr stays visible in the hook output.
if [ "$EVENT" = "stop" ]; then
  exec "${VOXELLO_CMD[@]}" hook stop --agent "$AGENT"
fi

# Notification: only the overrides that are set are passed, so config.yaml decides the rest.
# stdout is discarded (it is JSON for humans).
ARGS=(hook notification)
[ -n "${VOXELLO_HOOK_LANG:-}" ] && ARGS+=(--language "$VOXELLO_HOOK_LANG")
[ -n "${VOXELLO_HOOK_CHANNELS:-}" ] && ARGS+=(--channels "$VOXELLO_HOOK_CHANNELS")
exec "${VOXELLO_CMD[@]}" "${ARGS[@]}" >/dev/null
