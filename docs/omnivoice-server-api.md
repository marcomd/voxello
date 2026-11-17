# Using Voxello with omnivoice-server

`omnivoice-server` (PyPI `omnivoice-server`, MIT, https://github.com/maemreyo/omnivoice-server) is a
headless, OpenAI-compatible HTTP server around the k2-fsa OmniVoice model. It runs as a
local service on a workstation (`http://127.0.0.1:8880`) or on an intranet host shared by several
machines. Everything below was verified against version 0.1.0 on 2026-09-16 through its
`/openapi.json` and live requests, and the voice design and profile routes against 0.2.5 on
2026-11-17 (whose `/openapi.json` still reports 0.1.0); server-side flags come from the project's
`docs/configuration.md`.

Compared with VoiceStudio it has no GUI, no engine choice (OmniVoice only) and no dubbing tools, but
it runs as a plain service, which is what an always-on intranet TTS host needs. The same Voxello
provider (`tts.provider: voicestudio`) drives both; see `voicestudio-api.md` for the VoiceStudio side.

## 1. Server side (the intranet host)

Install and run (PyTorch must be installed first, matching the host's CPU/CUDA setup):

```bash
pip install omnivoice-server
omnivoice-server --host 0.0.0.0 --port 8880 --device cuda      # or --device cpu
```

Flags and their environment-variable equivalents (defaults in parentheses):

| Purpose | Flag | Env var | Default |
|---|---|---|---|
| Bind address; `0.0.0.0` for LAN access | `--host` | `OMNIVOICE_HOST` | `127.0.0.1` |
| Port | `--port` | `OMNIVOICE_PORT` | `8880` |
| Compute device: `cpu`, `cuda`, `mps`, `auto` | `--device` | `OMNIVOICE_DEVICE` | `cpu` |
| Bearer token; empty means no auth | `--api-key` | `OMNIVOICE_API_KEY` | none |
| Model repo id or local path | `--model` | `OMNIVOICE_MODEL_ID` | `k2-fsa/OmniVoice` |
| HuggingFace cache dir | | `OMNIVOICE_MODEL_CACHE_DIR` | HF default |
| Diffusion steps 1-64 (quality vs latency) | `--num-step` | `OMNIVOICE_NUM_STEP` | `32` |
| CFG scale 0-10 | `--guidance-scale` | `OMNIVOICE_GUIDANCE_SCALE` | `2.0` |
| Concurrent requests 1-16 | `--max-concurrent` | `OMNIVOICE_MAX_CONCURRENT` | `2` |
| Per-request timeout (s) | `--timeout` | `OMNIVOICE_REQUEST_TIMEOUT_S` | `120` |
| Voice profile storage | `--profile-dir` | `OMNIVOICE_PROFILE_DIR` | platform default |
| Log level | `--log-level` | `OMNIVOICE_LOG_LEVEL` | `info` |

Notes from the upstream README: CPU and CUDA work; MPS (Apple Silicon) is listed as broken, yet
omnivoice-server 0.2.5 with OmniVoice 0.1.5 ran with `--device mps` on an Apple Silicon Mac about
six times faster than `cpu` (see the measurements below); fall back to `cpu` if the audio sounds
wrong. The project warns that both the model and the wrapper are under active development, so
pin the version on the host. When binding to `0.0.0.0`, set `--api-key` and put the matching value in
Voxello's `tts.voicestudio.api_key`.

## 2. Voxello side

`~/Library/Application Support/voxello/config.yaml` on macOS (`voxello config path` prints the
location on any OS):

```yaml
tts:
  provider: voicestudio          # the provider name covers both VoiceStudio and omnivoice-server
  voicestudio:
    base_url: http://192.168.1.144:8880
    # api_key: "same value as OMNIVOICE_API_KEY on the server"   # only if the server sets one
    # voice: alloy               # omit to use the server default 'auto'
    engine: omnivoice            # sent as the OpenAI 'model' field; 'tts-1'/'tts-1-hd' are aliases
    language: it                 # ISO 639-1 pronunciation hint
    # num_step: 16               # override the server default (32) for lower latency
    # guidance_scale: 2.0
    # speed: 1.0                 # 0.25-4.0
    timeout_seconds: 120
```

Environment overrides: `VOXELLO_VOICESTUDIO_URL`, `VOXELLO_VOICESTUDIO_API_KEY`,
`VOXELLO_DEFAULT_VOICE`, or nested keys such as `VOXELLO_TTS__VOICESTUDIO__NUM_STEP=16`.

Check it:

```bash
voxello doctor          # health, model id, engines, voice ids, player, notifier
voxello speak "Ciao, sono Voxello." --voice nova
```

### Voices

`voice` is optional. Leave it unset for the server default `auto` (male, middle-aged, British
accent). Do **not** set `voice: default`: that is VoiceStudio's name and omnivoice-server rejects it
with a 422, which Voxello reports as `voice_not_found`.

Accepted values, from `GET /v1/voices`:

- `auto`: the fallback prompt.
- Presets mapped to designed voices: `alloy`, `ash`, `ballad`, `cedar`, `coral`, `echo`, `fable`,
  `marin`, `nova`, `onyx`, `sage`, `shimmer`, `verse`.
- `design:<attributes>`: voice design by attributes. Voxello prefers the `instructions` field
  (`voxello speak --instructions ...`, the `instructions` tool parameter, or a name from
  `speech.voice_designs`), which it validates against the vocabulary the server publishes as
  `design_attributes` in `GET /v1/voices` (gender, age, pitch, style, accent_en, dialect_zh).
  `italian accent` is not part of it.
- `clone:<profile_id>`: a cloned voice stored through `/v1/voices/profiles`. Manage them with
  `voxello voice clone|update|delete|list`.

Pass `voice` per call from an agent (`speak(text, voice="nova")`) when a single agent should sound
different, or set it once in the config.

## 3. HTTP contract Voxello relies on

| Endpoint | Used for | Notes |
|---|---|---|
| `POST /v1/audio/speech` | synthesis | JSON `SpeechRequest`; only `input` is required. `response_format` defaults to `wav` here (VoiceStudio defaults to `mp3`), Voxello always sends `wav`. Extra fields available: `speaker`, `instructions`, `stream`, `denoise`, `t_shift`, `position_temperature`, `class_temperature`, `duration`, `seed`, `request_timeout_s`. |
| `GET /health` | `get_status`, `doctor` | `{"status":"healthy","ready":true,"model_loaded":true,"uptime_s":…,"model_id":"k2-fsa/OmniVoice","memory_rss_mb":…}`. Voxello treats `ready`/`model_loaded` false as unhealthy. |
| `GET /v1/voices` | `doctor` voice list, `voice list`, design vocabulary | `{"voices":[{"id","type","description"}],"design_attributes":{...},"total":N}`; `type` is `auto`, `design`, `preset`, `file` or `clone`, and clone entries add `profile_id`, `created_at`, `ref_text`. Tried after VoiceStudio's `/v1/audio/voices` returns 404. |
| `GET /v1/models` | `doctor` engine list | OpenAI list shape, ids `omnivoice`, `tts-1`, `tts-1-hd`. Tried after `/engines/tts` returns 404. |
| `POST /v1/voices/profiles` | `voice clone` | multipart: `profile_id` (`^[a-zA-Z0-9_-]{1,64}$`), `ref_audio` (file, max 25 MB by default), optional `ref_text`, `overwrite` (`true`/`false`). 201 with `{"profile_id","name","ref_text","created_at"}`; 409 if it exists (`voice_exists`). Never retried. |
| `PATCH /v1/voices/profiles/{id}` | `voice update` | `ref_audio` and/or `ref_text`; 404 when missing (`voice_not_found`). |
| `DELETE /v1/voices/profiles/{id}` | `voice delete` | 204, or 404 `{"error":{"code":"not_found",...}}`. A bare FastAPI `{"detail":"Not Found"}` means the route itself is missing (`voice_cloning_unsupported`). |
| `POST /v1/audio/speech/clone`, `/v1/audio/script`, `/metrics` | not used | one-shot cloning without a profile, multi-speaker scripts, Prometheus metrics. |

Voice design on `POST /v1/audio/speech`: `instructions` is a comma-separated attribute string;
unknown or conflicting attributes return 422 (`Unsupported attributes: ...`, `Conflicting gender
attributes: ...`), which Voxello reports as `invalid_instructions`. A profile or voice file named in
`voice` wins over `instructions` on the server, which is why Voxello never sends both. An unknown
`clone:<id>` voice returns **404** `Voice profile '<id>' not found`, reported as `voice_not_found`.

Responses: a 200 is raw audio (24 kHz mono 16-bit WAV, `Content-Type: audio/wav`); Voxello sniffs
the `RIFF…WAVE` header and never parses a 200 as JSON. Validation errors are 422 with
`{"error":{"code":"validation_error","message":"…","detail":[…]}}`; Voxello maps messages that
mention the voice to `voice_not_found` and everything else to `tts_provider_error` with the message.

Measured on the intranet host: 2.8 s of Italian audio in about 1.9 s, 7.75 s in about 1.9 s as well,
so latency is dominated by a fixed cost at the default `num_step` of 32. Lower `num_step` if the
first word needs to come sooner. On an Apple Silicon Mac running the server locally, `--device mps`
brought one generation from 4.3 s (CPU) to 0.7 s with no errors in the server log (2026-11-17), even
though the upstream README lists MPS as broken; check the output quality on your machine.

## 4. Differences from VoiceStudio at a glance

| Concern | VoiceStudio | omnivoice-server |
|---|---|---|
| Runs | while the desktop app is open | headless service |
| Default port | 3900 | 8880 |
| Engines | OmniVoice, VoxCPM2, CosyVoice, mlx-audio, KittenTTS, MOSS-TTS-nano | OmniVoice only |
| Default voice | `default` | `auto` |
| Voices list | `GET /v1/audio/voices` | `GET /v1/voices` |
| Voice design | `instruct` (not sent by Voxello) | `instructions` |
| Cloned voices | not supported by Voxello | profiles, `voice clone` |
| Auth | Bearer required off-localhost | only if `OMNIVOICE_API_KEY` is set |
| Streaming | multipart `/generate` only | `"stream": true` on `/v1/audio/speech` (not used by Voxello yet) |
| License | AGPL-3.0 | MIT |

Both run the OmniVoice model, whose weights are CC-BY-NC (non-commercial). VoiceStudio is the only
option if a differently licensed engine is required.
