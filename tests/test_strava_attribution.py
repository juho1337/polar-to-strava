"""AC-15: unexpected HTTP bodies cannot invent an upload observation target."""

from io import BytesIO
from pathlib import Path

import httpx
import pytest

from strava.artifacts import VerifiedArtifact
from strava.client import PreparedAccess, RequestFailure, StravaClient, TokenStore
from strava.models import TokenSet
from strava.rate_limit import RateLimitPolicy
from strava.recovery import Action, Code, Operation, Remote, Submission, classify_actions
from strava.state import UploadStateStore
from strava.uploader import Uploader, _RecoveryRunner
from tests.test_strava_scheduler import Clock
from tests.test_strava_uploader import workspace


@pytest.mark.parametrize("status", [200, 400, 429, 500, 503])
@pytest.mark.parametrize("operation", [Operation.SUBMIT, Operation.OBSERVE])
def test_generic_error_identifiers_are_not_upload_evidence(
    tmp_path: Path, status: int, operation: Operation
) -> None:
    # POST 200 is unexpected; use 201 as the unexpected success code for GET.
    status = 201 if operation == Operation.OBSERVE and status == 200 else status
    payload = {"id": 123, "activity_id": 456, "message": "synthetic gateway error identifier"}
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload))
    ) as http:
        client = StravaClient("synthetic", "synthetic", TokenStore(tmp_path / "unused"), http)
        with pytest.raises(RequestFailure) as caught:
            if operation == Operation.SUBMIT:
                client.upload(
                    VerifiedArtifact(BytesIO(b"fit"), "a.fit", "a" * 64, 3, "a", 0),
                    "a",
                    PreparedAccess("synthetic", 9999999999),
                )
            else:
                client.get_upload("77", PreparedAccess("synthetic", 9999999999))
        evidence = caught.value.evidence
        assert evidence.upload_id == ("77" if operation == Operation.OBSERVE else None)
        assert evidence.activity_id is None and evidence.duplicate_activity_id is None
        assert evidence.remote is None and evidence.conflicting_ids == ()
        assert "synthetic gateway error identifier" not in repr(evidence)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 123},
        {"activity_id": 456},
        {"id": 123, "status": None},
        {"id": 123, "status": ""},
        {"id": 123, "status": "processing", "error": {"message": "synthetic"}},
    ],
)
def test_unexpected_body_requires_upload_envelope(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    client = StravaClient("s", "s", TokenStore(tmp_path / "unused"))
    try:
        with pytest.raises(RequestFailure) as caught:
            client._upload_response(httpx.Response(503, json=payload), Operation.SUBMIT)
        assert caught.value.evidence.upload_id is None
        assert caught.value.evidence.activity_id is None
    finally:
        client.http.close()


@pytest.mark.parametrize("status", [201, 503])
@pytest.mark.parametrize(
    "payload,upload,conflicts",
    [
        ({"id": 12, "status": "processing"}, "12", ()),
        ({"id": 12, "id_str": "13", "status": "processing"}, None, ("12", "13")),
    ],
)
def test_attributable_identity_and_conflicts_survive(
    tmp_path: Path,
    status: int,
    payload: dict[str, object],
    upload: str | None,
    conflicts: tuple[str, ...],
) -> None:
    client = StravaClient("s", "s", TokenStore(tmp_path / "unused"))
    try:
        if status == 201:
            evidence = client._upload_response(
                httpx.Response(status, json=payload), Operation.SUBMIT
            )
        else:
            with pytest.raises(RequestFailure) as caught:
                client._upload_response(httpx.Response(status, json=payload), Operation.SUBMIT)
            evidence = caught.value.evidence
        assert evidence.upload_id == upload and evidence.conflicting_ids == conflicts
        if conflicts:
            assert evidence.code == Code.ID_CONFLICT
    finally:
        client.http.close()


def test_generic_503_stays_uncertain_after_reopen_without_post_or_get(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    tokens = TokenStore(root / "synthetic-tokens.json")
    tokens.save(
        TokenSet(
            access_token="synthetic",
            refresh_token="synthetic",
            expires_at=9999999999,
            scope="activity:write",
        )
    )
    calls: list[tuple[str, str]] = []
    marker = "synthetic gateway error identifier"

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(503, json={"id": 123, "message": marker})

    path = root / "migration-state.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = StravaClient("synthetic", "synthetic", tokens, http)
        for restart in (False, True):
            calls.clear()
            with UploadStateStore(path) as store:
                uploader = Uploader(root, store)
                clock = Clock()
                runner = _RecoveryRunner(
                    root,
                    store,
                    client,
                    RateLimitPolicy(clock=clock.utc, sleep=clock.sleep),
                    clock=clock.time,
                )
                runner.run(uploader.select())
                assert calls == ([] if restart else [("POST", "/api/v3/uploads")])
                record = store.load(identifier)
                assert record.attempts[0].upload_id is None
                assert record.attempts[0].activity_id is None
                assert record.attempts[0].submission == Submission.UNCERTAIN
                assert record.attempts[0].remote == Remote.NOT_STARTED
                assert {a.kind for a in classify_actions(record)} == {Action.REVIEW}
                assert marker not in "\n".join(store.connection.iterdump())
