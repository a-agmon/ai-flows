"""Application settings, read from the environment (and an optional ``.env``).

All settings use the ``AI_FLOWS_`` prefix, e.g. ``AI_FLOWS_LOG_LEVEL=DEBUG`` or
``AI_FLOWS_CONFIGS_DIR=/etc/flows``. A single :data:`settings` instance is
created at import and shared across the app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the directory that contains the ``app`` package.
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. Field defaults are used when env vars are unset."""

    model_config = SettingsConfigDict(
        env_prefix="AI_FLOWS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Flow sources -------------------------------------------------------
    # Directory holding flow YAML files.
    configs_dir: Path = ROOT_DIR / "configs"
    # Directory holding Jinja2 prompt templates referenced by ``prompt_file``.
    prompts_dir: Path = ROOT_DIR / "app" / "prompts"
    # Python package from which ``module`` nodes import their functions.
    modules_package: str = "app.modules"

    # --- Logging ------------------------------------------------------------
    log_level: str = Field(default="INFO", description="Root log level name.")
    log_format: Literal["console", "json"] = Field(
        default="console",
        description="'console' for human-readable dev output, 'json' for prod.",
    )


settings = Settings()
