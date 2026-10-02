"""Approved 2026-10-01 amendment: capacity exclusion is not network permission."""

from dataclasses import replace
from pathlib import Path

import pytest

from strava.artifacts import VerifiedArtifact
from strava.client import PreparedAccess
from strava.progress import snapshot
from strava.recovery import (
    Action,
    Code,
    RecoveryEvent,
    Remote,
    ResponseEvidence,
    classify_actions,
    consumes_submission_capacity,
    observation_permission,
)
from strava.state import UploadStateStore
from strava.uploader import Uploader
from tests.test_strava_scheduler import Clock, Pipeline, engine, pending
from tests.test_strava_uploader import multi_workspace, start_attempt


@pytest.mark.parametrize("reset", [None, False, True])
def test_three_persisted_stops_allow_five_fresh_with_capacity_three(
    tmp_path: Path, reset: bool | None
) -> None:
    root, ids = multi_workspace(tmp_path, 8)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        for index, identifier in enumerate(ids[:3]):
            attempt = start_attempt(store, uploader, identifier)
            store.record_evidence(
                attempt,
                ResponseEvidence(
                    upload_id=str(101 + index),
                    remote=Remote.PROCESSING,
                    code=Code.DUPLICATE_UNRECOGNIZED,
                ),
            )
        before = {i: store.load(i).attempts for i in ids[:3]}
        blockers = {i: store.load(i).blockers for i in ids[:3]}

    class Bounded(Pipeline):
        active: set[str]
        peak = 0

        def __init__(self) -> None:
            super().__init__(Clock())
            self.active = set()

        def upload(
            self, artifact: VerifiedArtifact, external_id: str, access: PreparedAccess
        ) -> ResponseEvidence:
            assert external_id in ids[3:]
            assert len(self.active) < 3
            result = super().upload(artifact, external_id, access)
            assert result.upload_id
            self.active.add(result.upload_id)
            self.peak = max(self.peak, len(self.active))
            return result

        def get_upload(
            self, upload_id: str, access: PreparedAccess, *, expected_identifier: str | None = None
        ) -> ResponseEvidence:
            assert upload_id in self.active
            result = super().get_upload(upload_id, access, expected_identifier=expected_identifier)
            self.active.remove(upload_id)
            return result

    client = Bounded()
    for rerun in (False, True):
        with UploadStateStore(path) as store:
            uploader = Uploader(root, store)
            if reset is not None:
                for identifier in ids[:3]:
                    assert {a.kind for a in store.reset(identifier, force=reset)} == {Action.REVIEW}
            selected = uploader.select(limit=5)
            assert sum(a.kind == Action.SUBMIT for a in selected) == (0 if rerun else 5)
            runner = engine(root, store, client, capacity=3)
            events: list[RecoveryEvent] = []
            runner.on_event = events.append
            runner.run(selected)
            for identifier in ids[:3]:
                record = store.load(identifier)
                assert record.attempts == before[identifier]
                assert record.blockers == blockers[identifier]
                assert {a.kind for a in classify_actions(record)} == {Action.REVIEW}
                assert not consumes_submission_capacity(record, record.attempts[0])
                assert not observation_permission(
                    record, record.attempts[0].attempt_id, True
                ).allowed
            state = snapshot(uploader.manifest, store.records())
            assert (state.resolved, state.needs_review, state.observing) == (5, 3, 0)
            assert state.capacity_jobs == 0 and state.duplicate_review_stops == 3
            assert events[-1].batch_unattempted == 0
            assert events[-1].batch_capacity_jobs == 0
            assert events[-1].batch_review_stops == 3
    assert len(client.posts) == 5 and len(client.gets) == 5 and client.peak == 3
    assert not client.active and not set(client.gets) & {"101", "102", "103"}


@pytest.mark.parametrize("reason", [Code.NONE, Code.NETWORK, Code.RATE_LIMIT])
def test_actionable_retained_jobs_still_hold_lower_restart_capacity(
    tmp_path: Path, reason: Code
) -> None:
    root, ids = multi_workspace(tmp_path, 5)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        for index, identifier in enumerate(ids[:3]):
            attempt = start_attempt(store, uploader, identifier)
            store.record_evidence(
                attempt, ResponseEvidence(upload_id=str(101 + index), remote=Remote.PROCESSING)
            )
            if reason != Code.NONE:
                store.defer_observation(attempt, reason)
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        client = Pipeline(Clock())
        client.observe = pending
        events: list[RecoveryEvent] = []
        runner = engine(root, store, client, capacity=1, polls=1)
        runner.on_event = events.append
        runner.run(uploader.select(limit=2))
        assert not client.posts and set(client.gets) == {"101", "102", "103"}
        assert all(not store.load(i).attempts for i in ids[3:])
        state = snapshot(uploader.manifest, store.records())
        assert state.capacity_jobs == 3 and state.duplicate_review_stops == 0
        assert events[-1].batch_capacity_jobs == 3 and events[-1].batch_unattempted == 2


def test_capacity_does_not_follow_generic_observation_refusal(tmp_path: Path) -> None:
    root, ids = multi_workspace(tmp_path, 1)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, ids[0])
        store.record_evidence(attempt, ResponseEvidence(upload_id="77", remote=Remote.PROCESSING))
        store.set_blocker(ids[0], f"attempt:{attempt}", Code.AUTHORIZATION)
        record = store.load(ids[0])
        assert not observation_permission(record, attempt, True).allowed
        assert consumes_submission_capacity(record, record.attempts[0])
        # Stronger terminal evidence and absent IDs never become capacity jobs.
        assert not consumes_submission_capacity(
            record, replace(record.attempts[0], remote=Remote.COMPLETED)
        )
        assert not consumes_submission_capacity(record, replace(record.attempts[0], upload_id=None))
