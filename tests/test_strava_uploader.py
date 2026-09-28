"""Safety, persistence, and mocked API tests for the Strava uploader."""

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from core.cli import app
from core.errors import ConfigurationError, ValidationError
from strava.artifacts import VerifiedArtifact
from strava.client import (
    FailurePhase,
    PreparedAccess,
    RequestFailure,
    StravaClient,
    TokenStore,
    authorization_url,
    parse_rate_limit,
)
from strava.models import RateLimit, TokenSet
from strava.progress import snapshot
from strava.rate_limit import DailyLimitReached, RateLimitPolicy, RateWait
from strava.recovery import Action, Code, Remote, ResponseEvidence
from strava.state import UploadStateStore
from strava.uploader import Uploader


def workspace(tmp_path: Path, *, status: str = "eligible") -> tuple[Path, str]:
    fit = tmp_path / "fits" / "activity.fit"
    fit.parent.mkdir(parents=True)
    fit.write_bytes(b"valid-fit")
    identifier = "sha256:" + "a" * 64
    manifest = {
        "manifest_version": 1,
        "activities": [
            {
                "stable_activity_id": identifier,
                "source": {
                    "relative_path": "source.json",
                    "filename": "source.json",
                    "sha256": "a" * 64,
                    "local_start": "2025-01-01T10:00:00",
                },
                "time": {
                    "resolved_utc_start": "2025-01-01T08:00:00+00:00",
                    "resolution_source": "source",
                },
                "sport": {"domain": "running", "fit": "running", "fallback": False},
                "fit": {
                    "relative_path": "fits/activity.fit",
                    "sha256": hashlib.sha256(b"valid-fit").hexdigest(),
                    "size_bytes": 9,
                    "valid": True,
                },
                "migration": {"status": status, "warnings": []},
            }
        ],
    }
    (tmp_path / "migration-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path, identifier


def multi_workspace(tmp_path: Path, count: int) -> tuple[Path, list[str]]:
    root, _ = workspace(tmp_path)
    payload = json.loads((root / "migration-manifest.json").read_text(encoding="utf-8"))
    template = payload["activities"][0]
    activities = []
    identifiers = []
    for index in range(count):
        content = f"fit-{index}".encode()
        filename = f"activity-{index}.fit"
        (root / "fits" / filename).write_bytes(content)
        item = json.loads(json.dumps(template))
        identifier = f"sha256:{index:064x}"
        identifiers.append(identifier)
        item["stable_activity_id"] = identifier
        item["fit"] = {
            "relative_path": f"fits/{filename}",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "valid": True,
        }
        activities.append(item)
    payload["activities"] = activities
    (root / "migration-manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    return root, identifiers


def test_manifest_selection_and_dry_run_make_no_api_call(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        selected = uploader.select(limit=1)
        assert [(a.stable_activity_id, a.kind) for a in selected] == [(identifier, Action.SUBMIT)]
        uploader.run(selected, dry_run=True)
        assert store.load(identifier).attempts == ()


@pytest.mark.skip(
    reason="SPEC-001 WP4/WP5: orchestration disabled by approved Sprint 10.3D sequencing"
)
def test_successful_async_upload_persists_and_does_not_repeat() -> None:
    """Persist asynchronous completion and prevent a second POST on restart.

    Historical implementation: d9b0027:tests/test_strava_uploader.py.
    Restore/replace before the development guard is removed in WP8.
    """


def test_duplicate_and_permanent_states_are_not_reselected(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_evidence(
            attempt,
            ResponseEvidence(
                upload_id="12",
                duplicate_activity_id="99",
                remote=Remote.DUPLICATE,
                code=Code.DUPLICATE,
            ),
        )
        assert not any(a.kind == Action.SUBMIT for a in uploader.select())
        assert store.load(identifier).attempts[0].remote == Remote.DUPLICATE


def test_processing_state_resumes_polling_without_post(tmp_path: Path) -> None:
    """WP3 routes retained IDs to observation; actual resume scheduling belongs to WP5."""
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        attempt = start_attempt(store, uploader, identifier)
        store.record_evidence(attempt, ResponseEvidence(upload_id="42", remote=Remote.PROCESSING))
        assert [(a.kind, a.upload_id) for a in uploader.select()] == [(Action.OBSERVE, "42")]


@pytest.mark.parametrize("change", ["missing", "hash"])
def test_local_fit_change_blocks_upload(tmp_path: Path, change: str) -> None:
    """WP3 reset cannot clear an uncorrected artifact; fresh snapshot checks are WP4."""
    root, identifier = workspace(tmp_path)
    fit = root / "fits/activity.fit"
    fit.unlink() if change == "missing" else fit.write_bytes(b"changed")
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        store.set_blocker(
            identifier,
            "activity",
            Code.MISSING_FIT if change == "missing" else Code.FIT_HASH_MISMATCH,
        )
        assert all(a.kind != Action.SUBMIT for a in store.reset(identifier))


def test_ineligible_manifest_entry_is_ignored(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path, status="excluded_unresolved")
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        assert all(a.kind != Action.SUBMIT for a in Uploader(root, store).select(limit=1))


def test_unsupported_manifest_version_is_rejected(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    path = root / "migration-manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["manifest_version"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        with pytest.raises(ValidationError):
            Uploader(root, store)


def test_manifest_change_is_detected_without_overwriting_history(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    database = root / "migration-state.sqlite3"
    with UploadStateStore(database) as store:
        uploader = Uploader(root, store)
        store.record_evidence(
            start_attempt(store, uploader, identifier),
            ResponseEvidence(activity_id="99", remote=Remote.COMPLETED),
        )
    path = root / "migration-manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["activities"][0]["fit"]["sha256"] = "b" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with UploadStateStore(database) as store:
        Uploader(root, store)
        record = store.load(identifier)
        assert record.attempts[0].activity_id == "99"
        assert record.attempts[0].remote == Remote.COMPLETED
        assert any(b.active and b.code == Code.MANIFEST_CHANGED for b in record.blockers)


def test_retry_is_bounded(tmp_path: Path) -> None:
    from strava.recovery import Operation
    from tests.test_strava_artifacts import test_503_and_ambiguity_never_resubmit

    test_503_and_ambiguity_never_resubmit(
        tmp_path,
        RequestFailure(Operation.SUBMIT, FailurePhase.POSSIBLY_SENT, Code.SERVER, status_code=503),
    )


def test_oauth_url_token_exchange_and_refresh(tmp_path: Path) -> None:
    url, state = authorization_url("123", "http://localhost", "nonce")
    assert "scope=activity%3Awrite" in url and state == "nonce"
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "expires_at": 9_999_999_999,
                    "expires_in": 21_600,
                    "refresh_token": "refresh-1",
                    "access_token": "access-1",
                    "athlete": {
                        "id": 123,
                        "firstname": "Sanitized",
                        "profile": "https://example.invalid/private.jpg",
                    },
                    "server_extension": "accepted",
                },
            )
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "access-2",
                "refresh_token": "refresh-2",
                "expires_at": 9_999_999_999,
                "expires_in": 20_566,
            },
        )

    tokens = TokenStore(tmp_path / "tokens.json")
    client = StravaClient(
        "123", "secret", tokens, httpx.Client(transport=httpx.MockTransport(handler))
    )
    exchanged = client.exchange_code("code", "read,activity:write")
    assert exchanged.scope == "read activity:write"
    assert client.access_token() == "access-1"
    persisted = json.loads(tokens.path.read_text(encoding="utf-8"))
    assert set(persisted) == {"access_token", "refresh_token", "expires_at", "scope"}
    assert "athlete" not in tokens.path.read_text(encoding="utf-8")
    assert "Sanitized" not in tokens.path.read_text(encoding="utf-8")
    tokens.save(
        TokenSet(access_token="old", refresh_token="refresh", expires_at=0, scope="activity:write")
    )
    assert client.access_token() == "access-2"
    rotated = tokens.load()
    assert rotated.refresh_token == "refresh-2"
    assert rotated.scope == "activity:write"


def test_oauth_validation_error_does_not_expose_secrets(tmp_path: Path) -> None:
    access = "private-access-value"
    refresh = "private-refresh-value"
    secret = "private-client-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "token_type": "Unsupported",
                "expires_at": 1,
                "expires_in": 1,
                "refresh_token": refresh,
                "access_token": access,
                "athlete": {"id": 123},
            },
        )

    client = StravaClient(
        "123",
        secret,
        TokenStore(tmp_path / "tokens.json"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ConfigurationError) as caught:
        client.exchange_code("private-authorization-code", "activity:write")
    message = str(caught.value)
    assert access not in message
    assert refresh not in message
    assert secret not in message
    assert "private-authorization-code" not in message


def test_missing_credentials_do_not_expose_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRAVA_CLIENT_ID", raising=False)
    monkeypatch.delenv("STRAVA_CLIENT_SECRET", raising=False)
    from strava.client import credentials

    with pytest.raises(ConfigurationError) as caught:
        credentials()
    assert "secret-value" not in str(caught.value)


def test_rate_limit_headers_and_exhaustion() -> None:
    rate = parse_rate_limit(
        httpx.Headers({"X-RateLimit-Limit": "200,2000", "X-RateLimit-Usage": "200,1000"})
    )
    assert rate is not None and rate.exhausted


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_http_failures_are_classified(tmp_path: Path, status: int) -> None:
    client = StravaClient(
        "1",
        "SECRET_TEST_MARKER",
        TokenStore(tmp_path / "tokens.json"),
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status))),
    )
    artifact = VerifiedArtifact(BytesIO(b"fit"), "a.fit", "a" * 64, 3, "source", 0)
    with pytest.raises(RequestFailure) as caught:
        client.upload(artifact, "source", PreparedAccess("access", 9_999_999_999))
    assert caught.value.phase == FailurePhase.POSSIBLY_SENT
    assert caught.value.status_code == status
    assert not hasattr(caught.value, "retryable")
    assert "SECRET_TEST_MARKER" not in str(caught.value)


