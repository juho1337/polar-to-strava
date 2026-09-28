"""Atomic, conservative schema-1 upgrade. No network or clean-state fallback."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path

from core.errors import ValidationError
from strava.recovery import Code, Origin, Remote, Submission, positive_id

SCHEMA_VERSION = 2
LEGACY_STATUSES = frozenset(
    {
        "pending",
        "uploading",
        "processing",
        "completed",
        "duplicate",
        "retryable_failure",
        "permanent_failure",
        "local_file_changed",
        "uncertain",
        "skipped",
    }
)
V1_COLUMNS = frozenset(
    "stable_activity_id manifest_version fit_sha256 eligible present status "
    "strava_upload_id strava_activity_id attempt_count first_attempt_at "
    "latest_attempt_at completed_at last_http_status last_error_category "
    "last_error_message".split()
)


def _enum(values: type[Origin] | type[Submission] | type[Remote] | type[Code]) -> str:
    return ",".join(f"'{item.value}'" for item in values)


DDL = (
    f"""CREATE TABLE recovery_activities (
        stable_activity_id TEXT PRIMARY KEY NOT NULL,
        manifest_version INTEGER NOT NULL CHECK(manifest_version=1),
        present INTEGER NOT NULL CHECK(present IN (0,1)),
        eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
        accepted_fit_sha256 TEXT, current_fit_sha256 TEXT, fit_relative_path TEXT,
        fit_size_bytes INTEGER CHECK(fit_size_bytes>=0), resolved_utc_start TEXT, sport TEXT,
        origin TEXT NOT NULL CHECK(origin IN ({_enum(Origin)})),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
        legacy_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(legacy_attempt_count>=0),
        first_attempt_at TEXT, latest_attempt_at TEXT, legacy_status TEXT)""",
    f"""CREATE TABLE submission_attempts (
        attempt_id INTEGER PRIMARY KEY,
        stable_activity_id TEXT NOT NULL REFERENCES recovery_activities(stable_activity_id),
        kind TEXT NOT NULL CHECK(kind IN ('submission','legacy_summary')),
        submission TEXT NOT NULL CHECK(submission IN ({_enum(Submission)})),
        remote TEXT NOT NULL CHECK(remote IN ({_enum(Remote)})),
        upload_id TEXT, activity_id TEXT, duplicate_activity_id TEXT,
        evidence_code TEXT NOT NULL CHECK(evidence_code IN ({_enum(Code)})),
        additional_attempts_unknown INTEGER NOT NULL DEFAULT 0
            CHECK(additional_attempts_unknown IN (0,1)),
        conflicting_ids TEXT NOT NULL DEFAULT '[]', intent_at TEXT, response_at TEXT,
        completed_at TEXT, fit_sha256 TEXT, http_status INTEGER,
        error_code TEXT NOT NULL DEFAULT 'none' CHECK(error_code IN ({_enum(Code)})))""",
    f"""CREATE TABLE recovery_blockers (
        stable_activity_id TEXT NOT NULL REFERENCES recovery_activities(stable_activity_id),
        scope TEXT NOT NULL, code TEXT NOT NULL CHECK(code IN ({_enum(Code)})),
        blocks_submission INTEGER NOT NULL CHECK(blocks_submission IN (0,1)),
        blocks_observation INTEGER NOT NULL CHECK(blocks_observation IN (0,1)),
        active INTEGER NOT NULL CHECK(active IN (0,1)),
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, cleared_at TEXT,
        PRIMARY KEY(stable_activity_id,scope,code))""",
    "CREATE INDEX attempts_activity ON submission_attempts(stable_activity_id)",
    "CREATE INDEX blockers_activity ON recovery_blockers(stable_activity_id,active)",
)


def _tables(db: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' " "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _integrity(db: sqlite3.Connection) -> None:
    if [r[0] for r in db.execute("PRAGMA integrity_check")] != ["ok"]:
        raise ValidationError("Uploader state integrity check failed")


def validate_v2(db: sqlite3.Connection) -> None:
    if _tables(db) != {
        "metadata",
        "recovery_activities",
        "submission_attempts",
        "recovery_blockers",
    }:
        raise ValidationError("Incomplete uploader schema")
    _integrity(db)
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValidationError("Invalid recovery relationships")
    # Validate every column, including empty malformed databases, against our schema.
    with closing(sqlite3.connect(":memory:")) as reference:
        for statement in DDL:
            reference.execute(statement)
        for table in ("recovery_activities", "submission_attempts", "recovery_blockers"):
            expected = [tuple(r)[1:6] for r in reference.execute(f"PRAGMA table_info({table})")]
            actual = [tuple(r)[1:6] for r in db.execute(f"PRAGMA table_info({table})")]
            if expected != actual:
                raise ValidationError("Malformed recovery schema")
    for row in db.execute("SELECT * FROM submission_attempts"):
        submission = Submission(row["submission"])
        remote = Remote(row["remote"])
        Code(row["evidence_code"])
        Code(row["error_code"])
        for name in ("upload_id", "activity_id", "duplicate_activity_id"):
            if row[name] is not None and positive_id(row[name]) != row[name]:
                raise ValidationError("Malformed recovery identifier")
        conflicts = json.loads(row["conflicting_ids"])
        if not isinstance(conflicts, list) or any(positive_id(v) != v for v in conflicts):
            raise ValidationError("Malformed recovery conflicts")
        if remote == Remote.COMPLETED and (
            not row["activity_id"] or submission != Submission.CONFIRMED
        ):
            raise ValidationError("Completion lacks authoritative evidence")
        if remote == Remote.DUPLICATE and (
            submission != Submission.CONFIRMED
            or not (
                row["evidence_code"] == Code.LEGACY_DUPLICATE
                or row["upload_id"]
                and row["duplicate_activity_id"]
            )
        ):
            raise ValidationError("Duplicate lacks authoritative evidence")
        if (
            remote in {Remote.PROCESSING, Remote.DEFERRED, Remote.PROCESSING_FAILED}
            and not row["upload_id"]
        ):
            raise ValidationError("Remote observation lacks identity")
        if submission == Submission.NOT_SUBMITTED and (
            row["evidence_code"] != Code.CLIENT_PREFLIGHT
            or remote != Remote.NOT_STARTED
            or row["upload_id"]
            or row["activity_id"]
            or row["duplicate_activity_id"]
            or conflicts
        ):
            raise ValidationError("Non-submission proof contradicts remote evidence")
    for row in db.execute("SELECT * FROM recovery_activities"):
        Origin(row["origin"])
        if any(
            type(row[key]) is not int or row[key] < 0
            for key in ("revision", "legacy_attempt_count")
        ):
            raise ValidationError("Malformed recovery counter")
    for row in db.execute("SELECT * FROM recovery_blockers"):
        Code(row["code"])
        if row["scope"] != "activity":
            prefix, _, suffix = row["scope"].partition(":")
            if (
                prefix != "attempt"
                or not suffix.isdecimal()
                or not db.execute(
                    "SELECT 1 FROM submission_attempts WHERE attempt_id=? AND stable_activity_id=?",
                    (suffix, row["stable_activity_id"]),
                ).fetchone()
            ):
                raise ValidationError("Malformed blocker scope")


def block_shared_upload_ids(db: sqlite3.Connection) -> None:
    """Retain cross-activity IDs without attributing one upload to two activities."""
    shared = [
        r[0]
        for r in db.execute(
            """SELECT upload_id FROM submission_attempts
        WHERE upload_id IS NOT NULL GROUP BY upload_id
        HAVING count(DISTINCT stable_activity_id)>1"""
        )
    ]
    for upload_id in shared:
        for row in db.execute(
            "SELECT attempt_id,stable_activity_id FROM submission_attempts WHERE upload_id=?",
            (upload_id,),
        ).fetchall():
            db.execute(
                """INSERT INTO recovery_blockers VALUES(?,?,?,1,1,1,'conflict','conflict',NULL)
                ON CONFLICT(stable_activity_id,scope,code) DO UPDATE SET active=1,
                blocks_submission=1,blocks_observation=1,cleared_at=NULL""",
                (row["stable_activity_id"], f"attempt:{row['attempt_id']}", Code.ID_CONFLICT),
            )
            db.execute(
                "UPDATE recovery_activities SET revision=revision+1 WHERE stable_activity_id=?",
                (row["stable_activity_id"],),
            )


def _timestamp(value: object) -> str | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                return parsed.isoformat()
        except ValueError:
            pass
    return None


def _hash(value: object) -> str | None:
    import re

    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def _map_row(db: sqlite3.Connection, row: sqlite3.Row) -> None:
    identifier = row["stable_activity_id"]
    if not isinstance(identifier, str) or not identifier:
        raise ValidationError("Invalid legacy activity identity")
    status = row["status"] if row["status"] in LEGACY_STATUSES else "unknown"
    count = row["attempt_count"]
    invalid = type(count) is not int or count < 0 or status == "unknown"
    invalid |= row["eligible"] not in (0, 1) or row["present"] not in (0, 1)
    invalid |= row["manifest_version"] != 1
    upload = positive_id(row["strava_upload_id"])
    activity = positive_id(row["strava_activity_id"])
    invalid |= any(
        row[key] is not None and positive_id(row[key]) is None
        for key in ("strava_upload_id", "strava_activity_id")
    )
    timestamps = tuple(
        _timestamp(row[key]) for key in ("first_attempt_at", "latest_attempt_at", "completed_at")
    )
    no_history = (
        not invalid
        and count == 0
        and not upload
        and not activity
        and row["last_http_status"] is None
        and all(
            row[key] is None for key in ("first_attempt_at", "latest_attempt_at", "completed_at")
        )
    )
    category = row["last_error_category"]
    known_causes = {
        "network": Code.NETWORK,
        "server": Code.SERVER,
        "authorization": Code.AUTHORIZATION,
        "request": Code.REQUEST,
        "rate_limit": Code.RATE_LIMIT,
        "poll_timeout": Code.POLL_BUDGET,
        "processing_error": Code.PROCESSING_ERROR,
        "duplicate": Code.LEGACY_DUPLICATE,
        "uncertain": Code.UNCERTAIN,
        "interrupted_upload": Code.UNCERTAIN,
        "malformed_response": Code.MALFORMED_RESPONSE,
    }
    safe_cause = known_causes.get(category, Code.UNKNOWN_PHASE if category else Code.NONE)
    http_status = row["last_http_status"]
    safe_http = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
    preflight = status == "local_file_changed" and category in {"missing_fit", "fit_hash_mismatch"}
    fresh = no_history and (
        (status == "pending" and not category and not row["last_error_message"]) or preflight
    )
    origin = Origin.LEGACY_FRESH if fresh else Origin.LEGACY_REVIEW
    fit_hash = _hash(row["fit_sha256"])
    db.execute(
        """INSERT INTO recovery_activities (
        stable_activity_id,manifest_version,present,eligible,accepted_fit_sha256,
        current_fit_sha256,origin,legacy_attempt_count,first_attempt_at,latest_attempt_at,
        legacy_status) VALUES(?,1,?,?,?,?,?,?,?,?,?)""",
        (
            identifier,
            int(row["present"] == 1),
            int(row["eligible"] == 1),
            fit_hash,
            fit_hash,
            origin,
            count if type(count) is int and count >= 0 else 0,
            timestamps[0],
            timestamps[1],
            status,
        ),
    )
    blockers: list[tuple[Code, bool]] = []
    if invalid:
        blockers.append((Code.MALFORMED_STATE, True))
    if status == "local_file_changed":
        blockers.append((Code(category) if preflight else Code.MANIFEST_CHANGED, False))
    if status == "skipped":
        blockers.append((Code.SKIPPED, False))
    if not fresh:
        remote = Remote.DEFERRED if upload else Remote.NOT_STARTED
        submission = Submission.CONFIRMED if upload else Submission.LEGACY_UNKNOWN
        evidence = Code.LEGACY
        extras = invalid or count != 1 or status in {"pending", "uploading", "uncertain"}
        if status == "uncertain" and not upload:
            submission = Submission.UNCERTAIN
        if status == "processing" and upload:
            remote = Remote.PROCESSING
        elif status == "completed" and activity and not invalid:
            remote, submission, evidence = Remote.COMPLETED, Submission.CONFIRMED, Code.COMPLETED
        elif status == "duplicate" and not invalid and not activity:
            remote, submission, evidence = (
                Remote.DUPLICATE,
                Submission.CONFIRMED,
                Code.LEGACY_DUPLICATE,
            )
        elif status == "local_file_changed" and activity and timestamps[2] and not invalid:
            remote, submission, evidence = Remote.COMPLETED, Submission.CONFIRMED, Code.COMPLETED
        elif status == "permanent_failure" and upload:
            if category == "processing_error":
                remote, evidence = Remote.PROCESSING_FAILED, Code.PROCESSING_ERROR
            elif category in {"authorization", "request"}:
                blockers.append(
                    (Code.AUTHORIZATION if category == "authorization" else Code.RETRIEVAL, True)
                )
            else:
                blockers.append((Code.UNKNOWN_PHASE, False))
        if activity and remote != Remote.COMPLETED:
            blockers.append((Code.OUTCOME_CONFLICT, False))
        if extras or (not upload and remote not in {Remote.COMPLETED, Remote.DUPLICATE}):
            blockers.append((Code.HISTORY_UNKNOWN, False))
        db.execute(
            """INSERT INTO submission_attempts (
            stable_activity_id,kind,submission,remote,upload_id,activity_id,evidence_code,
            additional_attempts_unknown,intent_at,response_at,completed_at,fit_sha256,http_status,error_code)
            VALUES(?,'legacy_summary',?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                identifier,
                submission,
                remote,
                upload,
                activity,
                evidence,
                int(extras),
                timestamps[0],
                timestamps[1],
                timestamps[2],
                fit_hash,
                safe_http,
                safe_cause,
            ),
        )
    for code, observation in set(blockers):
        db.execute(
            """INSERT INTO recovery_blockers VALUES(?, 'activity', ?,1,?,1,
            'legacy','legacy',NULL)""",
            (identifier, code, int(observation)),
        )


