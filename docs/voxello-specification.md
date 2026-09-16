# Voxello

## Local Voice & Notification Bridge for AI Agents

**Status:** Technical specification / MVP design\
**Project name:** `voxello`\
**Tagline:** *A local voice and notification layer for AI agents.*\
**Primary use case:** allow agents such as Claude Code, Codex, Cursor,
and other MCP-capable clients to speak, notify, and route short audio
responses through a local TTS provider such as VoiceStudio/OmniVoice.

------------------------------------------------------------------------

## 1. Executive summary

Voxello is a lightweight local bridge between AI agents and
speech/output services.

Its purpose is **not** to implement text-to-speech itself. Instead,
Voxello exposes a small, stable, agent-friendly interface---primarily
through MCP---and translates high-level intents such as `speak`,
`stop_speaking`, and `notify` into calls to a configured TTS provider
and local output mechanisms.

The initial TTS provider is **VoiceStudio**, using its local HTTP/API
capabilities and OmniVoice or any other engine configured inside
VoiceStudio.

The core design principle is separation of responsibilities:

``` text
AI Agent
   │
   │ MCP
   ▼
Voxello
   │
   ├── TTS provider ──► VoiceStudio ──► WAV/audio
   │
   └── Output router ─► Local speaker / file / desktop notification / webhook
```

VoiceStudio owns:

``` text
text → synthesized audio
```

Voxello owns:

``` text
agent intent → synthesis request → playback/routing/lifecycle
```

This keeps the agent independent from the underlying TTS engine and
keeps VoiceStudio independent from OS-specific playback and agent UX.

------------------------------------------------------------------------

# 2. Goals

Voxello should:

1.  Expose simple MCP tools usable by Claude Code, Codex, Cursor, and
    other compatible agents.
2.  Generate speech through VoiceStudio without requiring the agent to
    understand VoiceStudio internals.
3.  Play generated audio automatically on the local machine.
4.  Support interruption/cancellation of active playback.
5.  Support optional saving of generated audio.
6.  Support queues and replacement/interruption policies.
7.  Support multiple TTS providers through an adapter architecture.
8.  Support multiple output channels in the future.
9.  Remain local-first and privacy-friendly.
10. Be easy to install and run as a background service.
11. Provide predictable behavior suitable for agent automation.
12. Avoid sending large WAV/base64 payloads through the LLM context when
    unnecessary.

------------------------------------------------------------------------

# 3. Non-goals

The MVP should **not** attempt to:

-   implement its own TTS model;
-   replace VoiceStudio;
-   perform LLM inference;
-   summarize arbitrary text itself;
-   decide what an agent should say;
-   provide voice authentication;
-   act as an identity-verification system;
-   expose VoiceStudio or Voxello directly to the public Internet;
-   implement a complete conversational voice assistant;
-   perform speech-to-text unless added later as a separate capability.

Summarization and reasoning belong to the calling agent.

For example:

``` text
Asana epic
    ↓
Claude Code
    ↓
summary
    ↓
voxello.speak(summary)
```

Voxello receives the final text to speak.

------------------------------------------------------------------------

# 4. Primary user experience

Example request:

> Fammi un riassunto di questo epic task Asana e leggimelo.

Expected execution:

``` text
User
 │
 ▼
Claude Code
 │
 ├── retrieves Asana epic
 ├── analyzes tasks
 ├── generates summary
 │
 └── MCP: voxello.speak(summary)
              │
              ▼
           Voxello
              │
              ├── calls VoiceStudio
              │
              ▼
          generated WAV
              │
              ▼
        local audio player
              │
              ▼
             🔊
```

The user receives the normal textual answer from the agent and, when
requested, a spoken version.

------------------------------------------------------------------------

# 5. Secondary use case: agent completion notifications

A particularly valuable workflow is asynchronous notification.

Example:

> Refactorizza il modulo auth, esegui i test e correggi gli errori.

The agent works for several minutes. At completion it calls:

``` text
notify(
    message="Refactoring completato. Tutti i 126 test passano.",
    channels=["voice", "desktop"]
)
```

The user can work elsewhere and hear:

