"""Configuration model and loading.

Precedence (highest wins): environment variables > YAML config file > defaults.

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
from pathlib import Path
from typing import Any, Literal

import yaml
from platformdirs import user_cache_dir, user_config_dir
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

log = logging.getLogger(__name__)

APP_NAME = "voxello"
CONFIG_ENV_VAR = "VOXELLO_CONFIG"

# ISO 639-1: two lowercase letters (roadmap 3.1). Shared with the service's validation.
LANGUAGE_PATTERN = r"^[a-z]{2}$"
LANGUAGE_RE = re.compile(LANGUAGE_PATTERN)

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
        "> server default.",
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


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from the resolved YAML file, then apply environment overrides."""
    path = resolve_config_path(config_path)
    data = load_yaml_config(path)
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
  #   it: italian_voice
  #   en: english_voice

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
