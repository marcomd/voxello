"""Guided recording (roadmap 8.2): reading scripts, the ffmpeg recorder and the session loop."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from voxello.errors import (
    INVALID_PARAMETER,
    RECORDER_UNAVAILABLE,
    RECORDING_FAILED,
    VoxelloError,
)
from voxello.recording.ffmpeg import (
    CLEANUP_FILTER,
    FfmpegRecorder,
    capture_input,
    detect_recorder,
)
from voxello.recording.registers import (
    NEUTRAL,
    ReadingScript,
    available_registers,
    profile_name,
    reading_script,
)
from voxello.recording.session import Clip, GuidedRecording

from .conftest import make_wav

# -- reading scripts -------------------------------------------------------------------------


def test_every_language_has_the_same_registers_with_hint_and_text():
    italian, english = available_registers("it"), available_registers("en")
    assert set(italian) == set(english) == {"neutral", "whisper", "sad", "angry", "happy"}
    for script in [*italian.values(), *english.values()]:
        assert script.hint.strip() and script.text.strip()
        assert 60 <= len(script.text) <= 160, "about 6-9 s of reading, inside the 3-10 s window"


def test_reading_script_comes_from_the_language_file():
    script = reading_script("whisper", "it")
    assert script.register == "whisper"
    assert script == available_registers("it")["whisper"]


def test_custom_text_replaces_the_reading_but_keeps_the_hint():
    script = reading_script("whisper", "it", text="  Parla piano,   per favore.  ")
    assert script.text == "Parla piano, per favore."
    assert script.hint == available_registers("it")["whisper"].hint


@pytest.mark.parametrize(("register", "language"), [("sarcastic", "it"), ("whisper", "fr")])
def test_unknown_register_or_language_needs_a_text(register, language):
    with pytest.raises(VoxelloError) as exc:
        reading_script(register, language)
    assert exc.value.code == INVALID_PARAMETER
    assert "--text" in exc.value.message
    script = reading_script(register, language, text="Una frase qualsiasi da leggere.")
    assert script == ReadingScript(register, "", "Una frase qualsiasi da leggere.")


@pytest.mark.parametrize("register", ["Whisper", "two words", "", "a" * 30, "x-y"])
def test_register_names_are_short_lowercase_identifiers(register):
    with pytest.raises(VoxelloError) as exc:
        reading_script(register, "it", text="Testo.")
    assert exc.value.code == INVALID_PARAMETER


def test_blank_custom_text_is_rejected():
    with pytest.raises(VoxelloError) as exc:
        reading_script("whisper", "it", text="   ")
    assert exc.value.code == INVALID_PARAMETER


def test_profile_name_appends_the_register_except_neutral():
    assert profile_name("marco", NEUTRAL) == "marco"
    assert profile_name("clone:marco", "whisper") == "marco-whisper"
    with pytest.raises(VoxelloError) as exc:
        profile_name("m" * 60, "whisper")
    assert exc.value.code == INVALID_PARAMETER


# -- ffmpeg recorder -------------------------------------------------------------------------


def no_tools(_name: str) -> str | None:
    return None


@pytest.mark.parametrize(
    ("platform", "device", "which", "expected"),
    [
        ("darwin", None, no_tools, ["-f", "avfoundation", "-i", ":0"]),
        ("darwin", "1", no_tools, ["-f", "avfoundation", "-i", ":1"]),
        ("darwin", ":2", no_tools, ["-f", "avfoundation", "-i", ":2"]),
        ("linux", None, lambda n: f"/usr/bin/{n}", ["-f", "pulse", "-i", "default"]),
        ("linux", None, no_tools, ["-f", "alsa", "-i", "default"]),
        ("linux", "hw:1", no_tools, ["-f", "alsa", "-i", "hw:1"]),
        ("linux", "mic", lambda n: f"/usr/bin/{n}", ["-f", "pulse", "-i", "mic"]),
    ],
)
def test_capture_input_per_platform(platform, device, which, expected):
    assert capture_input(platform, device, which) == expected


def test_windows_recording_is_not_supported_yet():
    with pytest.raises(VoxelloError) as exc:
        capture_input("win32", None, no_tools)
    assert exc.value.code == RECORDER_UNAVAILABLE


def test_detect_recorder_needs_ffmpeg():
    with pytest.raises(VoxelloError) as exc:
        detect_recorder(platform="darwin", which=no_tools)
    assert exc.value.code == RECORDER_UNAVAILABLE
    assert "ffmpeg" in exc.value.message
    recorder = detect_recorder(platform="darwin", which=lambda n: f"/opt/bin/{n}")
    assert recorder.ffmpeg == "/opt/bin/ffmpeg"
    assert recorder.describe() == "ffmpeg (avfoundation :0)"


def make_recorder(**kwargs) -> FfmpegRecorder:
    return FfmpegRecorder("/bin/ffmpeg", ["-f", "avfoundation", "-i", ":0"], "darwin", **kwargs)


def test_capture_and_cleanup_commands_are_argument_lists(tmp_path: Path):
    recorder = make_recorder()
    out = tmp_path / "a b.wav"
    capture = recorder.capture_command(out, 15)
    assert capture[0] == "/bin/ffmpeg" and capture[-1] == str(out)
    assert capture[capture.index("-t") + 1] == "15"
    assert capture[capture.index("-ar") + 1] == "24000"
    assert capture[capture.index("-ac") + 1] == "1"
    start = capture.index("-f")
    assert capture[start : start + 4] == ["-f", "avfoundation", "-i", ":0"]
    prepare = recorder.prepare_command(tmp_path / "raw.wav", out)
    assert prepare[prepare.index("-i") + 1] == str(tmp_path / "raw.wav")
    assert prepare[prepare.index("-af") + 1] == CLEANUP_FILTER
    assert prepare[prepare.index("-c:a") + 1] == "pcm_s16le"
    assert prepare[-1] == str(out)


class FakeProcess:
    def __init__(self, returncode: int = 0, stderr: bytes = b"", hang: bool = False) -> None:
        self.returncode: int | None = None
        self._final = returncode
        self._stderr = stderr
        self._hang = hang
        self.inputs: list[bytes | None] = []
        self.killed = False

    def communicate(self, input=None, timeout=None):
        self.inputs.append(input)
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        self.returncode = self._final
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True

    def poll(self) -> int | None:
        return self.returncode


def test_stop_sends_q_and_waits(tmp_path: Path):
    started: list[tuple[list[str], dict]] = []
    process = FakeProcess()

    def popen(argv, **kwargs):
        started.append((argv, kwargs))
        return process

    recording = make_recorder(popen=popen).start(tmp_path / "raw.wav", 15)
    assert started[0][1]["stdin"] == subprocess.PIPE
    recording.stop()
    assert process.inputs == [b"q"]


def test_failed_capture_explains_the_microphone_permission(tmp_path: Path):
    process = FakeProcess(returncode=1, stderr=b"Input/output error\n")
    recording = make_recorder(popen=lambda argv, **kw: process).start(tmp_path / "raw.wav", 15)
    with pytest.raises(VoxelloError) as exc:
        recording.stop()
    assert exc.value.code == RECORDING_FAILED
    assert "Input/output error" in exc.value.message
    assert "Microphone" in exc.value.message


def test_a_hanging_ffmpeg_is_killed(tmp_path: Path):
    process = FakeProcess(hang=True)
    recording = make_recorder(popen=lambda argv, **kw: process).start(tmp_path / "raw.wav", 15)
    recording.stop()
    assert process.killed


def test_abort_never_raises(tmp_path: Path):
    process = FakeProcess(returncode=255, stderr=b"Exiting normally, received signal 2.")
    recording = make_recorder(popen=lambda argv, **kw: process).start(tmp_path / "raw.wav", 15)
    recording.abort()
    assert process.inputs == [b"q"]


def test_ffmpeg_that_cannot_start_is_unavailable(tmp_path: Path):
    def popen(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    with pytest.raises(VoxelloError) as exc:
        make_recorder(popen=popen).start(tmp_path / "raw.wav", 15)
    assert exc.value.code == RECORDER_UNAVAILABLE


def test_prepare_reports_ffmpeg_errors(tmp_path: Path):
    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, b"", b"Invalid data found\n")

    with pytest.raises(VoxelloError) as exc:
        make_recorder(run=failing).prepare(tmp_path / "raw.wav", tmp_path / "out.wav")
    assert exc.value.code == RECORDING_FAILED
    assert "Invalid data found" in exc.value.message

    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    with pytest.raises(VoxelloError) as exc:
        make_recorder(run=missing).prepare(tmp_path / "raw.wav", tmp_path / "out.wav")
    assert exc.value.code == RECORDER_UNAVAILABLE


def test_prepare_runs_the_cleanup_command(tmp_path: Path):
    seen: list[list[str]] = []

    def ok(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    recorder = make_recorder(run=ok)
    recorder.prepare(tmp_path / "raw.wav", tmp_path / "out.wav")
    assert seen == [recorder.prepare_command(tmp_path / "raw.wav", tmp_path / "out.wav")]


# -- guided session --------------------------------------------------------------------------


class FakeRecording:
    def __init__(self, recorder: FakeRecorder, output: Path, duration_ms: int) -> None:
        self.recorder, self.output, self.duration_ms = recorder, output, duration_ms

    def stop(self) -> None:
        self.recorder.stopped += 1
        self.output.write_bytes(make_wav(self.duration_ms))

    def abort(self) -> None:
        self.recorder.aborted += 1


class FakeRecorder:
    name = "fake"

    def __init__(self, *durations_ms: int, prepared: bytes | None = None) -> None:
        self.durations = list(durations_ms) or [5000]
        self.started = self.stopped = self.aborted = 0
        self.raw_paths: list[Path] = []
        self.prepared_bytes = prepared

    def start(self, output: Path, max_seconds: float) -> FakeRecording:
        self.started += 1
        self.raw_paths.append(output)
        duration = self.durations[min(self.started, len(self.durations)) - 1]
        return FakeRecording(self, output, duration)

    def prepare(self, raw: Path, output: Path) -> None:
        output.write_bytes(self.prepared_bytes or raw.read_bytes())

    def describe(self) -> str:
        return "fake"


class ScriptedTerminal:
    def __init__(self, *answers: str | BaseException) -> None:
        self.answers = list(answers)
        self.said: list[str] = []
        self.prompts: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


SCRIPT = ReadingScript("whisper", "Sottovoce.", "Non fare rumore, dormono tutti.")


def run_session(
    tmp_path: Path, recorder: FakeRecorder, *answers: str | BaseException
) -> tuple[Clip | None, ScriptedTerminal, list[Path]]:
    terminal = ScriptedTerminal(*answers)
    played: list[Path] = []
    session = GuidedRecording(recorder, played.append, terminal, sleep=lambda _s: None)
    return session.run(SCRIPT, tmp_path / "marco-whisper.wav"), terminal, played


def test_session_records_cleans_plays_back_and_accepts(tmp_path: Path):
    recorder = FakeRecorder(6000)
    clip, terminal, played = run_session(tmp_path, recorder, "", "", "a")
    assert clip == Clip(tmp_path / "marco-whisper.wav", 6.0, [])
    assert clip.path.exists()
    assert played == [clip.path]
    assert recorder.started == recorder.stopped == 1
    assert not recorder.raw_paths[0].exists(), "the raw capture is removed after cleanup"
    shown = "\n".join(terminal.said)
    assert "Sottovoce." in shown and "Non fare rumore, dormono tutti." in shown
    assert "6.0 s" in shown


def test_redo_and_replay(tmp_path: Path):
    recorder = FakeRecorder(1500, 7000)
    clip, terminal, played = run_session(tmp_path, recorder, "", "", "r", "", "", "p", "x", "a")
    assert clip is not None and clip.seconds == 7.0
    assert recorder.started == 2
    assert len(played) == 3, "first take, second take, replay of the second"
    assert any("1.5 s" in line and "3-10 s" in line for line in terminal.said)


def test_short_clip_carries_the_duration_warning(tmp_path: Path):
    clip, _, _ = run_session(tmp_path, FakeRecorder(2000), "", "", "a")
    assert clip is not None and clip.warnings and "2.0 s" in clip.warnings[0]


@pytest.mark.parametrize("answers", [("q",), ("", "", "q")])
def test_quitting_records_nothing(tmp_path: Path, answers):
    clip, _, _ = run_session(tmp_path, FakeRecorder(), *answers)
    assert clip is None
    assert not (tmp_path / "marco-whisper.wav").exists()
    assert list(tmp_path.iterdir()) == []


def test_interrupt_while_recording_stops_ffmpeg_and_cleans_up(tmp_path: Path):
    recorder = FakeRecorder()
    with pytest.raises(KeyboardInterrupt):
        run_session(tmp_path, recorder, "", KeyboardInterrupt())
    assert recorder.aborted == 1 and recorder.stopped == 0
    assert list(tmp_path.iterdir()) == []


def test_unreadable_cleanup_output_is_a_recording_failure(tmp_path: Path):
    recorder = FakeRecorder(prepared=b"not a wav")
    with pytest.raises(VoxelloError) as exc:
        run_session(tmp_path, recorder, "", "")
    assert exc.value.code == RECORDING_FAILED
    assert list(tmp_path.iterdir()) == []


# -- real ffmpeg (opt-in) --------------------------------------------------------------------


@pytest.mark.integration
def test_cleanup_with_the_real_ffmpeg(tmp_path: Path):
    import array
    import math
    import shutil
    import wave

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    raw = tmp_path / "raw.wav"
    rate = 48000
    silence = [0] * rate  # 1 s of silence on each side of a 2 s tone
    tone = [int(8000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(2 * rate)]
    with wave.open(str(raw), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(array.array("h", silence + tone + silence).tobytes())
    out = tmp_path / "out.wav"
    detect_recorder().prepare(raw, out)
    with wave.open(str(out), "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 24000)
        assert 1.8 <= wf.getnframes() / 24000 <= 2.6, "leading and trailing silence trimmed"