> "Refactoring completato. Tutti i 126 test passano."

This turns Voxello into a **local notification layer for autonomous and
semi-autonomous agents**, rather than merely a TTS wrapper.

------------------------------------------------------------------------

# 6. Architecture

## 6.1 High-level architecture

``` text
┌─────────────────────────────────────────────┐
│                AI AGENTS                    │
│                                             │
│ Claude Code   Codex   Cursor   Other MCP    │
└─────────────────────┬───────────────────────┘
                      │
                      │ MCP
                      ▼
┌─────────────────────────────────────────────┐
│                  VOXELLO                    │
│                                             │
│  MCP Server                                 │
│      │                                      │
│      ▼                                      │
│  Command / Intent Layer                     │
│      │                                      │
│      ├───────────────┐                      │
│      ▼               ▼                      │
│  TTS Manager     Output Router              │
│      │               │                      │
│      ▼               ├─ Local speaker       │
│  Provider Adapter    ├─ Desktop notification│
│      │               ├─ File                │
│      │               └─ Webhook (future)    │
└──────┼──────────────────────────────────────┘
       │
       │ HTTP
       ▼
┌──────────────────────┐
│     VoiceStudio      │
│                      │
│ OmniVoice / other TTS│
└──────────────────────┘
```

------------------------------------------------------------------------

# 7. Protocol choices

## 7.1 Agent → Voxello

**Protocol:** MCP.

Rationale:

-   native tool semantics for AI agents;
-   easy discovery of capabilities;
-   typed tool parameters;
-   compatible with multiple agent ecosystems;
-   keeps the LLM-facing API small and semantic.

The agent should think in terms of:

``` text
speak()
stop_speaking()
notify()
get_status()
```

not:

``` text
generate_wav()
execute_afplay()
read_temp_file()
delete_temp_file()
```

OS and TTS implementation details must remain hidden from the agent.

------------------------------------------------------------------------

## 7.2 Voxello → VoiceStudio

**Preferred protocol:** local HTTP API.

Rationale:

-   VoiceStudio already exposes local API functionality;
-   avoids unnecessary MCP-to-MCP coupling;
-   reduces protocol dependencies;
-   allows Voxello to support other TTS backends later;
-   simplifies testing and provider abstraction.

Conceptually:

``` text
Agent
   │ MCP
   ▼
Voxello
   │ HTTP
   ▼
VoiceStudio
```

------------------------------------------------------------------------

# 8. MCP tool specification

## 8.1 `speak`

Synthesizes and optionally plays text.

### Input

``` json
{
  "text": "Build completata. Tutti i test passano.",
  "voice": null,
  "interrupt": true,
  "save": false,
  "play": true
}
```

### Parameters

  -----------------------------------------------------------------------------
  Parameter     Type                  Required          Default Description
  ------------- ------------- ---------------- ---------------- ---------------
  `text`        string                     yes              --- Text to
                                                                synthesize

  `voice`       string/null                 no       configured Voice/profile
                                                        default identifier

  `interrupt`   boolean                     no             true Stop current
                                                                playback before
                                                                speaking

  `save`        boolean                     no            false Persist
                                                                generated audio

  `play`        boolean                     no             true Play generated
                                                                audio locally
  -----------------------------------------------------------------------------

### Output

``` json
{
  "status": "playing",
  "request_id": "vox_01H...",
  "duration_ms": 5400,
  "saved_path": null
}
```

The tool should return metadata, not the complete WAV payload.

------------------------------------------------------------------------

# 9. `stop_speaking`

Stops active playback.

### Input

``` json
{}
```

or optionally:

``` json
{
  "request_id": "vox_01H..."
}
```

### Output

``` json
{
  "status": "stopped"
}
```

Cancellation is a first-class requirement, not an optional enhancement.

------------------------------------------------------------------------

# 10. `get_status`

Returns current Voxello state.

Example:

``` json
{
  "status": "playing",
  "request_id": "vox_01H...",
  "provider": "voicestudio",
  "voice": "default",
  "queue_length": 1
}
```

Possible states:

``` text
idle
generating
playing
queued
stopping
error
```

------------------------------------------------------------------------

