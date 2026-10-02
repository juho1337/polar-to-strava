"""Positive permission, retained evidence, local selection and execution barriers."""

from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from typer.testing import CliRunner

from core.cli import app
from core.errors import ValidationError
from strava.artifacts import VerifiedArtifact
from strava.models import MigrationManifest
from strava.recovery import (
    Action,
    Blocker,
    Code,
    Origin,
    Remote,
    ResponseEvidence,
    Submission,
    classify_actions,
    observation_permission,
    submission_permission,
)
from strava.state import UploadStateStore
from strava.uploader import Uploader
from tests.test_strava_uploader import start_attempt, workspace


def test_positive_permission_and_transactional_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import strava.state as state

    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        activity = uploader.manifest.activities[0]
        record = store.load(identifier)
        assert record.current_fit_sha256 is not None
        artifact = VerifiedArtifact(
            BytesIO(b"valid-fit"),
            "activity.fit",
            record.current_fit_sha256,
            9,
            identifier,
            record.revision,
        )
        assert submission_permission(record, activity, artifact, True).allowed
        assert not submission_permission(record, activity, artifact, False).allowed
        seen: list[bool] = []
        original = submission_permission

        def permission(*args: object, **kwargs: object) -> object:
            seen.append(store.connection.in_transaction)
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(state, "submission_permission", permission)
        attempt = store.begin_submission(identifier, record.revision, artifact, True)
        assert seen == [True]
        assert store.load(identifier).attempts[0].submission == Submission.INTENT
        with pytest.raises(ValidationError):
            store.begin_submission(identifier, record.revision, artifact, True)
        store.record_not_submitted(attempt.attempt_id, Code.CLIENT_PREFLIGHT)
        new = store.load(identifier)
        proof = replace(artifact, revision=new.revision)
        assert submission_permission(new, activity, proof, True).allowed
        store.begin_submission(identifier, new.revision, proof, True)
        assert len(store.load(identifier).attempts) == 2


@pytest.mark.parametrize(
    "condition",
    [
        "intent",
        "uncertain",
        "known",
        "completed",
        "duplicate",
        "processing_failed",
        "legacy",
        "extras",
        "blocker",
        "absent",
        "ineligible",
        "stale",
        "no_proof",
        "malformed_hash",
        "older_uncertain",
        "missing_manifest",
    ],
)
def test_submission_permission_matrix(tmp_path: Path, condition: str) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        activity = uploader.manifest.activities[0]
        attempt_id = start_attempt(store, uploader, identifier)
        store.record_not_submitted(attempt_id, Code.CLIENT_PREFLIGHT)
        record = store.load(identifier)
        assert record.current_fit_sha256
        proof = VerifiedArtifact(
            BytesIO(b"fit"),
            "activity.fit",
            record.current_fit_sha256,
            3,
            identifier,
            record.revision,
        )
        attempt = record.attempts[0]
        if condition in {"intent", "uncertain"}:
            record = replace(record, attempts=(replace(attempt, submission=Submission(condition)),))
        elif condition in {"known", "completed", "duplicate", "processing_failed"}:
            remote = Remote.PROCESSING if condition == "known" else Remote(condition)
            record = replace(record, attempts=(replace(attempt, upload_id="12", remote=remote),))
        elif condition == "legacy":
            record = replace(record, origin=Origin.LEGACY_REVIEW)
        elif condition == "extras":
            record = replace(record, attempts=(replace(attempt, additional_attempts_unknown=True),))
        elif condition == "blocker":
            record = replace(record, blockers=(Blocker("activity", Code.MISSING_FIT),))
        elif condition == "absent":
            record = replace(record, present=False)
        elif condition == "ineligible":
            record = replace(record, eligible=False)
        elif condition == "stale":
            proof = replace(proof, revision=proof.revision - 1)
        elif condition == "no_proof":
            proof = None  # type: ignore[assignment]
        elif condition == "malformed_hash":
            proof = replace(proof, sha256="not a hash")
        elif condition == "older_uncertain":
            record = replace(
                record, attempts=(replace(attempt, submission=Submission.UNCERTAIN), attempt)
            )
        elif condition == "missing_manifest":
            activity = None  # type: ignore[assignment]
        assert not submission_permission(record, activity, proof, True).allowed


def test_observation_ignores_artifact_and_orphan_blockers(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_evidence(attempt, ResponseEvidence(upload_id="12", remote=Remote.PROCESSING))
        store.reconcile(MigrationManifest(manifest_version=1, activities=[]), "empty")
        (root / "fits/activity.fit").unlink()
        store.set_blocker(identifier, "activity", Code.MISSING_FIT)
        record = store.load(identifier)
        assert observation_permission(record, attempt, True).allowed
        assert not observation_permission(record, attempt, False).allowed
        assert {a.kind for a in classify_actions(record)} == {Action.OBSERVE, Action.REVIEW}
        assert record.resolved_utc_start and record.sport == "running"
        assert all(a.kind != Action.SUBMIT for a in store.reset(identifier, force=True))


def test_orphan_date_filter_requires_explicit_selection(tmp_path: Path) -> None:
    from tests.test_strava_state import legacy

    root, _ = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    legacy(path, status="processing", strava_upload_id="42", attempt_count=1)
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        filtered = uploader.select(from_date=date(2025, 1, 1))
        assert any(
            a.stable_activity_id == "legacy" and a.reasons == (Code.DATE_UNAVAILABLE,)
            for a in filtered
        )
        assert any(a.kind == Action.OBSERVE for a in uploader.select(activity_id="legacy"))
        assert any(a.kind == Action.OBSERVE for a in uploader.select(limit=1))


@pytest.mark.parametrize(
    "outcome", ["uncertain", "processing", "processing_failed", "completed", "duplicate"]
)
def test_reset_matrix_preserves_evidence(tmp_path: Path, outcome: str) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        if outcome == "uncertain":
            store.record_uncertain(attempt)
        else:
            store.record_evidence(
                attempt,
                ResponseEvidence(
                    upload_id="12",
                    remote=Remote(outcome),
                    activity_id="99" if outcome == "completed" else None,
                    duplicate_activity_id="99" if outcome == "duplicate" else None,
                ),
            )
        store.set_blocker(identifier, "activity", Code.MISSING_FIT)
        before = store.load(identifier).attempts
        plain = store.reset(identifier)
        forced = store.reset(identifier, force=True)
        assert plain == forced
        assert all(a.kind != Action.SUBMIT for a in forced)
        assert store.load(identifier).attempts == before
        assert not any(
            b.active and b.code == Code.MISSING_FIT for b in store.load(identifier).blockers
        )


def test_corrected_preflight_candidate(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        store.set_blocker(identifier, "activity", Code.MISSING_FIT)
        assert all(a.kind != Action.SUBMIT for a in classify_actions(store.load(identifier)))
        assert any(a.kind == Action.SUBMIT for a in store.reset(identifier))


def test_local_preview_does_not_construct_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.cli as cli

    root, _ = workspace(tmp_path)

    def bomb(*args: object, **kwargs: object) -> None:
        pytest.fail("Local preview constructed network client")

    monkeypatch.setattr(cli, "StravaClient", bomb)
    monkeypatch.setattr(cli, "credentials", bomb)
    for args in (["upload", str(root), "--all", "--dry-run"], ["status", str(root)]):
        result = CliRunner().invoke(app, ["strava", *args])
        assert result.exit_code == 0, result.output
