"""YAML configuration loading and validation."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    """Validated application configuration."""

    model_config = ConfigDict(extra="forbid")
    polar_export: Path
    output_folder: Path = Path("output")
    database: Path = Path("migration.db")
    workers: int = Field(default=1, ge=1)
    upload: bool = False
    overwrite: bool = False

    @field_validator("polar_export")
    @classmethod
    def export_must_be_directory(cls, value: Path) -> Path:
        if not value.is_dir():
            raise ValueError(f"Polar export directory does not exist: {value}")
        return value


def load_config(path: Path | str) -> Settings:
    """Load YAML configuration and resolve relative paths against its directory."""
    config_path = Path(path).resolve()
    with config_path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError("Configuration root must be a mapping.")
    for key in ("polar_export", "output_folder", "database"):
        if key in data:
            candidate = Path(data[key])
            data[key] = candidate if candidate.is_absolute() else config_path.parent / candidate
    return Settings.model_validate(data)