# 11. `notify`

Routes a concise agent notification to one or more channels.

### Input

``` json
{
  "message": "Build completata. Due test sono falliti.",
  "channels": ["voice", "desktop"],
  "priority": "normal"
}
```

### Initial channels

``` text
voice
desktop
file
```

Future channels may include:

``` text
webhook
mobile
slack
mqtt
home-assistant
```

### Output

``` json
{
  "status": "delivered",
  "channels": {
    "voice": "playing",
    "desktop": "sent"
  }
}
```

------------------------------------------------------------------------

# 12. Optional semantic hint: `mode`

`speak` may accept an optional semantic hint:

``` json
{
  "text": "...",
  "mode": "notification"
}
```

Suggested values:

``` text
verbatim
summary
notification
```

Important: Voxello **must not summarize or rewrite the text** based on
this value.

`mode` is metadata that can influence presentation behavior, for
example:

-   notification volume;
-   queue priority;
-   desktop notification behavior;
-   audio caching;
-   future voice selection.

The calling LLM remains responsible for producing the appropriate
wording.

------------------------------------------------------------------------

# 13. Agent behavior recommendations

Agents integrating Voxello should follow several UX rules.

## 13.1 Keep spoken responses short

Default spoken notifications should generally be shorter than
approximately 20 seconds.

Instead of reading:

> the complete 900-word analysis

the agent should speak:

> "Analisi completata. Ho trovato tre blocker e due dipendenze critiche.
> I dettagli sono nella risposta testuale."

The textual interface remains the primary surface for detailed
information.

Voice is optimized for:

-   summaries;
-   status;
-   alerts;
-   completion notifications;
-   short answers;
-   hands-free interaction.

------------------------------------------------------------------------

# 14. Playback manager

Voxello requires a dedicated playback abstraction.

Interface:

``` text
play(path)
stop()
pause()       # future
resume()      # future
status()
set_volume()  # future
```

Platform implementations:

### macOS

Possible backend:

``` text
afplay
```

### Linux

Possible backends:

``` text
mpv
paplay
aplay
```

### Windows

Possible implementations:

``` text
PowerShell MediaPlayer
Windows Media APIs
native library
```

The implementation should avoid shell injection by never constructing
unsafe command strings from agent-provided values.

------------------------------------------------------------------------

# 15. Queue semantics

Voxello should support predictable playback policies.

Default:

``` text
interrupt = true
```

Meaning:

1.  stop currently playing audio;
2.  clear or preserve queue according to configuration;
3.  generate/play new message.

Optional queue behavior:

``` text
interrupt = false
```

Meaning:

``` text
current audio
    ↓
queued message A
    ↓
queued message B
```

Recommended configurable queue policy:

``` yaml
playback:
  default_interrupt: true
  max_queue_size: 10
  queue_policy: fifo
```

Future priorities:

``` text
low
normal
high
critical
```

------------------------------------------------------------------------

# 16. TTS provider abstraction

Voxello must not depend directly on OmniVoice.

Define a generic provider interface.

Conceptually:

``` text
TTSProvider
    synthesize(text, voice, options)
    list_voices()
    health()
```

Initial implementation:

``` text
VoiceStudioProvider
```

Future providers could include:

``` text
SystemTTSProvider
PiperProvider
ElevenLabsProvider
OpenAITTSProvider
CustomHTTPProvider
```

The existence of alternative providers must not change the MCP contract
exposed to agents.

------------------------------------------------------------------------

# 17. VoiceStudio provider

Initial configuration:

``` yaml
tts:
  provider: voicestudio

  voicestudio:
    base_url: http://localhost:3900
    voice: default
    timeout_seconds: 120
```

The adapter is responsible for:

1.  checking VoiceStudio availability;
2.  sending synthesis requests;
3.  selecting a configured voice/profile;
4.  receiving or locating generated audio;
5.  returning a normalized Voxello audio object.

Conceptual normalized result:

``` json
{
  "audio_path": "/tmp/voxello/abc123.wav",
  "mime_type": "audio/wav",
  "duration_ms": 6200,
  "provider": "voicestudio"
}
```

------------------------------------------------------------------------

