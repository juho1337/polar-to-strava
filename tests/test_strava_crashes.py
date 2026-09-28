"""SPEC-001 WP6: compose failure boundaries, migration and restart routing."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from strava.artifacts import VerifiedArtifact
from strava.models import MigrationManifest
from strava.recovery import Action, AttemptRecord, Remote, ResponseEvidence, classify_actions
from strava.state import UploadStateStore
from strava.uploader import Uploader
from tests.test_strava_scheduler import Clock, Pipeline, engine
from tests.test_strava_state import legacy
from tests.test_strava_uploader import workspace


@pytest.mark.parametrize(
    "boundary",
    [
        "before_intent",
        "after_intent",
        "during_post",
        "accepted_response_absent",
        "id_received",
        "id_committed",
        "during_get",
        "terminal_received",
    ],
)
def test_eight_crash_boundaries(
    tmp_path: Path, boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    client = Pipeline(Clock())
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        run = engine(root, store, client)
        begin, save = store.begin_submission, store.record_evidence

        def fault(*args: object, **kwargs: object) -> None:
            raise RuntimeError("synthetic termination")

        def begin_then_fail(
            identifier: str, revision: int, artifact: VerifiedArtifact, rate_ready: bool
        ) -> AttemptRecord:
            begin(identifier, revision, artifact, rate_ready)
            raise RuntimeError("synthetic termination")

        def save_or_fail(attempt_id: int, evidence: ResponseEvidence) -> None:
            if boundary == "id_received" or evidence.remote == Remote.COMPLETED:
                fault()
            save(attempt_id, evidence)

        if boundary == "before_intent":
            monkeypatch.setattr(client, "prepare_access", fault)
        elif boundary == "after_intent":
            monkeypatch.setattr(store, "begin_submission", begin_then_fail)
        elif boundary in {"during_post", "accepted_response_absent"}:
            client.failure = RuntimeError("synthetic termination")
        elif boundary in {"id_received", "terminal_received"}:
            monkeypatch.setattr(store, "record_evidence", save_or_fail)
        elif boundary == "id_committed":
            run.on_event = fault
        elif boundary == "during_get":
            monkeypatch.setattr(client, "observe", fault)
        with pytest.raises(RuntimeError, match="synthetic termination"):
            run.run(uploader.select())
        initial_posts = 0 if boundary in {"before_intent", "after_intent"} else 1
        initial_gets = 1 if boundary in {"during_get", "terminal_received"} else 0
        assert len(client.posts) == initial_posts
        assert client.gets == ["1"] * initial_gets
    restart = Pipeline(Clock())
    known = boundary in {"id_committed", "during_get", "terminal_received"}
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        actions = {a.kind for a in uploader.select()}
        assert (Action.SUBMIT in actions) == (boundary == "before_intent")
        assert (Action.OBSERVE in actions) == known
        if not known and boundary != "before_intent":
            assert Action.REVIEW in actions
            assert store.load(identifier).attempts[0].upload_id is None
        engine(root, store, restart).run(uploader.select())
        assert len(restart.posts) == (boundary == "before_intent")
        assert restart.gets == (["1"] if known or boundary == "before_intent" else [])
        if known:
            assert store.load(identifier).attempts[0].upload_id == "1"
            assert store.load(identifier).attempts[0].remote == Remote.COMPLETED


LEGACY_CASES = [
    ("pending", None, 0, None, Action.SUBMIT),
    ("retryable_failure", "12", 1, None, Action.OBSERVE),
    ("retryable_failure", None, 1, None, Action.REVIEW),
    ("uploading", None, 1, None, Action.REVIEW),
    ("processing", "12", 1, None, Action.OBSERVE),
    ("uncertain", None, 1, None, Action.REVIEW),
    ("completed", "12", 1, "99", Action.RESOLVED),
    ("duplicate", "12", 1, None, Action.RESOLVED),
    ("local_file_changed", "12", 1, None, Action.OBSERVE),
    ("orphan", "12", 1, None, Action.OBSERVE),
    ("unknown", None, 0, None, Action.REVIEW),
    ("processing", "not-an-id", 1, None, Action.REVIEW),
    ("uncertain", "12", 3, None, Action.OBSERVE),
]


@pytest.mark.parametrize(("status", "upload_id", "count", "activity_id", "action"), LEGACY_CASES)
def test_legacy_upgrade_scheduler_matrix(
    tmp_path: Path,
    status: str,
    upload_id: str | None,
    count: int,
    activity_id: str | None,
    action: Action,
) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    manifest, _ = MigrationManifest.load(root)
    legacy(
        path,
        stable_activity_id=identifier,
        fit_sha256=manifest.activities[0].fit.sha256,
        status="processing" if status == "orphan" else status,
        strava_upload_id=upload_id,
        strava_activity_id=activity_id,
        attempt_count=count,
    )
    if status == "orphan":
        manifest.activities.clear()
        (root / "migration-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
        (root / "fits/activity.fit").unlink()
    client = Pipeline(Clock())
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        assert action in {a.kind for a in uploader.select()}
        backup = store.backup_path
        assert backup is not None
        backup_bytes = backup.read_bytes()
        engine(root, store, client).run(uploader.select())
        assert len(client.posts) == (action == Action.SUBMIT)
        assert client.gets == (
            ["1"] if action == Action.SUBMIT else ["12"] if action == Action.OBSERVE else []
        )
        record = store.load(identifier)
        if action == Action.OBSERVE:
            assert record.attempts[0].upload_id == "12"
        if count == 3:
            assert record.attempts[0].additional_attempts_unknown
            assert Action.REVIEW in {a.kind for a in classify_actions(record)}
        store.reset(identifier, force=True)
        assert store.load(identifier).attempts == record.attempts
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        restart = Pipeline(Clock())
        engine(root, store, restart).run(uploader.select())
        assert not restart.posts and not restart.gets
        assert store.backup_path is None
        assert backup.read_bytes() == backup_bytes


def test_backup_not_restored_after_remote_effect(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    manifest, _ = MigrationManifest.load(root)
    legacy(path, stable_activity_id=identifier, fit_sha256=manifest.activities[0].fit.sha256)
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        backup = store.backup_path
        assert backup
        engine(root, store, Pipeline(Clock())).run(uploader.select())
    with sqlite3.connect(backup) as old:
        assert old.execute("SELECT attempt_count FROM uploads").fetchone()[0] == 0
        assert (
            old.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            == "1"
        )
    with UploadStateStore(path) as store:
        assert store.load(identifier).attempts[0].remote == Remote.COMPLETED
        assert all(a.kind != Action.SUBMIT for a in classify_actions(store.load(identifier)))


def test_callback_failure_then_reopen_preserves_terminal(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    client = Pipeline(Clock())
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)

        def callback(event: object) -> None:
            if store.load(identifier).attempts[0].remote == Remote.COMPLETED:
                raise RuntimeError("synthetic renderer failure")

        run = engine(root, store, client)
        run.on_event = callback
        with pytest.raises(RuntimeError):
            run.run(uploader.select())
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.COMPLETED
    assert len(client.posts) == 1 and client.gets == ["1"]


@pytest.mark.parametrize("boundary", ["intent", "saved_id", "migration"])
def test_subprocess_abrupt_exit(tmp_path: Path, boundary: str) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    if boundary == "migration":
        manifest, _ = MigrationManifest.load(root)
        legacy(
            path,
            stable_activity_id=identifier,
            fit_sha256=manifest.activities[0].fit.sha256,
            status="processing",
            strava_upload_id="12",
            attempt_count=1,
        )
    result = subprocess.run(
        [sys.executable, "-m", "tests.strava_crash_worker", str(root), boundary],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 81, result.stderr
    assert (root / "crash-boundary.txt").read_text() == boundary
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[
            0
        ] == ("1" if boundary == "migration" else "2")
    client = Pipeline(Clock())
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        engine(root, store, client).run(uploader.select())
        assert not client.posts
        assert client.gets == (
            [] if boundary == "intent" else ["12" if boundary == "migration" else "1"]
        )
        assert Action.SUBMIT not in {a.kind for a in uploader.select()}
