# VoiceStudio API contract used by Voxello

Source: the official OpenAPI 3.1 document published at
`https://voicestudio.sh/voicestudio-openapi.json` ("VoiceStudio Local API", version 0.5.0),
cross-checked with the docs at `https://voicestudio.sh/docs`. Verified on 2026-09-16.
Items marked *unverified* were not declared in the OpenAPI document; confirm them against the
installed version with `curl` and update this file.

## Lifecycle

- The API is a sidecar of the VoiceStudio desktop app. It listens on port **3900** only while
  the app is open. There is no documented headless mode, so "app not running" is a normal
  failure Voxello reports as `tts_provider_unavailable`.
- Port 3902 is a separate control plane for dictation; Voxello does not use it.

## Authentication

- Requests from `127.0.0.1`, `::1` or `localhost` need no credential.
- Remote access (Voxello on a workstation, VoiceStudio on an intranet host) requires
  `Authorization: Bearer <key>`. The key is configured on the VoiceStudio side through the
  `OMNIVOICE_API_KEY` environment variable. Voxello sends the header whenever
  `tts.voicestudio.api_key` is set. Voxello maps 401/403 to `tts_provider_unauthorized`.

## Synthesis: `POST /v1/audio/speech`

OpenAI-compatible JSON endpoint. Voxello uses only this route.

| Field | Type | Default | Voxello sends |
|---|---|---|---|
| `input` | string, max 4096 chars | required | the text (Voxello caps at `limits.max_text_chars`, default 2000) |
| `model` | string | `omnivoice` | `tts.voicestudio.engine` (also `voxcpm2`, `cosyvoice`, `mlx-audio`, `kittentts`, `moss-tts-nano`) |
| `voice` | string | `default` | profile id, or `default` |
| `response_format` | `mp3` `opus` `aac` `flac` `wav` `pcm` | `mp3` | always `wav` |
| `speed` | 0.25-4.0 | 1 | if configured |
| `language` | ISO 639-1 | null | `tts.voicestudio.language` (default `it`) |
| `num_step` | 1-128 | app default 16 | if configured (32 is the model's quality preset) |
| `guidance_scale` | 0-20 | app default 2.0 | if configured |
| `seed`, `instruct`, `description`, `duration`, `denoise`, `preprocess_prompt`, `chunk_duration`, `chunk_threshold` | | | not used |

Response:

- `200` with **raw audio bytes**. Never parse a 200 as JSON. Voxello checks that the body starts
  with `RIFF....WAVE`; the docs note that an unavailable encoder can fall back to WAV regardless
  of `response_format`, so the content type alone is not trusted.
- `422` with FastAPI `HTTPValidationError` JSON (`{"detail": [{"loc": [...], "msg": "..."}]}`).
  Voxello maps messages mentioning `voice`/`profile` to `voice_not_found`, the rest to
  `tts_provider_error` with the detail text.
- Output audio: 24 kHz mono WAV (16-bit inferred). Duration is read from the WAV header.

Not available on this route: streaming (`stream` exists only on the multipart `POST /generate`,
whose response shape is undocumented). Long-form and dubbing are job-based (`/jobs`, `/longform`,
`/audiobook`) and out of Voxello's scope.

## Discovery and health

| Endpoint | Used for | Body |
|---|---|---|
| `GET /health` | `get_status` health block, `doctor` | 200 expected; JSON like `{"status":"ok","device":"...","version":"0.5.0"}` *unverified* |
| `GET /v1/audio/voices` | `doctor` voice listing | JSON, shape *unverified*; Voxello accepts a list or `{"voices": [...]}` of strings or objects with `id`/`voice_id`/`profile_id` |
| `GET /engines/tts` | `doctor` engine listing | JSON, shape *unverified* |
| `GET /.well-known/voicestudio-speech` | discovery handshake (`voicestudio.speech.v1`) | not used yet |

## Licensing notes

- VoiceStudio app: AGPL-3.0. Voxello only talks to it over HTTP.
- OmniVoice (the default engine): code Apache-2.0, **model weights CC-BY-NC** (non-commercial).
  For commercial use pick another engine via `tts.voicestudio.engine` and review its license.