# 18. File handling

Temporary files should be stored in an application-controlled directory.

Example:

``` text
~/.cache/voxello/
```

or OS-equivalent application cache directory.

Generated temporary audio should:

1.  receive random/non-predictable identifiers;
2.  never use raw user text as filename;
3.  be deleted automatically;
4.  have configurable retention;
5.  use restrictive filesystem permissions where appropriate.

Example:

``` yaml
storage:
  temp_retention_minutes: 10
  cleanup_on_start: true
```

Persistent output:

``` yaml
output:
  directory: ~/Voxello
```

Only files explicitly requested with `save=true` should be persisted by
default.

------------------------------------------------------------------------

# 19. Configuration

Example complete MVP configuration:

``` yaml
server:
  transport: stdio

tts:
  provider: voicestudio

  voicestudio:
    base_url: http://localhost:3900
    voice: default
    timeout_seconds: 120

playback:
  enabled: true
  default_interrupt: true
  volume: 0.8
  max_queue_size: 10

notifications:
  voice: true
  desktop: true

storage:
  temp_retention_minutes: 10
  cleanup_on_start: true

output:
  save_by_default: false
  directory: ~/Voxello

logging:
  level: info
```

Environment variables should be able to override configuration for
deployment and automation.

Example:

``` text
VOXELLO_TTS_PROVIDER
VOXELLO_VOICESTUDIO_URL
VOXELLO_DEFAULT_VOICE
VOXELLO_LOG_LEVEL
```

------------------------------------------------------------------------

# 20. Suggested repository structure

``` text
voxello/
│
├── README.md
├── LICENSE
├── pyproject.toml
├── config.example.yaml
│
├── src/
│   └── voxello/
│       │
│       ├── __main__.py
│       ├── config.py
│       ├── server.py
│       │
│       ├── mcp/
│       │   ├── server.py
│       │   └── tools.py
│       │
│       ├── core/
│       │   ├── service.py
│       │   ├── queue.py
│       │   ├── models.py
│       │   └── lifecycle.py
│       │
│       ├── tts/
│       │   ├── base.py
│       │   └── voicestudio.py
│       │
│       ├── playback/
│       │   ├── base.py
│       │   ├── macos.py
│       │   ├── linux.py
│       │   └── windows.py
│       │
│       ├── notifications/
│       │   ├── base.py
│       │   └── desktop.py
│       │
│       └── storage/
│           └── files.py
│
└── tests/
    ├── test_speak.py
    ├── test_queue.py
    ├── test_cancel.py
    ├── test_storage.py
    └── providers/
        └── test_voicestudio.py
```

Python is a practical initial implementation language because the
surrounding AI/MCP and VoiceStudio ecosystem already has strong Python
support, but the architecture should not depend on language-specific
behavior.

------------------------------------------------------------------------

# 21. Core internal models

Suggested request object:

``` text
SpeechRequest
```

Fields:

``` text
id
text
voice
mode
interrupt
play
save
created_at
```

Suggested result:

``` text
SpeechResult
```

Fields:

``` text
request_id
status
provider
audio_path
saved_path
duration_ms
error
```

------------------------------------------------------------------------

# 22. Request lifecycle

``` text
RECEIVED
   │
   ▼
VALIDATING
   │
   ▼
GENERATING
   │
   ▼
GENERATED
   │
   ├── save requested ──► PERSISTED
   │
   └── play requested
            │
            ▼
         PLAYING
            │
      ┌─────┴─────┐
      ▼           ▼
 COMPLETED     CANCELLED
```

Failures move the request to:

``` text
ERROR
```

Each request should have a unique ID to simplify cancellation, logging,
debugging, and future observability.

------------------------------------------------------------------------

# 23. Error handling

Errors returned to the agent should be concise and actionable.

Examples:

### VoiceStudio unavailable

``` json
{
  "status": "error",
  "code": "tts_provider_unavailable",
  "message": "VoiceStudio is not reachable on localhost:3900."
}
```

### Playback unavailable

``` json
{
  "status": "error",
  "code": "playback_unavailable",
  "message": "No supported local audio player was detected."
}
```

