"""SPEC-001 evidence values and pure action authorization.

Candidate classification is not permission to send a request. The store repeats
the final positive permission check while committing intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from strava.artifacts import VerifiedArtifact
from strava.models import ManifestActivity


class Submission(StrEnum):
    INTENT = "intent"
    NOT_SUBMITTED = "not_submitted"
    UNCERTAIN = "uncertain"
    CONFIRMED = "confirmed"
    LEGACY_UNKNOWN = "legacy_unknown"


class Remote(StrEnum):
    NOT_STARTED = "not_started"
    PROCESSING = "processing"
    DEFERRED = "deferred"
    PROCESSING_FAILED = "processing_failed"
    COMPLETED = "completed"
    DUPLICATE = "duplicate"


class Origin(StrEnum):
    FRESH = "fresh"
    LEGACY_FRESH = "legacy_fresh"
    LEGACY_REVIEW = "legacy_review"


class Operation(StrEnum):
    SUBMIT = "submit"
    OBSERVE = "observe"


class Action(StrEnum):
    SUBMIT = "submit"
    OBSERVE = "observe"
    REVIEW = "review"
    RESOLVED = "resolved"


class Code(StrEnum):
    NONE = "none"
    FRESH = "fresh"
    INTENT = "intent"
    NOT_SENT = "not_sent"
    CLIENT_PREFLIGHT = "client_preflight"
    UNCERTAIN = "uncertain"
    LEGACY = "legacy"
    LEGACY_DUPLICATE = "legacy_duplicate"
    HISTORY_UNKNOWN = "history_unknown"
    MALFORMED_STATE = "malformed_state"
    MALFORMED_RESPONSE = "malformed_response"
    ID_CONFLICT = "id_conflict"
    OUTCOME_CONFLICT = "outcome_conflict"
    MISSING_UPLOAD_ID = "missing_upload_id"
    UNKNOWN_PHASE = "unknown_phase"
    MANIFEST_CHANGED = "manifest_changed"
    MANIFEST_MISSING = "manifest_missing"
    INELIGIBLE = "ineligible"
    MISSING_FIT = "missing_fit"
    FIT_HASH_MISMATCH = "fit_hash_mismatch"
    INVALID_ARTIFACT = "invalid_artifact"
    AUTHORIZATION = "authorization"
    RETRIEVAL = "retrieval"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    SERVER = "server"
    REQUEST = "request"
    POLL_BUDGET = "poll_budget"
    PROCESSING = "processing"
    PROCESSING_ERROR = "processing_error"
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    DUPLICATE_UNRECOGNIZED = "duplicate_unrecognized"
    SKIPPED = "skipped"
    DATE_UNAVAILABLE = "date_unavailable_use_explicit_id_or_all"
    STALE_PROOF = "stale_proof"


TERMINAL = frozenset({Remote.COMPLETED, Remote.DUPLICATE, Remote.PROCESSING_FAILED})
LOCAL_CODES = frozenset(
    {
        Code.MANIFEST_CHANGED,
        Code.MANIFEST_MISSING,
        Code.INELIGIBLE,
        Code.MISSING_FIT,
        Code.FIT_HASH_MISMATCH,
        Code.INVALID_ARTIFACT,
    }
)
OBSERVATION_BLOCKERS = frozenset(
    {Code.AUTHORIZATION, Code.RETRIEVAL, Code.ID_CONFLICT, Code.MALFORMED_STATE}
)


def positive_id(value: object) -> str | None:
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value) and value.strip("0"):
        return value.lstrip("0")
    return None


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_id: int
    stable_activity_id: str
    kind: str
    submission: Submission
    remote: Remote = Remote.NOT_STARTED
    upload_id: str | None = None
    activity_id: str | None = None
    duplicate_activity_id: str | None = None
    evidence_code: Code = Code.NONE
    additional_attempts_unknown: bool = False
    conflicting_ids: tuple[str, ...] = ()
    intent_at: str | None = None
    response_at: str | None = None
    completed_at: str | None = None
    fit_sha256: str | None = None
    http_status: int | None = None
    error_code: Code = Code.NONE


@dataclass(frozen=True, slots=True)
class Blocker:
    scope: str
    code: Code
    blocks_submission: bool = True
    blocks_observation: bool = False
    active: bool = True


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    stable_activity_id: str
    manifest_version: int
    present: bool
    eligible: bool
    accepted_fit_sha256: str | None
    current_fit_sha256: str | None
    fit_relative_path: str | None
    fit_size_bytes: int | None
    resolved_utc_start: str | None
    sport: str | None
    origin: Origin
    revision: int = 0
    legacy_attempt_count: int = 0
    first_attempt_at: str | None = None
    latest_attempt_at: str | None = None
    legacy_status: str | None = None
    attempts: tuple[AttemptRecord, ...] = ()
    blockers: tuple[Blocker, ...] = ()


@dataclass(frozen=True, slots=True)
class ResponseEvidence:
    upload_id: str | None = None
    activity_id: str | None = None
    duplicate_activity_id: str | None = None
    conflicting_ids: tuple[str, ...] = ()
    remote: Remote | None = None
    code: Code = Code.NONE
    http_status: int | None = None


@dataclass(frozen=True, slots=True)
class Permission:
    allowed: bool
    reasons: tuple[Code, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionDecision:
    kind: Action
    stable_activity_id: str
    attempt_id: int | None = None
    upload_id: str | None = None
    reasons: tuple[Code, ...] = ()


@dataclass(frozen=True, slots=True)
class RecoveryEvent:
    identifier: str
    action: Action
    reason_codes: tuple[Code, ...] = ()


def _submission_reasons(record: RecoveryRecord) -> list[Code]:
    reasons = [b.code for b in record.blockers if b.active and b.blocks_submission]
    if not record.present:
        reasons.append(Code.MANIFEST_MISSING)
    if not record.eligible:
        reasons.append(Code.INELIGIBLE)
    if record.origin not in {Origin.FRESH, Origin.LEGACY_FRESH}:
        reasons.append(Code.HISTORY_UNKNOWN)
    if record.legacy_attempt_count != 0:
        reasons.append(Code.HISTORY_UNKNOWN)
    for attempt in record.attempts:
        if (
            attempt.submission != Submission.NOT_SUBMITTED
            or attempt.evidence_code != Code.CLIENT_PREFLIGHT
            or attempt.remote != Remote.NOT_STARTED
            or attempt.upload_id
            or attempt.activity_id
            or attempt.duplicate_activity_id
            or attempt.additional_attempts_unknown
            or attempt.conflicting_ids
        ):
            reasons.append(Code.HISTORY_UNKNOWN)
    return reasons


def submission_permission(
    record: RecoveryRecord,
    activity: ManifestActivity | None,
    artifact: VerifiedArtifact | None,
    rate_ready: bool,
) -> Permission:
    reasons = _submission_reasons(record)
    if activity is None or activity.stable_activity_id != record.stable_activity_id:
        reasons.append(Code.MANIFEST_MISSING)
    elif (
        not activity.eligible
        or not activity.fit.valid
        or not activity.fit.relative_path
        or activity.fit.sha256 != record.current_fit_sha256
    ):
        reasons.append(Code.INVALID_ARTIFACT)
    if (
        artifact is None
        or artifact.stream.closed
        or artifact.stable_activity_id != record.stable_activity_id
        or artifact.revision != record.revision
        or not re.fullmatch(r"[0-9a-f]{64}", artifact.sha256)
        or artifact.sha256 != record.current_fit_sha256
        or artifact.sha256 != record.accepted_fit_sha256
        or artifact.size_bytes < 0
    ):
        reasons.append(Code.STALE_PROOF)
    if not rate_ready:
        reasons.append(Code.RATE_LIMIT)
    return Permission(not reasons, tuple(dict.fromkeys(reasons)))


def observation_permission(record: RecoveryRecord, attempt_id: int, rate_ready: bool) -> Permission:
    attempt = next((a for a in record.attempts if a.attempt_id == attempt_id), None)
    reasons: list[Code] = []
    if (
        attempt is None
        or positive_id(attempt.upload_id) != attempt.upload_id
        or not attempt.upload_id
    ):
        reasons.append(Code.MISSING_UPLOAD_ID)
    elif attempt.remote not in {Remote.PROCESSING, Remote.DEFERRED}:
        reasons.append(
            Code.PROCESSING_ERROR
            if attempt.remote == Remote.PROCESSING_FAILED
            else Code.OUTCOME_CONFLICT
        )
    if attempt is not None and attempt.conflicting_ids:
        reasons.append(Code.ID_CONFLICT)
    for blocker in record.blockers:
        if (
            blocker.active
            and blocker.blocks_observation
            and blocker.scope in {"activity", f"attempt:{attempt_id}"}
        ):
            reasons.append(blocker.code)
    if not rate_ready:
        reasons.append(Code.RATE_LIMIT)
    return Permission(not reasons, tuple(dict.fromkeys(reasons)))


def classify_actions(record: RecoveryRecord) -> tuple[ActionDecision, ...]:
    actions: list[ActionDecision] = []
    review = [b.code for b in record.blockers if b.active]
    for attempt in record.attempts:
        terminal_valid = attempt.submission == Submission.CONFIRMED and (
            attempt.remote == Remote.COMPLETED
            and positive_id(attempt.activity_id) is not None
            or attempt.remote == Remote.DUPLICATE
            and (
                attempt.evidence_code == Code.LEGACY_DUPLICATE
                or positive_id(attempt.upload_id) is not None
                and positive_id(attempt.duplicate_activity_id) is not None
            )
        )
        if attempt.remote in {Remote.COMPLETED, Remote.DUPLICATE} and terminal_valid:
            actions.append(
                ActionDecision(
                    Action.RESOLVED,
                    record.stable_activity_id,
                    attempt.attempt_id,
                    attempt.upload_id,
                )
            )
        elif attempt.remote in {Remote.COMPLETED, Remote.DUPLICATE}:
            review.append(Code.MALFORMED_STATE)
        elif observation_permission(record, attempt.attempt_id, True).allowed:
            actions.append(
                ActionDecision(
                    Action.OBSERVE, record.stable_activity_id, attempt.attempt_id, attempt.upload_id
                )
            )
        elif attempt.submission != Submission.NOT_SUBMITTED:
            review.append(
                Code.PROCESSING_ERROR
                if attempt.remote == Remote.PROCESSING_FAILED
                else Code.HISTORY_UNKNOWN
            )
        if attempt.additional_attempts_unknown:
            review.append(Code.HISTORY_UNKNOWN)
    if not _submission_reasons(record):
        actions.append(ActionDecision(Action.SUBMIT, record.stable_activity_id))
    elif not actions and not review:
        review.extend(_submission_reasons(record))
    if record.origin == Origin.LEGACY_REVIEW and not actions:
        review.append(Code.HISTORY_UNKNOWN)
    if review:
        actions.append(
            ActionDecision(
                Action.REVIEW, record.stable_activity_id, reasons=tuple(dict.fromkeys(review))
            )
        )
    return tuple(actions)
