"""Loading of flow YAML files into validated :class:`FlowConfig` objects."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.config.models import FlowConfig
from app.errors import ConfigError


def load_flow_file(path: Path) -> FlowConfig:
    """Parse and validate a single flow YAML file.

    Raises:
        ConfigError: on malformed YAML or schema validation failure, with the
            file name included so the operator knows which config is broken.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name}: top-level YAML must be a mapping")

    try:
        return FlowConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{path.name}: schema error:\n{exc}") from exc


def load_flow_dir(configs_dir: Path) -> list[FlowConfig]:
    """Load every ``*.yaml`` / ``*.yml`` file from a directory."""
    if not configs_dir.is_dir():
        raise ConfigError(f"configs directory not found: {configs_dir}")

    files = sorted(
        p for p in configs_dir.iterdir() if p.suffix in {".yaml", ".yml"}
    )
    return [load_flow_file(path) for path in files]
