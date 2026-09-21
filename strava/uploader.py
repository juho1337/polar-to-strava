"""Manifest-driven, resumable Strava upload orchestration."""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Protocol

from core.errors import ValidationError
from services.migration import file_sha256
from strava.client import StravaAPIError
from strava.models import ManifestActivity, MigrationManifest, UploadStatus
from strava.state import UploadState, UploadStateStore


class UploadClient(Protocol):
    def upload(self, path: Path, external_id: str) -> UploadStatus: ...
    def get_upload(self, upload_id: str) -> UploadStatus: ...


class Uploader:
    def __init__(
        self,
        workspace: Path,
        store: UploadStateStore,
        client: UploadClient | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 2,
        max_polls: int = 60,
        max_retries: int = 3,
    ) -> None:
        self.workspace = workspace
        self.store = store
        self.client = client
        self.sleep = sleep
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.max_retries = max_retries
        self.manifest, fingerprint = MigrationManifest.load(workspace)
        store.reconcile(self.manifest, fingerprint)

    def select(
        self,
        *,
        limit: int | None = None,
        activity_id: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[ManifestActivity]:
        items: list[ManifestActivity] = []
        for activity in self.manifest.activities:
            if not activity.eligible or (
                activity_id and activity.stable_activity_id != activity_id
            ):
                continue
            started = activity.time.resolved_utc_start
            if started is None:
                raise ValidationError(
                    f"Eligible activity has no resolved UTC time: {activity.stable_activity_id}"
                )
            if from_date and started.date() < from_date or to_date and started.date() > to_date:
                continue
            state = UploadState(self.store.get(activity.stable_activity_id)["status"])
            if state not in {
                UploadState.PENDING,
                UploadState.RETRYABLE_FAILURE,
                UploadState.PROCESSING,
            }:
                continue
            self.verify(activity)
            items.append(activity)
        if activity_id and not any(item.stable_activity_id == activity_id for item in items):
            row = self.store.get(activity_id)
            raise ValidationError(f"Activity is not selectable in state {row['status']}")
        return items[:limit] if limit is not None else items

    def verify(self, activity: ManifestActivity) -> Path:
        relative = activity.fit.relative_path
        expected = activity.fit.sha256
        if not relative or not expected or not activity.fit.valid:
            raise ValidationError(
                f"Eligible activity lacks valid FIT metadata: {activity.stable_activity_id}"
            )
        path = (self.workspace / relative).resolve()
        try:
            path.relative_to(self.workspace.resolve())
        except ValueError as error:
            raise ValidationError("FIT path escapes migration workspace") from error
        if not path.is_file():
            self.store.set_status(
                activity.stable_activity_id,
                UploadState.LOCAL_FILE_CHANGED,
                error_category="missing_fit",
                error_message="Manifest FIT file is missing",
            )
            raise ValidationError(f"FIT file is missing: {relative}")
        if file_sha256(path) != expected:
            self.store.set_status(
                activity.stable_activity_id,
                UploadState.LOCAL_FILE_CHANGED,
                error_category="fit_hash_mismatch",
                error_message="FIT SHA-256 differs from manifest",
            )
            raise ValidationError(f"FIT hash differs from manifest: {relative}")
        return path

    def run(self, activities: list[ManifestActivity], dry_run: bool = False) -> dict[str, int]:
        if dry_run:
            return self.store.summary()
        if self.client is None:
            raise ValidationError("Strava client is required for upload")
        for activity in activities:
            row = self.store.get(activity.stable_activity_id)
            if row["status"] == UploadState.PROCESSING and row["strava_upload_id"]:
                self._poll(activity, row["strava_upload_id"])
            else:
                self._submit(activity)
            row = self.store.get(activity.stable_activity_id)
            if row["last_error_category"] == "rate_limit":
                break
        return self.store.summary()

    def _submit(self, activity: ManifestActivity) -> None:
        assert self.client is not None
        path = self.verify(activity)
        identifier = activity.stable_activity_id
        for attempt in range(self.max_retries):
            self.store.set_status(identifier, UploadState.UPLOADING, increment_attempt=True)
            try:
                result = self.client.upload(path, identifier)
                upload_id = result.upload_id
                self.store.set_status(identifier, UploadState.PROCESSING, upload_id=upload_id)
                self._handle_status(activity, result)
                if self.store.get(identifier)["status"] == UploadState.PROCESSING:
                    self._poll(activity, upload_id)
                return
            except StravaAPIError as error:
                if error.category == "uncertain":
                    self.store.set_status(
                        identifier,
                        UploadState.UNCERTAIN,
                        error_category=error.category,
                        error_message=str(error),
                        http_status=error.status_code,
                    )
                    return
                if error.category == "rate_limit":
                    self.store.set_status(
                        identifier,
                        UploadState.RETRYABLE_FAILURE,
                        error_category=error.category,
                        error_message=str(error),
                        http_status=error.status_code,
                    )
                    return
                if error.retryable and attempt + 1 < self.max_retries:
                    self.sleep(2**attempt)
                    continue
                state = (
                    UploadState.RETRYABLE_FAILURE
                    if error.retryable
                    else UploadState.PERMANENT_FAILURE
                )
                self.store.set_status(
                    identifier,
                    state,
                    error_category=error.category,
                    error_message=str(error),
                    http_status=error.status_code,
                )
                return

    def _poll(self, activity: ManifestActivity, upload_id: str) -> None:
        assert self.client is not None
        for _ in range(self.max_polls):
            self.sleep(self.poll_interval)
            try:
                result = self.client.get_upload(upload_id)
            except StravaAPIError as error:
                state = (
                    UploadState.RETRYABLE_FAILURE
                    if error.retryable
                    else UploadState.PERMANENT_FAILURE
                )
                self.store.set_status(
                    activity.stable_activity_id,
                    state,
                    error_category=error.category,
                    error_message=str(error),
                    http_status=error.status_code,
                )
                return
            self._handle_status(activity, result)
            if self.store.get(activity.stable_activity_id)["status"] != UploadState.PROCESSING:
                return
        self.store.set_status(
            activity.stable_activity_id,
            UploadState.RETRYABLE_FAILURE,
            error_category="poll_timeout",
            error_message="Upload processing did not finish in time",
        )

    def _handle_status(self, activity: ManifestActivity, result: UploadStatus) -> None:
        identifier = activity.stable_activity_id
        if result.activity_id is not None:
            self.store.set_status(
                identifier, UploadState.COMPLETED, activity_id=str(result.activity_id)
            )
        elif result.error:
            duplicate = "duplicate" in result.error.lower()
            self.store.set_status(
                identifier,
                UploadState.DUPLICATE if duplicate else UploadState.PERMANENT_FAILURE,
                error_category="duplicate" if duplicate else "processing_error",
                error_message=sanitize_error(result.error),
            )


def sanitize_error(message: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(message)).strip()[:500]
