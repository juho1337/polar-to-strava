"""Synthetic upgrade and monotonic persistence evidence for SPEC-001 WP1/WP3."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from core.errors import ValidationError
from strava.recovery import Action, Code, Remote, ResponseEvidence, Submission, classify_actions
from strava.state import UploadStateStore
from strava.uploader import Uploader
from tests.test_strava_uploader import start_attempt, workspace


def legacy(path: Path, **changes: object) -> None:
    values: dict[str, object] = dict(
        stable_activity_id="legacy",
        manifest_version=1,
        fit_sha256="a" * 64,
        eligible=1,
        present=1,
        status="pending",
        strava_upload_id=None,
        strava_activity_id=None,
        attempt_count=0,
        first_attempt_at=None,
        latest_attempt_at=None,
        completed_at=None,
        last_http_status=None,
        last_error_category=None,
        last_error_message=None,
    )
    values.update(changes)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO metadata VALUES('schema_version','1')")
        db.execute(
            """CREATE TABLE uploads (
            stable_activity_id TEXT PRIMARY KEY, manifest_version INTEGER NOT NULL,
            fit_sha256 TEXT, eligible INTEGER NOT NULL, present INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL, strava_upload_id TEXT, strava_activity_id TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0, first_attempt_at TEXT,
            latest_attempt_at TEXT, completed_at TEXT, last_http_status INTEGER,
            last_error_category TEXT, last_error_message TEXT)"""
        )
        db.execute(
            "INSERT INTO uploads VALUES(" + ",".join("?" for _ in values) + ")",
            tuple(values.values()),
        )


@pytest.mark.parametrize(
    ("status", "upload", "count", "expected"),
    [
        ("pending", None, 0, Action.SUBMIT),
        ("pending", None, 1, Action.REVIEW),
        ("pending", "12", 1, Action.OBSERVE),
        ("uploading", None, 1, Action.REVIEW),
        ("uploading", "12", 1, Action.OBSERVE),
        ("processing", None, 1, Action.REVIEW),
        ("processing", "12", 1, Action.OBSERVE),
        ("retryable_failure", None, 1, Action.REVIEW),
        ("retryable_failure", "12", 1, Action.OBSERVE),
        ("uncertain", None, 1, Action.REVIEW),
        ("uncertain", "12", 1, Action.OBSERVE),
        ("permanent_failure", None, 1, Action.REVIEW),
        ("permanent_failure", "12", 1, Action.OBSERVE),
        ("duplicate", "12", 1, Action.RESOLVED),
        ("local_file_changed", "12", 1, Action.OBSERVE),
        ("local_file_changed", None, 0, Action.REVIEW),
        ("skipped", None, 0, Action.REVIEW),
        ("unknown", None, 0, Action.REVIEW),
    ],
)
def test_v1_mapping_matrix(
    tmp_path: Path, status: str, upload: str | None, count: int, expected: Action
) -> None:
    path = tmp_path / "state.sqlite3"
    legacy(path, status=status, strava_upload_id=upload, attempt_count=count)
    with UploadStateStore(path) as store:
        record = store.load("legacy")
        actions = classify_actions(record)
        assert expected in {a.kind for a in actions}
        assert (Action.SUBMIT in {a.kind for a in actions}) == (expected == Action.SUBMIT)
        assert record.legacy_attempt_count == count
        assert len(store.records()) == 1
        assert len(record.attempts) == (0 if expected == Action.SUBMIT else 1)
        if upload:
            assert record.attempts[0].upload_id == upload
        assert store.backup_path and store.backup_path.exists()
        before = record
    with UploadStateStore(path) as reopened:
        assert reopened.load("legacy") == before
        assert reopened.backup_path is None
        assert len(list(tmp_path.glob("*.backup"))) == 1


@pytest.mark.parametrize(
    "stage", ["validated", "backup", "created", "mapped_row", "mapped", "versioned", "committed"]
)
def test_upgrade_rollback_at_each_stage(tmp_path: Path, stage: str) -> None:
    path = tmp_path / "state.sqlite3"
    legacy(path, status="uncertain", attempt_count=1)

    def fail(at: str) -> None:
        if at == stage:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        UploadStateStore(path, checkpoint=fail)
    with sqlite3.connect(path) as db:
        version = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert version == ("2" if stage == "committed" else "1")
        assert ("uploads" in tables) == (stage != "committed")
        assert ("submission_attempts" in tables) == (stage == "committed")
    with UploadStateStore(path) as store:
        assert all(a.kind != Action.SUBMIT for a in classify_actions(store.load("legacy")))


@pytest.mark.parametrize(
    "mode", ["newer", "metadata_missing", "partial", "corrupt", "malformed_v2"]
)
def test_bad_database_refused_without_recreation(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "state.sqlite3"
    if mode == "corrupt":
        path.write_bytes(b"not a database")
    else:
        legacy(path)
        with sqlite3.connect(path) as db:
            if mode == "newer":
                db.execute("UPDATE metadata SET value='999'")
            elif mode == "metadata_missing":
                db.execute("DROP TABLE metadata")
            elif mode == "partial":
                db.execute("CREATE TABLE recovery_activities (id TEXT)")
            else:
                db.execute("UPDATE metadata SET value='2'")
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        UploadStateStore(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("status", ["completed", "local_file_changed"])
def test_legacy_terminal_evidence_preserved(tmp_path: Path, status: str) -> None:
    path = tmp_path / "state.sqlite3"
    legacy(
        path,
        status=status,
        attempt_count=1,
        strava_upload_id="12",
        strava_activity_id="99",
        completed_at="2025-01-01T00:00:00+00:00",
    )
    with UploadStateStore(path) as store:
        record = store.load("legacy")
        assert record.attempts[0].remote == Remote.COMPLETED
        assert Action.RESOLVED in {a.kind for a in classify_actions(record)}
        assert all(a.kind != Action.SUBMIT for a in store.reset("legacy", force=True))


@pytest.mark.parametrize(
    "category", ["processing_error", "authorization", "request", "SECRET_TEST_MARKER"]
)
def test_legacy_permanent_failure_provenance(tmp_path: Path, category: str) -> None:
    path = tmp_path / "state.sqlite3"
    legacy(
        path,
        status="permanent_failure",
        strava_upload_id="12",
        attempt_count=1,
        last_error_category=category,
        last_error_message="SECRET_TEST_MARKER",
    )
    with UploadStateStore(path) as store:
        record = store.load("legacy")
        assert record.attempts[0].upload_id == "12"
        kinds = {a.kind for a in classify_actions(record)}
        assert Action.SUBMIT not in kinds
        assert (Action.OBSERVE in kinds) == (category == "SECRET_TEST_MARKER")
        dump = "\n".join(store.connection.iterdump())
        assert "SECRET_TEST_MARKER" not in dump
        assert store.backup_path
        with sqlite3.connect(store.backup_path) as backup:
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert (
                backup.execute("SELECT last_error_message FROM uploads").fetchone()[0]
                == "SECRET_TEST_MARKER"
            )


def test_known_id_and_unknown_other_attempt_coexist(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    legacy(path, status="uncertain", strava_upload_id="12", attempt_count=2)
    with UploadStateStore(path) as store:
        record = store.load("legacy")
        assert {a.kind for a in classify_actions(record)} == {Action.OBSERVE, Action.REVIEW}
        store.record_evidence(
            record.attempts[0].attempt_id,
            ResponseEvidence(upload_id="12", activity_id="99", remote=Remote.COMPLETED),
        )
        assert {a.kind for a in classify_actions(store.load("legacy"))} == {
            Action.RESOLVED,
            Action.REVIEW,
        }


@pytest.mark.parametrize("outcome", [Remote.COMPLETED, Remote.DUPLICATE, Remote.PROCESSING_FAILED])
def test_terminal_evidence_survives_updates(tmp_path: Path, outcome: Remote) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        evidence = ResponseEvidence(
            upload_id="12",
            remote=outcome,
            activity_id="99" if outcome == Remote.COMPLETED else None,
            duplicate_activity_id="99" if outcome == Remote.DUPLICATE else None,
        )
        store.record_evidence(attempt, evidence)
        store.record_uncertain(attempt)
        store.defer_observation(attempt, Code.NETWORK)
        store.record_evidence(attempt, replace(evidence, remote=Remote.PROCESSING))
        store.reset(identifier, force=True)
        record = store.load(identifier)
        assert record.attempts[0].remote == outcome
        assert record.attempts[0].upload_id == "12"
        assert all(a.kind != Action.SUBMIT for a in classify_actions(record))


def test_safe_proof_does_not_erase_uncertainty(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_uncertain(attempt)
        with pytest.raises(ValidationError):
            store.record_not_submitted(attempt, Code.CLIENT_PREFLIGHT)
        assert store.load(identifier).attempts[0].submission == Submission.UNCERTAIN


def test_backup_failure_prevents_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import strava.state_migration as migration

    path = tmp_path / "state.sqlite3"
    legacy(path, status="uploading", attempt_count=1)
    original = migration._integrity
    calls = 0

    def integrity(db: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValidationError("backup validation failed")
        original(db)

    monkeypatch.setattr(migration, "_integrity", integrity)
    with pytest.raises(ValidationError):
        UploadStateStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM metadata").fetchone()[0] == "1"


@pytest.mark.parametrize(
    "changes",
    [
        {"last_http_status": 503},
        {"first_attempt_at": "invalid"},
        {"attempt_count": -1},
        {"strava_upload_id": "invalid"},
        {
            "status": "local_file_changed",
            "last_error_category": "missing_fit",
            "last_http_status": 503,
        },
    ],
)
def test_contradictory_zero_attempts_never_become_fresh(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = tmp_path / "state.sqlite3"
    legacy(path, **changes)
    with UploadStateStore(path) as store:
        assert all(a.kind != Action.SUBMIT for a in classify_actions(store.load("legacy")))


def test_response_write_failure_keeps_intent_barrier(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.connection.execute(
            """CREATE TRIGGER fail_response BEFORE UPDATE ON submission_attempts
            BEGIN SELECT RAISE(ABORT, 'injected persistence failure'); END"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.record_evidence(
                attempt, ResponseEvidence(upload_id="12", remote=Remote.PROCESSING)
            )
        assert store.load(identifier).attempts[0].submission == Submission.INTENT
        store.connection.execute("DROP TRIGGER fail_response")
    with UploadStateStore(path) as store:
        assert all(a.kind != Action.SUBMIT for a in classify_actions(store.load(identifier)))


