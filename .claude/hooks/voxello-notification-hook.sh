#!/usr/bin/env bash
# Claude Code Notification hook -> Voxello voice + desktop notification.
#
# Claude Code pipes a JSON object on stdin with at least:
#   {"hook_event_name":"Notification","notification_type":"permission_prompt","message":"..."}
# This script turns the notification type into a short spoken sentence and delivers it
# through `voxello notify`, so you hear when Claude Code needs you while you are away
# from the terminal. It never blocks Claude Code (configure it with "async": true).
#
# Environment:
#   VOXELLO_REPO       Voxello checkout used to run the CLI (default: this repo)
#   VOXELLO_HOOK_LANG  "it" (default) or "en" for the spoken sentences
#   VOXELLO_HOOK_CHANNELS  comma-separated channels (default: voice,desktop)
set -u

REPO="${VOXELLO_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
LANG_CODE="${VOXELLO_HOOK_LANG:-it}"
CHANNELS="${VOXELLO_HOOK_CHANNELS:-voice,desktop}"

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

exec uv run --directory "$REPO" voxello notify "$text" \
  --channels "$CHANNELS" --priority high >/dev/null 2>&1
