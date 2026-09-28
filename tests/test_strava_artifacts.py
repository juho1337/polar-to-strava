"""SPEC-001 WP4: exact-byte proof and ordered one-shot submission."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strava.artifacts import ArtifactFailure, VerifiedArtifact, verified_snapshot
from strava.client import FailurePhase, PreparedAccess, RequestFailure
from strava.models import MigrationManifest, RateLimit
from strava.rate_limit import RateLimitPolicy
from strava.recovery import Code, Operation, Remote, ResponseEvidence, Submission
from strava.state import UploadStateStore
from strava.uploader import Uploader, _RecoveryRunner
from tests.test_strava_uploader import workspace


class Transport:
    def __init__(self) -> None:
        self.rate_limit: RateLimit | None = None
        self.posts: list[bytes] = []
        self.gets: list[str] = []
        self.reply = ResponseEvidence(
            upload_id="11", remote=Remote.PROCESSING, code=Code.PROCESSING
        )
        self.failure: BaseException | None = None
        self.trace: list[str] = []

    def prepare_access(self) -> PreparedAccess:
        self.trace.append("access")
        return PreparedAccess("synthetic", 9999999999)

    def upload(
        self, artifact: VerifiedArtifact, external_id: str, access: PreparedAccess
    ) -> ResponseEvidence:
        self.trace.append("post")
        self.posts.append(artifact.stream.read())
        if self.failure:
            raise self.failure
        return self.reply

    def get_upload(self, upload_id: str, access: PreparedAccess) -> ResponseEvidence:
        self.gets.append(upload_id)
        return ResponseEvidence(
            upload_id=upload_id, activity_id="22", remote=Remote.COMPLETED, code=Code.COMPLETED
        )


def runner(root: Path, store: UploadStateStore, client: Transport) -> _RecoveryRunner:
    return _RecoveryRunner(
        root, store, client, RateLimitPolicy(sleep=lambda _: None), clock=lambda: 0
    )


def test_snapshot_bytes_are_sent(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    manifest, _ = MigrationManifest.load(root)
    with verified_snapshot(root, manifest.activities[0], 1) as artifact:
        (root / "fits/activity.fit").write_bytes(b"replacement")
        assert artifact.stream.read() == b"valid-fit"
        assert artifact.sha256 == hashlib.sha256(b"valid-fit").hexdigest()
    assert artifact.stream.closed


@pytest.mark.parametrize("change", ["missing", "hash", "outside"])
def test_invalid_snapshot_refused(tmp_path: Path, change: str) -> None:
    root, _ = workspace(tmp_path)
    manifest, _ = MigrationManifest.load(root)
    activity = manifest.activities[0]
    if change == "missing":
        (root / "fits/activity.fit").unlink()
    elif change == "hash":
        (root / "fits/activity.fit").write_bytes(b"wrong-fit")
    else:
        activity.fit.relative_path = "../outside.fit"
    with pytest.raises(ArtifactFailure), verified_snapshot(root, activity, 1):
        pytest.fail("unverified snapshot yielded")


def test_order_and_intent_before_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, identifier = workspace(tmp_path)
    client = Transport()
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        # Observe committed intent from the transport boundary, not a store mock.
        upload = client.upload

        def checked_upload(
            artifact: VerifiedArtifact, external_id: str, access: PreparedAccess
        ) -> ResponseEvidence:
            assert store.load(identifier).attempts[0].submission == Submission.INTENT
            assert not store.connection.in_transaction
            assert artifact.stream.tell() == 0
            return upload(artifact, external_id, access)

        monkeypatch.setattr(client, "upload", checked_upload)
        run = runner(root, store, client)

        def notify(event: object) -> None:
            assert store.load(identifier).attempts[0].upload_id == "11"
            client.trace.append("callback")

        run.on_event = notify
        run.submit(identifier)
        assert client.posts == [b"valid-fit"]
        assert client.trace == ["access", "post", "callback"]


@pytest.mark.parametrize(
    "failure",
    [
        RequestFailure(Operation.SUBMIT, FailurePhase.POSSIBLY_SENT, Code.SERVER, status_code=503),
        RequestFailure(Operation.SUBMIT, FailurePhase.POSSIBLY_SENT, Code.NETWORK),
        RequestFailure(
            Operation.SUBMIT, FailurePhase.POSSIBLY_SENT, Code.RATE_LIMIT, status_code=429
        ),
    ],
)
def test_503_and_ambiguity_never_resubmit(tmp_path: Path, failure: RequestFailure) -> None:
    root, identifier = workspace(tmp_path)
    client = Transport()
    client.failure = failure
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        Uploader(root, store)
        runner(root, store, client).submit(identifier)
        assert store.load(identifier).attempts[0].submission == Submission.UNCERTAIN
    with UploadStateStore(path) as store:
        Uploader(root, store)
        runner(root, store, client).submit(identifier)
    assert len(client.posts) == 1


@pytest.mark.parametrize("boundary", ["intent", "response"])
def test_persistence_fault_stops_post_or_restart(tmp_path: Path, boundary: str) -> None:
    root, identifier = workspace(tmp_path)
    client = Transport()
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        Uploader(root, store)
        operation = "INSERT" if boundary == "intent" else "UPDATE"
        store.connection.execute(
            f"CREATE TRIGGER fail BEFORE {operation} ON submission_attempts BEGIN SELECT RAISE(ABORT, 'synthetic'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            runner(root, store, client).submit(identifier)
        assert len(client.posts) == (boundary == "response")
        store.connection.execute("DROP TRIGGER fail")
    if boundary == "response":
        with UploadStateStore(path) as store:
            Uploader(root, store)
            runner(root, store, client).submit(identifier)
        assert len(client.posts) == 1


def test_not_sent_allows_later_corrected_attempt(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    client = Transport()
    client.failure = RequestFailure(Operation.SUBMIT, FailurePhase.NOT_SENT, Code.CLIENT_PREFLIGHT)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        runner(root, store, client).submit(identifier)
        assert store.load(identifier).attempts[0].submission == Submission.NOT_SUBMITTED
        client.failure = None
        runner(root, store, client).submit(identifier)
        assert len(store.load(identifier).attempts) == 2
        assert store.load(identifier).attempts[1].upload_id == "11"


def test_daily_reserve_preserves_fresh_provenance(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    client = Transport()
    client.rate_limit = RateLimit(short_limit=100, short_usage=0, daily_limit=100, daily_usage=95)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        run = runner(root, store, client)
        run.submit(identifier)
        assert run.stopped
        assert store.load(identifier).attempts == ()
        assert client.posts == []


@pytest.mark.parametrize("during", ["access", "wait"])
def test_hash_rechecked_after_preparation(
    tmp_path: Path, during: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identifier = workspace(tmp_path)
    client = Transport()

    def mutate() -> None:
        (root / "fits/activity.fit").write_bytes(b"changed!!")

    def access() -> PreparedAccess:
        mutate()
        return PreparedAccess("synthetic", 9999999999)

    if during == "access":
        monkeypatch.setattr(client, "prepare_access", access)
    else:
        client.rate_limit = RateLimit(
            short_limit=100, short_usage=95, daily_limit=1000, daily_usage=0
        )
    policy = RateLimitPolicy(
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC), sleep=lambda _: mutate()
    )
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        run = _RecoveryRunner(root, store, client, policy, clock=lambda: 0)
        run.submit(identifier)
        assert not client.posts
        assert not store.load(identifier).attempts
        assert any(
            b.code == Code.FIT_HASH_MISMATCH and b.active for b in store.load(identifier).blockers
        )


def test_snapshot_copy_is_bounded_and_mutation_blocks_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identifier = workspace(tmp_path)
    source = root / "fits/activity.fit"
    payload = b"a" * (3 * 1024 * 1024)
    source.write_bytes(payload)
    manifest, _ = MigrationManifest.load(root)
    manifest.activities[0].fit.sha256 = hashlib.sha256(payload).hexdigest()
    (root / "migration-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    original_open = Path.open
    sizes: list[int] = []

    class ChangingReader:
        def __init__(self) -> None:
            self.offset = 0

        def __enter__(self) -> "ChangingReader":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self, size: int) -> bytes:
            assert 0 < size <= 1024 * 1024
            sizes.append(size)
            chunk = payload[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk if self.offset <= size else chunk.replace(b"a", b"b")

    def opened(path: Path, *args: object, **kwargs: object) -> object:
        if path == source and args == ("rb",):
            return ChangingReader()
        return original_open(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", opened)
    client = Transport()
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        runner(root, store, client).submit(identifier)
        assert not client.posts and not store.load(identifier).attempts
    assert len(sizes) == 4


def test_callback_failure_preserves_saved_evidence(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    client = Transport()
    client.reply = ResponseEvidence(activity_id="22", remote=Remote.COMPLETED, code=Code.COMPLETED)

    def fail(event: object) -> None:
        raise OSError("synthetic callback")

    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        run = runner(root, store, client)
        run.on_event = fail
        with pytest.raises(OSError):
            run.submit(identifier)
        assert store.load(identifier).attempts[0].remote == Remote.COMPLETED
        runner(root, store, client).submit(identifier)
        assert len(client.posts) == 1
