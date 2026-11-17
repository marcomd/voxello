"""Configuration model and loading.

Precedence (highest wins): environment variables > project file (``.voxello.yaml``) >
user YAML config file > defaults. The project file may only set the voice and notification
keys listed in ``PROJECT_KEYS``.

Environment variables use the ``VOXELLO_`` prefix with ``__`` for nesting, e.g.
``VOXELLO_TTS__VOICESTUDIO__BASE_URL``. The flat aliases from the specification are
also honoured: ``VOXELLO_TTS_PROVIDER``, ``VOXELLO_VOICESTUDIO_URL``,
``VOXELLO_VOICESTUDIO_API_KEY``, ``VOXELLO_DEFAULT_VOICE``, ``VOXELLO_LANGUAGE``,
``VOXELLO_LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from platformdirs import user_cache_dir, user_config_dir
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from voxello.core.voice_design import normalize_instructions
from voxello.errors import VoxelloError

log = logging.getLogger(__name__)

APP_NAME = "voxello"
CONFIG_ENV_VAR = "VOXELLO_CONFIG"

# ISO 639-1: two lowercase letters (roadmap 3.1). Shared with the service's validation.
LANGUAGE_PATTERN = r"^[a-z]{2}$"
LANGUAGE_RE = re.compile(LANGUAGE_PATTERN)

# Names of reusable voice designs: the same alphabet omnivoice-server allows for profile ids.
VOICE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Per-project overrides (``.voxello.yaml`` at the project root). Only what shapes the voice
# and the notification may be set there: a cloned repository must not be able to redirect the
# TTS server (and so the spoken text), the logs or the storage. ``None`` allows a whole section.
PROJECT_CONFIG_NAME = ".voxello.yaml"
PROJECT_KEYS: dict[str, frozenset[str] | None] = {
    "integrations": None,
    "speech": frozenset({"default_language", "voices_by_language", "voice_designs"}),
    "notifications": frozenset({"title"}),
}
# Where the project is looked for: an explicit override, then (after the hook payload's
# `cwd`) the project root Claude Code gives its MCP servers and hooks, then the cwd.
PROJECT_DIR_ENV_VAR = "VOXELLO_PROJECT_DIR"
CLAUDE_PROJECT_DIR_ENV_VAR = "CLAUDE_PROJECT_DIR"

# Flat env aliases from the spec -> dotted path in the settings tree.
FLAT_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "VOXELLO_TTS_PROVIDER": ("tts", "provider"),
    "VOXELLO_VOICESTUDIO_URL": ("tts", "voicestudio", "base_url"),
    "VOXELLO_VOICESTUDIO_API_KEY": ("tts", "voicestudio", "api_key"),
    "VOXELLO_DEFAULT_VOICE": ("tts", "voicestudio", "voice"),
    "VOXELLO_LANGUAGE": ("speech", "default_language"),
    "VOXELLO_LOG_LEVEL": ("logging", "level"),
}


class ServerSettings(BaseModel):
    transport: Literal["stdio"] = "stdio"
    name: str = "voxello"


class VoiceStudioSettings(BaseModel):
    base_url: str = "http://localhost:3900"
    api_key: SecretStr | None = Field(
        default=None, description="Bearer token; required when VoiceStudio is on another host."
    )
    voice: str | None = Field(
        default=None,
        description="Voice id sent to the server; omit to use the server's own default "
        "('default' on VoiceStudio, 'auto' on omnivoice-server).",
    )
    engine: str = Field(default="omnivoice", description="VoiceStudio TTS engine ('model' field).")
    language: str | None = Field(
        default=None,
        pattern=LANGUAGE_PATTERN,
        description="Deprecated since 0.3: use speech.default_language. Still honoured as the "
        "default when speech.default_language is not set.",
    )
    speed: float | None = Field(default=None, ge=0.25, le=4.0)
    num_step: int | None = Field(default=None, ge=1, le=128)
    guidance_scale: float | None = Field(default=None, ge=0, le=20)
    timeout_seconds: float = Field(
        default=120, gt=0, description="Read/write timeout of one synthesis request."
    )
    connect_timeout_seconds: float = Field(
        default=5, gt=0, description="Time allowed to open the TCP connection."
    )
    retries: int = Field(
        default=1,
        ge=0,
        le=5,
        description="Extra attempts after a connection failure or an HTTP 502/503/504; "
        "4xx answers and read timeouts are never retried.",
    )
    retry_backoff_seconds: float = Field(
        default=0.5,
        ge=0,
        description="Wait before the first retry; each further retry doubles it.",
    )

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


class TTSSettings(BaseModel):
    provider: Literal["voicestudio"] = "voicestudio"
    voicestudio: VoiceStudioSettings = Field(default_factory=VoiceStudioSettings)


class SpeechSettings(BaseModel):
    """Language defaults and per-language voices (roadmap 3.1 / 3.2)."""

    default_language: str = Field(
        default="it",
        pattern=LANGUAGE_PATTERN,
        description="ISO 639-1 code used when a request does not pass `language`.",
    )
    voices_by_language: dict[str, str] = Field(
        default_factory=dict,
        description="Voice id per language, used when a request passes no explicit voice. "
        "Precedence: request voice > voices_by_language[language] > tts.voicestudio.voice "
        "> server default. A value may name an entry of voice_designs.",
    )
    voice_designs: dict[str, str] = Field(
        default_factory=dict,
        description="Reusable OmniVoice voice designs: name -> comma-separated attributes such "
        "as 'female, young adult, high pitch'. Use the name wherever a voice is accepted.",
    )

    @field_validator("voices_by_language")
    @classmethod
    def _check_map(cls, value: dict[str, str]) -> dict[str, str]:
        for language, voice in value.items():
            if not LANGUAGE_RE.fullmatch(language):
                raise ValueError(
                    f"speech.voices_by_language key '{language}' must be a two-letter ISO 639-1 "
                    "code such as 'it' or 'en'"
                )
            if not voice or not voice.strip():
                raise ValueError(f"speech.voices_by_language['{language}'] must name a voice")
        return value

    @field_validator("voice_designs")
    @classmethod
    def _check_designs(cls, value: dict[str, str]) -> dict[str, str]:
        designs: dict[str, str] = {}
        for name, instructions in value.items():
            if not VOICE_NAME_RE.fullmatch(name):
                raise ValueError(
                    f"speech.voice_designs name '{name}' must be 1-64 letters, digits, '-' or '_'"
                )
            try:
                designs[name] = normalize_instructions(instructions)
            except VoxelloError as exc:
                raise ValueError(f"speech.voice_designs['{name}']: {exc.message}") from exc
        return designs


class _RoleVoiceSettings(BaseModel):
    """Voice of one kind of agent message: a voice name or a voice design, never both."""

    voice: str | None = Field(
        default=None,
        description="Server voice id, clone:<id> or a name from speech.voice_designs. "
        "Wins over speech.voices_by_language for this kind of message.",
    )
    instructions: str | None = Field(
        default=None,
        description="OmniVoice attributes such as 'female, young adult' instead of `voice`.",
    )

    _section: ClassVar[str] = "integrations"

    @field_validator("voice")
    @classmethod
    def _check_voice(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError(f"{cls._section}.voice must name a voice; omit it for the default")
        return value.strip() if value is not None else None

    @field_validator("instructions")
    @classmethod
    def _check_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_instructions(value)
        except VoxelloError as exc:
            raise ValueError(f"{cls._section}.instructions: {exc.message}") from exc

    @model_validator(mode="after")
    def _voice_or_instructions(self) -> _RoleVoiceSettings:
        if self.voice is not None and self.instructions is not None:
            raise ValueError(
                f"{self._section}: set voice or instructions, not both (a cloned voice ignores "
                "the design and a design overrides a preset)"
            )
        return self


class PhrasesSettings(_RoleVoiceSettings):
    """Fixed sentences spoken by the agent hooks (Claude Code Notification, Codex Stop)."""

    _section: ClassVar[str] = "integrations.phrases"

    mode: Literal["cached", "live", "off"] = Field(
        default="cached",
        description="cached: the sentence from the message files, synthesized once; live: the "
        "event's own message, synthesized every time; off: the hooks stay silent.",
    )
    language: str | None = Field(
        default=None,
        pattern=LANGUAGE_PATTERN,
        description="Language of the hook sentences; default speech.default_language. "
        "VOXELLO_HOOK_LANG and --language override it.",
    )
    channels: list[Literal["voice", "desktop", "file"]] | None = Field(
        default=None,
        description="Channels of the hook notifications; default voice and desktop. "
        "VOXELLO_HOOK_CHANNELS and --channels override it.",
    )


class SummariesSettings(_RoleVoiceSettings):
    """Messages the agent writes itself and sends through the MCP tools."""

    _section: ClassVar[str] = "integrations.summaries"

    mode: Literal["on_request", "always"] = Field(
        default="on_request",
        description="on_request: the agent speaks only when the user asks; always: the agent "
        "ends every non-trivial task with a spoken summary.",
    )
    enforce: bool = Field(
        default=False,
        description="With mode 'always', the Stop hook asks the agent for the summary when a "
        "turn ended without one.",
    )
    min_turn_seconds: int = Field(
        default=60, ge=0, description="Turns shorter than this are never asked for a summary."
    )


class IntegrationsSettings(BaseModel):
    """What the agent integrations (Claude Code, Codex) say and with which voice."""

    phrases: PhrasesSettings = Field(default_factory=PhrasesSettings)
    summaries: SummariesSettings = Field(default_factory=SummariesSettings)


class PlaybackSettings(BaseModel):
    enabled: bool = True
    default_interrupt: bool = True
    interrupt_clears_queue: bool = True
    volume: float = Field(default=0.8, ge=0.0, le=1.0)
    max_queue_size: int = Field(default=10, ge=1, le=100)
    backend: str | None = Field(
        default=None, description="Force a player (afplay, mpv, paplay, aplay, ffplay, powershell)."
    )


class NotificationsSettings(BaseModel):
    voice: bool = True
    desktop: bool = True
    title: str = "Voxello"


class StorageSettings(BaseModel):
    temp_dir: Path | None = Field(default=None, description="Defaults to the OS cache dir.")
    temp_retention_minutes: int = Field(default=10, ge=1)
    cleanup_on_start: bool = True

    def resolved_temp_dir(self) -> Path:
        if self.temp_dir is not None:
            return self.temp_dir.expanduser()
        return Path(user_cache_dir(APP_NAME)) / "tmp"


class CacheSettings(BaseModel):
    """Audio cache for phrases that repeat (roadmap milestone 2)."""

    enabled: bool = True
    directory: Path | None = Field(
        default=None, description="Defaults to the OS cache dir, next to the temp dir."
    )
    max_entries: int = Field(default=200, ge=1)
    max_age_days: int = Field(default=90, ge=1)
    min_text_chars: int = Field(default=1, ge=1)
    max_text_chars: int = Field(
        default=300, ge=1, description="Only short phrases repeat; long answers are not cached."
    )

    def resolved_directory(self) -> Path:
        if self.directory is not None:
            return self.directory.expanduser()
        return Path(user_cache_dir(APP_NAME)) / "audio"

    @model_validator(mode="after")
    def _check_bounds(self) -> CacheSettings:
        if self.min_text_chars > self.max_text_chars:
            raise ValueError("cache.min_text_chars must not exceed cache.max_text_chars")
        return self


class OutputSettings(BaseModel):
    save_by_default: bool = False
    directory: Path = Path("~/Voxello")

    def resolved_directory(self) -> Path:
        return self.directory.expanduser()


class LimitsSettings(BaseModel):
    max_text_chars: int = Field(default=2000, ge=1, le=4096)


class LoggingSettings(BaseModel):
    level: Literal["debug", "info", "warning", "error"] = "info"
    file: Path | None = None
    log_text: bool = Field(default=False, description="Log full spoken text (privacy: off).")

    @field_validator("level", mode="before")
    @classmethod
    def _lower(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VOXELLO_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    speech: SpeechSettings = Field(default_factory=SpeechSettings)
    playback: PlaybackSettings = Field(default_factory=PlaybackSettings)
    notifications: NotificationsSettings = Field(default_factory=NotificationsSettings)
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @model_validator(mode="after")
    def _separate_cache_and_temp(self) -> Settings:
        # TempStore.owns() is what stops the service from deleting cache files; that guard
        # only works while the two directories differ.
        if self.cache.resolved_directory().resolve() == self.storage.resolved_temp_dir().resolve():
            raise ValueError("cache.directory must differ from storage.temp_dir")
        return self

    @model_validator(mode="after")
    def _migrate_deprecated_language(self) -> Settings:
        """``tts.voicestudio.language`` moved to ``speech.default_language`` (roadmap 3.1).

        The old key keeps working as the default, unless the new one is set explicitly. The
        warning goes through logging (stderr), never stdout, which carries the MCP protocol.
        """
        legacy = self.tts.voicestudio.language
        if legacy is None:
            return self
        if "default_language" in self.speech.model_fields_set:
            log.warning(
                "tts.voicestudio.language is deprecated and ignored because "
                "speech.default_language is set; remove it from the configuration."
            )
        else:
            self.speech = SpeechSettings.model_validate(
                {**self.speech.model_dump(), "default_language": legacy}
            )
            log.warning(
                "tts.voicestudio.language is deprecated; move it to speech.default_language "
                "(using '%s' as the default language for now).",
                legacy,
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # init kwargs carry the YAML content; env vars must beat them.
        return (env_settings, init_settings)


def default_config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.yaml"


def describe_config_path(explicit: Path | None = None) -> tuple[Path, str]:
    """Return the config path and where it came from (for ``doctor``).

    Precedence: explicit ``--config`` argument, then ``VOXELLO_CONFIG``, then the
    per-user config directory chosen by ``platformdirs``.
    """
    if explicit is not None:
        return explicit.expanduser(), "--config"
    env_value = os.environ.get(CONFIG_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser(), CONFIG_ENV_VAR
    return default_config_path(), "default (platformdirs)"


def resolve_config_path(explicit: Path | None = None) -> Path:
    return describe_config_path(explicit)[0]


def _set_nested(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = data
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value


def load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file {path} must contain a mapping at the top level.")
    return loaded


@dataclass(frozen=True)
class ProjectConfig:
    """The project file found from ``searched_from``: its allowed keys and the ignored ones."""

    searched_from: Path
    path: Path | None = None
    data: dict[str, Any] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)


def project_start_dir(hook_cwd: Path | None = None) -> Path:
    """Directory the project file is searched from.

    ``$VOXELLO_PROJECT_DIR``, then the ``cwd`` of a hook payload, then ``$CLAUDE_PROJECT_DIR``
    (set by Claude Code for MCP servers and hooks), then the process working directory.
    """
    explicit = os.environ.get(PROJECT_DIR_ENV_VAR)
    if explicit:
        return Path(explicit).expanduser()
    if hook_cwd is not None:
        return hook_cwd
    claude = os.environ.get(CLAUDE_PROJECT_DIR_ENV_VAR)
    if claude:
        return Path(claude).expanduser()
    return Path.cwd()


def find_project_config(start: Path) -> Path | None:
    """``.voxello.yaml`` in ``start`` or a parent, up to the repository root (a ``.git``).

    The home directory and the filesystem root are never a project: a file there would
    silently apply everywhere, which is what the user config is for.
    """
    home = Path.home().resolve()
    current = start.expanduser().resolve()
    while current != home and current.parent != current:
        candidate = current / PROJECT_CONFIG_NAME
        if candidate.is_file():
            return candidate
        if (current / ".git").exists():
            return None
        current = current.parent
    return None


def load_project_config(start: Path) -> ProjectConfig:
    """Read and filter the project file found from ``start`` (empty when there is none)."""
    path = find_project_config(start)
    if path is None:
        return ProjectConfig(start)
    raw = load_yaml_config(path)
    data: dict[str, Any] = {}
    ignored: list[str] = []
    for section, value in raw.items():
        allowed = PROJECT_KEYS.get(section, ())
        if allowed is None:
            data[section] = value
        elif not allowed or not isinstance(value, dict):
            ignored.append(str(section))
        else:
            kept = {k: v for k, v in value.items() if k in allowed}
            ignored.extend(f"{section}.{k}" for k in value if k not in allowed)
            if kept:
                data[section] = kept
    if ignored:
        log.warning(
            "%s: ignored keys a project file may not set: %s", path, ", ".join(sorted(ignored))
        )
    return ProjectConfig(start, path, data, sorted(ignored))


def merge_project(base: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``project`` over ``base``.

    For a role in ``integrations`` a project ``voice`` or ``instructions`` replaces both of
    the user's, so "my voice here" never collides with "a designed voice everywhere".
    """
    merged = dict(base)
    for key, value in project.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            current = dict(current)
            if key in ("phrases", "summaries") and ({"voice", "instructions"} & value.keys()):
                current.pop("voice", None)
                current.pop("instructions", None)
            merged[key] = merge_project(current, value)
        else:
            merged[key] = value
    return merged


