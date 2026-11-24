"""Read-only lookup of the athlete's existing Strava activities (SPEC-005).

The lookup returns only the fields duplicate matching needs and never writes to Strava. A lookup that
did not read every page is reported as incomplete; callers must then treat the whole window as
unavailable and must never conclude "no match" from it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, field_validator

from core.errors import ConfigurationError
from strava.client import API_URL, RequestFailure, StravaClient
from strava.rate_limit import DailyLimitReached, RateLimitPolicy

PER_PAGE = 200
DEFAULT_MAX_PAGES = 50
READ_ALL = "activity:read_all"
READ = "activity:read"
_RETRIES = 2


class ActivityLookupError(Exception):
    """A lookup that cannot start: not connected or the read permission is missing."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class _Row(BaseModel):
    """One element of ``GET /athlete/activities``; unknown fields (names, routes, ...) are ignored."""

    model_config = ConfigDict(extra="ignore")
    id: int
    start_date: str
    start_date_local: str | None = None
    timezone: str | None = None
    sport_type: str | None = None
    type: str | None = None
    # Required: a missing measurement is a malformed response, never a silent zero.
    distance: float
    moving_time: int
    elapsed_time: int
    average_heartrate: float | None = None
    private: bool = False
    external_id: str | None = None

    @field_validator("start_date")
    @classmethod
    def _start_has_a_zone(cls, value: str) -> str:
        """An unparsable or zone-less start time makes the whole page untrusted."""
        moment = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )  # ValueError -> invalid_response
        if moment.tzinfo is None:
            raise ValueError("start_date has no timezone")
        return value


_ROWS = TypeAdapter(list[_Row])


@dataclass(frozen=True, slots=True)
class StravaActivity:
    strava_id: str
    start_utc: str
    start_local: str | None
    timezone: str | None
    sport_type: str
    distance_m: float
    moving_time_s: int
    elapsed_time_s: int
    average_hr: float | None
    private: bool
    external_id: str | None


@dataclass(slots=True)
class LookupResult:
    activities: list[StravaActivity] = field(default_factory=list)
    complete: bool = False
    pages: int = 0
    limited_visibility: bool = False
    stop_code: str | None = None
    stop_message: str | None = None


def _activity(row: _Row) -> StravaActivity:
    return StravaActivity(
        strava_id=str(row.id),
        start_utc=row.start_date,
        start_local=row.start_date_local,
        timezone=row.timezone,
        sport_type=row.sport_type or row.type or "Unknown",
        distance_m=row.distance,
        moving_time_s=row.moving_time,
        elapsed_time_s=row.elapsed_time,
        average_hr=row.average_heartrate,
        private=row.private,
        external_id=row.external_id,
    )


def check_read_scope(client: StravaClient) -> bool:
    """Return ``limited_visibility``; raise when not connected or no read permission was granted."""
    try:
        granted = set(client.token_store.load().scope.replace(",", " ").split())
    except (ConfigurationError, OSError):
        raise ActivityLookupError(
            "not_authorized", "Strava is not connected; authorize first."
        ) from None
    if READ_ALL in granted:
        return False
    if READ in granted:
        return True
    raise ActivityLookupError(
        "insufficient_scope",
        "Looking up activities requires the activity:read_all permission; reconnect Strava.",
        {"required": [READ_ALL], "granted": sorted(granted)},
    )


def list_activities(
    client: StravaClient,
    *,
    start: datetime,
    end: datetime,
    policy: RateLimitPolicy,
    should_stop: Callable[[], bool] = lambda: False,
    on_page: Callable[[int, int, int], None] | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> LookupResult:
    """Page through ``[start, end)``; every stop is reported on the result, never swallowed."""
    result = LookupResult(limited_visibility=check_read_scope(client))
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        if should_stop():
            return _stopped(result, "cancelled", "Lookup was cancelled.")
        try:
            rows = _fetch_page(client, policy, start, end, page)
        except _Stop as stop:
            return _stopped(result, stop.code, stop.message)
        except KeyboardInterrupt:  # the engine's cooperative cancel interrupts waits
            return _stopped(result, "cancelled", "Lookup was cancelled.")
        result.pages = page
        if not rows:
            result.complete = True
            return result
        for row in rows:
            if not _within(row, start, end):
                continue  # outside the exact window; the API range is deliberately a little wider
            activity = _activity(row)
            if activity.strava_id not in seen:
                seen.add(activity.strava_id)
                result.activities.append(activity)
        if on_page is not None:
            on_page(page, len(rows), len(result.activities))
    return _stopped(result, "max_pages", "The page limit was reached before the window ended.")


def _within(row: _Row, start: datetime, end: datetime) -> bool:
    """Exact ``[start, end)`` check on the returned start time (the API filter is coarser)."""
    moment = datetime.fromisoformat(
        row.start_date.replace("Z", "+00:00")
    )  # validated by the row model
    return start <= moment < end


def _stopped(result: LookupResult, code: str, message: str) -> LookupResult:
    result.complete = False
    result.stop_code, result.stop_message = code, message
    return result


class _Stop(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _fetch_page(
    client: StravaClient, policy: RateLimitPolicy, start: datetime, end: datetime, page: int
) -> list[_Row]:
    for attempt in range(_RETRIES + 1):
        token = _access(client, policy)
        try:
            response = client.http.get(
                f"{API_URL}/athlete/activities",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    # Strava's filters are strict whole seconds: widen so that an activity exactly at
                    # ``start`` (or in the last partial second before ``end``) is returned; _within() trims.
                    "after": math.floor(start.timestamp()) - 1,
                    "before": math.ceil(end.timestamp()) + 1,
                    "page": page,
                    "per_page": PER_PAGE,
                },
            )
        except httpx.HTTPError:
            if attempt == _RETRIES:
                raise _Stop("network_error", "Strava could not be reached.") from None
            policy.sleep(2.0 * (attempt + 1))
            continue
        client._capture_rate_limit(response)
        status = response.status_code
        if status == 429:
            raise _Stop("rate_limit", "Strava rate limit reached.")
        if status == 401:
            raise _Stop("not_authorized", "Strava rejected the credentials.")
        if status == 403:
            raise _Stop("insufficient_scope", "Strava refused access to the activities.")
        if status >= 500:
            if attempt == _RETRIES:
                raise _Stop("server_error", f"Strava returned HTTP {status}.")
            policy.sleep(2.0 * (attempt + 1))
            continue
        if status != 200:
            raise _Stop("invalid_response", f"Unexpected HTTP {status} from Strava.")
        try:
            return _ROWS.validate_python(response.json())
        except (ValueError, ValidationError):
            raise _Stop("invalid_response", "Strava returned an unexpected response.") from None
    raise _Stop("network_error", "Strava could not be reached.")  # pragma: no cover


def _access(client: StravaClient, policy: RateLimitPolicy) -> str:
    try:
        while True:
            access = client.prepare_access((READ_ALL, READ))
            if policy.before_request(client.rate_limit, read=True):
                client.rate_limit = None  # waited for the window; the next response refreshes it
                continue
            return access.token
    except DailyLimitReached as error:
        raise _Stop("daily_limit_reached", str(error)) from None
    except RequestFailure:
        raise _Stop("not_authorized", "Strava credentials could not be prepared.") from None
