"""Evidence-based selection and safe submission/observation orchestration."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from core.errors import ValidationError
from strava.artifacts import ArtifactFailure, verified_snapshot
from strava.client import FailurePhase, PreparedAccess, RequestFailure, UploadClient
from strava.models import MigrationManifest
from strava.progress import ProgressSnapshot, snapshot
from strava.rate_limit import DailyLimitReached, RateLimitPolicy
from strava.recovery import (
    Action,
    ActionDecision,
    Code,
    Operation,
    RecoveryEvent,
    Remote,
    classify_actions,
    observation_permission,
    submission_permission,
)
from strava.state import UploadStateStore


@dataclass(slots=True)
class ProcessingJob:
    identifier: str
    attempt_id: int
    upload_id: str
    due: float
    interval: float
    polls: int = 0
    failures: int = 0
    deferred: bool = False


class Uploader:
    def __init__(
        self,
        workspace: Path,
        store: UploadStateStore,
        client: UploadClient | None = None,
        *,
        policy: RateLimitPolicy | None = None,
        clock: Callable[[], float] = time.time,
        on_event: Callable[[RecoveryEvent], None] | None = None,
        max_in_flight: int = 3,
        poll_interval: float = 2,
        max_polls: int = 60,
        max_retries: int = 3,
    ) -> None:
        self.workspace, self.store = workspace, store
        self.client = client
        self.policy = policy or RateLimitPolicy(sleep=time.sleep)
        self.clock, self.on_event = clock, on_event
        self.max_in_flight, self.poll_interval = max_in_flight, poll_interval
        self.max_polls, self.max_retries = max_polls, max_retries
        self.manifest, fingerprint = MigrationManifest.load(workspace)
        store.reconcile(self.manifest, fingerprint)

    def select(
        self,
        *,
        limit: int | None = None,
        activity_id: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[ActionDecision]:
        if limit is not None and limit < 1:
            raise ValidationError("Selection limit must be positive")
        selected: list[ActionDecision] = []
        new = 0
        records = self.store.records()
        if activity_id is not None and not any(
            r.stable_activity_id == activity_id for r in records
        ):
            raise ValidationError("Unknown recovery activity")
        for record in records:
            if activity_id is not None and record.stable_activity_id != activity_id:
                continue
            if from_date or to_date:
                started = None
                try:
                    if record.resolved_utc_start:
                        parsed = datetime.fromisoformat(record.resolved_utc_start)
                        if parsed.tzinfo:
                            started = parsed.date()
                except ValueError:
                    pass
                if started is None:
                    selected.append(
                        ActionDecision(
                            Action.REVIEW,
                            record.stable_activity_id,
                            reasons=(Code.DATE_UNAVAILABLE,),
                        )
                    )
                    continue
                if from_date and started < from_date or to_date and started > to_date:
                    continue
            for action in classify_actions(record):
                if action.kind == Action.SUBMIT:
                    if limit is not None and new >= limit:
                        continue
                    new += 1
                selected.append(action)
        return selected

    def preview(self, selected: list[ActionDecision]) -> list[ActionDecision]:
        """Check local artifacts without access preparation, reservations or submission intent."""
        activities = {a.stable_activity_id: a for a in self.manifest.activities}
        result: list[ActionDecision] = []
        seen: set[str] = set()
        for decision in selected:
            identifier = decision.stable_activity_id
            if identifier in seen:
                continue
            seen.add(identifier)
            if Code.DATE_UNAVAILABLE in decision.reasons:
                result.append(decision)
                continue
            record = self.store.load(identifier)
            activity = activities.get(identifier)
            local_reasons: tuple[Code, ...] = ()
            if activity is not None:
                try:
                    with verified_snapshot(self.workspace, activity, record.revision) as artifact:
                        if any(a.kind == Action.SUBMIT for a in classify_actions(record)):
                            permission = submission_permission(record, activity, artifact, True)
                            local_reasons = permission.reasons
                except ArtifactFailure as error:
                    self.store.set_blocker(identifier, "activity", error.code)
            actions = classify_actions(self.store.load(identifier))
            if local_reasons:
                actions = tuple(a for a in actions if a.kind != Action.SUBMIT) + (
                    ActionDecision(Action.REVIEW, identifier, reasons=local_reasons),
                )
            result.extend(actions)
        return result

    def run(self, activities: list[ActionDecision], dry_run: bool = False) -> ProgressSnapshot:
        if dry_run:
            self.preview(activities)
        elif any(a.kind in {Action.SUBMIT, Action.OBSERVE} for a in activities):
            if self.client is None:
                raise ValidationError("Upload execution requires a client")
            _RecoveryRunner(
                self.workspace,
                self.store,
                self.client,
                self.policy,
                clock=self.clock,
                on_event=self.on_event,
                max_in_flight=self.max_in_flight,
                poll_interval=self.poll_interval,
                max_polls=self.max_polls,
                max_retries=self.max_retries,
            ).run(activities)
            self.manifest, _ = MigrationManifest.load(self.workspace)
        return snapshot(self.manifest, self.store.records())


class _RecoveryRunner:
    """Single safe runner shared by production composition and synthetic tests."""

    def __init__(
        self,
        workspace: Path,
        store: UploadStateStore,
        client: UploadClient,
        policy: RateLimitPolicy,
        *,
        clock: Callable[[], float],
        on_event: Callable[[RecoveryEvent], None] | None = None,
        max_in_flight: int = 3,
        poll_interval: float = 2,
        max_polls: int = 60,
        max_retries: int = 3,
    ) -> None:
        if not 1 <= max_in_flight <= 10 or max_polls < 1 or max_retries < 1:
            raise ValueError("Invalid observation capacity or budget")
        self.workspace, self.store, self.client = workspace, store, client
        self.policy, self.clock, self.on_event = policy, clock, on_event
        self.capacity, self.max_polls, self.max_retries = max_in_flight, max_polls, max_retries
        self.poll_interval = max(1.0, poll_interval)
        self.stopped = False

    def _access(self, *, read: bool = False) -> PreparedAccess:
        while True:
            access = self.client.prepare_access()
            if self.policy.before_request(self.client.rate_limit, read=read):
                self.client.rate_limit = None
                continue
            return access

    def _notify(self, identifier: str, reason: Code | None = None) -> None:
        if self.on_event:
            for action in classify_actions(self.store.load(identifier)):
                reasons = action.reasons + (
                    (reason,) if reason and reason not in action.reasons else ()
                )
                self.on_event(RecoveryEvent(identifier, action.kind, reasons))

    def submit(self, identifier: str) -> None:
        # Only stale pre-intent preparation repeats. An attempted POST never does.
        while not self._prepared_submission(identifier):
            pass

    def _prepared_submission(self, identifier: str) -> bool:
        if not any(a.kind == Action.SUBMIT for a in classify_actions(self.store.load(identifier))):
            return True
        try:
            access = self._access()
        except DailyLimitReached:
            self.stopped = True
            self._notify(identifier, Code.RATE_LIMIT)
            return True
        except RequestFailure as error:
            self.store.set_blocker(identifier, "activity", error.code)
            self._notify(identifier)
            return True
        manifest, fingerprint = MigrationManifest.load(self.workspace)
        self.store.reconcile(manifest, fingerprint)
        activity = next(
            (a for a in manifest.activities if a.stable_activity_id == identifier), None
        )
        if activity is None:
            return True
        record = self.store.load(identifier)
        try:
            with verified_snapshot(self.workspace, activity, record.revision) as artifact:
                # Slow copying can outlive access readiness. Discard, never refresh here.
                if access.expires_at <= self.clock():
                    return False
                record = self.store.load(identifier)
                if not submission_permission(record, activity, artifact, True).allowed:
                    return True
                attempt = self.store.begin_submission(identifier, record.revision, artifact, True)
                try:
                    evidence = self.client.upload(artifact, identifier, access)
                except RequestFailure as error:
                    if (
                        error.operation == Operation.SUBMIT
                        and error.phase == FailurePhase.NOT_SENT
                        and error.code
                        in {
                            Code.CLIENT_PREFLIGHT,
                            Code.AUTHORIZATION,
                            Code.RATE_LIMIT,
                            Code.INVALID_ARTIFACT,
                        }
                        and error.status_code is None
                        and error.evidence.http_status is None
                        and not error.evidence.upload_id
                        and not error.evidence.activity_id
                        and not error.evidence.duplicate_activity_id
                        and not error.evidence.conflicting_ids
                        and error.evidence.remote is None
                        and error.evidence.code in {Code.NONE, error.code}
                    ):
                        self.store.record_not_submitted(attempt.attempt_id, Code.CLIENT_PREFLIGHT)
                    else:
                        self.store.record_evidence(attempt.attempt_id, error.evidence)
                        self.store.record_uncertain(attempt.attempt_id, error.code)
                    if error.code == Code.RATE_LIMIT:
                        self.stopped = True
                except BaseException:
                    self.store.record_uncertain(attempt.attempt_id)
                    raise
                else:
                    self.store.record_evidence(attempt.attempt_id, evidence)
                    self.store.record_uncertain(attempt.attempt_id)
                self._notify(identifier)
        except ArtifactFailure as error:
            self.store.set_blocker(identifier, "activity", error.code)
            self._notify(identifier)
        return True

    def _restore(
        self, identifiers: set[str], jobs: dict[int, ProcessingJob], *, restored: bool
    ) -> None:
        retained: set[int] = set()
        for identifier in identifiers:
            record = self.store.load(identifier)
            for attempt in record.attempts:
                if not attempt.upload_id or attempt.remote not in {
                    Remote.PROCESSING,
                    Remote.DEFERRED,
                }:
                    continue
                retained.add(attempt.attempt_id)
                if attempt.attempt_id not in jobs:
                    jobs[attempt.attempt_id] = ProcessingJob(
                        identifier,
                        attempt.attempt_id,
                        attempt.upload_id,
                        self.clock() + (0 if restored else self.poll_interval),
                        self.poll_interval,
                    )
                if not observation_permission(record, attempt.attempt_id, True).allowed:
                    jobs[attempt.attempt_id].deferred = True
        for attempt_id in jobs.keys() - retained:
            del jobs[attempt_id]

    def _observe(self, job: ProcessingJob) -> None:
        try:
            access = self._access(read=True)
        except DailyLimitReached:
            self.store.defer_observation(job.attempt_id, Code.RATE_LIMIT)
            self.stopped = True
            self._notify(job.identifier, Code.RATE_LIMIT)
            return
        except RequestFailure as error:
            self.store.defer_observation(job.attempt_id, error.code)
            job.deferred = True
            self._notify(job.identifier, error.code)
            return
        if not observation_permission(
            self.store.load(job.identifier), job.attempt_id, True
        ).allowed:
            job.deferred = True
            return
        job.polls += 1
        try:
            evidence = self.client.get_upload(job.upload_id, access)
        except RequestFailure as error:
            # Preserve partial/conflicting evidence before adding a retrieval deferral.
            self.store.record_evidence(job.attempt_id, error.evidence)
            reason = Code.RETRIEVAL if error.code == Code.REQUEST else error.code
            self.store.defer_observation(job.attempt_id, reason, error.status_code)
            if error.code == Code.RATE_LIMIT:
                self.stopped = True
            if error.code in {Code.NETWORK, Code.SERVER}:
                job.failures += 1
                job.deferred = job.failures >= self.max_retries
            else:
                job.deferred = True
        except KeyboardInterrupt:
            self.store.defer_observation(job.attempt_id, Code.NETWORK)
            raise
        else:
            self.store.record_evidence(job.attempt_id, evidence)
            job.failures = 0
        if job.polls >= self.max_polls:
            self.store.defer_observation(job.attempt_id, Code.POLL_BUDGET)
            job.deferred = True
        job.interval = min(30.0, job.interval * 2)
        job.due = self.clock() + job.interval
        attempt = next(
            item
            for item in self.store.load(job.identifier).attempts
            if item.attempt_id == job.attempt_id
        )
        event_reason = (
            attempt.error_code
            if attempt.remote == Remote.DEFERRED and attempt.error_code != Code.NONE
            else None
        )
        self._notify(job.identifier, event_reason)

    def run(self, selected: list[ActionDecision]) -> tuple[ActionDecision, ...]:
        """Run selected internal jobs; each invocation starts fresh observation budgets."""
        self.stopped = False
        # Missing date metadata is an explanation, not selection authorization.
        excluded = tuple(a for a in selected if Code.DATE_UNAVAILABLE in a.reasons)
        excluded_ids = {a.stable_activity_id for a in excluded}
        identifiers = {a.stable_activity_id for a in selected} - excluded_ids
        pending = list(
            dict.fromkeys(
                a.stable_activity_id
                for a in selected
                if a.kind == Action.SUBMIT and a.stable_activity_id in identifiers
            )
        )
        jobs: dict[int, ProcessingJob] = {}
        self._restore(identifiers, jobs, restored=True)
        try:
            while not self.stopped:
                while pending and len(jobs) < self.capacity and not self.stopped:
                    self.submit(pending.pop(0))
                    self._restore(identifiers, jobs, restored=False)
                if self.stopped:
                    break
                active = [job for job in jobs.values() if not job.deferred]
                if not active:
                    break
                job = min(active, key=lambda item: (item.due, item.attempt_id))
                delay = job.due - self.clock()
                if delay > 0:
                    self.policy.sleep(delay)
                self._observe(job)
                self._restore(identifiers, jobs, restored=False)
        except KeyboardInterrupt:
            self.stopped = True
        return excluded + tuple(
            action
            for identifier in sorted(identifiers)
            for action in classify_actions(self.store.load(identifier))
        )
