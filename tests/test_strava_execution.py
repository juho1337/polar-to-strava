"""WP8: production entry points compose the same safe runner with mocked I/O."""

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

import core.cli as cli
from strava.client import StravaClient, TokenStore
from strava.models import TokenSet
from strava.rate_limit import RateLimitPolicy
from strava.recovery import Action, Remote, ResponseEvidence, classify_actions
from strava.state import UploadStateStore
from strava.uploader import Uploader
from tests.test_strava_scheduler import Clock
from tests.test_strava_uploader import start_attempt, workspace


@pytest.mark.parametrize("entry", ["service", "cli"])
@pytest.mark.parametrize("state", ["fresh", "known", "uncertain"])
def test_production_entry_uses_safe_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str, state: str
) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        if state != "fresh":
            attempt = start_attempt(store, uploader, identifier)
            if state == "known":
                store.record_evidence(
                    attempt, ResponseEvidence(upload_id="77", remote=Remote.PROCESSING)
                )
            else:
                store.record_uncertain(attempt)
    tokens = TokenStore(root / ".strava-tokens.json")
    tokens.save(
        TokenSet(
            access_token="synthetic",
            refresh_token="synthetic",
            expires_at=9999999999,
            scope="activity:write",
        )
    )
    calls: list[tuple[str, str]] = []
    clock = Clock()

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.url.path == "/api/v3/uploads"
            with UploadStateStore(path) as observer:
                assert observer.load(identifier).attempts[-1].submission.value == "intent"
            assert b"valid-fit" in request.read()
            return httpx.Response(
                201,
                json={"id": 77, "status": "processing"},
                headers={"X-RateLimit-Limit": "600,30000", "X-RateLimit-Usage": "12,345"},
            )
        assert request.url.path == "/api/v3/uploads/77"
        return httpx.Response(
            200,
            json={"id": 77, "status": "ready", "activity_id": 99},
            headers={"X-RateLimit-Limit": "600,30000", "X-RateLimit-Usage": "13,346"},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = StravaClient("synthetic", "synthetic", tokens, http)
        if entry == "service":
            with UploadStateStore(path) as store:
                uploader = Uploader(
                    root,
                    store,
                    client,
                    policy=RateLimitPolicy(clock=clock.utc, sleep=clock.sleep),
                    clock=clock.time,
                )
                result = uploader.run(uploader.select())
                assert result.resolved == (state != "uncertain")
        else:
            monkeypatch.setattr(cli, "StravaClient", lambda *args: client)
            monkeypatch.setattr(cli, "credentials", lambda: ("synthetic", "synthetic"))
            monkeypatch.setattr(
                cli, "time", SimpleNamespace(time=clock.time, sleep=clock.sleep), raising=False
            )
            result_cli = CliRunner().invoke(cli.app, ["strava", "upload", str(root), "--all"])
            assert result_cli.exit_code == 0, result_cli.output
            if state != "uncertain":
                assert "API 15 min" in result_cli.output
                assert "13 / 600" in result_cli.output
                assert "346 / 30000" in result_cli.output
            assert "recovery integration incomplete" not in result_cli.output
            assert (
                "no Strava request was made" not in result_cli.output
                if state != "uncertain"
                else True
            )
    expected = [] if state == "uncertain" else [("GET", "/api/v3/uploads/77")]
    if state == "fresh":
        expected.insert(0, ("POST", "/api/v3/uploads"))
    assert calls == expected
    with UploadStateStore(path) as store:
        actions = {a.kind for a in classify_actions(store.load(identifier))}
        assert actions == ({Action.REVIEW} if state == "uncertain" else {Action.RESOLVED})


def test_cli_upload_force_is_not_supported(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.app, ["strava", "upload", str(tmp_path), "--all", "--force"])
    assert result.exit_code != 0 and "No such option" in result.output


def test_service_requires_injected_client_for_network_work(tmp_path: Path) -> None:
    from core.errors import ValidationError

    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        with pytest.raises(ValidationError, match="requires a client"):
            uploader.run(uploader.select())
        assert not store.load(identifier).attempts
