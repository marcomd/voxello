"""Configuration model and loading.

Precedence (highest wins): environment variables > YAML config file > defaults.

Environment variables use the ``VOXELLO_`` prefix with ``__`` for nesting, e.g.
``VOXELLO_TTS__VOICESTUDIO__BASE_URL``. The flat aliases from the specification are
also honoured: ``VOXELLO_TTS_PROVIDER``, ``VOXELLO_VOICESTUDIO_URL``,
``VOXELLO_VOICESTUDIO_API_KEY``, ``VOXELLO_DEFAULT_VOICE``, ``VOXELLO_LOG_LEVEL``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from platformdirs import user_cache_dir, user_config_dir
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

APP_NAME = "voxello"
CONFIG_ENV_VAR = "VOXELLO_CONFIG"

# Flat env aliases from the spec -> dotted path in the settings tree.
FLAT_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "VOXELLO_TTS_PROVIDER": ("tts", "provider"),
    "VOXELLO_VOICESTUDIO_URL": ("tts", "voicestudio", "base_url"),
    "VOXELLO_VOICESTUDIO_API_KEY": ("tts", "voicestudio", "api_key"),
    "VOXELLO_DEFAULT_VOICE": ("tts", "voicestudio", "voice"),
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
    language: str | None = Field(default="it", description="ISO 639-1 language hint.")
    speed: float | None = Field(default=None, ge=0.25, le=4.0)
    num_step: int | None = Field(default=None, ge=1, le=128)
    guidance_scale: float | None = Field(default=None, ge=0, le=20)
    timeout_seconds: float = Field(default=120, gt=0)

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


class TTSSettings(BaseModel):
    provider: Literal["voicestudio"] = "voicestudio"
    voicestudio: VoiceStudioSettings = Field(default_factory=VoiceStudioSettings)


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
    playback: PlaybackSettings = Field(default_factory=PlaybackSettings)
    notifications: NotificationsSettings = Field(default_factory=NotificationsSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    limits: LimitsSettings = Field(default_factory=LimitsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

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
    language: it
    timeout_seconds: 120

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
