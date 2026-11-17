"""Per-project overrides: ``.voxello.yaml`` in the project, layered over the user config."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from voxello.config import (
    PROJECT_CONFIG_NAME,
    find_project_config,
    load_project_config,
    load_settings,
    project_start_dir,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for var in ("VOXELLO_PROJECT_DIR", "CLAUDE_PROJECT_DIR", "VOXELLO_CONFIG", "VOXELLO_LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    # Path.home() reads HOME on POSIX but USERPROFILE on Windows: patch it directly.
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


def project(root: Path, text: str, *, git: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if git:
        (root / ".git").mkdir(exist_ok=True)
    (root / PROJECT_CONFIG_NAME).write_text(text, encoding="utf-8")
    return root


def user_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "user.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# -- finding the file -------------------------------------------------------------------


def test_found_in_the_start_dir_or_a_parent_up_to_the_repository_root(tmp_path: Path):
    root = project(tmp_path / "home" / "alfa", "speech: {default_language: en}\n")
    (root / "src" / "pkg").mkdir(parents=True)
    assert find_project_config(root / "src" / "pkg") == root / PROJECT_CONFIG_NAME
    assert find_project_config(root) == root / PROJECT_CONFIG_NAME


def test_search_stops_at_the_repository_root(tmp_path: Path):
    project(tmp_path / "home" / "work", "speech: {default_language: en}\n", git=False)
    inner = tmp_path / "home" / "work" / "beta"
    (inner / ".git").mkdir(parents=True)
    assert find_project_config(inner / "sub") is None, "never looks above the repository root"
    assert find_project_config(inner) is None


def test_home_directory_is_never_a_project(tmp_path: Path):
    home = tmp_path / "home"
    project(home, "speech: {default_language: en}\n", git=False)
    (home / "scratch").mkdir()
    assert find_project_config(home / "scratch") is None
    assert find_project_config(home) is None


def test_start_dir_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    assert project_start_dir() == tmp_path
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "claude"))
    assert project_start_dir() == tmp_path / "claude"
    assert project_start_dir(tmp_path / "payload") == tmp_path / "payload", "hook cwd first"
    monkeypatch.setenv("VOXELLO_PROJECT_DIR", str(tmp_path / "explicit"))
    assert project_start_dir(tmp_path / "payload") == tmp_path / "explicit"


# -- what a project may set -------------------------------------------------------------


def test_only_voice_and_notification_keys_are_taken(tmp_path: Path):
    root = project(
        tmp_path / "home" / "alfa",
        "integrations:\n  summaries: {voice: clone:marco}\n"
        "speech:\n  default_language: en\n  voices_by_language: {it: clone:marco}\n"
        "notifications: {title: Alfa, desktop: false}\n"
        "tts:\n  voicestudio: {base_url: 'http://evil.example:8880'}\n"
        "logging: {log_text: true}\n",
    )
    loaded = load_project_config(root)
    assert loaded.path == root / PROJECT_CONFIG_NAME
    assert loaded.ignored == ["logging", "notifications.desktop", "tts"]
    assert set(loaded.data) == {"integrations", "speech", "notifications"}
    assert loaded.data["notifications"] == {"title": "Alfa"}


def test_no_project_file(tmp_path: Path):
    loaded = load_project_config(tmp_path / "nowhere")
    assert loaded.path is None and loaded.data == {} and loaded.ignored == []


def test_invalid_project_file_names_it(tmp_path: Path):
    root = project(tmp_path / "home" / "alfa", "- just\n- a list\n")
    with pytest.raises(ValueError, match=re.escape(str(root / PROJECT_CONFIG_NAME))):
        load_project_config(root)


# -- layering ---------------------------------------------------------------------------


def test_project_overrides_the_user_config_and_env_overrides_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    user = user_config(
        tmp_path,
        "tts:\n  voicestudio: {base_url: 'http://tts.lan:8880'}\n"
        "speech:\n  default_language: it\n  voice_designs: {narrator: female}\n"
        "integrations:\n  phrases: {mode: cached, voice: narrator}\n"
        "  summaries: {mode: always}\n",
    )
    root = project(
        tmp_path / "home" / "beta",
        "integrations:\n  phrases: {instructions: 'female, young adult'}\n"
        "  summaries: {voice: clone:marco}\n"
        "notifications: {title: Beta}\n",
    )
    settings = load_settings(user, project_dir=root)
    # A voice set in the project replaces the user's voice or instructions for that role.
    assert settings.integrations.phrases.voice is None
    assert settings.integrations.phrases.instructions == "female, young adult"
    assert settings.integrations.phrases.mode == "cached", "untouched keys are kept"
    assert settings.integrations.summaries.voice == "clone:marco"
    assert settings.integrations.summaries.mode == "always"
    assert settings.notifications.title == "Beta"
    assert settings.speech.voice_designs == {"narrator": "female"}
    assert settings.tts.voicestudio.base_url == "http://tts.lan:8880"

    monkeypatch.setenv("VOXELLO_INTEGRATIONS__SUMMARIES__VOICE", "clone:zoe")
    assert load_settings(user, project_dir=root).integrations.summaries.voice == "clone:zoe"


def test_two_projects_two_voices(tmp_path: Path):
    user = user_config(tmp_path, "integrations:\n  summaries: {mode: always}\n")
    alfa = project(tmp_path / "home" / "alfa", "integrations:\n  summaries: {voice: clone:marco}\n")
    beta = project(
        tmp_path / "home" / "beta", "integrations:\n  summaries: {instructions: female}\n"
    )
    assert load_settings(user, project_dir=alfa).integrations.summaries.voice == "clone:marco"
    assert load_settings(user, project_dir=beta).integrations.summaries.instructions == "female"
    assert load_settings(user, project_dir=tmp_path / "home").integrations.summaries.voice is None


def test_project_can_be_turned_off(tmp_path: Path):
    root = project(tmp_path / "home" / "alfa", "notifications: {title: Alfa}\n")
    user = user_config(tmp_path, "")
    assert load_settings(user, project_dir=root, use_project=False).notifications.title == (
        "Voxello"
    )


def test_a_section_with_only_disallowed_keys_is_dropped(tmp_path: Path):
    root = project(tmp_path / "home" / "alfa", "notifications: {desktop: false}\nspeech: x\n")
    loaded = load_project_config(root)
    assert loaded.data == {}
    assert loaded.ignored == ["notifications.desktop", "speech"]
