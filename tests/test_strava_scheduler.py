"""SPEC-001 WP5: synchronous observation, capacity, rate and restart evidence."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from strava.artifacts import VerifiedArtifact
from strava.client import FailurePhase, PreparedAccess, RequestFailure
from strava.models import MigrationManifest, RateLimit
from strava.rate_limit import RateLimitPolicy
from strava.recovery import Action, Code, Operation, Remote, ResponseEvidence
from strava.state import UploadStateStore
from strava.uploader import Uploader, _RecoveryRunner
from tests.test_strava_artifacts import Transport
from tests.test_strava_uploader import multi_workspace, workspace


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def utc(self) -> datetime:
        return datetime.fromtimestamp(self.now, UTC)


class Pipeline(Transport):
    def __init__(self, clock: Clock) -> None:
        super().__init__()
        self.clock = clock
        self.requests: list[tuple[str, str, float]] = []
        self.observe: Callable[[str], ResponseEvidence] = self.complete

    @staticmethod
    def complete(upload_id: str) -> ResponseEvidence:
        return ResponseEvidence(
            upload_id=upload_id,
            activity_id=str(1000 + int(upload_id)),
            remote=Remote.COMPLETED,
            code=Code.COMPLETED,
        )

    def upload(
        self, artifact: VerifiedArtifact, external_id: str, access: PreparedAccess
    ) -> ResponseEvidence:
        self.requests.append(("POST", external_id, self.clock.now))
        super().upload(artifact, external_id, access)
        return ResponseEvidence(
            upload_id=str(len(self.posts)), remote=Remote.PROCESSING, code=Code.PROCESSING
        )

    def get_upload(self, upload_id: str, access: PreparedAccess) -> ResponseEvidence:
        self.requests.append(("GET", upload_id, self.clock.now))
        self.gets.append(upload_id)
        return self.observe(upload_id)


def engine(
    root: Path, store: UploadStateStore, client: Pipeline, *, capacity: int = 3, polls: int = 60
) -> _RecoveryRunner:
    clock = client.clock
    return _RecoveryRunner(
        root,
        store,
        client,
        RateLimitPolicy(clock=clock.utc, sleep=clock.sleep),
        clock=clock.time,
        max_in_flight=capacity,
        max_polls=polls,
    )


def pending(upload_id: str) -> ResponseEvidence:
    return ResponseEvidence(upload_id=upload_id, remote=Remote.PROCESSING, code=Code.PROCESSING)


def test_async_completion_restart_no_post(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.COMPLETED
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
    assert [item[0] for item in client.requests] == ["POST", "GET"]
    assert client.requests[1][2] == 2


@pytest.mark.parametrize("reason", [Code.NETWORK, Code.SERVER, Code.POLL_BUDGET])
def test_poll_failure_and_budget_restart_get_only(tmp_path: Path, reason: Code) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())

    def failed(upload_id: str) -> ResponseEvidence:
        raise RequestFailure(
            Operation.OBSERVE, FailurePhase.OBSERVATION, reason, evidence=pending(upload_id)
        )

    client.observe = pending if reason == Code.POLL_BUDGET else failed
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client, polls=5).run(uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.DEFERRED
        assert len(client.gets) == (5 if reason == Code.POLL_BUDGET else 3)
    before = client.clock.now
    client.observe = client.complete
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.COMPLETED
    assert len(client.posts) == 1
    assert set(client.gets) == {"1"}
    assert client.requests[-1][2] == before


def test_deferred_capacity_and_lower_capacity_restore(tmp_path: Path) -> None:
    root, identifiers = multi_workspace(tmp_path, 5)
    client = Pipeline(Clock())
    client.observe = pending
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client, polls=1).run(uploader.select())
        assert [r[0] for r in client.requests] == ["POST"] * 3 + ["GET"] * 3
        assert all(not store.load(i).attempts for i in identifiers[3:])
    client.requests.clear()
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client, capacity=1, polls=1).run(uploader.select())
    assert [r[:2] for r in client.requests] == [("GET", "1"), ("GET", "2"), ("GET", "3")]
    assert len(client.posts) == 3


@pytest.mark.parametrize("reason", [Code.RATE_LIMIT, Code.AUTHORIZATION, Code.REQUEST])
def test_get_failure_preserves_id_and_batch_policy(tmp_path: Path, reason: Code) -> None:
    root, identifiers = multi_workspace(tmp_path, 2)
    client = Pipeline(Clock())

    def observe(upload_id: str) -> ResponseEvidence:
        if upload_id == "1":
            raise RequestFailure(
                Operation.OBSERVE,
                FailurePhase.OBSERVATION,
                reason,
                status_code=429 if reason == Code.RATE_LIMIT else 404,
                evidence=pending(upload_id),
            )
        return client.complete(upload_id)

    client.observe = observe
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        run = engine(root, store, client)
        run.run(uploader.select())
        assert store.load(identifiers[0]).attempts[0].upload_id == "1"
        assert not any(a.kind == Action.SUBMIT for a in uploader.select())
        assert client.gets == (["1"] if reason == Code.RATE_LIMIT else ["1", "2"])
        assert store.load(identifiers[1]).attempts[0].remote == (
            Remote.PROCESSING if reason == Code.RATE_LIMIT else Remote.COMPLETED
        )
        assert run.stopped == (reason == Code.RATE_LIMIT)


@pytest.mark.parametrize("local", ["missing", "changed", "ineligible", "orphan"])
def test_observation_independent_of_artifact(tmp_path: Path, local: str) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        engine(root, store, client).submit(identifier)
        if local == "missing":
            (root / "fits/activity.fit").unlink()
        elif local == "changed":
            (root / "fits/activity.fit").write_bytes(b"different")
            store.set_blocker(identifier, "activity", Code.FIT_HASH_MISMATCH)
        else:
            manifest, _ = MigrationManifest.load(root)
            if local == "orphan":
                manifest.activities.clear()
            else:
                manifest.activities[0].migration.status = "excluded"
            (root / "migration-manifest.json").write_text(
                manifest.model_dump_json(), encoding="utf-8"
            )
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
        assert client.gets == ["1"] and len(client.posts) == 1
        assert store.load(identifier).attempts[0].remote == Remote.COMPLETED


def test_pending_resets_failure_budget_and_backoff_is_bounded(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    client = Pipeline(Clock())

    def intermittent(upload_id: str) -> ResponseEvidence:
        if len(client.gets) % 3:
            raise RequestFailure(Operation.OBSERVE, FailurePhase.OBSERVATION, Code.NETWORK)
        return pending(upload_id)

    client.observe = intermittent
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        engine(root, store, client, polls=9).run(uploader.select())
    assert len(client.gets) == 9
    assert client.clock.sleeps == [2, 4, 8, 16, 30, 30, 30, 30, 30]


def test_processing_failure_stays_review_after_reset_and_restart(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())
    client.observe = lambda i: ResponseEvidence(
        upload_id=i, remote=Remote.PROCESSING_FAILED, code=Code.PROCESSING_ERROR
    )
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
        store.reset(identifier, force=True)
        engine(root, store, client).run(uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.PROCESSING_FAILED
    assert len(client.posts) == 1 and client.gets == ["1"]


@pytest.mark.parametrize("boundary", ["post", "get", "sleep"])
def test_keyboard_interrupt_preserves_recovery(
    tmp_path: Path, boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())

    def interrupted(*args: object) -> ResponseEvidence:
        raise KeyboardInterrupt

    if boundary == "post":
        client.failure = KeyboardInterrupt()
    elif boundary == "get":
        client.observe = interrupted
    else:
        monkeypatch.setattr(client.clock, "sleep", interrupted)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        run = engine(root, store, client)
        run.run(uploader.select())
        assert run.stopped and len(client.posts) == 1
        assert not any(a.kind == Action.SUBMIT for a in uploader.select())
        assert store.load(identifier).attempts[0].upload_id == (None if boundary == "post" else "1")


def test_read_daily_reserve_stops_without_get_or_new_post(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        engine(root, store, client).submit(identifier)
        client.rate_limit = RateLimit(
            short_limit=100,
            short_usage=0,
            daily_limit=1000,
            daily_usage=0,
            read_daily_limit=100,
            read_daily_usage=95,
        )
        run = engine(root, store, client)
        run.run(uploader.select())
        assert run.stopped and not client.gets and len(client.posts) == 1
        assert store.load(identifier).attempts[0].upload_id == "1"


def test_post_429_stops_remaining_batch(tmp_path: Path) -> None:
    root, identifiers = multi_workspace(tmp_path, 4)
    client = Pipeline(Clock())
    client.failure = RequestFailure(
        Operation.SUBMIT, FailurePhase.POSSIBLY_SENT, Code.RATE_LIMIT, status_code=429
    )
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        run = engine(root, store, client)
        run.run(uploader.select())
        assert run.stopped and len(client.posts) == 1 and not client.gets
        assert all(not store.load(i).attempts for i in identifiers[1:])


@pytest.mark.parametrize("conflict", [False, True])
def test_malformed_get_keeps_original_identity(tmp_path: Path, conflict: bool) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())
    client.observe = lambda i: ResponseEvidence(
        upload_id=i,
        remote=Remote.DEFERRED,
        conflicting_ids=("99",) if conflict else (),
        code=Code.ID_CONFLICT if conflict else Code.MALFORMED_RESPONSE,
    )
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        engine(root, store, client, polls=3).run(uploader.select())
        assert client.gets == ["1"] * (1 if conflict else 3)
        assert len(client.posts) == 1
        assert store.load(identifier).attempts[0].upload_id == "1"
        assert Action.REVIEW in {a.kind for a in uploader.select()}


def test_default_sixty_get_budget_and_minimum_interval(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())
    client.observe = pending
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        run = engine(root, store, client)
        run.run(uploader.select())
        assert client.gets == ["1"] * 60
        assert store.load(identifier).attempts[0].remote == Remote.DEFERRED
        assert len(client.posts) == 1


def test_date_unavailable_review_does_not_schedule_get(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    manifest, _ = MigrationManifest.load(root)
    manifest.activities[0].time.resolved_utc_start = None
    (root / "migration-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    client = Pipeline(Clock())
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        engine(root, store, client).submit(identifier)
        manifest.activities.clear()
        (root / "migration-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
        uploader = Uploader(root, store)
        selected = uploader.select(from_date=date(2025, 1, 1))
        assert len(selected) == 1 and selected[0].reasons == (Code.DATE_UNAVAILABLE,)
        result = engine(root, store, client).run(selected)
        assert client.gets == []
        assert result == tuple(selected)
        engine(root, store, client).run(uploader.select(activity_id=identifier))
        assert client.gets == ["1"] and len(client.posts) == 1
