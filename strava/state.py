"""Durable SQLite upload state for a migration workspace."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from core.errors import ValidationError
from strava.models import MigrationManifest

SCHEMA_VERSION = 1


class UploadState(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    LOCAL_FILE_CHANGED = "local_file_changed"
    UNCERTAIN = "uncertain"
    SKIPPED = "skipped"


def now() -> str:
    return datetime.now(UTC).isoformat()


class UploadStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> UploadStateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection:
            yield self.connection

    def _create(self) -> None:
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS uploads (
                stable_activity_id TEXT PRIMARY KEY, manifest_version INTEGER NOT NULL,
                fit_sha256 TEXT, eligible INTEGER NOT NULL, present INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL, strava_upload_id TEXT, strava_activity_id TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0, first_attempt_at TEXT,
                latest_attempt_at TEXT, completed_at TEXT, last_http_status INTEGER,
                last_error_category TEXT, last_error_message TEXT)"""
            )
            current = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if current is not None and int(current[0]) != SCHEMA_VERSION:
                raise ValidationError(f"Unsupported uploader state schema {current[0]}")
            db.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    def reconcile(self, manifest: MigrationManifest, fingerprint: str) -> None:
        existing = {
            row["stable_activity_id"]: row
            for row in self.connection.execute("SELECT * FROM uploads").fetchall()
        }
        incoming = {item.stable_activity_id: item for item in manifest.activities}
        with self.transaction() as db:
            for identifier, activity in incoming.items():
                fit_hash = activity.fit.sha256
                eligible = int(activity.eligible)
                row = existing.get(identifier)
                if row is None:
                    db.execute(
                        "INSERT INTO uploads(stable_activity_id,manifest_version,fit_sha256,eligible,present,status) VALUES(?,?,?,?,1,?)",
                        (
                            identifier,
                            manifest.manifest_version,
                            fit_hash,
                            eligible,
                            UploadState.PENDING,
                        ),
                    )
                elif row["fit_sha256"] != fit_hash or row["eligible"] != eligible:
                    db.execute(
                        "UPDATE uploads SET fit_sha256=?,eligible=?,present=1,status=?,last_error_category=?,last_error_message=? WHERE stable_activity_id=?",
                        (
                            fit_hash,
                            eligible,
                            UploadState.LOCAL_FILE_CHANGED,
                            "manifest_changed",
                            "FIT hash or eligibility changed in regenerated manifest",
                            identifier,
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE uploads SET present=1 WHERE stable_activity_id=?", (identifier,)
                    )
            for identifier in set(existing) - set(incoming):
                db.execute(
                    "UPDATE uploads SET present=0,status=?,last_error_category=?,last_error_message=? WHERE stable_activity_id=?",
                    (
                        UploadState.LOCAL_FILE_CHANGED,
                        "manifest_changed",
                        "Activity disappeared from regenerated manifest",
                        identifier,
                    ),
                )
            db.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('manifest_fingerprint',?)",
                (fingerprint,),
            )

    def get(self, identifier: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM uploads WHERE stable_activity_id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise ValidationError(f"Unknown activity ID: {identifier}")
        return row  # type: ignore[no-any-return]

    def set_status(
        self,
        identifier: str,
        status: UploadState,
        *,
        upload_id: str | None = None,
        activity_id: str | None = None,
        http_status: int | None = None,
        error_category: str | None = None,
        error_message: str | None = None,
        increment_attempt: bool = False,
    ) -> None:
        stamp = now()
        with self.transaction() as db:
            db.execute(
                """UPDATE uploads SET status=?,strava_upload_id=COALESCE(?,strava_upload_id),
                strava_activity_id=COALESCE(?,strava_activity_id),last_http_status=?,
                last_error_category=?,last_error_message=?,latest_attempt_at=CASE WHEN ? THEN ? ELSE latest_attempt_at END,
                first_attempt_at=CASE WHEN ? AND first_attempt_at IS NULL THEN ? ELSE first_attempt_at END,
                attempt_count=attempt_count+CASE WHEN ? THEN 1 ELSE 0 END,
                completed_at=CASE WHEN ? THEN ? ELSE completed_at END WHERE stable_activity_id=?""",
                (
                    status,
                    upload_id,
                    activity_id,
                    http_status,
                    error_category,
                    error_message,
                    increment_attempt,
                    stamp,
                    increment_attempt,
                    stamp,
                    increment_attempt,
                    status == UploadState.COMPLETED,
                    stamp,
                    identifier,
                ),
            )

    def reset(self, identifier: str, force: bool = False) -> None:
        row = self.get(identifier)
        if row["status"] in {UploadState.COMPLETED, UploadState.DUPLICATE} and not force:
            raise ValidationError("Resetting completed or duplicate state requires --force")
        with self.transaction() as db:
            db.execute(
                """UPDATE uploads SET status=?,strava_upload_id=NULL,strava_activity_id=NULL,
                last_http_status=NULL,last_error_category=NULL,last_error_message=NULL WHERE stable_activity_id=?""",
                (UploadState.PENDING, identifier),
            )

    def summary(self) -> dict[str, int]:
        counts = Counter(
            row[0]
            for row in self.connection.execute(
                "SELECT status FROM uploads WHERE present=1 AND eligible=1"
            )
        )
        return {state.value: counts[state.value] for state in UploadState}
