#!/usr/bin/env bash
# Claude Code Notification hook -> Voxello voice + desktop notification.
#
# Claude Code pipes a JSON object on stdin with at least:
#   {"hook_event_name":"Notification","notification_type":"permission_prompt","message":"..."}
# This script turns the notification type into a short spoken sentence and delivers it
# through `voxello notify`, so you hear when Claude Code needs you while you are away
# from the terminal. It never blocks Claude Code (configure it with "async": true).
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
#   VOXELLO_HOOK_LANG      "it" (default) or "en" for the spoken sentences
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

input="$(cat)"

extract() {
  # $1 = JSON key; prints the string value or nothing. Uses jq when present, else python3.
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r --arg k "$1" '.[$k] // empty' 2>/dev/null
  else
    printf '%s' "$input" | /usr/bin/env python3 -c \
      'import json,sys; d=json.load(sys.stdin); v=d.get(sys.argv[1]); print(v if isinstance(v,str) else "")' "$1" 2>/dev/null
  fi
}

ntype="$(extract notification_type)"
raw_message="$(extract message)"

if [ "$LANG_CODE" = "en" ]; then
  case "$ntype" in
    permission_prompt)  text="Claude Code is asking for permission to continue." ;;
    idle_prompt)        text="Claude Code has finished and is waiting for you." ;;
    agent_needs_input)  text="A Claude agent needs your input." ;;
    agent_completed)    text="A Claude agent has completed its work." ;;
    elicitation_dialog|elicitation_url_dialog) text="Claude Code needs some information from you." ;;
    *)                  text="${raw_message:-Claude Code needs your attention.}" ;;
  esac
else
  case "$ntype" in
    permission_prompt)  text="Claude Code chiede un permesso per continuare." ;;
    idle_prompt)        text="Claude Code ha finito e aspetta una tua risposta." ;;
    agent_needs_input)  text="Un agente Claude ha bisogno di un tuo input." ;;
    agent_completed)    text="Un agente Claude ha completato il lavoro." ;;
    elicitation_dialog|elicitation_url_dialog) text="Claude Code ha bisogno di un'informazione da te." ;;
    *)                  text="${raw_message:-Claude Code richiede la tua attenzione.}" ;;
  esac
fi

# Keep it short: it is spoken.
text="${text:0:250}"

# stdout is discarded (it is JSON for humans); stderr stays visible in Claude Code's hook output.
exec "${VOXELLO_CMD[@]}" notify "$text" --channels "$CHANNELS" --priority high >/dev/null
