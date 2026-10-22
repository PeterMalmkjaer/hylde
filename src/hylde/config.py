"""Indstillinger. Nøgler læses fra miljøet eller ~/.hylde/.env."""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_init_only: ContextVar[bool] = ContextVar("hylde_settings_init_only", default=False)


def default_data_dir() -> Path:
    raw = os.environ.get("HYLDE_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hylde"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HYLDE_",
        env_file=default_data_dir() / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HYLDE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HYLDE_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("HYLDE_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )

    local_base_url: str = "http://127.0.0.1:11434/v1"
    local_api_key: SecretStr = SecretStr("ollama")
    local_model: str | None = None

    llama_base_url: str = "http://127.0.0.1:8080/v1"
    llama_model: str | None = None
    llama_enabled: bool = False

    ollama_cloud_base_url: str = "https://ollama.com/v1"
    ollama_cloud_model: str | None = None
    ollama_cloud_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "HYLDE_OLLAMA_CLOUD_API_KEY",
            "OLLAMA_API_KEY",
            "ollama_cloud_api_key",
        ),
    )

    data_dir: Path = Field(default_factory=default_data_dir)
    table_path: Path | None = None
    log_level: str = "INFO"
    dry_run: bool = False
    max_prompt_chars: int = 24_000

    anthropic_base_url: str = "https://api.anthropic.com"
    openai_base_url: str = "https://api.openai.com/v1"
    openrouter_rankings_url: str = "https://openrouter.ai/api/v1/datasets/rankings-daily"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        if _init_only.get():
            return (init_settings,)
        if os.environ.get("HYLDE_DISABLE_DOTENV") == "1":
            return (init_settings, env_settings)
        return (init_settings, env_settings, dotenv_settings)

    def secret(self, value: SecretStr | None) -> str | None:
        if value is None:
            return None
        text = value.get_secret_value().strip()
        return text or None


def settings_from_file(data_dir: Path, **values: Any) -> Settings:
    """Byg indstillinger kun ud fra de værdier, appen har gemt."""
    token = _init_only.set(True)
    try:
        return Settings(data_dir=data_dir, **values)
    finally:
        _init_only.reset(token)
