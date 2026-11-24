"""SPEC-005: read-only Strava activity lookup (mocked transport, synthetic data only)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from integration.lock import WorkspaceLock
from tests.test_integration_engine import (
    SENTINEL_ACCESS,
    SENTINEL_REFRESH,
    SENTINEL_SECRET,
    call,
    results,
    save_tokens,
)
from tests.test_strava_uploader import workspace

READ_SCOPE = "activity:write activity:read_all"


def row(identifier: int, start: str = "2025-03-01T08:00:00Z", **extra: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "start_date": start,
        "start_date_local": start.replace("Z", ""),
        "timezone": "(GMT+02:00) Europe/Helsinki",
        "sport_type": "Run",
        "distance": 10000.0,
        "moving_time": 3000,
        "elapsed_time": 3100,
        "average_heartrate": 150.0,
        "private": False,
        "external_id": None,
        # Private data that must never be returned.
        "name": "Secret morning run",
        "description": "private notes",
        "map": {"summary_polyline": "SENTINEL-POLYLINE"},
        "athlete": {"id": 1},
        **extra,
    }


def client_for(
    handler: Callable[[httpx.Request], httpx.Response], seen: list[httpx.Request] | None = None
) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(respond))


def request_for(root: Path, **extra: Any) -> dict[str, Any]:
    return {
        "workspace": str(root),
        "credentials": {"client_id": "client-1", "client_secret": SENTINEL_SECRET},
        "from_utc": "2025-03-01T00:00:00Z",
        "to_utc": "2025-04-01T00:00:00Z",
        **extra,
    }


def paged(pages: list[list[dict[str, Any]]]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        index = int(request.url.params["page"]) - 1
        return httpx.Response(200, json=pages[index] if index < len(pages) else [])

    return handler


def run(
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    scope: str | None = READ_SCOPE,
    **options: Any,
) -> tuple[int, list[dict[str, Any]], str, list[httpx.Request]]:
    root, _ = workspace(tmp_path)
    if scope is not None:
        save_tokens(root, scope)
    seen: list[httpx.Request] = []
    extra = options.pop("request", {})
    code, lines, text = call(
        "strava.activities.list",
        request_for(root, **extra),
        http=client_for(handler, seen),
        sleep=lambda _seconds: None,
        **options,
    )
    return code, lines, text, seen


def test_complete_lookup_returns_only_minimal_fields_and_pages_until_empty(tmp_path: Path) -> None:
    code, lines, text, seen = run(tmp_path, paged([[row(1), row(2)], [row(3)]]))
    summary = results(lines)["summary"]
    assert code == 0 and summary["complete"] is True and summary["stopped"] is None
    assert summary["count"] == 3 and summary["pages"] == 3  # the empty third page ended it
    assert summary["covered_from"] == "2025-03-01T00:00:00Z"
    assert summary["covered_to"] == "2025-04-01T00:00:00Z"
    assert set(summary["activities"][0]) == {
        "strava_id",
        "start_utc",
        "start_local",
        "timezone",
        "sport_type",
        "distance_m",
        "moving_time_s",
        "elapsed_time_s",
        "average_hr",
        "private",
        "external_id",
    }
    for forbidden in (
        "Secret morning run",
        "private notes",
        "SENTINEL-POLYLINE",
        "summary_polyline",
    ):
        assert forbidden not in text
    pages = [line for line in lines if line.get("event") == "strava.activities.page"]
    assert [p["page"] for p in pages] == [1, 2]
    assert all(r.method == "GET" for r in seen)  # read-only


def test_request_parameters_and_no_secret_in_output(tmp_path: Path) -> None:
    _, lines, text, seen = run(tmp_path, paged([[row(1)]]))
    params = seen[0].url.params
    assert params["per_page"] == "200" and params["page"] == "1"
    # Strava's after/before are strict whole seconds, so the request is widened by one second each side
    assert params["after"] == "1740787199" and params["before"] == "1743465601"
    assert seen[0].headers["Authorization"] == f"Bearer {SENTINEL_ACCESS}"
    for secret in (SENTINEL_ACCESS, SENTINEL_REFRESH, SENTINEL_SECRET):
        assert secret not in text


def test_duplicate_ids_across_pages_are_reported_once(tmp_path: Path) -> None:
    _, lines, _, _ = run(tmp_path, paged([[row(1), row(2)], [row(2), row(3)]]))
    assert [a["strava_id"] for a in results(lines)["summary"]["activities"]] == ["1", "2", "3"]


def test_rate_limit_mid_lookup_is_incomplete_not_success(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=[row(1)])
        return httpx.Response(429, json={"message": "Rate Limit Exceeded"})

    code, lines, _, _ = run(tmp_path, handler)
    final = results(lines)
    summary = final["summary"]
    assert code == 1 and final["status"] == "completed_with_failures"
    assert summary["complete"] is False and summary["stopped"]["code"] == "rate_limit"
    assert summary["covered_from"] is None and summary["covered_to"] is None
    assert summary["count"] == 1  # evidence of a match stays valid; absence is uncertain


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "not_authorized"), (403, "insufficient_scope"), (418, "invalid_response")],
)
def test_http_failures_stop_with_a_specific_code(tmp_path: Path, status: int, code: str) -> None:
    _, lines, _, _ = run(tmp_path, lambda _r: httpx.Response(status, json={}))
    summary = results(lines)["summary"]
    assert summary["complete"] is False and summary["stopped"]["code"] == code


def test_server_errors_are_retried_a_bounded_number_of_times(tmp_path: Path) -> None:
    _, lines, _, seen = run(tmp_path, lambda _r: httpx.Response(503))
    assert results(lines)["summary"]["stopped"]["code"] == "server_error"
    assert len(seen) == 3  # one attempt plus two retries, then stop


def test_network_errors_stop_after_bounded_retries(tmp_path: Path) -> None:
    def broken(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _, lines, _, seen = run(tmp_path, broken)
    assert results(lines)["summary"]["stopped"]["code"] == "network_error"
    assert len(seen) == 3


def test_malformed_response_is_not_trusted(tmp_path: Path) -> None:
    _, lines, _, _ = run(tmp_path, lambda _r: httpx.Response(200, json=[{"id": "x"}]))
    summary = results(lines)["summary"]
    assert summary["stopped"]["code"] == "invalid_response" and summary["count"] == 0


def test_missing_read_scope_is_refused_without_any_request(tmp_path: Path) -> None:
    code, lines, _, seen = run(tmp_path, paged([[row(1)]]), scope="activity:write")
    error = results(lines)["error"]
    assert code != 0 and error["code"] == "insufficient_scope" and seen == []


def test_not_connected_is_refused_without_any_request(tmp_path: Path) -> None:
    _, lines, _, seen = run(tmp_path, paged([[row(1)]]), scope=None)
    assert results(lines)["error"]["code"] == "not_authorized" and seen == []


def test_read_only_scope_reports_limited_visibility(tmp_path: Path) -> None:
    _, lines, _, _ = run(tmp_path, paged([[row(1)]]), scope="activity:write activity:read")
    assert results(lines)["summary"]["limited_visibility"] is True


def test_full_scope_is_not_limited(tmp_path: Path) -> None:
    _, lines, _, _ = run(tmp_path, paged([[row(1)]]))
    assert results(lines)["summary"]["limited_visibility"] is False


@pytest.mark.parametrize(
    "window",
    [
        {"from_utc": "2025-04-01T00:00:00Z", "to_utc": "2025-03-01T00:00:00Z"},
        {"from_utc": "2025-03-01T00:00:00Z", "to_utc": "2025-03-01T00:00:00Z"},
        {"from_utc": "2024-01-01T00:00:00Z", "to_utc": "2025-03-01T00:00:00Z"},
    ],
)
def test_invalid_windows_are_rejected_before_any_request(
    tmp_path: Path, window: dict[str, str]
) -> None:
    _, lines, _, seen = run(tmp_path, paged([[row(1)]]), request=window)
    assert results(lines)["error"]["code"] == "validation_failed" and seen == []


def test_cancellation_before_the_first_page_returns_cancelled(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    _, lines, _, seen = run(tmp_path, paged([[row(1)]]), cancel=cancel)
    final = results(lines)
    assert final["status"] == "cancelled" and final["summary"]["complete"] is False
    assert seen == []


def test_lookup_is_refused_while_another_writer_holds_the_workspace(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    save_tokens(root, READ_SCOPE)
    seen: list[httpx.Request] = []
    with WorkspaceLock(root, "upload.run"):
        code, lines, _ = call(
            "strava.activities.list",
            request_for(root),
            http=client_for(paged([[row(1)]]), seen),
        )
    assert code == 3 and results(lines)["error"]["code"] == "workspace_locked" and seen == []


def test_lookup_never_changes_recovery_state_or_workspace_files(tmp_path: Path) -> None:
    root, _ = workspace(tmp_path)
    save_tokens(root, READ_SCOPE)
    before = {
        p: p.read_bytes() for p in root.rglob("*") if p.is_file() and p.name != ".migration.lock"
    }
    call(
        "strava.activities.list",
        request_for(root),
        http=client_for(paged([[row(1)]])),
        sleep=lambda _seconds: None,
    )
    after = {
        p: p.read_bytes() for p in root.rglob("*") if p.is_file() and p.name != ".migration.lock"
    }
    assert after == before


def test_version_command_reports_contract_1_1_and_the_command() -> None:
    _, lines, _ = call("version", {})
    summary = results(lines)["summary"]
    assert summary["contract"] == "1.1" and "strava.activities.list" in summary["commands"]
    assert summary["capabilities"]["activity_lookup"] is True
    assert json.dumps(summary)  # serializable


def test_activity_exactly_at_the_lower_bound_is_included_and_the_exact_upper_bound_excluded(
    tmp_path: Path,
) -> None:
    rows = [
        row(
            1, "2025-02-28T23:59:59Z"
        ),  # one second before the window: returned by the widened API range
        row(2, "2025-03-01T00:00:00Z"),  # exactly at from_utc: must be kept
        row(3, "2025-03-31T23:59:59Z"),  # last second inside
        row(4, "2025-04-01T00:00:00Z"),  # exactly at to_utc: exclusive, must be dropped
    ]
    _, lines, _, seen = run(tmp_path, paged([rows]))
    summary = results(lines)["summary"]
    assert summary["complete"] is True
    assert [a["strava_id"] for a in summary["activities"]] == ["2", "3"]
    assert (
        int(seen[0].url.params["after"]) < 1740787200
    )  # the lower bound second is not lost to "after"


def test_fractional_upper_bound_keeps_activities_in_its_last_partial_second(tmp_path: Path) -> None:
    window = {"from_utc": "2025-03-01T00:00:00Z", "to_utc": "2025-03-01T10:00:00.500Z"}
    _, lines, _, seen = run(
        tmp_path,
        paged([[row(1, "2025-03-01T10:00:00Z"), row(2, "2025-03-01T10:00:01Z")]]),
        request=window,
    )
    assert [a["strava_id"] for a in results(lines)["summary"]["activities"]] == ["1"]
    assert (
        int(seen[0].url.params["before"]) >= 1740823201
    )  # ceil of the fractional end, never truncated


def test_a_read_only_token_without_upload_permission_can_look_up(tmp_path: Path) -> None:
    _, lines, _, seen = run(tmp_path, paged([[row(1)]]), scope="activity:read_all")
    summary = results(lines)["summary"]
    assert summary["complete"] is True and summary["count"] == 1 and seen
    _, lines, _, _ = run(tmp_path / "second", paged([[row(1)]]), scope="activity:read")
    assert results(lines)["summary"]["limited_visibility"] is True


@pytest.mark.parametrize("missing", ["distance", "moving_time", "elapsed_time"])
def test_missing_matching_measurements_are_a_malformed_response_not_zeros(
    tmp_path: Path, missing: str
) -> None:
    broken = row(1)
    del broken[missing]
    _, lines, _, _ = run(tmp_path, paged([[broken]]))
    summary = results(lines)["summary"]
    assert summary["complete"] is False and summary["stopped"]["code"] == "invalid_response"
    assert summary["count"] == 0  # no fabricated zero is returned as evidence


@pytest.mark.parametrize("start", ["not-a-date", "2025-03-01T08:00:00", "", "2025-13-45T99:00:00Z"])
def test_invalid_or_zone_less_start_times_make_the_page_untrusted(
    tmp_path: Path, start: str
) -> None:
    _, lines, _, _ = run(tmp_path, paged([[row(1), row(2, start)]]))
    summary = results(lines)["summary"]
    assert summary["complete"] is False and summary["stopped"]["code"] == "invalid_response"
    assert summary["count"] == 0  # not even the valid row of the bad page is trusted