### Invalid voice

``` json
{
  "status": "error",
  "code": "voice_not_found",
  "message": "The requested voice profile does not exist."
}
```

### Queue full

``` json
{
  "status": "error",
  "code": "queue_full",
  "message": "The playback queue is full."
}
```

Do not expose stack traces or sensitive local paths to the LLM unless
debug mode is explicitly enabled.

------------------------------------------------------------------------

# 24. Health checks

Voxello should expose internal/provider health information.

Example:

``` json
{
  "voxello": "ok",
  "tts": {
    "provider": "voicestudio",
    "status": "ok"
  },
  "playback": {
    "status": "ok",
    "backend": "afplay"
  }
}
```

This can be exposed through `get_status` or a dedicated future `health`
tool.

------------------------------------------------------------------------

# 25. Security model

Voxello is intended primarily as a **local service**.

Default binding should therefore be:

``` text
localhost only
```

It should never bind to:

``` text
0.0.0.0
```

without explicit configuration.

Security requirements:

-   validate all MCP inputs;
-   impose maximum text length;
-   reject unsupported parameters;
-   prevent path traversal;
-   avoid arbitrary command execution;
-   never interpolate agent text into shell commands;
-   restrict file output to configured directories;
-   sanitize filenames;
-   use random request IDs;
-   limit queue size;
-   limit generated file retention;
-   avoid returning arbitrary local files through MCP;
-   do not expose unauthenticated services publicly.

------------------------------------------------------------------------

# 26. Voice cloning and identity

Voice cloning must never be treated as authentication.

A recognizable voice does not prove:

``` text
speaker identity
authorization
approval
intent
```

Company workflows involving payments, privileged operations, password
resets, approvals, or sensitive instructions should use independent
authentication mechanisms such as:

-   MFA;
-   signed approvals;
-   application confirmation;
-   ERP workflow approval;
-   authenticated secondary channels.

Only voices for which appropriate permission has been obtained should be
cloned or used.

Model-specific licenses must also be reviewed before commercial
deployment.

------------------------------------------------------------------------

# 27. Privacy

A local-first deployment can keep the primary pipeline on the
workstation:

``` text
Agent
  ↓
Voxello
  ↓
VoiceStudio
  ↓
local audio
```

Voxello should avoid retaining text/audio unnecessarily.

Recommended defaults:

``` text
log full spoken text: false
retain temporary audio: short-lived
telemetry: disabled unless explicitly enabled
```

Logs should preferably contain request IDs and operational metadata
rather than complete potentially sensitive content.

------------------------------------------------------------------------

# 28. Observability

Suggested log event:

``` json
{
  "event": "speech_completed",
  "request_id": "vox_01H...",
  "provider": "voicestudio",
  "generation_ms": 1820,
  "playback_ms": 4300,
  "saved": false
}
```

Useful metrics:

``` text
speech requests
generation latency
playback duration
provider failures
cancellations
queue depth
notification deliveries
```

------------------------------------------------------------------------

# 29. Desktop notifications

Desktop notifications complement voice well.

Example agent event:

``` text
Build completed with 2 failed tests.
```

Voxello may simultaneously:

``` text
🔊 speak short summary
🖥 show desktop notification
```

The desktop notification can contain more information than the spoken
message.

Future configuration:

``` yaml
notifications:
  desktop:
    enabled: true

  voice:
    enabled: true
```

------------------------------------------------------------------------

# 30. Hotkey support

A future local companion process may provide global hotkeys.

Highest-value hotkey:

``` text
STOP VOICE
```

Possible future shortcuts:

``` text
stop playback
mute Voxello
repeat last notification
toggle voice notifications
```

Cancellation should nevertheless exist at the service/API level from
MVP.

------------------------------------------------------------------------

# 31. Multi-agent behavior

Voxello should assume that multiple agents may call it concurrently.

Example:

``` text
Claude Code ─┐
             │
Codex ───────┼──► Voxello
             │
Cursor ──────┘
```

Each request therefore needs:

``` text
request_id
optional client_id
timestamp
priority
```

Future policy:

``` yaml
agents:
  codex:
    voice: developer
  claude-code:
    voice: assistant
```

