# Windows manual test checklist

Voxello's Windows support (PowerShell playback, WinRT toast notifications) is implemented and
unit-tested for command construction, and the unit suite runs on `windows-latest` in CI with
fakes. It has **not yet been exercised with real audio on a Windows machine**. This checklist is
the procedure to do that; fill in the results table at the end and link it from
`docs/roadmap.md` when done.

What the code does on Windows:

- Playback: `powershell -NoProfile -NonInteractive -Command "(New-Object Media.SoundPlayer
  '<path>').PlaySync()"` (`src/voxello/playback/backends.py`). Windows PowerShell 5.1 is
  preferred, PowerShell 7 (`pwsh`) is the fallback. `System.Media.SoundPlayer` plays WAV only
  and has no volume control, so `playback.volume` is ignored and `doctor` prints no `volume=`.
- Notifications: a `ToastText02` WinRT toast sent from Windows PowerShell 5.1
  (`src/voxello/notifications/desktop.py`); title and body travel as environment variables.
  `pwsh` is not used for toasts because the WinRT type projection is unavailable there.
- Interrupt: `stop_speaking` terminates the PowerShell host process (`TerminateProcess`) while
  `PlaySync()` is running.

## Prerequisites

- Windows 10 21H2+ or Windows 11, with sound output and notifications enabled (Focus Assist off
  for the first pass).
- `uv` installed (`winget install astral-sh.uv`), and Python 3.12+ available to uv.
- A reachable TTS server. `docs/TUTORIAL.md` explains how to run `omnivoice-server` on the same
  Windows machine (NVIDIA GPU) or elsewhere on the network.
- Optional: PowerShell 7 (`winget install Microsoft.PowerShell`) for step 6; Git for Windows
  (Git Bash) for step 7.

Record the versions once:

```powershell
winver                                  # Windows build
$PSVersionTable.PSVersion               # in Windows PowerShell 5.1
pwsh -NoProfile -Command '$PSVersionTable.PSVersion'   # PowerShell 7, if installed
uv --version
```

## Steps

Run every command in Windows PowerShell unless stated otherwise.

1. **Install and inspect.**

   ```powershell
   uv tool install voxello
   voxello --version
   $env:VOXELLO_VOICESTUDIO_URL = "http://<host>:8880"
   voxello doctor
   ```

   Expected: `Playback: powershell` (no `volume=` suffix), `Desktop notifications: powershell`,
   `health: ok`, a `voices:` block, `Result: OK`.

2. **Timed synthesis.**

   ```powershell
   voxello doctor --synth -l en
   ```

   Expected: a `synthesis: ok in X.XX s (...)` line and exit code 0 (`$LASTEXITCODE`). Note the
   latency.

3. **Speak and notify.**

   ```powershell
   voxello speak "Build finished. All tests pass." -l en
   voxello notify "Refactoring completato." --channels desktop
   voxello notify "Refactoring completato." --channels voice,desktop
   ```

   Expected: audio plays through the default output device; a toast titled `Voxello` appears.
   Record whether the toast shows with the unregistered app id `Voxello` (the code does not
   register one in the Start menu), and repeat the desktop call with Focus Assist set to
   "Priority only" to record whether it is suppressed silently.

4. **Save and cache.**

   ```powershell
   voxello speak "Salvami" --save            # writes %USERPROFILE%\Voxello\<timestamp>_<id>.wav
   voxello cache list
   voxello notify "Refactoring completato." --channels voice   # second run should be a cache hit
   ```

   Expected: the WAV opens in any player; `cache list` shows the notify phrase; the second
   notify starts noticeably faster (log at `--log-level debug` shows `cache hit`).

5. **Interrupt from the MCP tools.** Register Voxello in Claude Code (`claude mcp add --scope
   user voxello -- voxello serve`), then ask the agent to call `speak` twice in a row with a
   long text and `interrupt: true`, or use two terminals:

   ```powershell
   voxello speak "Uno due tre quattro cinque sei sette otto nove dieci, ancora una volta uno due tre quattro cinque sei sette otto nove dieci."
   ```

   and, while it plays, from the agent: `stop_speaking`. Expected: audio stops within about a
   second and `get_status` returns `idle` with the request in state `cancelled`. Record how
   long the sound continued after the stop.

6. **PowerShell 7 playback.** With `pwsh` installed, force the backend:

   ```powershell
   $env:VOXELLO_PLAYBACK__BACKEND = "pwsh"
   voxello doctor          # Playback: pwsh
   voxello speak "PowerShell seven plays this." -l en
   Remove-Item Env:VOXELLO_PLAYBACK__BACKEND
   ```

   Expected: identical behaviour to step 3. If `New-Object Media.SoundPlayer` fails in pwsh,
   record the error verbatim: the fallback would then need `Add-Type -AssemblyName
   System.Windows.Extensions` or removal.

7. **Claude Code hook.** In Git Bash:

   ```bash
   voxello install claude --yes
   echo '{"notification_type":"idle_prompt"}' | voxello hook notification -l en
   ```

   Expected: the English idle sentence is spoken and a toast appears. Then trigger a real
   Notification event from a Claude Code session and confirm the hook fires.

8. **Failure paths.** Stop the TTS server, then:

   ```powershell
   voxello speak "prova"
   voxello notify "Refactoring completato." --channels voice   # cached in step 4
   ```

   Expected: the first prints `error: tts_provider_unavailable` after one retry (about
   2 × `connect_timeout_seconds` + `retry_backoff_seconds`); the second still plays from the
   cache.

## Results

| Date | Windows build | PowerShell 5.1 / 7 | Step | Result | Notes |
|------|---------------|--------------------|------|--------|-------|
|      |               |                    | 1    |        |       |
|      |               |                    | 2    |        |       |
|      |               |                    | 3    |        |       |
|      |               |                    | 4    |        |       |
|      |               |                    | 5    |        |       |
|      |               |                    | 6    |        |       |
|      |               |                    | 7    |        |       |
|      |               |                    | 8    |        |       |

Open a GitHub issue labelled `type:bug` plus `area:playback` or `area:notifications` for
anything that fails, quoting the exact PowerShell error text.
