# Tutorial: setting up a TTS server for Voxello

This tutorial takes you from a bare machine to a running `omnivoice-server` that Voxello can talk
to. It covers two hosts: a Windows PC with an NVIDIA GPU (the fast option) and a Mac with Apple
Silicon (CPU only, slower but fine for short notifications). The server can run on the same machine
as Voxello or on another machine on your network.

Versions checked on 2026-09-17: `omnivoice-server` 0.2.5, PyTorch 2.14.0, both requiring Python
3.10 or newer. Commands that depend on a version are marked so you can re-check them.

## 1. What you need

- **A host** for the server. Either
  - Windows 10/11 (64-bit) with an NVIDIA GPU (any recent GeForce or RTX card with at least 6 GB of
    VRAM works well), or
  - a Mac with Apple Silicon (M1 or later) and at least 16 GB of memory.
- **Disk space**: about 10 GB for PyTorch, the CUDA runtime libraries and the model weights.
- **Internet access** the first time you start the server: it downloads the OmniVoice model
  (`k2-fsa/OmniVoice`) from HuggingFace and caches it locally.
- **uv**, the Python package manager used throughout this project. It also installs Python for you,
  so you do not need a system Python.

A licensing note: `omnivoice-server` is MIT, but the OmniVoice model weights are CC-BY-NC
(non-commercial). Keep that in mind if the server is for commercial use.

## 2. Windows with an NVIDIA GPU

All commands run in **PowerShell** (search "PowerShell" in the Start menu; no administrator rights
needed unless stated).

### 2.1 NVIDIA driver

Install or update the driver from https://www.nvidia.com/drivers (or through GeForce Experience /
NVIDIA App). You do not need to install the CUDA Toolkit separately: the PyTorch wheels ship the
CUDA runtime libraries they need. Reboot, then check:

```powershell
nvidia-smi
```

You should see a table with your GPU name, the driver version and, in the top-right corner,
`CUDA Version: 12.x` or `13.x`. Note this number: it is the **highest** CUDA version the driver
supports, and the PyTorch build you install must be at or below it. If `nvidia-smi` is not found,
the driver is not installed or PowerShell needs to be reopened to pick up the new PATH.

### 2.2 uv and Python

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen PowerShell, then:

```powershell
uv --version
uv python install 3.12
```

### 2.3 A dedicated virtual environment

Keep the server in its own folder so its PyTorch does not interfere with anything else:

```powershell
mkdir $HOME\omnivoice
cd $HOME\omnivoice
uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
```

If activation fails with a message about execution policies, run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

and try again. Your prompt now starts with `(omnivoice)` or `(.venv)`.

### 2.4 PyTorch for CUDA

PyTorch publishes one wheel index per CUDA version. Pick the index whose number is **at or below**
the `CUDA Version` shown by `nvidia-smi`:

| `nvidia-smi` shows | Index to use |
|---|---|
| 13.2 or higher | `https://download.pytorch.org/whl/cu132` |
| 13.0 or 13.1 | `https://download.pytorch.org/whl/cu130` |
| 12.6 to 12.9 | `https://download.pytorch.org/whl/cu126` |
| lower than 12.6 | update the driver first |

*Version-dependent:* the set of indexes changes with every PyTorch release. If one of these gives a
"no matching distribution" error, the current list is at https://pytorch.org/get-started/locally/.

Then install (example for a 13.x driver):

```powershell
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu130
```

This downloads about 3 GB. Check that PyTorch sees the GPU:

```powershell
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Expected output is something like `2.14.0+cu130 True NVIDIA GeForce RTX 4070`. If it prints `False`,
the wheel is a CPU build (the version has no `+cu…` suffix) or the driver is older than the CUDA
version you picked: uninstall with `uv pip uninstall torch torchaudio` and retry with a lower index.

### 2.5 omnivoice-server

With the environment still active:

```powershell
uv pip install omnivoice-server
omnivoice-server --help
```

`uv pip` respects the PyTorch you installed above and does not replace it with a CPU build.

### 2.6 Allow the port through Windows Firewall

Only needed if Voxello runs on a different machine. Run PowerShell **as administrator**:

```powershell
New-NetFirewallRule -DisplayName "omnivoice-server" -Direction Inbound -Protocol TCP -LocalPort 8880 -Action Allow -Profile Private
```

Find the PC's address for later with `ipconfig` (look for "IPv4 Address" on your Wi-Fi or Ethernet
adapter).

## 3. macOS with Apple Silicon

All commands run in **Terminal** (Applications > Utilities, or search "Terminal" in Spotlight).

### 3.1 Command Line Tools

Some Python packages need a compiler. Install Apple's tools once:

```bash
xcode-select --install
```

A dialog opens; accept and wait for the download. If it says the tools are already installed, you
are done.

### 3.2 uv and Python

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen Terminal, then:

```bash
uv --version
uv python install 3.12
```

### 3.3 A dedicated virtual environment

```bash
mkdir -p ~/omnivoice
cd ~/omnivoice
uv venv --python 3.12
source .venv/bin/activate
```

### 3.4 PyTorch

On macOS there is only one PyTorch build, and it comes from the regular PyPI index:

```bash
uv pip install torch torchaudio
python -c "import torch; print(torch.__version__, torch.backends.mps.is_available())"
```

Expected: `2.14.0 True`. `True` means the Metal (MPS) backend is available, but see the next note.

**About MPS on Apple Silicon.** The upstream `omnivoice-server` README lists `--device mps` as
broken ("use CPU instead"). Start with `--device cpu`, which works and is fast enough for short
sentences (a one-sentence notification takes a few seconds on an M-series chip). You can try
`--device mps` later; if the server crashes or produces silence, go back to `cpu`.

### 3.5 omnivoice-server

```bash
uv pip install omnivoice-server
omnivoice-server --help
```

### 3.6 Network access

macOS has no inbound firewall by default. If you enabled it (System Settings > Network > Firewall),
the first start of the server prompts you to allow incoming connections; click Allow. Find the Mac's
address for later with:

```bash
ipconfig getifaddr en0        # Wi-Fi; try en1 for Ethernet
```

## 4. Start the server

Activate the environment first if you opened a new terminal (`.\.venv\Scripts\Activate.ps1` on
Windows, `source .venv/bin/activate` on macOS). Then:

```bash
# Windows with NVIDIA GPU
omnivoice-server --host 0.0.0.0 --port 8880 --device cuda