This would allow different voices for different agents without changing
their MCP calls.

------------------------------------------------------------------------

# 32. Future voice routing

A future configuration could support:

``` yaml
voices:
  default: main

  agents:
    claude-code: claude_voice
    codex: codex_voice
    cursor: cursor_voice
```

This can make concurrent agent workflows immediately distinguishable by
sound.

------------------------------------------------------------------------

# 33. Output router

Long-term, the most important abstraction after TTS is the output
router.

``` text
                 ┌── local speaker
                 │
Agent → Voxello ─┼── desktop notification
                 │
                 ├── audio file
                 │
                 ├── webhook
                 │
                 └── remote device
```

The core API then evolves from pure TTS into:

``` text
notify(message, channels)
```

This makes Voxello useful even when voice synthesis is not required.

------------------------------------------------------------------------

# 34. Potential future features

Post-MVP possibilities:

### Speech input

``` text
microphone
   ↓
STT
   ↓
agent
   ↓
Voxello
   ↓
speaker
```

This would create a complete local voice loop.

### Remote output

Send generated audio to:

-   another workstation;
-   mobile application;
-   Raspberry Pi;
-   smart speaker;
-   Home Assistant;
-   meeting-room device.

### Scheduled speech

Examples:

``` text
daily briefing
build report
morning operations summary
CI/CD completion alert
```

### Streaming TTS

Start playback before the entire synthesis completes.

This could substantially reduce perceived latency for longer messages.

### Audio ducking

Automatically reduce music/system audio while Voxello speaks.

### Presence awareness

Do not speak when:

-   microphone is active in a meeting;
-   system is in Do Not Disturb;
-   headphones are disconnected;
-   workstation is locked.

Instead route to desktop/mobile notification.

------------------------------------------------------------------------

# 35. Company workflows

## 35.1 Engineering agent notifications

``` text
Codex / Claude Code
      ↓
code/test/build
      ↓
Voxello
      ↓
"Build completata. Due test falliti."
```

High-value because users do not need to watch the terminal continuously.

------------------------------------------------------------------------

## 35.2 Project management summaries

``` text
Asana / Jira
     ↓
LLM
     ↓
short executive summary
     ↓
Voxello
```

Example:

> "L'epic è al 72%. Tre task sono bloccati e la milestone è a rischio di
> due giorni."

------------------------------------------------------------------------

## 35.3 Operational briefings

``` text
ERP / CRM / monitoring
         ↓
        LLM
         ↓
      summary
         ↓
      Voxello
```

Useful for hands-free environments and periodic operational summaries.

------------------------------------------------------------------------

## 35.4 Internal knowledge assistant

``` text
Employee
   ↓
AI agent + company RAG
   ↓
answer
   ↓
Voxello
   ↓
spoken answer
```

Potential environments:

-   warehouse;
-   field operations;
-   maintenance;
-   manufacturing;
-   support desks.

------------------------------------------------------------------------

## 35.5 Training and accessibility

Documents and procedures can be transformed by an agent into concise
spoken material and delivered through Voxello.

This can support:

-   accessibility;
-   hands-free work;
-   onboarding;
-   internal training;
-   multilingual narration.

------------------------------------------------------------------------

# 36. MVP scope

The first release should deliberately remain small.

## Required

-   MCP server;
-   `speak`;
-   `stop_speaking`;
-   `get_status`;
-   VoiceStudio provider;
-   local WAV playback;
-   macOS/Linux/Windows playback abstraction;
-   temporary file management;
-   configuration file;
-   structured errors;
-   queue;
-   interrupt semantics;
-   basic logs.

## Strongly recommended

-   `notify`;
-   desktop notifications;
-   provider health check;
-   saved audio option.

## Not required for MVP

-   STT;
-   streaming;
-   mobile app;
-   web UI;
-   remote speakers;
-   multiple simultaneous audio streams;
-   cloud TTS providers;
-   sophisticated authentication.

------------------------------------------------------------------------

# 37. MVP success criteria

The MVP is successful when this workflow is reliable:

