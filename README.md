# Voxello

**Give your AI a voice — from spoken updates to podcast-style dialogues.**

![cover.jpeg](docs/cover.jpeg)

Voxello gives your AI agent a voice on your machine. Start with a simple spoken alert, hear a
summary of the work it just did, or produce a complete audio file from a long text. The agent
decides *what* to say; Voxello handles *how* it is delivered.

## Three ways to use Voxello

1. **Reusable voice notifications.** Hear a fixed phrase when your agent finishes or needs
   your attention. Voxello generates the audio once and replays it from a cache on subsequent
   alerts, without asking the speech server to generate it again while that entry is cached.
   [Try a cached phrase](#1-hear-a-cached-phrase).
2. **Spoken summaries of the work just completed.** Your agent writes a short summary of what
   it did, including results or problems. Voxello generates fresh audio and reads it to you at
   the end of the task. [Try a live summary](#2-hear-your-agent-summarize-a-task).
3. **Finished audio from long texts and dialogues.** Voxello renders the text piece by piece,
   lets you review or regenerate individual lines, and exports one complete audio file. Use a
   single narrator or assign different voices to the speakers: the cast has no preset limit
   on its number of characters. Narrate a chapter or listen to a prepared meeting summary
   like a podcast. [Try a dialogue](#3-produce-a-dialogue-with-several-voices).

All three modes can use your own cloned voice, other voice profiles or designed voices
([voice setup](#cloned-and-designed-voices)). Voice cloning and design need omnivoice-server.
For summaries, the agent writes the words; for dialogue, you supply a script or ask your AI
to write one first. Voxello turns that text into audio.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- A TTS server on this machine or on an intranet host: [VoiceStudio](https://github.com/debpalash/VoiceStudio)
  (port 3900, API runs while the app is open) or [omnivoice-server](https://github.com/maemreyo/omnivoice-server)
  (port 8880, headless). New to this? [docs/TUTORIAL.md](docs/TUTORIAL.md) walks through installing
  `omnivoice-server` on Windows with an NVIDIA GPU and on Apple Silicon, starting it and testing it with curl.
- An audio player: `afplay` (macOS, built in), `mpv`/`paplay`/`aplay`/`ffplay` (Linux), PowerShell
  (Windows: `powershell` or `pwsh`; implemented and unit-tested but not yet verified with real
  audio, see [docs/windows-testing.md](docs/windows-testing.md))

## Install

Install Voxello once as a [uv tool](https://docs.astral.sh/uv/guides/tools/); the `voxello` command
then works from any directory and any project:

```bash
uv tool install voxello
voxello doctor
```

Alternatives:

```bash
uvx voxello speak "Hello"                                    # run without installing
uv tool install git+https://github.com/marcomd/voxello       # latest main instead of the PyPI release
uv tool upgrade voxello                                      # later
```

To work on Voxello itself, clone it and use `uv run` inside the checkout (or
`uv run --directory /path/to/voxello voxello ...` from elsewhere):

```bash
git clone https://github.com/marcomd/voxello && cd voxello
uv sync --all-groups
uv run voxello doctor
```

The rest of this README uses the installed `voxello` command; in a checkout prefix `uv run`.

## Configure

```bash
voxello config init        # writes ~/Library/Application Support/voxello/config.yaml (macOS)
voxello config path        # shows where the file lives on this OS
```

Minimal setup with `omnivoice-server` on this machine:

```yaml
tts:
  voicestudio:
    base_url: http://localhost:8880
    # voice: alloy          # omit for the server default; presets: alloy, ash, ballad, cedar, coral, echo, fable, marin, nova, onyx, sage, shimmer, verse
speech:
  default_language: it      # used when a request does not pass `language`
```

If the server runs on another machine, replace `localhost` with its intranet address, for
example `192.168.1.144`. Then check the connection and your audio player:

```bash
voxello doctor
```

## Try the three modes

These examples use English audio. Start with Voxello installed, the TTS server running and
the configuration above. Run terminal commands yourself; in a checkout, prefix `voxello`
with `uv run`.

### 1. Hear a cached phrase

No AI agent is needed for this first test. With the default cache settings, run the same
command twice:

```bash
voxello speak "Your agent needs your attention." --cache -l en
voxello speak "Your agent needs your attention." --cache -l en
voxello cache list
```

**What to expect:** you hear the same phrase twice. The first command reports `"cached": false`
and the second `"cached": true` in its JSON output (if the phrase was already cached, both
report `true`). The second call reuses the audio instead of synthesizing it again;
`cache list` shows the stored entry. Changing the text, voice or synthesis settings selects
a different cache entry.

This is the same audio cache used by static agent notifications. To hear those phrases when
your agent finishes or needs you, follow
[Voice notifications for Claude Code and Codex](#voice-notifications-for-claude-code-and-codex).
You can pre-generate the built-in phrases with `voxello cache warm --hook-phrases -l en`.

**Try your own voice:** [clone a short recording](#cloned-and-designed-voices), then add
`--voice clone:my-voice` to both `speak` commands, replacing `my-voice` with your profile name.
The first call generates that phrase in your voice; the next reuses it.

### 2. Hear your agent summarize a task

This test needs an AI client with [Voxello registered](#register-with-your-agent). Open a
project containing a README and ask the agent:

> Read README.md and identify what this project does and how to run it. Do not change any
> files. When you finish, use Voxello to read me a two-sentence summary of what you learned,
> in English.

**What to expect:** the agent reads the file, answers in text and delivers a spoken summary
about that project's actual contents. The summary is written by the agent and synthesized
for this task, rather than replaying a fixed "work completed" phrase. The same approach works
after running tests, fixing a bug or investigating a failure: ask for the result by voice.

To receive a spoken summary at the end of tasks without asking each time, see
[notification setup](#set-it-up) and the `summaries.mode: always` option.

### 3. Produce a dialogue with several voices

This test needs an agent with the `voice-dialogue` skill. Install it for your client:

```bash
voxello install claude    # for Claude Code
# or:
voxello install codex    # for Codex
```

Run one of these commands for your client and follow its installation prompts. Create a new
working folder, then save this sample as `dialogue-demo.txt` inside it:

```text
The team has finished a short planning meeting. Anna and Bruno review the outcome.

"What did we decide today?" asked Anna.

"We will release the small update first," replied Bruno.

"What is included?" asked Anna.

"The new search page and the keyboard shortcuts," said Bruno.

"Who will test them?" asked Anna.

"I will run the checks tomorrow," replied Bruno.

"And I will update the guide," said Anna.

"Then we can review the results on Friday," said Bruno.

"What is still open?" asked Anna.

"The release date. We will choose it after the review," replied Bruno.

The next steps are clear. The release date remains undecided.
```

Open your agent in that folder and ask:

> Use voice-dialogue to produce English audio from dialogue-demo.txt, with a narrator and
> distinct voices for Anna and Bruno. Create the project in dialogue-demo. Help me choose
> three available voice profiles; if any are missing, guide me through preparing them.
> Preserve the script's words, render the audio and give me the commands to review and export it.

The skill prepares the speakers and voices with you, then generates the audio line by line.
Voice profiles must exist on your TTS server; use `voxello voice list` to see them. The agent
can help you select profiles or explain how to record your own in your terminal
([voice recording guide](docs/recording-voices.md)).

When rendering is complete, run these commands in your own terminal:

```bash
voxello dialogue review -p dialogue-demo
voxello dialogue play -p dialogue-demo
voxello dialogue export -p dialogue-demo --srt
```

**What to expect:** during review, you hear each line in its assigned voice. Press Enter to
approve and continue, `p` to replay, or `r` to generate another take of just that line. You can
quit with `q` and resume later. Approve every line before exporting. `play` lets you hear the
whole scene with pauses; export creates `dialogue-demo/final.wav`, a timeline and subtitles.
The WAV is the complete audio, ready to listen to later.

The same workflow handles longer texts, a single narrator or a larger cast, with no preset
cap on the number of characters. For a meeting recap, ask your AI to write a summary dialogue
from the transcript first, then give that script to `voice-dialogue`. See
[dialogue details and future ideas](#review-a-narrated-text-or-dialogue).

## Configuration reference

With VoiceStudio on another host:

```yaml
tts:
  voicestudio:
    base_url: http://voicestudio.lan:3900
    api_key: "the key configured as OMNIVOICE_API_KEY on the VoiceStudio host"
    engine: omnivoice       # or voxcpm2, cosyvoice, mlx-audio, kittentts, moss-tts-nano
    # voice: <profile id>
speech:
  default_language: it
```

Leave `voice` unset to use the server's default: VoiceStudio and omnivoice-server disagree on
its name (`default` vs `auto`) and each rejects the other's.

A synthesis request waits `connect_timeout_seconds` (5) to reach the server and
`timeout_seconds` (120) for the audio. When the connection fails or the server answers HTTP
502/503/504 (typically while the model is still loading) the request is retried `retries` times
(1) after `retry_backoff_seconds` (0.5, doubled at every further retry). A 4xx answer or a read
timeout is never retried. All four live under `tts.voicestudio`.

The language is chosen per request: agents pass `language` (ISO 639-1, `it`, `en`, ...) with the
text, the CLI takes `--language/-l`, and `speech.default_language` covers calls that pass none.
Cloned voices often sound best in one language, so an optional map picks the voice from the
language when the request names none (an explicit `voice` still wins, then the map, then the
global `voice`, then the server default):

```yaml
speech:
  default_language: it
  voices_by_language:
    it: italian_voice
    en: english_voice
```

`tts.voicestudio.language`, the global setting of earlier versions, is deprecated: it still acts
as the default and `voxello doctor` reminds you to move it to `speech.default_language`.

### Cloned and designed voices

OmniVoice can also clone a voice from a short recording or design one from a few attributes.
Both need omnivoice-server; VoiceStudio exposes neither through this API.

**Cloned voices** are profiles stored on the TTS server, so every client of that server shares
them. Record 3-10 seconds of clear speech in the language you will use, then:

```bash
voxello voice clone marco ~/marco.wav --ref-text "The words spoken in the recording."
voxello speak "Ciao, sono la voce clonata." --voice clone:marco
voxello voice list                              # cloned voices on the server, designs in the config
voxello voice update marco --audio ~/better.wav # or --ref-text "..."
voxello voice delete marco
```

A recording you already have (a phone memo, a longer reading, any format ffmpeg reads) can be
cleaned first, as `voxello voice record` does, and cut to a 3-10 s window; the clean clip is
kept in `storage.recordings_dir`:

```bash
voxello voice clone narratore ~/lettura.m4a --prepare --start 12.5 --duration 8 \
  --ref-text "The words spoken in those eight seconds."
```

Without `--ref-text` the server transcribes the recording with Whisper. `clone` asks you to
confirm you have the speaker's permission (`--yes` skips the prompt): only clone voices whose
owner agreed, and never treat a voice as proof of identity. Cloning is CLI-only; agents can use
a cloned voice but cannot create one. Updating or deleting a voice through Voxello also drops the
phrases cached with it; if you change profiles directly on the server, run `voxello cache clear`.

**Guided recording.** `voxello voice record` records the clip for you and clones it: no
recorder app, no transcript. Run it in your own terminal (macOS or Linux, needs `ffmpeg`):

```bash
voxello voice record francesca --all      # every register in one session
voxello voice record francesca            # just the main (neutral) voice
```

OmniVoice copies the tone of the clip it was cloned from, so each way of speaking
(**register**: `neutral`, `whisper`, `sad`, `angry`, `happy`) becomes a voice of its own,
`clone:francesca`, `clone:francesca-whisper` and so on. The command shows the sentence to read
and how to read it, records until you press Enter, cleans up and plays back the clip, and lets
you accept, redo or skip each one. **Step by step: [docs/recording-voices.md](docs/recording-voices.md).**

**Voice design** describes a voice instead of picking one, with at most one attribute per
category: gender (`male`, `female`), age (`child`, `teenager`, `young adult`, `middle-aged`,
`elderly`), pitch (`very low pitch` ... `very high pitch`), style (`whisper`) and, for English
text only, an accent (`american`, `british`, `australian`, `canadian`, `indian`, `chinese`,
`korean`, `japanese`, `portuguese`, `russian accent`). The model was trained on English and
Chinese, so designs can sound unstable in other languages such as Italian.

```bash
voxello speak "The build is green." -l en --instructions "female, young adult, high pitch"
```

Name the designs you reuse and use the name wherever a voice is accepted (`--voice`, the
`voice` tool parameter, `voices_by_language`, `tts.voicestudio.voice`):

```yaml
speech:
  voice_designs:
    narrator: "female, young adult, high pitch"
    whisperer: "male, elderly, whisper"
  voices_by_language:
    it: clone:marco
    en: narrator
```

A configured name takes precedence over a server voice of the same name. A request passes
either `voice` or `instructions`, never both; explicit `instructions` ignore the configured
voices. `voxello doctor` lists the designs and cloned voices and warns when the server's design
vocabulary differs from Voxello's.

Phrases that repeat, such as the Claude Code hook alerts, are synthesized once and replayed from
an on-disk audio cache. The hooks and `voxello notify` cache by default; text an agent sends
through the MCP tools, and `speak`, only on request (`cache: true`, `--cache`); only phrases up
to `max_text_chars` qualify:

```yaml
cache:
  enabled: true
  # directory: ~/.cache/voxello/audio   # default: OS cache dir
  max_entries: 200
  max_age_days: 90
  max_text_chars: 300
```

Environment variables override the file: `VOXELLO_VOICESTUDIO_URL`, `VOXELLO_VOICESTUDIO_API_KEY`,
`VOXELLO_DEFAULT_VOICE`, `VOXELLO_LANGUAGE`, `VOXELLO_LOG_LEVEL`, or any nested key as
`VOXELLO_TTS__VOICESTUDIO__ENGINE`.
`VOXELLO_CONFIG` points to an alternative config file. See `config.example.yaml` for every option.
`voxello doctor` prints which config file it read and where it came from (`--config`,
`VOXELLO_CONFIG` or the OS default), plus which executable and Python interpreter are running.

## More CLI examples

```bash
voxello doctor                                  # provider reachable? voices per language? player? notifier?
voxello doctor --synth -l en                    # also synthesize a sample phrase and time it
voxello speak "Build completata. Tutti i test passano."
voxello speak "Build finished. All tests pass." --language en   # -l en: pronunciation follows the text
voxello speak "Salvami" --save                  # also writes ~/Voxello/<timestamp>_<id>.wav
voxello notify "Refactoring completato." --channels voice,desktop
voxello notify "Build completata." --no-cache   # notify caches by default; speak needs --cache
voxello cache warm --hook-phrases --language it # pre-synthesize the Claude Code hook sentences
voxello cache warm --hook-phrases -l en         # the English ones, synthesized as English
voxello cache warm phrases.txt                  # one phrase per line ('-' reads stdin)
voxello cache list                              # hash, size, length, voice, last use; never the text
voxello cache clear
voxello speak "Done." -l en -i "male, elderly, whisper"   # voice design (omnivoice-server)
voxello speak "Fatto." --voice clone:marco      # a voice cloned with `voxello voice clone`
```

## Review a narrated text or dialogue

Dialogue extends Voxello's voice beyond short updates: turn a prepared script into audio with
distinct voices and pauses, and listen to it like a podcast. For example, have your AI turn a
meeting transcript into a structured conversation about the key points, decisions and next
steps, then listen to that summary when you have time. A narrated story or chapter can use the
same workflow, with a narrator and character voices. A project can also use a single narrator;
there is no preset limit on the number of characters in the cast. Start with the
[complete local example](#3-produce-a-dialogue-with-several-voices) above.

### Prepare the script

Today, you prepare the dialogue yourself or ask your AI to write it first. For example:

> Read meeting-transcript.txt and write a concise conversation between two hosts covering the
> main topics, decisions and next steps. Stay faithful to the transcript and save the dialogue
> in meeting-summary.txt.

Then ask the agent to produce the audio:

> Use voice-dialogue to turn meeting-summary.txt into audio with a different voice for each host.

The `voice-dialogue` skill (installed by `voxello install claude` or `voxello install codex`)
starts from an existing UTF-8 text. It annotates who speaks, helps you choose the voices, renders
the lines and hands you the review, play and export commands. The skill preserves the script's
words; summarizing sources and writing a new conversation are a separate step you request from
your AI.

### Produce, review and export

`voxello dialogue` splits the script into lines and renders a take for each voice in
`cast.yaml`. `voxello dialogue cast` drafts that file from the annotated speakers and your
cloned voices, and `voxello dialogue status` tells you the next command at any point.

Review the audio in your own terminal. Approve with Enter, regenerate with
`r`, pick a take with `1`-`9`, replay with `p`, listen in context with `c`, edit with `e`,
change register with `g`, or adjust speed with `+`/`-`. Quit with `q` and resume later;
approvals, take selections and the review position are saved after each action. The project
directory keeps the source text, audio takes and exported files so you can return to them.

To change the source during production, preview with
`voxello dialogue sync SOURCE -p DIR --dry-run`, then run the same command without
`--dry-run` and confirm its plan. Unambiguous unchanged paragraphs/segments keep pronunciation
corrections (`tts_text`), takes, selections and valid approvals even when their IDs change.
Changed or ambiguous text starts without transferred annotations/audio; the previous work is
retained under `DIR/sync-backups/`, with custom pronunciation losses called out before confirmation.
`SOURCE` may be `DIR/source.txt` even if you already edited it. See
[source synchronization and recovery](docs/dialogue-review.md#change-the-source-during-review).

When every line is approved, `voxello dialogue play` plays the whole scene with the cast's
pauses (Ctrl-C jumps into review at the line you stopped on) and `voxello dialogue export`
writes `final.wav` with evened loudness, a timeline and, with `--srt`, subtitles.

See [the review walkthrough](docs/dialogue-review.md) for a short test with a cloned narrator
and the workflow for a chapter or an existing sample file.

### Future exploration

We are exploring a workflow in which the agent reads meeting transcripts or other sources,
suggests topics and writes the dialogue before handing it to `voice-dialogue`. Ideas include a
target listening duration, a choice of topics and an editorial approach: an explanatory
overview, a debate, a critique looking for weaknesses, or a discussion highlighting strengths.
Source references and script review would help keep the result faithful to the material.
These are ideas for future development, with no scheduled implementation; see the
[roadmap discussion points](docs/roadmap.md#future-exploration--from-sources-to-podcast-style-dialogues).

## Register with your agent

**Claude Code** (user scope, available in every project):

```bash
claude mcp add --scope user --transport stdio voxello -- voxello serve
```

Check the connection with `claude mcp get voxello`; it should report `Connected`. A user-scoped
entry is preferable to a project `.mcp.json` because Claude Code warns when the same server is
defined in two scopes with different commands.

**Codex CLI** (`~/.codex/config.toml`):

```toml
[mcp_servers.voxello]
command = "voxello"
args = ["serve"]
```

**Cursor** (`~/.cursor/mcp.json`):

```json
{ "mcpServers": { "voxello": { "command": "voxello", "args": ["serve"] } } }
```

If the agent's environment does not see `~/.local/bin`, use the absolute path printed by
`voxello doctor` (`Executable:`). From a checkout without a tool install, use
`uv run --directory /path/to/voxello voxello serve` as the command instead.

Then ask the agent, for example: *"Refactor the auth module, run the tests, and tell me by
voice when you are done."* To get alerts and summaries without asking every time, see
[Voice notifications for Claude Code and Codex](#voice-notifications-for-claude-code-and-codex).

## Voice notifications for Claude Code and Codex

Voxello can tell you, out loud, when an agent needs you or has finished. There are two kinds of
message, and you choose each one's mode and voice:

| You hear | When | Who writes it | Cached |
|----------|------|---------------|--------|
| **Phrase**: "Claude Code chiede un permesso per continuare." | Claude Code waits on a permission prompt or goes idle; a Codex turn ends | Voxello, from fixed sentences | yes (`mode: cached`) |
| **Summary**: "Refactoring completato, tutti i test passano. I dettagli sono nella risposta." | The agent finishes, fails or gets blocked | The agent itself, through the `notify` tool | no |

Phrases are deterministic: a hook fires them, whatever the model does. Summaries carry the
outcome, but they depend on the agent making the call; Voxello never writes or shortens them.

### Set it up

1. **Register the MCP server** with your agent (see [Register with your agent](#register-with-your-agent)).
2. **Install the skill and the hooks:**

   ```bash
   voxello install claude --stop-hook   # Claude Code: skill + Notification hook (+ Stop hook)
   voxello install codex                # Codex: skill + Stop hook
   ```

   Each command copies the files and shows the JSON entry it needs (`~/.claude/settings.json`
   or `~/.codex/hooks.json`); it writes that file only after you confirm, or with `--yes`.
   Running it again is harmless. `config.toml` of Codex is never edited.
3. **Choose what you want to hear** in Voxello's `config.yaml` (`voxello config path`):

   ```yaml
   integrations:
     phrases:
       mode: cached            # cached | live | off
     summaries:
       mode: always            # on_request | always
       enforce: true           # needs the Stop hook from step 2
   ```

4. **Pre-synthesize the phrases** so the first alert is instant, and check everything:

   ```bash
   voxello cache warm --hook-phrases
   voxello doctor              # "Agent integrations" and "Project config" lines
   ```

Restart the agent after changing `summaries`: the MCP server reads it when it starts.

### Modes

`integrations.phrases.mode`:

- `cached` (default): the sentence for the event from the message files shipped with Voxello
  (`it`, `en`), synthesized once and replayed from the audio cache, even while the TTS server is
  down.
- `live`: the event's own text, for example "Claude needs your permission to use Bash",
  synthesized every time and never cached. Claude Code writes it in English, so it is spoken
  in English.
- `off`: no voice. The desktop notification still appears if `desktop` is among the channels.

`integrations.summaries.mode`:

- `on_request` (default): the agent speaks only when you ask, for example "avvisami a voce
  quando hai finito" or "tell me out loud when you're done" (the `voice-notify` skill).
- `always`: the server instructions ask the agent to end every task that took more than a few
  seconds with a spoken summary. This works with any MCP client.
- `enforce: true` (with `always`): when a turn of at least `min_turn_seconds` (60) ends without
  a summary, the Stop hook asks the agent once to deliver it before stopping.

With Codex, which has no event for "waiting for you", the Stop hook says "Codex ha finito il
lavoro." at the end of a turn without a summary (a phrase: it follows `phrases.mode` and voice).

### Voices

Each kind of message takes its own voice, so you can tell an alert from a summary by ear:

```yaml
speech:
  voice_designs:
    narrator: "female, young adult"
integrations:
  phrases:
    voice: clone:marco          # a cloned voice (`voxello voice clone marco FILE`)
  summaries:
    voice: narrator             # a named design, or:
    # instructions: "male, middle-aged, low pitch"
```

`voice` takes a server voice, `clone:<name>` or a name from `speech.voice_designs`;
`instructions` takes the attributes directly. Set one of the two, never both. The voice of a
message is chosen in this order:

1. `voice` or `instructions` passed in the request (only if you ask the agent for a voice);
2. `integrations.phrases` or `integrations.summaries`, depending on the kind of message;
3. `speech.voices_by_language` for the message's language;
4. `tts.voicestudio.voice`;
5. the server's default voice.

A new phrase voice means new audio: run `voxello cache warm --hook-phrases` again.

### A different voice in each project

Put a `.voxello.yaml` at the root of a project. It is layered over your `config.yaml` for
everything that runs in that project: the hooks, the agent's summaries, and the CLI started
there. For example, to hear your own voice for one project and a voice of the opposite sex for
another, and know which agent is talking before you read a word:

```yaml
# ~/code/alfa/.voxello.yaml
integrations:
  phrases:   { voice: clone:marco }
  summaries: { voice: clone:marco }
notifications:
  title: Alfa                   # title of the desktop notification
```

```yaml
# ~/code/beta/.voxello.yaml
integrations:
  phrases:   { instructions: "female, young adult" }
  summaries: { instructions: "female, young adult" }
notifications:
  title: Beta
```

Then, in each project, `voxello cache warm --hook-phrases` and `voxello doctor`, which prints
the project file in use.

- **Where it is looked for.** In the project directory and its parents, up to the repository
  root (the directory with `.git`), never in your home directory. The hooks start from the
  `cwd` of the event, the MCP server from `$CLAUDE_PROJECT_DIR` (set by Claude Code) or its
  working directory. `VOXELLO_PROJECT_DIR` overrides all of them.
- **What it may set.** `integrations`, `speech.default_language`, `speech.voices_by_language`,
  `speech.voice_designs` and `notifications.title`. Anything else, such as `tts.*` or
  `logging.*`, is ignored with a warning, so a cloned repository cannot send your spoken text
  to another TTS server or turn on text logging.
- **What wins.** Environment variables, then `.voxello.yaml`, then `config.yaml`, then the
  defaults. A `voice` or `instructions` in the project replaces both of them from
  `config.yaml` for that kind of message; other keys are merged.
- **Commit it or not.** A file that names your cloned voice is personal: add `.voxello.yaml`
  to `.gitignore` (or to `.git/info/exclude`) unless the team shares the voices.
- **Codex.** Its hooks pass the project's `cwd`, so phrases always follow the project. Its
  documentation does not say which working directory the MCP server starts in; if summaries
  keep the global voice, set it in the project's `.codex/config.toml`:

  ```toml
  [mcp_servers.voxello]
  command = "voxello"
  args = ["serve"]
  cwd = "/Users/you/code/beta"
  ```

### Overrides and reference

Every key also has an environment variable, for example `VOXELLO_INTEGRATIONS__PHRASES__MODE=off`
or `VOXELLO_INTEGRATIONS__SUMMARIES__VOICE=clone:marco`. The hook script also honours
`VOXELLO_HOOK_LANG=en` (language of the phrases) and `VOXELLO_HOOK_CHANNELS=desktop` (no voice),
which you can put in front of its command in `settings.json`; `voxello install claude --lang en`
does that for you. Review or disable the Claude Code hooks with `/hooks`.

**What gets installed.** The `voice-notify` skill (`~/.claude/skills/` or `~/.codex/skills/`)
tells the agent how to write a spoken summary: one or two sentences, in your language, no code
or paths, `priority: high` when it failed or is blocked, `cache: false`, voice left to the
configuration. It can also be invoked explicitly with `/voice-notify`. The `voice-dialogue`
skill guides the agent through a multi-voice dialogue project (see
[Review a narrated text or dialogue](#review-a-narrated-text-or-dialogue)). The hook script
`voxello-notification-hook.sh` only finds the `voxello` command (on the PATH, in
`~/.local/bin`, or `uv run --directory "$VOXELLO_REPO"` from a checkout) and hands it the event;
when none is available it logs to stderr and exits without speaking. Adding a language for the
phrases is adding a file under `voxello/assets/claude/messages/`. The same files live in this
repository under `.claude/` for people working in the checkout.

The entries `voxello install claude --stop-hook` proposes for `~/.claude/settings.json` (the
Stop hook is synchronous because Claude Code reads its output):

```json
{
  "hooks": {
    "Notification": [
      {
        "matcher": "permission_prompt|idle_prompt|agent_needs_input|elicitation_dialog",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/you/.claude/hooks/voxello-notification-hook.sh",
            "async": true,
            "timeout": 30
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/you/.claude/hooks/voxello-notification-hook.sh stop",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

`voxello install codex` proposes the same `Stop` entry for `~/.codex/hooks.json`, with the
command ending in `stop codex`. Options of both commands: `--claude-dir` / `--codex-dir`
(default `$CLAUDE_CONFIG_DIR` or `~/.claude`, `$CODEX_HOME` or `~/.codex`), `--no-skill`,
`--no-hook`, `--yes`.

## Use inside a project with `uv run`

`uv run voxello` resolves `voxello` against the *current* project, so inside another repository it
fails unless that project depends on Voxello. If you want exactly that form (for example in a
`Makefile` shared by a team), add Voxello as a development dependency:

```bash
uv add --dev voxello
uv run voxello speak "Build completed. All tests pass."
```

For everything else the tool install above is simpler: one `voxello` on the PATH, shared by every
project and by the agents' MCP configuration. `voxello doctor` reports which mode is in use.

## How it works

Voxello sends text to a TTS server on your machine or intranet (VoiceStudio or the headless
`omnivoice-server`, both running OmniVoice), plays audio through a local player and can show
desktop notifications. Claude Code, Codex, Cursor and other MCP clients use the tools below
for short messages. Dialogue projects use the CLI, guided by the `voice-dialogue` skill.

```text
Agent ──MCP tools──▶ Voxello ──HTTP──▶ TTS server ──▶ audio playback / saved file
                       └──▶ desktop notification
Agent ──skill + CLI──▶ Voxello dialogue ──▶ audio takes ──▶ review ──▶ final.wav
```

## Tools

| Tool            | Purpose                                                               | Key parameters                                                                            |
| --------------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `speak`         | synthesize and play text                                              | `text`, `language`, `voice`, `instructions`, `interrupt` (default true), `save`, `play`, `mode`, `cache`, `client_id` |
| `stop_speaking` | stop playback and clear the queue                                     | optional `request_id`                                                                     |
| `get_status`    | state, current request, queue, cache hits, provider and player health | none                                                                                      |
| `notify`        | route a short message to `voice`, `desktop` and/or `file`             | `message`, `language`, `channels`, `priority`, `title`, `cache`, `voice`, `instructions`  |

`speak` returns metadata (`request_id`, `status`, `duration_ms`, `saved_path`, `language`,
`cached`, `instructions`), never audio. `voice` takes a server voice, `clone:<name>` or a design
name from the config; `instructions` designs a voice from attributes (see
[Cloned and designed voices](#cloned-and-designed-voices)); agents are asked to leave both unset
unless the user wants a particular voice. `language` is the ISO 639-1 code of the text (`it`, `en`); agents are asked
to pass it so pronunciation follows the text, and it falls back to `speech.default_language`.
`cache` is tri-state: unset follows the policy (notifications yes, verbatim speech no), `true`
caches a phrase the agent will repeat, `false` keeps one-off text out of the cache. Errors come
back as tool errors with a stable code: `tts_provider_unavailable`, `tts_provider_unauthorized`,
`voice_not_found`, `invalid_instructions`, `invalid_language`, `text_too_long`, `queue_full`,
`playback_unavailable`, `notification_unavailable`. The `voice` commands add `voice_exists`,
`voice_cloning_unsupported` and `invalid_reference_audio`.

## How it behaves

- **Interrupt by default.** A new `speak` stops what is playing and clears the queue. Pass
  `interrupt: false` to queue instead (FIFO, `playback.max_queue_size` items).
- **Short MCP messages.** `speak` and `notify` text is capped at `limits.max_text_chars` (2000),
  for summaries and status updates. Longer texts use the dialogue workflow, rendered line by line.
- **Private by default.** Temporary audio lives in the OS cache directory with 0700 permissions,
  gets random names, and is deleted after playback and after `storage.temp_retention_minutes`.
  Logs record text length, not text, unless `logging.log_text: true`.
  Each service uses a private subdirectory. On POSIX, startup removes recognized abandoned
  subdirectories only when their owner is confirmed dead; on Windows they are retained. After
  stopping all Voxello instances, remove any abandoned subdirectories and old `vox_*.wav` files
  directly under the configured `storage.temp_dir` manually. The default directory is shown by
  `voxello doctor`. Older versions can still be using those loose files, so they are not removed
  automatically during an upgrade.
- **Cached phrases persist, hashed.** Cached audio lives in its own 0700 directory under a
  SHA-256 of the text and synthesis parameters; the text itself is never written there. Only
  hook phrases and CLI notifications (or explicit `cache: true`) up to 300 characters are cached, the least recently
  used entries are dropped beyond `cache.max_entries` or `cache.max_age_days`, and
  `voxello cache clear` empties it. Set `cache.enabled: false` to keep every new WAV
  short-lived; phrases cached earlier stay on disk until `voxello cache clear`, which works
  either way.
- **stdout is the protocol.** Logs go to stderr (and to `logging.file` if set).

## Troubleshooting

- `tts_provider_unavailable`: the TTS server is down or `base_url` is wrong. VoiceStudio's API only
  runs while the desktop app is open; `omnivoice-server` runs headless.
- `tts_provider_unauthorized`: the host is remote and needs `api_key` (`OMNIVOICE_API_KEY` on the server).
- `voice_not_found`: the voice id does not exist on this server; `voxello doctor` lists the valid ids
  per language and warns when a configured `voice` or `voices_by_language` entry is not among them.
  `doctor --synth` synthesizes a sample phrase with the voice a request would really get.
- No desktop notification on macOS: the terminal app hosting the agent needs notification
  permission in System Settings. Installing `terminal-notifier` (`brew install terminal-notifier`)
  is picked up automatically as an alternative.
- No sound on Linux: install `mpv` or make sure `paplay`/`aplay` are on PATH; `doctor` shows what
  was detected. Force one with `playback.backend`.
- A notification still plays with the old voice after you changed `voice` or `base_url`: it should
  not, since both are part of the cache key, but `voxello cache clear` removes any doubt.
  `voxello cache list` and `doctor` show what the cache holds.

## Development

Start with [CONTRIBUTING.md](CONTRIBUTING.md) for TDD, Python conventions and review practices.
[AGENTS.md](AGENTS.md) shares the rules with coding agents.

```bash
uv sync --locked --all-groups
uv run pytest                      # unit tests (fake provider and player)
uv run pytest --cov --cov-report=term-missing --cov-report=html --cov-report=xml  # combined line/branch floor: 90%
uv run pytest -m integration tests/integration   # real afplay; real VoiceStudio if VOXELLO_VOICESTUDIO_URL is set
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src
uv build                           # verify packaging when changing package configuration/assets
```

Open `htmlcov/index.html` to inspect uncovered lines and branches. Coverage reports are local
artifacts and are not committed. For a focused test during development, pass its path or `-k`
filter to `uv run pytest` without `--cov` so the whole-project coverage threshold does not apply.

Docs: `docs/TUTORIAL.md` (setting up an omnivoice-server host on Windows/NVIDIA or Apple Silicon),
`docs/recording-voices.md` (recording and cloning voices step by step),
`docs/voxello-specification.md` (the specification), `docs/voicestudio-api.md` (VoiceStudio HTTP
contract), `docs/omnivoice-server-api.md` (omnivoice-server flags and contract) and `docs/roadmap.md`
(what comes next).

### Releasing

The version lives only in `src/voxello/__init__.py` (`pyproject.toml` reads it through hatch).
Bump it, move the `Unreleased` entries of `CHANGELOG.md` under the new version, commit, then tag
and push:

```bash
git tag v0.2.0 && git push origin v0.2.0
```

`.github/workflows/release.yml` first runs the CI matrix on the tagged commit (a tag on a commit
that fails the checks publishes nothing), then checks that the tag matches `__version__`, builds
the sdist and wheel, smoke-tests the wheel, creates a GitHub release with the files and publishes
to PyPI through [trusted publishing](https://docs.pypi.org/trusted-publishers/). One-time setup on
pypi.org: add a GitHub publisher for project `voxello` with owner `marcomd`, repository `voxello`,
workflow `release.yml`, environment `pypi`, and create the `pypi` environment in the GitHub
repository settings. `ci.yml` runs ruff, pyright and pytest with coverage on macOS and Linux with
Python 3.12–3.14 for pushes to `main` and pull requests, plus one Windows leg (Python 3.12) that
runs the same suite with fakes and without the coverage floor, since POSIX-only branches are
skipped there; it also supports manual runs from the Actions tab once the workflow is on the
default branch. Each job uses the lockfile, enforces the coverage threshold and builds the
package. Tests requiring real audio hardware or a live TTS server are excluded from CI. Coverage
reports are uploaded as workflow artifacts and retained for 14 days. Superseded CI runs are
cancelled, and each job has a 15-minute timeout.

## Licensing

Voxello is Apache-2.0. VoiceStudio is AGPL-3.0 and omnivoice-server is MIT; both are only called
over HTTP. OmniVoice's model weights are CC-BY-NC (non-commercial); with VoiceStudio you can choose
another engine through `tts.voicestudio.engine` where that matters. Cloned voices carry the
rights of the person they belong to: clone only with their permission.
