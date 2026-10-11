"""Indstillinger. Nøgler læses fra miljøet eller ~/.hylde/.env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
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
        if os.environ.get("HYLDE_DISABLE_DOTENV") == "1":
            return (init_settings, env_settings)
        return (init_settings, env_settings, dotenv_settings)

    def secret(self, value: SecretStr | None) -> str | None:
        if value is None:
            return None
        text = value.get_secret_value().strip()
        return text or None