def ensure_schema(
    db: sqlite3.Connection, path: Path, checkpoint: Callable[[str], None] = lambda _: None
) -> Path | None:
    """Return the new private backup path, if upgraded; never restore automatically."""
    try:
        tables = _tables(db)
        if not tables:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                for statement in DDL:
                    db.execute(statement)
                db.execute("INSERT INTO metadata VALUES('schema_version','2')")
                validate_v2(db)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            return None
        if "metadata" not in tables:
            raise ValidationError("Nonempty uploader state lacks version metadata")
        version = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if version is None or version[0] not in {"1", "2"}:
            raise ValidationError("Unsupported uploader state schema")
        if version[0] == "2":
            validate_v2(db)
            return None
        if tables != {"metadata", "uploads"}:
            raise ValidationError("Partial or malformed legacy schema")
        if {r[1] for r in db.execute("PRAGMA table_info(uploads)")} != V1_COLUMNS:
            raise ValidationError("Malformed legacy schema columns")
        _integrity(db)
        rows = db.execute("SELECT * FROM uploads").fetchall()
        checkpoint("validated")
        fd, name = tempfile.mkstemp(prefix=f"{path.name}.v1-", suffix=".backup", dir=path.parent)
        os.close(fd)
        backup = Path(name)
        with closing(sqlite3.connect(backup)) as copied:
            db.backup(copied)
            _integrity(copied)
            if copied.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[
                0
            ] != "1" or copied.execute("SELECT count(*) FROM uploads").fetchone()[0] != len(rows):
                raise ValidationError("Legacy backup validation failed")
        checkpoint("backup")
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in DDL:
                db.execute(statement)
            checkpoint("created")
            for row in rows:
                _map_row(db, row)
                checkpoint("mapped_row")
            block_shared_upload_ids(db)
            if db.execute("SELECT count(*) FROM recovery_activities").fetchone()[0] != len(rows):
                raise ValidationError("Legacy activity count mismatch")
            db.execute("DROP TABLE uploads")
            validate_v2(db)
            checkpoint("mapped")
            db.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
            checkpoint("versioned")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        checkpoint("committed")
        with closing(sqlite3.connect(path)) as reopened:
            reopened.row_factory = sqlite3.Row
            validate_v2(reopened)
        return backup
    except (sqlite3.DatabaseError, ValueError, TypeError, KeyError):
        raise ValidationError("Invalid uploader recovery database; preserved for review") from None
