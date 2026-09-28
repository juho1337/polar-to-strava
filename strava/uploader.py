"""Local recovery selection foundation. Execution is disabled until integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from core.errors import ValidationError
from strava.artifacts import ArtifactFailure, verified_snapshot
from strava.client import FailurePhase, PreparedAccess, RequestFailure, UploadClient
from strava.models import MigrationManifest
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

INTEGRATION_INCOMPLETE = "recovery integration incomplete"


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
        client: object | None = None,
        **options: object,
    ) -> None:
        self.workspace, self.store = workspace, store
        # Never inspect/construct/use an injected client during foundation work.
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

    def run(self, activities: list[ActionDecision], dry_run: bool = False) -> dict[str, int]:
        # Authoritative guard: no escape hatch, client access, callback or scheduler.
        if not dry_run:
            raise ValidationError(INTEGRATION_INCOMPLETE)
        return self.store.summary()


class _RecoveryRunner:
    """Internal composition under development; neither production guard invokes it.

    Dependencies are mandatory. There is no live-client factory, flag or guard bypass.
    WP4/WP5 integration tests compose this with synthetic workspaces and transports.
    """

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

    def _notify(self, identifier: str) -> None:
        if self.on_event:
            for action in classify_actions(self.store.load(identifier)):
                self.on_event(RecoveryEvent(identifier, action.kind, action.reasons))

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
            return True
        except RequestFailure as error:
            self.store.set_blocker(identifier, "activity", error.code)
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
            return
        except RequestFailure as error:
            self.store.defer_observation(job.attempt_id, error.code)
            job.deferred = True
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
        self._notify(job.identifier)

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
