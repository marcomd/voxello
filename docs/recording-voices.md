# Record a voice, step by step

This guide takes you from a microphone to a set of cloned voices that Voxello can use: one
for each way a person speaks (normal, whispering, sad, angry, happy). It takes about ten
minutes per person.

Why several voices for one person? OmniVoice, the model behind the server, cannot be told
"say this sadly". It copies the tone of the recording it was cloned from instead. So the
recording you make in a whisper becomes a voice that whispers, and so on. Each of these is
called a **register** and becomes a cloned voice of its own:

| Register | Cloned voice | How to read it |
|---|---|---|
| `neutral` | `clone:francesca` | Plainly, as if reading a message to a friend. This is the main voice. |
| `whisper` | `clone:francesca-whisper` | Under your breath, as if someone were asleep next door. |
| `sad` | `clone:francesca-sad` | Slow and tired, with little energy. |
| `angry` | `clone:francesca-angry` | Fed up but not shouting. Lean on the words. |
| `happy` | `clone:francesca-happy` | Smiling while you speak. |

You do not have to remember any of this: the command shows each register in turn, with
the sentence to read and a hint on the tone.

> Only record and clone a voice with the speaker's permission. The OmniVoice model is
> licensed for non-commercial use only.

## Before you start

Run `voxello doctor` and check two lines:

```
  health: ok (model_id=k2-fsa/OmniVoice)
Recorder: ffmpeg (avfoundation :0)
```

- **`health: ok`** means the TTS server (omnivoice-server) is running. If it is not, see
  [TUTORIAL.md](TUTORIAL.md).
- **`Recorder: ffmpeg ...`** means Voxello can record. If it says `unavailable`, install ffmpeg
  (`brew install ffmpeg` on macOS, `sudo apt install ffmpeg` on Debian or Ubuntu) and run
  `voxello doctor` again. Recording works on macOS and Linux, not yet on Windows.

Then prepare the room:

