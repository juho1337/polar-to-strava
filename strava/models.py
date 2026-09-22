"""Validated Strava uploader input and API values."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.errors import ValidationError as AppValidationError


class SourceInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    relative_path: str
    filename: str
    sha256: str
    local_start: str | None = None


class TimeInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    resolved_utc_start: datetime | None
    resolution_source: str


class SportInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    polar: str | None = None
    domain: str | None = None
    fit: str | None = None
    fit_sub_sport: str | None = None
    fallback: bool = False


class FitInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    relative_path: str | None
    sha256: str | None
    size_bytes: int | None = None
    valid: bool


class MigrationInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str
    warnings: list[str] = Field(default_factory=list)
    reason: str | None = None


class ManifestActivity(BaseModel):
    model_config = ConfigDict(extra="ignore")
    stable_activity_id: str
    source: SourceInfo
    time: TimeInfo
    sport: SportInfo
    fit: FitInfo
    migration: MigrationInfo

    @property
    def eligible(self) -> bool:
        return self.migration.status in {"eligible", "eligible_with_warnings"}


class MigrationManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    manifest_version: Literal[1]
    activities: list[ManifestActivity]

    @classmethod
    def load(cls, workspace: Path) -> tuple[MigrationManifest, str]:
        path = workspace / "migration-manifest.json"
        try:
            content = path.read_bytes()
            manifest = cls.model_validate(json.loads(content))
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise AppValidationError(f"Invalid migration manifest {path}: {error}") from error
        identifiers = [activity.stable_activity_id for activity in manifest.activities]
        if len(identifiers) != len(set(identifiers)):
            raise AppValidationError("Manifest contains duplicate stable activity IDs")
        return manifest, hashlib.sha256(content).hexdigest()


class TokenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str
    refresh_token: str
    expires_at: int
    scope: str = ""


class StravaTokenResponse(BaseModel):
    """Documented OAuth response envelope; only required auth state is persisted."""

    model_config = ConfigDict(extra="ignore")
    token_type: Literal["Bearer"]
    access_token: str
    refresh_token: str
    expires_at: int
    expires_in: int
    scope: str | None = None
    athlete: dict[str, Any] | None = None

    def token_set(self, fallback_scope: str | None = None) -> TokenSet:
        scope = self.scope or fallback_scope or ""
        normalized_scope = " ".join(scope.replace(",", " ").split())
        return TokenSet(
            access_token=self.access_token,
            refresh_token=self.refresh_token,
            expires_at=self.expires_at,
            scope=normalized_scope,
        )


class UploadStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int | None = None
    id_str: str | None = None
    activity_id: int | None = None
    external_id: str | None = None
    error: str | None = None
    status: str

    @property
    def upload_id(self) -> str:
        value = self.id_str or (str(self.id) if self.id is not None else None)
        if value is None:
            raise ValueError("Strava upload response did not contain an upload ID")
        return value


class RateLimit(BaseModel):
    short_limit: int
    daily_limit: int
    short_usage: int
    daily_usage: int

    @property
    def exhausted(self) -> bool:
        return self.short_usage >= self.short_limit or self.daily_usage >= self.daily_limit