def test_intent_failure_rolls_back_atomically(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        before = store.load(identifier)
        store.connection.execute(
            """CREATE TRIGGER fail_intent BEFORE INSERT ON submission_attempts
            BEGIN SELECT RAISE(ABORT, 'injected intent failure'); END"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            start_attempt(store, uploader, identifier)
        assert store.load(identifier) == before


def test_partial_evidence_retains_observation_and_conflicts_do_not_replace_id(
    tmp_path: Path,
) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_evidence(
            attempt, ResponseEvidence(upload_id="12", code=Code.MALFORMED_RESPONSE)
        )
        assert Action.OBSERVE in {a.kind for a in classify_actions(store.load(identifier))}
        store.record_evidence(
            attempt, ResponseEvidence(upload_id="13", remote=Remote.COMPLETED, activity_id="99")
        )
        record = store.load(identifier)
        assert record.attempts[0].upload_id == "12"
        assert record.attempts[0].remote == Remote.DEFERRED
        assert record.attempts[0].conflicting_ids == ("12", "13")
        assert {a.kind for a in classify_actions(record)} == {Action.REVIEW}


@pytest.mark.parametrize("remote", ["completed", "duplicate", "processing", "processing_failed"])
def test_semantically_malformed_v2_is_refused(tmp_path: Path, remote: str) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        with store.transaction() as db:
            db.execute(
                "UPDATE submission_attempts SET remote=? WHERE attempt_id=?", (remote, attempt)
            )
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        UploadStateStore(path)
    assert path.read_bytes() == before


def test_shared_legacy_upload_ids_are_preserved_but_blocked(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    legacy(path, status="processing", strava_upload_id="12", attempt_count=1)
    with sqlite3.connect(path) as db:
        row = list(db.execute("SELECT * FROM uploads").fetchone())
        row[0] = "another"
        db.execute("INSERT INTO uploads VALUES(" + ",".join("?" for _ in row) + ")", row)
    with UploadStateStore(path) as store:
        assert len(store.records()) == 2
        for record in store.records():
            assert record.attempts[0].upload_id == "12"
            assert {a.kind for a in classify_actions(record)} == {Action.REVIEW}
            assert any(b.active and b.code == Code.ID_CONFLICT for b in record.blockers)


def test_shared_new_upload_ids_block_both_records(tmp_path: Path) -> None:
    from tests.test_strava_uploader import multi_workspace

    root, identifiers = multi_workspace(tmp_path, 2)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        for identifier in identifiers:
            store.record_evidence(
                start_attempt(store, uploader, identifier),
                ResponseEvidence(upload_id="12", remote=Remote.PROCESSING),
            )
        assert all(a.kind == Action.REVIEW for r in store.records() for a in classify_actions(r))


def test_shared_upload_cannot_manufacture_new_terminal_attribution(tmp_path: Path) -> None:
    from tests.test_strava_uploader import multi_workspace

    root, identifiers = multi_workspace(tmp_path, 2)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        first = start_attempt(store, uploader, identifiers[0])
        store.record_evidence(first, ResponseEvidence(upload_id="12", remote=Remote.PROCESSING))
        second = start_attempt(store, uploader, identifiers[1])
        store.record_evidence(
            second, ResponseEvidence(upload_id="12", activity_id="99", remote=Remote.COMPLETED)
        )
        incoming = store.load(identifiers[1])
        assert incoming.attempts[0].remote == Remote.NOT_STARTED
        assert incoming.attempts[0].conflicting_ids == ("12",)
        assert all(a.kind == Action.REVIEW for r in store.records() for a in classify_actions(r))