``` text
1. VoiceStudio is running locally.
2. Voxello is running locally.
3. Claude Code/Codex connects to Voxello through MCP.
4. User asks the agent to perform a task and announce completion.
5. Agent calls voxello.speak() or voxello.notify().
6. Voxello asks VoiceStudio to synthesize the message.
7. Audio is generated.
8. Voxello plays it automatically.
9. User can interrupt playback.
10. Temporary audio is cleaned up.
```

No manual audio-file handling should be necessary.

------------------------------------------------------------------------

# 38. Suggested implementation phases

## Phase 1 --- Proof of concept

Implement:

``` text
speak(text)
```

Pipeline:

``` text
MCP
 ↓
VoiceStudio
 ↓
WAV
 ↓
local player
```

Target: prove the complete round trip.

------------------------------------------------------------------------

## Phase 2 --- Usable local tool

Add:

``` text
stop_speaking
status
queue
configuration
temporary-file cleanup
cross-platform playback
```

------------------------------------------------------------------------

## Phase 3 --- Agent notification layer

Add:

``` text
notify
desktop notifications
priorities
client IDs
per-agent configuration
```

At this stage Voxello becomes more than a VoiceStudio bridge.

------------------------------------------------------------------------

## Phase 4 --- Provider abstraction

Formalize:

``` text
TTSProvider
PlaybackProvider
NotificationProvider
```

Add alternative TTS engines if there is real demand.

------------------------------------------------------------------------

## Phase 5 --- Advanced local assistant

Potential additions:

``` text
STT
streaming
hotkeys
presence detection
remote outputs
mobile companion
```

------------------------------------------------------------------------

# 39. Product positioning

Voxello should not primarily position itself as:

> "an OmniVoice MCP bridge."

That description is technically narrow and duplicates capabilities
already present in VoiceStudio.

A stronger definition is:

> **Voxello is a local voice and notification layer for AI agents.**

VoiceStudio is the initial speech engine, while Voxello handles:

``` text
agent semantics
playback
interruptions
queues
notifications
routing
provider abstraction
local UX
```

This distinction gives the project an independent purpose and allows it
to remain useful as models, agents, and TTS engines evolve.

------------------------------------------------------------------------

# 40. Design principles

The project should follow these principles:

**Local first.**\
The normal workflow should not require a cloud service.

**Agent friendly.**\
Expose semantic tools, not implementation details.

**Provider agnostic.**\
VoiceStudio is the first provider, not a permanent architectural
dependency.

**Short spoken output by default.**\
Voice complements text rather than replacing it.

**Interruptible.**\
Users must always be able to stop audio quickly.

**Deterministic plumbing.**\
The LLM decides what to say; Voxello deterministically handles how it is
delivered.

**Privacy conscious.**\
Avoid unnecessary retention of text and audio.

**Secure by default.**\
Local binding, controlled filesystem access, strict input validation.

**Composable.**\
TTS, playback, storage, and notifications should be replaceable
adapters.

------------------------------------------------------------------------

# 41. Example final interaction

User:

``` text
Controlla l'epic checkout-redesign su Asana.
Dimmi se ci sono blocker e quando hai finito avvisami a voce.
```

Agent:

``` text
1. Reads Asana.
2. Analyzes epic.
3. Produces detailed textual response.
4. Generates concise spoken summary:

   "Analisi completata. Ho trovato tre blocker.
    Il più critico riguarda l'integrazione dei pagamenti.
    I dettagli sono nella risposta."

5. Calls:

   voxello.notify(
       message=...,
       channels=["voice", "desktop"]
   )
```

Voxello:

``` text
receives notification
        ↓
VoiceStudio synthesis
        ↓
temporary WAV
        ↓
desktop notification
        +
local playback
        ↓
automatic cleanup
```

User hears:

> 🔊 "Analisi completata. Ho trovato tre blocker. Il più critico
> riguarda l'integrazione dei pagamenti. I dettagli sono nella
> risposta."

This is the target Voxello experience.

------------------------------------------------------------------------

# 42. One-line specification

> **Voxello provides AI agents with a small MCP interface for turning
> semantic notifications and short responses into locally synthesized,
> interruptible, routable voice output.**
