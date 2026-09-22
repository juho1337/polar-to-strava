"""Versioned migration workspace identity, configuration, and eligibility."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.errors import ConfigurationError

MANIFEST_VERSION = 1
CONFIG_VERSION = 1


class MigrationStatus(StrEnum):
    ELIGIBLE = "eligible"
    ELIGIBLE_WITH_WARNINGS = "eligible_with_warnings"
    REQUIRES_CONFIGURATION = "requires_configuration"
    EXCLUDED_SUMMARY_ONLY = "excluded_summary_only"
    EXCLUDED_INVALID_SOURCE = "excluded_invalid_source"
    EXCLUDED_UNRESOLVED = "excluded_unresolved"


class ActivityOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone_offset: str | None = None

    @field_validator("timezone_offset")
    @classmethod
    def valid_offset(cls, value: str | None) -> str | None:
        if value is None:
            return None
        match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
        if not match:
            raise ValueError("timezone_offset must have format +HH:MM or -HH:MM")
        hours, minutes = int(match[2]), int(match[3])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
            raise ValueError("timezone_offset must be between -14:00 and +14:00")
        return value

    def as_timezone(self) -> timezone | None:
        if self.timezone_offset is None:
            return None
        sign = 1 if self.timezone_offset[0] == "+" else -1
        hours, minutes = map(int, self.timezone_offset[1:].split(":"))
        return timezone(sign * timedelta(hours=hours, minutes=minutes))


class MigrationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_version: int = Field(default=CONFIG_VERSION)
    activity_overrides: dict[str, ActivityOverride] = Field(default_factory=dict)
    review_requirements: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("config_version")
    @classmethod
    def supported_version(cls, value: int) -> int:
        if value != CONFIG_VERSION:
            raise ValueError(f"unsupported migration config version {value}")
        return value


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_activity_id(source_hash: str) -> str:
    return f"sha256:{source_hash}"


def file_sha256(path: Path) -> str:
    return source_sha256(path)


def load_migration_config(path: Path | None) -> MigrationConfig:
    if path is None:
        return MigrationConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return MigrationConfig.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ConfigurationError(f"Invalid migration configuration {path}: {error}") from error


def classify(row: dict[str, Any]) -> MigrationStatus:
    if row.get("fit_valid") and row.get("fit_sha256"):
        losses = [
            warning
            for warning in row["warnings"]
            if warning.startswith("source_") and warning.endswith("_loss")
        ]
        if losses:
            return MigrationStatus.EXCLUDED_UNRESOLVED
        return (
            MigrationStatus.ELIGIBLE_WITH_WARNINGS if row["warnings"] else MigrationStatus.ELIGIBLE
        )
    error = str(row.get("error", "")).lower()
    if "timezone offset" in error:
        return MigrationStatus.REQUIRES_CONFIGURATION
    if "recorded points" in error or "no_trackpoints" in row.get("warnings", []):
        return MigrationStatus.EXCLUDED_SUMMARY_ONLY
    if "outside its session" in error:
        return MigrationStatus.EXCLUDED_INVALID_SOURCE
    if row.get("failure_stage") == "fit_generation":
        return MigrationStatus.EXCLUDED_UNRESOLVED
    return MigrationStatus.EXCLUDED_INVALID_SOURCE
