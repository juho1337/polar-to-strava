"""Typed request documents (read from stdin as one JSON object). Unknown fields are rejected."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancel_on_stdin_close: bool = False


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1)
    client_secret: SecretStr


class TokenStoreSpec(BaseModel):
    """``file`` keeps the CLI-compatible plaintext file; ``dpapi`` encrypts for the Windows user."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["file", "dpapi"] = "file"
    path: Path | None = None


class WorkspaceRequest(Request):
    workspace: Path


class StravaRequest(WorkspaceRequest):
    credentials: Credentials | None = None
    token_store: TokenStoreSpec = Field(default_factory=TokenStoreSpec)


class AuditRequest(WorkspaceRequest):
    source: Path
    config: Path | None = None
    overwrite: bool = False
    incremental: bool = False
    workers: int | None = Field(default=None, ge=1)


class StatusRequest(WorkspaceRequest):
    details: bool = False


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int | None = Field(default=None, ge=1)
    activity_id: str | None = None
    all: bool = False
    from_date: date | None = None
    to_date: date | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> Selector:
        if sum((self.limit is not None, self.activity_id is not None, self.all)) != 1:
            raise ValueError("choose exactly one of limit, activity_id or all")
        return self


class UploadDryRunRequest(WorkspaceRequest):
    selector: Selector


class UploadRunRequest(StravaRequest):
    selector: Selector
    max_in_flight: int = Field(default=3, ge=1, le=10)
    rate_limit_reserve: int = Field(default=10, ge=0)


class ResetRequest(WorkspaceRequest):
    activity_id: str
    # Where the reconnected tokens live; the authorization repair check validates this store.
    token_store: TokenStoreSpec = Field(default_factory=TokenStoreSpec)


class ActivityListRequest(WorkspaceRequest):
    migration_status: list[str] | None = None
    sport: str | None = None
    upload_state: list[str] | None = None
    from_date: date | None = None
    to_date: date | None = None
    reason: str | None = None
    outside_current_eligible: bool | None = None
    offset: int = Field(default=0, ge=0)
    limit: int | None = Field(default=None, ge=1)


class ActivityGetRequest(WorkspaceRequest):
    activity_id: str


class ConnectionRequest(StravaRequest):
    pass


class AuthorizeBeginRequest(StravaRequest):
    redirect_uri: str
    features: list[Literal["migration", "sync"]] = Field(default=["migration"])
    state: str | None = None


class AuthorizeCompleteRequest(StravaRequest):
    code: SecretStr
    state: SecretStr
    expected_state: SecretStr
    granted_scope: str
    features: list[Literal["migration", "sync"]] = Field(default=["migration"])


class TimezoneListRequest(WorkspaceRequest):
    from_date: date | None = None
    to_date: date | None = None
    sport: str | None = None
    reason: str | None = None
    config: Path | None = None


class TimezoneChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stable_activity_id: str
    offset: str | None = None
    iana_zone: str | None = None


class TimezonePreviewRequest(WorkspaceRequest):
    choices: list[TimezoneChoice] = Field(min_length=1)
    iana_zone: str | None = None  # applied to choices that name neither offset nor zone


class TimezoneApplyRequest(TimezonePreviewRequest):
    source: Path
    config: Path | None = None
    confirm: bool = False
    incremental: bool = False
    workers: int | None = Field(default=None, ge=1)


class ReportExportRequest(WorkspaceRequest):
    destination: Path
    format: Literal["json", "csv", "markdown"] = "json"
