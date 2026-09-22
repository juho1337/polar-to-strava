"""Safety, persistence, and mocked API tests for the Strava uploader."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from core.cli import app
from core.errors import ConfigurationError, ValidationError
from strava.client import (
    StravaAPIError,
    StravaClient,
    TokenStore,
    authorization_url,
    parse_rate_limit,
)
from strava.models import RateLimit, TokenSet, UploadStatus
from strava.progress import snapshot
from strava.rate_limit import DailyLimitReached, RateLimitPolicy, RateWait
from strava.state import UploadState, UploadStateStore
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


class FakeClient:
    rate_limit: RateLimit | None = None

    def __init__(self, upload: UploadStatus, polls: list[UploadStatus] | None = None) -> None:
        self.upload_result = upload
        self.polls = polls or []
        self.upload_calls = 0
        self.poll_calls = 0

    def upload(self, path: Path, external_id: str) -> UploadStatus:
        self.upload_calls += 1
        return self.upload_result

    def get_upload(self, upload_id: str) -> UploadStatus:
        self.poll_calls += 1
        return self.polls.pop(0)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_manifest_selection_and_dry_run_make_no_api_call(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    fake = FakeClient(UploadStatus(id_str="1", status="processing"))
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        clock = FakeClock()
        uploader = Uploader(root, store, fake, sleep=clock.sleep, clock=clock)
        selected = uploader.select(limit=1)
        assert [item.stable_activity_id for item in selected] == [identifier]
        uploader.run(selected, dry_run=True)
        assert fake.upload_calls == 0
        assert store.get(identifier)["status"] == "pending"


def test_successful_async_upload_persists_and_does_not_repeat(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    fake = FakeClient(
        UploadStatus(id_str="12", status="processing"),
        [UploadStatus(id_str="12", activity_id=99, status="Your activity is ready.")],
    )
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        clock = FakeClock()
        uploader = Uploader(root, store, fake, sleep=clock.sleep, clock=clock)
        uploader.run(uploader.select(limit=1))
        row = store.get(identifier)
        assert row["status"] == "completed"
        assert row["strava_upload_id"] == "12"
        assert row["strava_activity_id"] == "99"
    with UploadStateStore(root / "migration-state.sqlite3") as reopened:
        uploader = Uploader(root, reopened, fake, sleep=lambda _: None)
        assert uploader.select(limit=1) == []
        assert reopened.get(identifier)["attempt_count"] == 1


def test_duplicate_and_permanent_states_are_not_reselected(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    fake = FakeClient(UploadStatus(id_str="12", error="duplicate of activity 99", status="error"))
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store, fake, sleep=lambda _: None)
        uploader.run(uploader.select(limit=1))
        assert store.get(identifier)["status"] == "duplicate"
        assert uploader.select(limit=1) == []


def test_processing_state_resumes_polling_without_post(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    fake = FakeClient(
        UploadStatus(id_str="unused", status="processing"),
        [UploadStatus(id_str="42", activity_id=100, status="ready")],
    )
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        clock = FakeClock()
        uploader = Uploader(root, store, fake, sleep=clock.sleep, clock=clock)
        store.set_status(identifier, UploadState.PROCESSING, upload_id="42")
        uploader.run(uploader.select(limit=1))
        assert fake.upload_calls == 0
        assert store.get(identifier)["status"] == "completed"


@pytest.mark.parametrize("change", ["missing", "hash"])
def test_local_fit_change_blocks_upload(tmp_path: Path, change: str) -> None:
    root, identifier = workspace(tmp_path)
    fit = root / "fits" / "activity.fit"
    fit.unlink() if change == "missing" else fit.write_bytes(b"changed")
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        with pytest.raises(ValidationError):
            uploader.select(limit=1)
        assert store.get(identifier)["status"] == "local_file_changed"


def test_ineligible_manifest_entry_is_ignored(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path, status="excluded_unresolved")
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        assert Uploader(root, store).select(limit=1) == []


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
        Uploader(root, store)
        store.set_status(identifier, UploadState.COMPLETED, activity_id="99")
    path = root / "migration-manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["activities"][0]["fit"]["sha256"] = "b" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with UploadStateStore(database) as store:
        Uploader(root, store)
        row = store.get(identifier)
        assert row["status"] == "local_file_changed"
        assert row["strava_activity_id"] == "99"


def test_retry_is_bounded(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)

    class FailingClient(FakeClient):
        def upload(self, path: Path, external_id: str) -> UploadStatus:
            self.upload_calls += 1
            raise StravaAPIError("server", "temporary", retryable=True, status_code=503)

    fake = FailingClient(UploadStatus(id_str="1", status="processing"))
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store, fake, sleep=lambda _: None, max_retries=3)
        uploader.run(uploader.select(limit=1))
        assert fake.upload_calls == 3
        assert store.get(identifier)["status"] == "retryable_failure"


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


@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, "authorization", False),
        (403, "authorization", False),
        (429, "rate_limit", True),
        (503, "server", True),
    ],
)
def test_http_failures_are_classified(
    tmp_path: Path, status: int, category: str, retryable: bool
) -> None:
    token_store = TokenStore(tmp_path / "tokens.json")
    token_store.save(
        TokenSet(
            access_token="access",
            refresh_token="refresh",
            expires_at=9_999_999_999,
            scope="activity:write",
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "failure"})

    client = StravaClient(
        "1", "secret", token_store, httpx.Client(transport=httpx.MockTransport(handler))
    )
    fit = tmp_path / "activity.fit"
    fit.write_bytes(b"fit")
    with pytest.raises(StravaAPIError) as caught:
        client.upload(fit, "source")
    assert caught.value.category == category
    assert caught.value.retryable is retryable
    assert "secret" not in str(caught.value)


def test_malformed_upload_response_is_rejected(tmp_path: Path) -> None:
    token_store = TokenStore(tmp_path / "tokens.json")
    token_store.save(
        TokenSet(
            access_token="access",
            refresh_token="refresh",
            expires_at=9_999_999_999,
            scope="activity:write",
        )
    )
    client = StravaClient(
        "1",
        "secret",
        token_store,
        httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(201, content=b"bad"))
        ),
    )
    fit = tmp_path / "activity.fit"
    fit.write_bytes(b"fit")
    with pytest.raises(StravaAPIError, match="Malformed"):
        client.upload(fit, "source")


def test_uncertain_network_outcome_is_not_retried(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)

    class UncertainClient(FakeClient):
        def upload(self, path: Path, external_id: str) -> UploadStatus:
            self.upload_calls += 1
            raise StravaAPIError("uncertain", "outcome unknown")

    fake = UncertainClient(UploadStatus(id_str="1", status="processing"))
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store, fake, sleep=lambda _: None)
        uploader.run(uploader.select(limit=1))
        assert fake.upload_calls == 1
        assert store.get(identifier)["status"] == "uncertain"


def test_rate_limit_stops_batch_without_retrying(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)

    class LimitedClient(FakeClient):
        def upload(self, path: Path, external_id: str) -> UploadStatus:
            self.upload_calls += 1
            raise StravaAPIError("rate_limit", "limit", retryable=True, status_code=429)

    fake = LimitedClient(UploadStatus(id_str="1", status="processing"))
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store, fake, sleep=lambda _: None)
        uploader.run(uploader.select(limit=1))
        assert fake.upload_calls == 1
        assert store.get(identifier)["status"] == "retryable_failure"


def test_reset_protects_completed_state(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.set_status(identifier, UploadState.COMPLETED, activity_id="99")
        with pytest.raises(ValidationError, match="requires --force"):
            store.reset(identifier)
        store.reset(identifier, force=True)
        assert store.get(identifier)["status"] == "pending"
        assert uploader.select(limit=1)


def test_cli_requires_explicit_upload_selector(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    result = CliRunner().invoke(app, ["strava", "upload", str(root), "--dry-run"])
    assert result.exit_code != 0
    assert "exactly one" in result.output


def test_bounded_pipeline_has_multiple_processing_uploads(tmp_path: Path) -> None:
    root, _ = multi_workspace(tmp_path, 5)

    class PipelineClient(FakeClient):
        def __init__(self) -> None:
            super().__init__(UploadStatus(id_str="unused", status="processing"))
            self.active = 0
            self.maximum_active = 0

        def upload(self, path: Path, external_id: str) -> UploadStatus:
            self.upload_calls += 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            return UploadStatus(id_str=str(self.upload_calls), status="processing")

        def get_upload(self, upload_id: str) -> UploadStatus:
            self.poll_calls += 1
            self.active -= 1
            return UploadStatus(id_str=upload_id, activity_id=100 + self.poll_calls, status="ready")

    fake = PipelineClient()
    clock = FakeClock()
    events: list[str] = []
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(
            root,
            store,
            fake,
            sleep=clock.sleep,
            clock=clock,
            max_in_flight=2,
            on_progress=lambda _activity, event: events.append(event),
        )
        uploader.run(uploader.select())
        assert fake.maximum_active == 2
        assert fake.upload_calls == 5
        assert store.summary()["completed"] == 5
        assert events.count("uploading") == 5
        assert events.count("completed") == 5


def test_limit_counts_new_uploads_but_resumes_processing(tmp_path: Path) -> None:
    root, identifiers = multi_workspace(tmp_path, 3)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.set_status(identifiers[0], UploadState.PROCESSING, upload_id="42")
        selected = uploader.select(limit=1)
        assert [item.stable_activity_id for item in selected] == identifiers[:2]


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
    root, identifier = workspace(tmp_path)
    fake = FakeClient(UploadStatus(id_str="1", status="processing"))
    fake.rate_limit = RateLimit(short_limit=400, daily_limit=4000, short_usage=1, daily_usage=3990)
    policy = RateLimitPolicy(
        reserve=10,
        clock=lambda: datetime(2025, 1, 1, 10, tzinfo=UTC),
        sleep=lambda _seconds: pytest.fail("daily limit must not sleep"),
    )
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store, fake, rate_policy=policy)
        uploader.run(uploader.select(limit=1))
        assert fake.upload_calls == 0
        assert store.get(identifier)["status"] == "pending"
        assert "midnight UTC" in str(uploader.last_stop_reason)


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
        store.set_status(identifiers[0], UploadState.COMPLETED)
        store.set_status(identifiers[1], UploadState.DUPLICATE)
        store.set_status(identifiers[2], UploadState.PROCESSING, upload_id="3")
        store.set_status(identifiers[3], UploadState.RETRYABLE_FAILURE)
        store.set_status(identifiers[4], UploadState.UNCERTAIN)
        store.set_status(identifiers[5], UploadState.PERMANENT_FAILURE)
        result = snapshot(uploader.manifest, store.summary())
    assert result.resolved == 2
    assert result.completed == 1 and result.duplicate == 1
    assert result.remaining == 4
    assert result.needs_attention == 2
    assert result.percent == pytest.approx(100 / 3)


def test_keyboard_interrupt_preserves_resumable_state(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)

    class InterruptedClient(FakeClient):
        def upload(self, path: Path, external_id: str) -> UploadStatus:
            raise KeyboardInterrupt

    fake = InterruptedClient(UploadStatus(id_str="1", status="processing"))
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store, fake)
        uploader.run(uploader.select(limit=1))
        assert "interrupted" in str(uploader.last_stop_reason)
        assert store.get(identifier)["status"] == "uncertain"


def test_status_is_local_and_reports_progress(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        Uploader(root, store)
        store.set_status(identifier, UploadState.COMPLETED)
    result = CliRunner().invoke(app, ["strava", "status", str(root), "--details"])
    assert result.exit_code == 0
    assert "1 / 1 (100.00%)" in result.output
    assert "pending=0" in result.output
