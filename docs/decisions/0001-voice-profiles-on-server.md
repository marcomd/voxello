# 0001 — Cloned voices live on the TTS server; cloning is CLI-only

Status: accepted, 2026-11-17

## Context

OmniVoice clones a voice from a 3-10 s reference recording (plus an optional transcript) and
designs a voice from attributes such as `female, young adult, high pitch`. Voxello needed to let
users create cloned voices once and reuse them, and to let agents and the config ask for a
designed voice.

omnivoice-server already stores cloned voices as named profiles (`/v1/voices/profiles`, files
under its own data directory) and accepts them on every synthesis request as
`voice="clone:<id>"`. It also offers a one-shot route, `POST /v1/audio/speech/clone`, that takes the
reference recording with each request.

## Decision

1. **Profiles on the server.** `voxello voice clone|update|delete|list` wrap
   `/v1/voices/profiles`; Voxello keeps no copy of the reference audio. A cloned voice is then an
   ordinary voice id (`clone:marco`) that works in `--voice`, the `voice` tool parameter,
   `voices_by_language` and `tts.voicestudio.voice`.
2. **CLI only.** The MCP tools can use a cloned voice but cannot create, change or delete one.
   Cloning someone's voice needs their permission (specification section 26), so it stays a
   deliberate human action; `voice clone` asks for that confirmation unless `--yes` is passed.
3. **Designs by value or by name.** `instructions` on `speak`/`notify` carries the attributes,
   validated locally against the upstream vocabulary and canonicalized. Named designs live in
   `speech.voice_designs` and resolve wherever a voice is accepted. A request never sends both a
   voice and instructions, because the server would silently drop one of them.

## Consequences

- No local storage, permissions or sync for reference audio; every client of the same server
  shares the voices, and the server's existing limits (25 MB, profile-id alphabet) apply.
- VoiceStudio is not covered: it has no profile API, and its design field is named `instruct`.
  The commands report `voice_cloning_unsupported` there.
- Replacing a profile's audio keeps its name, so cached phrases could replay the old voice.
  Voxello drops the cache entries recorded under `clone:<id>` and `<id>` on every change it makes;
  changes made directly on the server need `voxello cache clear`.
- Instructions enter the cache key only when set, so phrases cached before this change (the
  hook sentences) keep their keys.
- The local design vocabulary can drift from the server's; `voxello doctor` compares it with the
  server's `design_attributes` and warns.

## Alternatives considered

- **Local library + `/v1/audio/speech/clone`.** Works without server-side state, but uploads the
  recording on every synthesis, cannot be combined with instructions, and duplicates storage the
  server already provides.
- **MCP tools for cloning.** Convenient for agents, but would let an agent clone any audio file it
  can read without the speaker's consent being checked by a person.