# macOS with Apple Silicon
omnivoice-server --host 0.0.0.0 --port 8880 --device cpu
```

What the flags mean:

- `--host 0.0.0.0` accepts connections from other machines on your network. Use `127.0.0.1`
  instead if Voxello runs on the same machine and you want the server to be local only.
- `--port 8880` is the default; change it if something else uses that port.
- `--device` selects the compute device: `cuda` for NVIDIA, `cpu` on macOS (see section 3.4).

The **first start** downloads the model weights (a few GB) and takes several minutes. Later starts
take about 10-30 seconds to load the model. The server is ready when the log shows a line like
`Uvicorn running on http://0.0.0.0:8880` and `/health` reports `"ready":true` (next section).

Useful extras (full list in `omnivoice-server-api.md`):

- `--api-key SOMESECRET` requires a bearer token on every request. Strongly recommended when
  binding to `0.0.0.0`. Put the same value in Voxello's `tts.voicestudio.api_key`.
- `--num-step 16` halves synthesis time at a small quality cost (default 32).
- `--max-concurrent 2` limits parallel requests; raise it on a large GPU.

Stop the server with `Ctrl+C`. To keep it running unattended, use Task Scheduler on Windows or a
`launchd` agent on macOS; both are outside the scope of this tutorial.

## 5. Test with curl

Replace `127.0.0.1` with the server's network address if you are testing from another machine.

### 5.1 Health check

macOS / Linux:

```bash
curl http://127.0.0.1:8880/health
```

Windows PowerShell (note `curl.exe`: plain `curl` in PowerShell is an alias for
`Invoke-WebRequest` and behaves differently):

```powershell
curl.exe http://127.0.0.1:8880/health
```

Expected: `{"status":"healthy","ready":true,"model_loaded":true,...}`. If `ready` is `false`, the
model is still loading; wait and retry.

### 5.2 Synthesize a sentence

macOS / Linux:

```bash
curl -X POST http://127.0.0.1:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model": "omnivoice", "input": "Hello from omnivoice-server.", "response_format": "wav"}' \
  --output speech.wav
afplay speech.wav
```

Windows PowerShell. JSON quoting is the tricky part, so write the body to a variable first:

```powershell
$body = '{"model": "omnivoice", "input": "Hello from omnivoice-server.", "response_format": "wav"}'
curl.exe -X POST http://127.0.0.1:8880/v1/audio/speech -H "Content-Type: application/json" -d $body --output speech.wav
Start-Process speech.wav
```

`Start-Process` opens the file in your default media player. A one-sentence WAV is about 200-400 KB.
If the file is tiny (under 1 KB), it contains an error message instead of audio: open it with
`Get-Content speech.wav` (Windows) or `cat speech.wav` (macOS) to read it. A 422 usually means an
invalid field, and a 401 means the server has `--api-key` set and you did not pass it.

With an API key, add the header to either command:

```
-H "Authorization: Bearer SOMESECRET"
```

## 6. Point Voxello at the server

On the machine where the AI agent runs, edit Voxello's config (`voxello config path` prints its
location):

```yaml
tts:
  provider: voicestudio          # this provider name covers omnivoice-server too
  voicestudio:
    base_url: http://192.168.1.144:8880    # the server's address; 127.0.0.1 if local
    # api_key: SOMESECRET        # only if the server was started with --api-key
    engine: omnivoice
    language: en                 # or it, de, fr, ...
```

Leave `voice` unset so the server default is used. Then:

```bash
voxello doctor
voxello speak "Voxello is connected."
```

`doctor` reports the server health, the model id and the available voices. Everything else about
configuring Voxello (voices, cache, the Claude Code hook) is in the [README](../README.md) and in
`omnivoice-server-api.md`.

## 7. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `nvidia-smi` not found | Driver not installed, or PowerShell opened before the install. Reinstall the driver and reopen the terminal. |
| `torch.cuda.is_available()` is `False` | CPU wheel installed (version without `+cu…`), or the CUDA index is newer than the driver supports. Uninstall torch and reinstall from a lower index. |
| `CUDA out of memory` at first request | GPU too small for the defaults. Try `--max-concurrent 1` and `--num-step 16`, and close other GPU applications. |
| Server crashes or outputs silence with `--device mps` | Known upstream issue on Apple Silicon. Use `--device cpu`. |
| Model download fails or hangs | HuggingFace unreachable. Check the connection or a proxy; set `HF_HOME` to a disk with space if the default drive is full. |
| `curl.exe` works locally but not from another machine | Server bound to `127.0.0.1` instead of `0.0.0.0`, or the firewall blocks port 8880 (section 2.6 / 3.6). |
| Voxello reports `tts_provider_unavailable` | Wrong `base_url`, server not started, or blocked port. `curl http://HOST:8880/health` from the Voxello machine tells which. |
| Voxello reports `tts_provider_unauthorized` | Server started with `--api-key`; set the same value in `tts.voicestudio.api_key`. |
| Voxello reports `voice_not_found` | `voice` set to a name the server does not know (for example VoiceStudio's `default`). Remove it or pick one from `voxello doctor`. |