def test_malformed_upload_response_is_rejected(tmp_path: Path) -> None:
    client = StravaClient(
        "1",
        "secret",
        TokenStore(tmp_path / "tokens.json"),
        httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(201, content=b"bad"))
        ),
    )
    artifact = VerifiedArtifact(BytesIO(b"fit"), "a.fit", "a" * 64, 3, "source", 0)
    evidence = client.upload(artifact, "source", PreparedAccess("access", 9_999_999_999))
    assert evidence.code == Code.MALFORMED_RESPONSE and evidence.remote is None


def test_uncertain_network_outcome_is_not_retried(tmp_path: Path) -> None:
    from strava.recovery import Operation
    from tests.test_strava_artifacts import test_503_and_ambiguity_never_resubmit

    test_503_and_ambiguity_never_resubmit(
        tmp_path, RequestFailure(Operation.SUBMIT, FailurePhase.POSSIBLY_SENT, Code.NETWORK)
    )


def test_reset_protects_completed_state(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.record_evidence(
            start_attempt(store, uploader, identifier),
            ResponseEvidence(activity_id="99", remote=Remote.COMPLETED),
        )
        before = store.load(identifier).attempts
        assert store.reset(identifier) == store.reset(identifier, force=True)
        assert store.load(identifier).attempts == before
        assert not any(a.kind == Action.SUBMIT for a in uploader.select())


def test_rate_limit_stops_batch_without_retrying(tmp_path: Path) -> None:
    from strava.recovery import Operation
    from tests.test_strava_artifacts import test_503_and_ambiguity_never_resubmit

    test_503_and_ambiguity_never_resubmit(
        tmp_path,
        RequestFailure(
            Operation.SUBMIT, FailurePhase.POSSIBLY_SENT, Code.RATE_LIMIT, status_code=429
        ),
    )


def test_cli_requires_explicit_upload_selector(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    result = CliRunner().invoke(app, ["strava", "upload", str(root), "--dry-run"])
    assert result.exit_code != 0
    assert "exactly one" in result.output


@pytest.mark.skip(reason="SPEC-001 WP5: orchestration disabled by approved Sprint 10.3D sequencing")
def test_bounded_pipeline_has_multiple_processing_uploads() -> None:
    """Bound remote jobs without discarding identities or exceeding new-submission capacity.

    Historical implementation: d9b0027:tests/test_strava_uploader.py.
    Restore/replace before the development guard is removed in WP8.
    """


def test_limit_counts_new_uploads_but_resumes_processing(tmp_path: Path) -> None:
    root, identifiers = multi_workspace(tmp_path, 3)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.record_evidence(
            start_attempt(store, uploader, identifiers[0]),
            ResponseEvidence(upload_id="42", remote=Remote.PROCESSING),
        )
        assert [a.stable_activity_id for a in uploader.select(limit=1)] == identifiers[:2]


def test_short_rate_limit_waits_to_natural_window() -> None:
    current = datetime(2025, 1, 1, 10, 7, 30, tzinfo=UTC)
    sleeps: list[float] = []
    waits: list[RateWait] = []

    def sleep(seconds: float) -> None:
        nonlocal current
        sleeps.append(seconds)
        current = current.fromtimestamp(current.timestamp() + seconds, UTC)

    policy = RateLimitPolicy(reserve=10, clock=lambda: current, sleep=sleep, on_wait=waits.append)
    waited = policy.before_request(
        RateLimit(short_limit=200, daily_limit=2000, short_usage=190, daily_usage=100)
    )
    assert waited
    assert sleeps == [451.0]
    assert waits[0].resume_at == datetime(2025, 1, 1, 10, 15, 1, tzinfo=UTC)


def test_daily_rate_limit_stops_without_sleeping() -> None:
    sleeps: list[float] = []
    policy = RateLimitPolicy(
        reserve=10,
        clock=lambda: datetime(2025, 1, 1, 10, tzinfo=UTC),
        sleep=sleeps.append,
    )
    with pytest.raises(DailyLimitReached, match="midnight UTC"):
        policy.before_request(
            RateLimit(short_limit=400, daily_limit=4000, short_usage=1, daily_usage=3990)
        )
    assert sleeps == []


def test_daily_rate_limit_stops_uploader_with_pending_state(tmp_path: Path) -> None:
    from tests.test_strava_artifacts import test_daily_reserve_preserves_fresh_provenance

    test_daily_reserve_preserves_fresh_provenance(tmp_path)


def test_read_rate_limit_applies_only_to_polling_requests() -> None:
    current = datetime(2025, 1, 1, 10, 14, 30, tzinfo=UTC)
    sleeps: list[float] = []
    policy = RateLimitPolicy(reserve=10, clock=lambda: current, sleep=sleeps.append)
    rate = RateLimit(
        short_limit=600,
        daily_limit=30000,
        short_usage=20,
        daily_usage=100,
        read_short_limit=300,
        read_daily_limit=15000,
        read_short_usage=290,
        read_daily_usage=200,
    )
    assert not policy.before_request(rate, read=False)
    assert policy.before_request(rate, read=True)
    assert sleeps == [31.0]


def test_rate_headers_support_missing_malformed_and_different_limits() -> None:
    assert parse_rate_limit(httpx.Headers()) is None
    assert parse_rate_limit(httpx.Headers({"X-RateLimit-Limit": "bad"})) is None
    rate = parse_rate_limit(
        httpx.Headers(
            {
                "X-RateLimit-Limit": "600,30000",
                "X-RateLimit-Usage": "12,345",
                "X-ReadRateLimit-Limit": "300,15000",
                "X-ReadRateLimit-Usage": "3,44",
            }
        )
    )
    assert rate is not None
    assert (rate.short_limit, rate.daily_limit) == (600, 30000)
    assert (rate.read_short_usage, rate.read_daily_usage) == (3, 44)


def test_progress_counts_only_completed_and_duplicate_as_resolved(tmp_path: Path) -> None:
    root, identifiers = multi_workspace(tmp_path, 6)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        outcomes = [
            ResponseEvidence(activity_id="99", remote=Remote.COMPLETED),
            ResponseEvidence(upload_id="2", duplicate_activity_id="100", remote=Remote.DUPLICATE),
            ResponseEvidence(upload_id="3", remote=Remote.PROCESSING),
        ]
        for identifier, evidence in zip(identifiers[:3], outcomes, strict=True):
            store.record_evidence(start_attempt(store, uploader, identifier), evidence)
        for identifier in identifiers[3:]:
            store.record_uncertain(start_attempt(store, uploader, identifier))
        result = snapshot(uploader.manifest, store.summary())
    assert result.resolved == 2 and result.remaining == 4
    assert result.completed == 1 and result.duplicate == 1
    assert result.needs_attention == 3
    assert result.percent == pytest.approx(100 / 3)


@pytest.mark.skip(
    reason="SPEC-001 WP4/WP5: orchestration disabled by approved Sprint 10.3D sequencing"
)
def test_keyboard_interrupt_preserves_resumable_state() -> None:
    """Interrupt handling retains intent, IDs and terminal facts at each request boundary.

    Historical implementation: d9b0027:tests/test_strava_uploader.py.
    Restore/replace before the development guard is removed in WP8.
    """


def test_status_is_local_and_reports_progress(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.record_evidence(
            start_attempt(store, uploader, identifier),
            ResponseEvidence(activity_id="99", remote=Remote.COMPLETED),
        )
    result = CliRunner().invoke(app, ["strava", "status", str(root), "--details"])
    assert result.exit_code == 0
    assert "1 / 1 (100.00%)" in result.output and "pending=0" in result.output


def start_attempt(store: UploadStateStore, uploader: Uploader, identifier: str) -> int:
    activity = next(a for a in uploader.manifest.activities if a.stable_activity_id == identifier)
    record = store.load(identifier)
    assert activity.fit.sha256 is not None
    artifact = VerifiedArtifact(
        BytesIO(b"synthetic"),
        "activity.fit",
        activity.fit.sha256,
        activity.fit.size_bytes or 0,
        identifier,
        record.revision,
    )
    return store.begin_submission(identifier, record.revision, artifact, True).attempt_id