- a quiet room, with no music or TV, and notifications silenced;
- the microphone 20-30 cm from the mouth (a headset or the laptop's built-in mic both work);
- the person whose voice you record sitting at the keyboard, or you next to them.

## Step 1: start the session

Open your own terminal (not the agent's chat) and run:

```bash
voxello voice record francesca --all
```

Replace `francesca` with the name you want; it becomes `clone:francesca`,
`clone:francesca-whisper` and so on. For English sentences add `-l en`.

**macOS only, first time:** macOS asks whether your terminal may use the microphone.
Click **Allow**. If you clicked Don't Allow, see [Troubleshooting](#troubleshooting).

## Step 2: confirm permission

```
Record and clone the voices francesca, francesca-whisper, francesca-sad, francesca-angry,
francesca-happy? Confirm the speaker has given permission. [y/N]
```

Type `y` and press Enter.

If some of these voices already exist on the server, you get one more question:

```
Already cloned: francesca. Record them again and replace them? [y/N]
```

- `y` records them again and replaces them.
- `n` keeps them and records only the missing registers. This is also how you resume a
  session you stopped halfway.

## Step 3: record each register

For each register the command shows the tone and the sentence to read:

```
[2/5] whisper -> clone:francesca-whisper

== whisper: Sottovoce, come se qualcuno dormisse nella stanza accanto.

    Non fare rumore, dormono tutti. Prendo le chiavi e usciamo piano, senza accendere la luce.

Read it exactly as written: it becomes the transcript of the cloned voice.
Enter to record, s skip, q quit >
```

1. Read the sentence once in your head.
2. Press **Enter**. A countdown runs: `3... 2... 1...`
3. When you see `Recording`, read the sentence **exactly as written**, in the tone of the hint.
4. Press **Enter** as soon as you finish. (It stops by itself after 15 seconds.)
5. The clip is cleaned up and played back to you. You see how long it is, for example
   `7.8 s after cleanup.`
6. Choose:

| Key | What it does |
|---|---|
| `a` | Accept. The voice is cloned on the server right away, then the next register starts. |
| `r` | Record this register again. |
| `p` | Play the clip again. |
| `s` | Skip this register and go to the next one. |
| `q` | Stop the session. Registers already accepted stay cloned. |

Tips for a good clip:

- Aim for 6-9 seconds. If the clip is shorter than 3 or longer than 10 seconds you get a
  warning; press `r` and read it again.
- Do not change a word: what you read must match the sentence on screen.
- For `angry` and `happy`, exaggerate a little. The cloned voice softens the emotion, and in
  our tests these two came out weaker than `whisper` and `sad`.
- A mistake, a cough or a laugh? Press `r`.

## Step 4: read the summary

When the last register is done you get a summary:

```
Summary:
  neutral  clone:francesca          created
  whisper  clone:francesca-whisper  created
  sad      clone:francesca-sad      created
  angry    clone:francesca-angry    created
  happy    clone:francesca-happy    skipped
```

- `created`: the voice is ready.
- `skipped` or `not recorded`: run the same command again and answer `n` to the replace
  question. Only the missing registers are recorded.
- `upload failed`: the clip is saved and the error message shows the exact
  `voxello voice clone ...` command to upload it again once the server is back.

## Step 5: listen to the result

Play the same sentence with every voice:

```bash
for v in francesca francesca-whisper francesca-sad francesca-angry francesca-happy; do
  voxello speak "Era una domanda. Non un invito a decidere la giornata." --voice clone:$v
done
```

If a register does not sound right, record just that one again (next section).

## Redo or add one register

```bash
voxello voice record francesca --register happy --overwrite    # record happy again
voxello voice record francesca                                 # the neutral voice only
voxello voice record francesca -r tired --text "Una frase da leggere con quel tono."
```

The last line creates a register of your own (`clone:francesca-tired`); a register that is not
built in needs the sentence to read with `--text`.

## Clone a recording you already have

A voice recorded elsewhere (a phone memo, a longer reading) does not need `voice record`:
`--prepare` applies the same cleanup (high-pass, silence trimmed, loudness, 24 kHz mono) and
`--start`/`--duration` pick the 3-10 s to clone. Pass the words spoken in that window:

```bash
voxello voice clone narratore ~/lettura.m4a --prepare --start 12.5 --duration 8 \
  --ref-text "Le parole dette in quegli otto secondi."
```

The clean clip and a `narratore.json` sidecar (source file, window, transcript) stay in the
recordings directory, like the clips `voice record` keeps.

## Use the voices

Pass a voice wherever Voxello accepts one:

```bash
voxello speak "Ci vediamo dopo." --voice clone:francesca-whisper
```

or set it in `config.yaml`, for example as the voice of the agent's spoken summaries:

```yaml
integrations:
  summaries:
    voice: clone:francesca
```

## Where the clips are

Every accepted clip is kept on your computer with a small JSON file holding the sentence
that was read, so you can clone it again on another server without recording again:

- macOS: `~/Library/Application Support/voxello/voices/`
- Linux: `~/.local/share/voxello/voices/`

Change the folder with `storage.recordings_dir` in `config.yaml`. Add `--no-keep` to delete
each clip once it is cloned.

## Troubleshooting

| What you see | What to do |
|---|---|
| `recorder_unavailable: Recording needs ffmpeg` | Install ffmpeg (see [Before you start](#before-you-start)). |
| `recorder_unavailable: ... needs an interactive terminal` | Run the command in your own terminal, not through an agent. |
| `recording_failed: ffmpeg could not record ... Input/output error` (macOS) | System Settings > Privacy & Security > Microphone: turn on your terminal app, quit the terminal and open it again. |
| The playback is silent or comes from the wrong microphone | List the microphones with `ffmpeg -f avfoundation -list_devices true -i ""` (macOS) and pass the number of the right one: `--device 1`. On Linux pass a PulseAudio or ALSA source name. |
| `The clip lasts 2.1 s; OmniVoice clones best from 3-10 s.` | Press `r`; read the whole sentence before pressing Enter. |
| `tts_provider_unavailable` after accepting | The server is down. The clip is kept; start the server and run the `voxello voice clone ...` command printed below the error. |
| The cloned voice sounds flat | Record that register again and exaggerate the tone a little. |