def load_settings(
    config_path: Path | None = None,
    *,
    project_dir: Path | None = None,
    use_project: bool = True,
) -> Settings:
    """Load the user YAML file, layer the project file over it, then the environment.

    ``project_dir`` is where the project search starts (see :func:`project_start_dir`).
    """
    path = resolve_config_path(config_path)
    data = load_yaml_config(path)
    if use_project:
        project = load_project_config(project_start_dir(project_dir))
        data = merge_project(data, project.data)
    for var, dotted in FLAT_ENV_ALIASES.items():
        if os.environ.get(var):
            _set_nested(data, dotted, os.environ[var])
    return Settings(**data)


EXAMPLE_CONFIG = """\
# Voxello configuration. Environment variables (VOXELLO_*) override these values.
server:
  transport: stdio

tts:
  provider: voicestudio
  voicestudio:
    base_url: http://localhost:3900     # VoiceStudio; omnivoice-server uses http://host:8880
    # api_key: "change-me"              # required when VoiceStudio is on another host
    # voice: alloy                      # omit for the server default; VoiceStudio: profile id
    engine: omnivoice                   # VoiceStudio: voxcpm2, cosyvoice, mlx-audio, kittentts, ...
    timeout_seconds: 120                # read timeout of one synthesis request
    connect_timeout_seconds: 5
    retries: 1                          # retry once on connection errors and HTTP 502/503/504
    retry_backoff_seconds: 0.5          # doubled at every further retry

speech:
  default_language: it                  # ISO 639-1; agents pass `language` per request
  # voices_by_language:                 # voice used when a request passes no voice
  #   it: clone:marco                   # a cloned voice (`voxello voice clone marco FILE`)
  #   en: narrator                      # a name from voice_designs below
  # voice_designs:                      # reusable OmniVoice voice designs, used by name
  #   narrator: "female, young adult, high pitch"
  #   whisperer: "male, elderly, whisper"

playback:
  enabled: true
  default_interrupt: true
  interrupt_clears_queue: true
  volume: 0.8
  max_queue_size: 10

notifications:
  voice: true
  desktop: true
  title: Voxello

integrations:                           # what Claude Code and Codex say, and with which voice
                                        # per project: .voxello.yaml at the repository root
  phrases:                              # fixed hook sentences (permission prompt, idle, ...)
    mode: cached                        # cached | live (the event's own message) | off
    # voice: clone:marco                # or instructions: "male, middle-aged" (never both)
    # language: it                      # default: speech.default_language
    # channels: [voice, desktop]
  summaries:                            # messages the agent writes (MCP speak / notify)
    mode: on_request                    # on_request | always (a summary at every task end)
    # voice: narrator                   # a name from speech.voice_designs, clone:<id>, ...
    # instructions: "female, young adult"
    enforce: false                      # with always: the Stop hook asks for a missing summary
    min_turn_seconds: 60                # shorter turns are never asked for one

storage:
  temp_retention_minutes: 10
  cleanup_on_start: true

cache:                                  # synthesize repeated phrases (hook alerts) only once
  enabled: true
  # directory: ~/.cache/voxello/audio   # default: OS cache dir, next to the temp dir
  max_entries: 200
  max_age_days: 90
  min_text_chars: 1
  max_text_chars: 300                   # short phrases only: long answers do not repeat

output:
  save_by_default: false
  directory: ~/Voxello

limits:
  max_text_chars: 2000

logging:
  level: info
  log_text: false
  # file: ~/Library/Logs/voxello.log
"""
