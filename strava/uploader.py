"""Manifest-driven, resumable Strava upload orchestration."""

from __future__ import annotations

import html
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from core.errors import ValidationError
from services.migration import file_sha256
from strava.client import StravaAPIError
from strava.models import ManifestActivity, MigrationManifest, RateLimit, UploadStatus
from strava.rate_limit import DailyLimitReached, RateLimitPolicy
from strava.state import UploadState, UploadStateStore


class UploadClient(Protocol):
    rate_limit: RateLimit | None

    def upload(self, path: Path, external_id: str) -> UploadStatus: ...
    def get_upload(self, upload_id: str) -> UploadStatus: ...


@dataclass(slots=True)
class ProcessingJob:
    activity: ManifestActivity
    upload_id: str
    next_poll: float
    interval: float
    polls: int = 0


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
        max_in_flight: int = 3,
        max_poll_interval: float = 30,
        clock: Callable[[], float] = time.monotonic,
        rate_policy: RateLimitPolicy | None = None,
        on_progress: Callable[[ManifestActivity | None, str], None] | None = None,
    ) -> None:
        self.workspace = workspace
        self.store = store
        self.client = client
        self.sleep = sleep
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.max_retries = max_retries
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be at least one")
        self.max_in_flight = max_in_flight
        self.max_poll_interval = max_poll_interval
        self.clock = clock
        self.rate_policy = rate_policy or RateLimitPolicy(reserve=10, sleep=sleep)
        self.on_progress = on_progress
        self.last_stop_reason: str | None = None
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
        selected_new = 0
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
            if state is not UploadState.PROCESSING:
                if limit is not None and selected_new >= limit:
                    continue
                selected_new += 1
            self.verify(activity)
            items.append(activity)
        if activity_id and not any(item.stable_activity_id == activity_id for item in items):
            row = self.store.get(activity_id)
            raise ValidationError(f"Activity is not selectable in state {row['status']}")
        return items

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
        pending: deque[ManifestActivity] = deque()
        processing: dict[str, ProcessingJob] = {}
        for activity in activities:
            row = self.store.get(activity.stable_activity_id)
            if row["status"] == UploadState.PROCESSING and row["strava_upload_id"]:
                processing[activity.stable_activity_id] = ProcessingJob(
                    activity,
                    str(row["strava_upload_id"]),
                    self.clock(),
                    max(1.0, self.poll_interval),
                )
            else:
                pending.append(activity)
        try:
            while (pending or processing) and self.last_stop_reason is None:
                while pending and len(processing) < self.max_in_flight:
                    activity = pending.popleft()
                    upload_id = self._submit(activity)
                    if upload_id:
                        processing[activity.stable_activity_id] = ProcessingJob(
                            activity,
                            upload_id,
                            self.clock() + max(1.0, self.poll_interval),
                            max(1.0, self.poll_interval),
                        )
                    if self.last_stop_reason:
                        break
                if self.last_stop_reason or not processing:
                    continue
                now = self.clock()
                due = [job for job in processing.values() if job.next_poll <= now]
                if not due:
                    self.sleep(max(0.0, min(job.next_poll for job in processing.values()) - now))
                    continue
                for job in due:
                    if self._poll_once(job):
                        processing.pop(job.activity.stable_activity_id)
                    if self.last_stop_reason:
                        break
        except KeyboardInterrupt:
            self.last_stop_reason = "interrupted by user; durable state is safe to resume"
        return self.store.summary()

    def _submit(self, activity: ManifestActivity) -> str | None:
        assert self.client is not None
        path = self.verify(activity)
        identifier = activity.stable_activity_id
        for attempt in range(self.max_retries):
            if not self._before_request(read=False):
                return None
            self.store.set_status(identifier, UploadState.UPLOADING, increment_attempt=True)
            self._notify(activity, "uploading")
            try:
                result = self.client.upload(path, identifier)
                upload_id = result.upload_id
                self.store.set_status(identifier, UploadState.PROCESSING, upload_id=upload_id)
                self._notify(activity, "processing")
                self._handle_status(activity, result)
                return (
                    upload_id
                    if self.store.get(identifier)["status"] == UploadState.PROCESSING
                    else None
                )
            except KeyboardInterrupt:
                self.store.set_status(
                    identifier,
                    UploadState.UNCERTAIN,
                    error_category="interrupted_upload",
                    error_message="Interrupted while awaiting Strava upload response",
                )
                self._notify(activity, "uncertain")
                raise
            except StravaAPIError as error:
                if error.category == "uncertain":
                    self.store.set_status(
                        identifier,
                        UploadState.UNCERTAIN,
                        error_category=error.category,
                        error_message=str(error),
                        http_status=error.status_code,
                    )
                    self._notify(activity, "uncertain")
                    return None
                if error.category == "rate_limit":
                    self.store.set_status(
                        identifier,
                        UploadState.RETRYABLE_FAILURE,
                        error_category=error.category,
                        error_message=str(error),
                        http_status=error.status_code,
                    )
                    self.last_stop_reason = "Strava returned HTTP 429; resume safely later"
                    self._notify(activity, "rate_limited")
                    return None
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
                self._notify(activity, state.value)
                return None
        return None

    def _poll_once(self, job: ProcessingJob) -> bool:
        assert self.client is not None
        if not self._before_request(read=True):
            return False
        try:
            result = self.client.get_upload(job.upload_id)
        except StravaAPIError as error:
            state = (
                UploadState.RETRYABLE_FAILURE if error.retryable else UploadState.PERMANENT_FAILURE
            )
            self.store.set_status(
                job.activity.stable_activity_id,
                state,
                error_category=error.category,
                error_message=str(error),
                http_status=error.status_code,
            )
            if error.category == "rate_limit":
                self.last_stop_reason = "Strava returned HTTP 429; resume safely later"
            self._notify(job.activity, state.value)
            return True
        self._handle_status(job.activity, result)
        if self.store.get(job.activity.stable_activity_id)["status"] != UploadState.PROCESSING:
            return True
        job.polls += 1
        if job.polls >= self.max_polls:
            self.store.set_status(
                job.activity.stable_activity_id,
                UploadState.RETRYABLE_FAILURE,
                error_category="poll_timeout",
                error_message="Upload processing did not finish in time",
            )
            self._notify(job.activity, "retryable_failure")
            return True
        job.interval = min(self.max_poll_interval, max(1.0, job.interval * 2))
        job.next_poll = self.clock() + job.interval
        self._notify(job.activity, "processing")
        return False

    def _before_request(self, *, read: bool) -> bool:
        assert self.client is not None
        try:
            waited = self.rate_policy.before_request(self.client.rate_limit, read=read)
            if waited:
                self.client.rate_limit = None
            return True
        except DailyLimitReached as error:
            self.last_stop_reason = str(error)
            self._notify(None, "daily_rate_limit")
            return False

    def _notify(self, activity: ManifestActivity | None, event: str) -> None:
        if self.on_progress:
            self.on_progress(activity, event)

    def _handle_status(self, activity: ManifestActivity, result: UploadStatus) -> None:
        identifier = activity.stable_activity_id
        if result.activity_id is not None:
            self.store.set_status(
                identifier, UploadState.COMPLETED, activity_id=str(result.activity_id)
            )
            self._notify(activity, "completed")
        elif result.error:
            duplicate = "duplicate" in result.error.lower()
            state = UploadState.DUPLICATE if duplicate else UploadState.PERMANENT_FAILURE
            self.store.set_status(
                identifier,
                state,
                error_category="duplicate" if duplicate else "processing_error",
                error_message=sanitize_error(result.error),
            )
            self._notify(activity, state.value)


def sanitize_error(message: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(message)).strip()[:500]
