"""Synthetic HTTPX integration through the snapshot, store, client and scheduler."""

from pathlib import Path

import httpx

from strava.client import StravaClient, TokenStore
from strava.models import TokenSet
from strava.rate_limit import RateLimitPolicy
from strava.recovery import Remote
from strava.state import UploadStateStore
from strava.uploader import Uploader, _RecoveryRunner
from tests.test_strava_scheduler import Clock
from tests.test_strava_uploader import workspace


def test_composed_http_transport_exact_bytes_and_private_evidence(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    tokens = TokenStore(root / "synthetic-tokens.json")
    marker = "SYNTHETIC_SECRET_MARKER"
    tokens.save(
        TokenSet(
            access_token=marker, refresh_token=marker, expires_at=9999999999, scope="activity:write"
        )
    )
    requests: list[tuple[str, str]] = []
    with UploadStateStore(root / "migration-state.sqlite3") as store:

        def http(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.method == "POST":
                assert len(store.load(identifier).attempts) == 1
                assert not store.connection.in_transaction
                assert b"valid-fit" in request.read()
                (root / "fits/activity.fit").write_bytes(b"later-change")
                return httpx.Response(
                    201,
                    json={
                        "id": 77,
                        "status": "Your activity is still being processed.",
                        "private": marker,
                    },
                )
            assert store.load(identifier).attempts[0].upload_id == "77"
            return httpx.Response(
                200,
                json={
                    "id": 77,
                    "activity_id": 99,
                    "status": "Your activity is ready.",
                    "private": marker,
                },
            )

        with httpx.Client(transport=httpx.MockTransport(http)) as http_client:
            client = StravaClient("synthetic", marker, tokens, http_client)
            clock = Clock()
            uploader = Uploader(root, store)
            events: list[str] = []
            run = _RecoveryRunner(
                root,
                store,
                client,
                RateLimitPolicy(clock=clock.utc, sleep=clock.sleep),
                clock=clock.time,
                on_event=lambda e: events.append(repr(e)),
            )
            run.run(uploader.select())
            assert store.load(identifier).attempts[0].remote == Remote.COMPLETED
            assert marker not in repr(client.prepare_access())
            assert marker not in "\n".join(store.connection.iterdump())
            assert marker not in "\n".join(events)
            assert str(root) not in "\n".join(events)
    assert requests == [("POST", "/api/v3/uploads"), ("GET", "/api/v3/uploads/77")]
