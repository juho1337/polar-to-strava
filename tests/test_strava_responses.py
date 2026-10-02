"""Operation-specific HTTP and conservative response recognition; mocked transport only."""

from io import BytesIO
from pathlib import Path

import httpx
import pytest

from strava.artifacts import VerifiedArtifact
from strava.client import FailurePhase, PreparedAccess, RequestFailure, StravaClient, TokenStore
from strava.recovery import Code, Operation, Remote
from strava.responses import parse_upload_response


def test_activity_id_without_upload_id_completes() -> None:
    result = parse_upload_response(
        {"activity_id": 99, "status": "Your activity is ready."}, operation=Operation.SUBMIT
    )
    assert result.remote == Remote.COMPLETED and result.activity_id == "99"
    assert result.upload_id is None
    result = parse_upload_response(
        {"activity_id": 99, "status": "ready"}, operation=Operation.OBSERVE, expected_upload_id="12"
    )
    assert result.remote is None and result.upload_id == "12"


@pytest.mark.parametrize("bad", [True, False, -1, 0, 1.5, "99", "SECRET_TEST_MARKER"])
def test_partial_valid_id_survives_malformed_fields(bad: object) -> None:
    result = parse_upload_response(
        {"id": 12, "status": "ready", "activity_id": bad}, operation=Operation.SUBMIT
    )
    assert result.upload_id == "12" and result.remote != Remote.COMPLETED
    assert "SECRET_TEST_MARKER" not in repr(result)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 12, "id_str": "13", "status": "ready", "activity_id": 99},
        {"id": 13, "status": "processing"},
    ],
)
def test_get_identity_conflict_retains_original(payload: dict[str, object]) -> None:
    result = parse_upload_response(payload, operation=Operation.OBSERVE, expected_upload_id="12")
    assert result.upload_id == "12" and result.remote is None
    assert result.code == Code.ID_CONFLICT and result.conflicting_ids


@pytest.mark.parametrize(
    "error,accepted",
    [
        ("activity.fit duplicate of activity 99", True),
        ("activity.fit duplicate of activity <a href='example'>99</a>", True),
        ("duplicate of activity 99", False),
        ("activity.fit is not duplicate of activity 99", False),
        ("activity.fit duplicate of activity -1", False),
        ("activity.fit duplicate of activity 0", False),
        ("activity.fit duplicate of activity 99 maybe", False),
        ("possible duplicate SECRET_TEST_MARKER", False),
        ("activity.fit may be a duplicate of activity 99", False),
        ("activity.fit is possibly a duplicate of activity 99", False),
        ("Warning: suspected duplicate of activity 99", False),
        ("Warning: activity.fit duplicate of activity 99", False),
    ],
)
def test_duplicate_assertion_matrix(error: str, accepted: bool) -> None:
    result = parse_upload_response(
        {"id": 12, "status": "There was an error processing your activity.", "error": error},
        operation=Operation.SUBMIT,
    )
    assert (result.remote == Remote.DUPLICATE) == accepted
    assert result.upload_id == "12"
    assert "SECRET_TEST_MARKER" not in repr(result) and "activity.fit" not in repr(result)


def test_contradictory_completion_error_is_review() -> None:
    result = parse_upload_response(
        {
            "id": 12,
            "activity_id": 99,
            "status": "error",
            "error": "activity.fit duplicate of activity 99",
        },
        operation=Operation.SUBMIT,
    )
    assert result.remote is None and result.code == Code.OUTCOME_CONFLICT
    assert result.upload_id == "12" and result.activity_id == "99"


@pytest.mark.parametrize("operation", [Operation.SUBMIT, Operation.OBSERVE])
@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_request_errors_keep_phase_and_identity(
    tmp_path: Path, operation: Operation, status: int
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert "/oauth/" not in str(request.url)
        return httpx.Response(status, json={"message": "SECRET_TEST_MARKER"})

    client = StravaClient(
        "1",
        "SECRET_TEST_MARKER",
        TokenStore(tmp_path / "absent.json"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    access = PreparedAccess("SECRET_TEST_MARKER", 9_999_999_999)
    assert "SECRET_TEST_MARKER" not in repr(access)
    with pytest.raises(RequestFailure) as caught:
        if operation == Operation.SUBMIT:
            client.upload(
                VerifiedArtifact(BytesIO(b"fit"), "a.fit", "a" * 64, 3, "a", 0), "a", access
            )
        else:
            client.get_upload("12", access)
    failure = caught.value
    assert calls == (["POST"] if operation == Operation.SUBMIT else ["GET"])
    assert failure.phase == (
        FailurePhase.POSSIBLY_SENT if operation == Operation.SUBMIT else FailurePhase.OBSERVATION
    )
    assert failure.evidence.upload_id == (None if operation == Operation.SUBMIT else "12")
    assert not hasattr(failure, "retryable")
    assert "SECRET_TEST_MARKER" not in str(failure) + repr(failure.evidence)


@pytest.mark.parametrize("operation", [Operation.SUBMIT, Operation.OBSERVE])
def test_network_ambiguity_is_not_non_submission(tmp_path: Path, operation: Operation) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("SECRET_TEST_MARKER")

    client = StravaClient(
        "1",
        "s",
        TokenStore(tmp_path / "absent"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(RequestFailure) as caught:
        if operation == Operation.SUBMIT:
            client.upload(
                VerifiedArtifact(BytesIO(b"x"), "a.fit", "a" * 64, 1, "a", 0),
                "a",
                PreparedAccess("token", 9_999_999_999),
            )
        else:
            client.get_upload("12", PreparedAccess("token", 9_999_999_999))
    assert caught.value.phase != FailurePhase.NOT_SENT
    assert "SECRET_TEST_MARKER" not in str(caught.value)


def test_expired_prepared_access_refuses_without_refresh(tmp_path: Path) -> None:
    def bomb(request: httpx.Request) -> httpx.Response:
        pytest.fail("Expired prepared token caused network access")

    client = StravaClient(
        "1", "s", TokenStore(tmp_path / "absent"), httpx.Client(transport=httpx.MockTransport(bomb))
    )
    with pytest.raises(RequestFailure) as caught:
        client.upload(
            VerifiedArtifact(BytesIO(b"x"), "a.fit", "a" * 64, 1, "a", 0),
            "a",
            PreparedAccess("token", 0),
        )
    assert caught.value.phase == FailurePhase.NOT_SENT


def test_unexpected_http_retains_parser_identity_conflict(tmp_path: Path) -> None:
    client = StravaClient(
        "1",
        "s",
        TokenStore(tmp_path / "absent"),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json={"id": 13, "status": "processing"})
            )
        ),
    )
    with pytest.raises(RequestFailure) as caught:
        client.get_upload("12", PreparedAccess("t", 9_999_999_999))
    assert caught.value.code == Code.SERVER
    assert caught.value.evidence.code == Code.ID_CONFLICT
    assert caught.value.evidence.upload_id == "12"
    assert caught.value.evidence.conflicting_ids == ("13",)
