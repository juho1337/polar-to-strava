"""Durable, monotonic SPEC-001 recovery evidence for one workspace."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.errors import ValidationError
from services.migration import file_sha256
from strava.artifacts import VerifiedArtifact
from strava.models import ManifestActivity, MigrationManifest
from strava.recovery import (
    LOCAL_CODES,
    OBSERVATION_BLOCKERS,
    TERMINAL,
    Action,
    ActionDecision,
    AttemptRecord,
    Blocker,
    Code,
    Origin,
    RecoveryRecord,
    Remote,
    ResponseEvidence,
    Submission,
    classify_actions,
    positive_id,
    submission_permission,
)
from strava.state_migration import block_shared_upload_ids, ensure_schema


class UploadState(StrEnum):
    """Read-only legacy presentation names; never submission authority."""

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


def _attempt(row: sqlite3.Row) -> AttemptRecord:
    # SQLite is the untyped boundary; schema and identifiers are validated on open.
    values: dict[str, Any] = dict(row)
    values["submission"] = Submission(values["submission"])
    values["remote"] = Remote(values["remote"])
    values["evidence_code"] = Code(values["evidence_code"])
    values["error_code"] = Code(values["error_code"])
    values["additional_attempts_unknown"] = bool(values["additional_attempts_unknown"])
    values["conflicting_ids"] = tuple(json.loads(values["conflicting_ids"]))
    return AttemptRecord(**values)


class UploadStateStore:
    def __init__(self, path: Path, *, checkpoint: Callable[[str], None] = lambda _: None) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._activities: dict[str, ManifestActivity] = {}
        try:
            self.backup_path = ensure_schema(self.connection, path, checkpoint)
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> UploadStateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.connection.in_transaction:
            raise ValidationError("Nested recovery transaction refused")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def load(self, identifier: str) -> RecoveryRecord:
        row = self.connection.execute(
            "SELECT * FROM recovery_activities WHERE stable_activity_id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise ValidationError("Unknown recovery activity")
        values: dict[str, Any] = dict(row)
        values["origin"] = Origin(values["origin"])
        values["present"], values["eligible"] = bool(values["present"]), bool(values["eligible"])
        values["attempts"] = tuple(
            _attempt(r)
            for r in self.connection.execute(
                "SELECT * FROM submission_attempts WHERE stable_activity_id=? ORDER BY attempt_id",
                (identifier,),
            )
        )
        values["blockers"] = tuple(
            Blocker(
                r["scope"],
                Code(r["code"]),
                bool(r["blocks_submission"]),
                bool(r["blocks_observation"]),
                bool(r["active"]),
            )
            for r in self.connection.execute(
                "SELECT * FROM recovery_blockers WHERE stable_activity_id=? ORDER BY scope,code",
                (identifier,),
            )
        )
        return RecoveryRecord(**values)

    def records(self) -> tuple[RecoveryRecord, ...]:
        identifiers = [
            r[0]
            for r in self.connection.execute(
                "SELECT stable_activity_id FROM recovery_activities ORDER BY stable_activity_id"
            )
        ]
        return tuple(self.load(identifier) for identifier in identifiers)

    def _touch(self, identifier: str) -> None:
        self.connection.execute(
            "UPDATE recovery_activities SET revision=revision+1 " "WHERE stable_activity_id=?",
            (identifier,),
        )

    def _block(self, identifier: str, scope: str, code: Code, active: bool) -> None:
        code = Code(code)  # reject arbitrary diagnostic strings at the write boundary
        if scope != "activity":
            prefix, _, suffix = scope.partition(":")
            if (
                prefix != "attempt"
                or not suffix.isdecimal()
                or not self.connection.execute(
                    "SELECT 1 FROM submission_attempts WHERE attempt_id=? AND stable_activity_id=?",
                    (suffix, identifier),
                ).fetchone()
            ):
                raise ValidationError("Invalid recovery blocker scope")
        stamp = now()
        self.connection.execute(
            """INSERT INTO recovery_blockers VALUES(?,?,?,1,?,?,?,?,?)
            ON CONFLICT(stable_activity_id,scope,code) DO UPDATE SET
            active=excluded.active,last_seen=excluded.last_seen,cleared_at=excluded.cleared_at""",
            (
                identifier,
                scope,
                code,
                int(code in OBSERVATION_BLOCKERS),
                int(active),
                stamp,
                stamp,
                None if active else stamp,
            ),
        )

    def set_blocker(self, identifier: str, scope: str, code: Code, active: bool = True) -> None:
        if not active:
            raise ValidationError("Only verified reset/reconciliation can clear blockers")
        with self.transaction():
            self._block(identifier, scope, code, True)
            self._touch(identifier)

    def reconcile(self, manifest: MigrationManifest, fingerprint: str) -> None:
        incoming = {a.stable_activity_id: a for a in manifest.activities}
        if len(incoming) != len(manifest.activities):
            raise ValidationError("Manifest contains duplicate stable activity IDs")
        with self.transaction() as db:
            existing = {r.stable_activity_id: r for r in self.records()}
            for identifier, activity in incoming.items():
                old = existing.get(identifier)
                started = activity.time.resolved_utc_start
                metadata = (
                    activity.fit.sha256,
                    activity.fit.relative_path,
                    activity.fit.size_bytes,
                    started.isoformat() if started else None,
                    activity.sport.domain,
                )
                if old is None:
                    db.execute(
                        """INSERT INTO recovery_activities (
                        stable_activity_id,manifest_version,present,eligible,accepted_fit_sha256,
                        current_fit_sha256,fit_relative_path,fit_size_bytes,resolved_utc_start,
                        sport,origin) VALUES(?,1,1,?,?,?,?,?,?,?,'fresh')""",
                        (identifier, int(activity.eligible), activity.fit.sha256, *metadata),
                    )
                else:
                    changed = (
                        old.current_fit_sha256 != activity.fit.sha256
                        or old.eligible != activity.eligible
                    )
                    if changed:
                        self._block(identifier, "activity", Code.MANIFEST_CHANGED, True)
                    db.execute(
                        """UPDATE recovery_activities SET present=1,eligible=?,
                        current_fit_sha256=?,fit_relative_path=?,fit_size_bytes=?,
                        resolved_utc_start=COALESCE(?,resolved_utc_start),sport=COALESCE(?,sport)
                        WHERE stable_activity_id=?""",
                        (int(activity.eligible), *metadata, identifier),
                    )
                self._block(identifier, "activity", Code.MANIFEST_MISSING, False)
                self._block(identifier, "activity", Code.INELIGIBLE, not activity.eligible)
                self._touch(identifier)
            for identifier in existing.keys() - incoming.keys():
                db.execute(
                    "UPDATE recovery_activities SET present=0 WHERE stable_activity_id=?",
                    (identifier,),
                )
                self._block(identifier, "activity", Code.MANIFEST_MISSING, True)
                self._touch(identifier)
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES('manifest_fingerprint',?)", (fingerprint,)
            )
        self._activities = incoming

    def begin_submission(
        self, identifier: str, expected_revision: int, artifact: VerifiedArtifact, rate_ready: bool
    ) -> AttemptRecord:
        with self.transaction() as db:
            record = self.load(identifier)
            if (
                record.revision != expected_revision
                or not submission_permission(
                    record, self._activities.get(identifier), artifact, rate_ready
                ).allowed
            ):
                raise ValidationError("Submission permission denied or stale")
            stamp = now()
            cursor = db.execute(
                """INSERT INTO submission_attempts (
                stable_activity_id,kind,submission,remote,evidence_code,intent_at,fit_sha256)
                VALUES(?,'submission','intent','not_started','intent',?,?)""",
                (identifier, stamp, artifact.sha256),
            )
            attempt_id = cursor.lastrowid
            db.execute(
                """UPDATE recovery_activities SET revision=revision+1,
                first_attempt_at=COALESCE(first_attempt_at,?),latest_attempt_at=?
                WHERE stable_activity_id=?""",
                (stamp, stamp, identifier),
            )
            assert attempt_id is not None
        return self._load_attempt(attempt_id)

    def _load_attempt(self, attempt_id: int) -> AttemptRecord:
        row = self.connection.execute(
            "SELECT * FROM submission_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise ValidationError("Unknown recovery attempt")
        return _attempt(row)

    def record_evidence(self, attempt_id: int, evidence: ResponseEvidence) -> None:
        code = Code(evidence.code)
        for value in (
            evidence.upload_id,
            evidence.activity_id,
            evidence.duplicate_activity_id,
            *evidence.conflicting_ids,
        ):
            if value is not None and (not isinstance(value, str) or positive_id(value) != value):
                raise ValidationError("Invalid response identifier")
        with self.transaction() as db:
            old = self._load_attempt(attempt_id)
            identifier, scope = old.stable_activity_id, f"attempt:{attempt_id}"
            remote = Remote(evidence.remote) if evidence.remote is not None else old.remote
            if evidence.upload_id and remote == Remote.NOT_STARTED:
                remote = Remote.DEFERRED
            conflicts = set(old.conflicting_ids) | set(evidence.conflicting_ids)
            id_conflict = bool(conflicts)
            if evidence.upload_id:
                other_attempts = db.execute(
                    "SELECT attempt_id,stable_activity_id FROM submission_attempts "
                    "WHERE upload_id=? AND stable_activity_id<>?",
                    (evidence.upload_id, identifier),
                ).fetchall()
                if other_attempts:
                    id_conflict = True
                    conflicts.add(evidence.upload_id)
                    for other in other_attempts:
                        self._block(
                            other["stable_activity_id"],
                            f"attempt:{other['attempt_id']}",
                            Code.ID_CONFLICT,
                            True,
                        )
                        self._touch(other["stable_activity_id"])
            for previous, new in (
                (old.upload_id, evidence.upload_id),
                (old.activity_id, evidence.activity_id),
                (old.duplicate_activity_id, evidence.duplicate_activity_id),
            ):
                if previous and new and previous != new:
                    id_conflict = True
            if old.upload_id and evidence.upload_id and old.upload_id != evidence.upload_id:
                conflicts.update((old.upload_id, evidence.upload_id))
            if id_conflict:
                self._block(identifier, scope, Code.ID_CONFLICT, True)
                remote = old.remote
            outcome_conflict = old.remote in TERMINAL and remote != old.remote
            if outcome_conflict:
                self._block(identifier, scope, Code.OUTCOME_CONFLICT, True)
                remote = old.remote
            if (
                remote == Remote.COMPLETED
                and not (old.activity_id or evidence.activity_id)
                or remote == Remote.DUPLICATE
                and not (
                    old.remote == Remote.DUPLICATE
                    or evidence.duplicate_activity_id
                    and (old.upload_id or evidence.upload_id)
                )
                or remote in {Remote.PROCESSING, Remote.DEFERRED, Remote.PROCESSING_FAILED}
                and not (old.upload_id or evidence.upload_id)
            ):
                raise ValidationError("Remote outcome lacks supporting identity")
            trusted = bool(evidence.upload_id or remote in {Remote.COMPLETED, Remote.DUPLICATE})
            submission = Submission.CONFIRMED if trusted and not id_conflict else old.submission
            if code not in {Code.NONE, Code.PROCESSING, Code.COMPLETED, Code.DUPLICATE}:
                self._block(identifier, scope, code, True)
            db.execute(
                """UPDATE submission_attempts SET submission=?,remote=?,
                upload_id=COALESCE(upload_id,?),activity_id=COALESCE(activity_id,?),
                duplicate_activity_id=COALESCE(duplicate_activity_id,?),conflicting_ids=?,
                evidence_code=?,response_at=?,completed_at=COALESCE(completed_at,?),
                http_status=?,error_code=? WHERE attempt_id=?""",
                (
                    submission,
                    remote,
                    None if id_conflict else evidence.upload_id,
                    None if id_conflict or outcome_conflict else evidence.activity_id,
                    None if id_conflict or outcome_conflict else evidence.duplicate_activity_id,
                    json.dumps(sorted(conflicts)),
                    old.evidence_code if old.remote in TERMINAL else code,
                    now(),
                    now() if remote in {Remote.COMPLETED, Remote.DUPLICATE} else None,
                    evidence.http_status,
                    code,
                    attempt_id,
                ),
            )
            self._touch(identifier)
            block_shared_upload_ids(db)

    def record_not_submitted(self, attempt_id: int, proof_code: Code) -> None:
        if proof_code != Code.CLIENT_PREFLIGHT:
            raise ValidationError("Positive pre-transmission proof required")
        with self.transaction() as db:
            old = self._load_attempt(attempt_id)
            if (
                old.submission != Submission.INTENT
                or old.remote != Remote.NOT_STARTED
                or old.upload_id
                or old.activity_id
                or old.conflicting_ids
            ):
                raise ValidationError("Cannot erase prior submission evidence")
            db.execute(
                "UPDATE submission_attempts SET submission='not_submitted',evidence_code=? "
                "WHERE attempt_id=?",
                (proof_code, attempt_id),
            )
            self._touch(old.stable_activity_id)

    def record_uncertain(self, attempt_id: int, reason: Code = Code.UNCERTAIN) -> None:
        reason = Code(reason)
        with self.transaction() as db:
            old = self._load_attempt(attempt_id)
            if old.submission == Submission.INTENT and old.remote == Remote.NOT_STARTED:
                db.execute(
                    "UPDATE submission_attempts SET submission='uncertain',error_code=? "
                    "WHERE attempt_id=?",
                    (reason, attempt_id),
                )
                self._touch(old.stable_activity_id)

    def defer_observation(
        self, attempt_id: int, reason: Code, http_status: int | None = None
    ) -> None:
        reason = Code(reason)
        with self.transaction() as db:
            old = self._load_attempt(attempt_id)
            if old.upload_id and old.remote not in TERMINAL:
                db.execute(
                    "UPDATE submission_attempts SET remote='deferred',error_code=?,"
                    "http_status=? WHERE attempt_id=?",
                    (reason, http_status, attempt_id),
                )
                if reason in OBSERVATION_BLOCKERS:
                    self._block(old.stable_activity_id, f"attempt:{attempt_id}", reason, True)
                self._touch(old.stable_activity_id)

    def reset(self, identifier: str, force: bool = False) -> tuple[ActionDecision, ...]:
        """The deprecated force argument intentionally has no effect on recovery."""
        record = self.load(identifier)
        activity = self._activities.get(identifier)
        corrected = False
        if activity is not None and activity.eligible and activity.fit.valid:
            relative = activity.fit.relative_path
            if relative and activity.fit.sha256:
                path = (self.path.parent / relative).resolve()
                try:
                    path.relative_to(self.path.parent.resolve())
                    corrected = path.is_file() and file_sha256(path) == activity.fit.sha256
                except (ValueError, OSError):
                    corrected = False
        with self.transaction() as db:
            if corrected and activity is not None:
                for blocker in record.blockers:
                    if blocker.code in LOCAL_CODES:
                        self._block(identifier, blocker.scope, blocker.code, False)
                db.execute(
                    "UPDATE recovery_activities SET accepted_fit_sha256=current_fit_sha256 "
                    "WHERE stable_activity_id=?",
                    (identifier,),
                )
            # Local auth validation is proof only that a bounded GET may be retried.
            if any(
                b.active and b.code in {Code.AUTHORIZATION, Code.RETRIEVAL} for b in record.blockers
            ):
                from core.errors import ConfigurationError
                from strava.client import REQUIRED_SCOPE, TokenStore

                try:
                    token = TokenStore(self.path.parent / ".strava-tokens.json").load()
                    repaired = REQUIRED_SCOPE in token.scope.split() and bool(token.refresh_token)
                except ConfigurationError:
                    repaired = False
                if repaired:
                    for blocker in record.blockers:
                        if blocker.code in {Code.AUTHORIZATION, Code.RETRIEVAL}:
                            self._block(identifier, blocker.scope, blocker.code, False)
            self._touch(identifier)
        return classify_actions(self.load(identifier))

    def summary(self) -> dict[str, int]:
        """Temporary local projection for existing UI; WP7 adds independent categories."""
        counts: Counter[str] = Counter()
        for record in self.records():
            if not record.present or not record.eligible:
                continue
            actions = classify_actions(record)
            outcomes = {a.remote for a in record.attempts}
            if Remote.COMPLETED in outcomes:
                counts["completed"] += 1
            elif Remote.DUPLICATE in outcomes:
                counts["duplicate"] += 1
            elif any(a.kind == Action.OBSERVE for a in actions):
                counts["processing"] += 1
            elif any(a.kind == Action.SUBMIT for a in actions):
                counts["pending"] += 1
            else:
                counts["uncertain"] += 1
        return {s.value: counts[s.value] for s in UploadState}
