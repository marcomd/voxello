#!/usr/bin/env bash
# Claude Code Notification hook -> Voxello voice + desktop notification.
#
# Claude Code pipes a JSON object on stdin with at least:
#   {"hook_event_name":"Notification","notification_type":"permission_prompt","message":"..."}
# This script only finds the Voxello CLI and hands that JSON to `voxello hook notification`,
# which picks the spoken sentence for the notification type from the message files shipped
# in the package (voxello/assets/claude/messages/<language>.yaml) and delivers it through
# `notify --cache` in that language. The fixed sentences are synthesized once and replayed
# from the audio cache afterwards (`voxello cache warm --hook-phrases` pre-fills them).
# Adding a language means adding a message file; this script does not change. It never
# blocks Claude Code (configure it with "async": true).
#
# How the CLI is found, in order:
#   1. `voxello` on the PATH (for example after `uv tool install voxello`)
#   2. ~/.local/bin/voxello (uv's tool directory, often missing from a hook's PATH)
#   3. `uv run --directory "$VOXELLO_REPO" voxello` when VOXELLO_REPO points to a checkout
#   4. the checkout this script lives in, when run from .claude/hooks/ inside the repo
# When none applies, the script logs to stderr and exits 1 without speaking.
#
# Environment:
#   VOXELLO_REPO           Voxello checkout, used only when `voxello` is not on the PATH
#   VOXELLO_HOOK_LANG      language of the spoken sentence: "it" (default) or "en"
#   VOXELLO_HOOK_CHANNELS  comma-separated channels (default: voice,desktop)
set -u

LANG_CODE="${VOXELLO_HOOK_LANG:-it}"
CHANNELS="${VOXELLO_HOOK_CHANNELS:-voice,desktop}"

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

# stdin (the Notification JSON) flows through to voxello. stdout is discarded (it is JSON for
# humans); stderr stays visible in Claude Code's hook output.
exec "${VOXELLO_CMD[@]}" hook notification --language "$LANG_CODE" --channels "$CHANNELS" >/dev/null
