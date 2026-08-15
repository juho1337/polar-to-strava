"""Robust conversion of Polar Flow JSON exports into domain activities."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from domain import (
    Activity,
    ActivitySource,
    Cadence,
    Device,
    HeartRate,
    Lap,
    Location,
    Power,
    Sport,
    Temperature,
    TrackPoint,
    Zone,
)
from polar.errors import PolarImportError, PolarLoadError, PolarValidationError

# Backward-compatible name retained for callers of the Sprint 1 parser API.
PolarParseError = PolarImportError

_DURATION = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
_ACTIVITY_KEYS = frozenset(
    {
        "id",
        "activity-id",
        "start-time",
        "start_time",
        "end-time",
        "end_time",
        "duration",
        "title",
        "name",
        "description",
        "distance",
        "calories",
        "calorie",
        "ascent",
        "descent",
        "sport",
        "detailed-sport-info",
        "detailed_sport_info",
        "device",
        "samples",
        "laps",
        "heart-rate-zones",
        "heart_rate_zones",
        "power-zones",
        "power_zones",
    }
)
_POINT_KEYS = frozenset(
    {
        "timestamp",
        "time",
        "latitude",
        "lat",
        "longitude",
        "lon",
        "lng",
        "altitude",
        "alt",
        "heart-rate",
        "heart_rate",
        "hr",
        "cadence",
        "power",
        "temperature",
        "distance",
        "speed",
    }
)


def load_activity_json(path: Path | str) -> Mapping[str, Any]:
    """Read one JSON object while translating all loader failures to import errors."""
    activity_path = Path(path)
    try:
        with activity_path.open(encoding="utf-8") as stream:
            payload: Any = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolarLoadError(activity_path, f"could not read JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise PolarLoadError(activity_path, "JSON root must be an object")
    return payload


def parse_activity(path: Path | str) -> Activity:
    """Load and validate one Polar export as an immutable domain activity."""
    activity_path = Path(path)
    try:
        return _parse_payload(load_activity_json(activity_path))
    except PolarLoadError:
        raise
    except (ValidationError, TypeError, ValueError, KeyError, OverflowError) as error:
        raise PolarValidationError(activity_path, str(error)) from error


def _parse_payload(payload: Mapping[str, Any]) -> Activity:
    started_at = _required_datetime(payload, "start-time", "start_time")
    trackpoints = _trackpoints(payload.get("samples"))
    ended_at = _optional_datetime(payload, "end-time", "end_time")
    if ended_at is None:
        seconds = _duration_seconds(payload.get("duration"))
        ended_at = (
            started_at + timedelta(seconds=seconds)
            if seconds is not None
            else trackpoints[-1].timestamp if trackpoints else started_at
        )
    laps = _laps(payload.get("laps"), started_at, ended_at, trackpoints)
    if not laps and trackpoints:
        laps = (Lap(index=1, started_at=started_at, ended_at=ended_at, trackpoints=trackpoints),)
    return Activity(
        id=str(_first(payload, "id", "activity-id") or f"polar-{started_at.isoformat()}"),
        source=ActivitySource.POLAR_FLOW,
        sport=_sport(_first(payload, "detailed-sport-info", "detailed_sport_info", "sport")),
        started_at=started_at,
        ended_at=ended_at,
        name=_string(_first(payload, "title", "name")),
        description=_string(payload.get("description")),
        distance_m=_number(payload.get("distance")),
        calories=_integer(_first(payload, "calories", "calorie")),
        ascent_m=_number(payload.get("ascent")),
        descent_m=_number(payload.get("descent")),
        device=_device(payload.get("device")),
        laps=laps,
        zones=_zones(payload),
        extensions={key: value for key, value in payload.items() if key not in _ACTIVITY_KEYS},
    )


def _trackpoints(samples: Any) -> tuple[TrackPoint, ...]:
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)):
        return ()
    points: list[TrackPoint] = []
    for group in samples:
        if not isinstance(group, Mapping):
            continue
        data = group.get("data")
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            continue
        for raw_point in data:
            if isinstance(raw_point, Mapping):
                points.append(_trackpoint(raw_point))
    return tuple(sorted(points, key=lambda point: point.timestamp))


def _trackpoint(payload: Mapping[str, Any]) -> TrackPoint:
    latitude = _number(_first(payload, "latitude", "lat"))
    longitude = _number(_first(payload, "longitude", "lon", "lng"))
    location = None
    if latitude is not None and longitude is not None:
        location = Location(
            latitude=latitude,
            longitude=longitude,
            altitude_m=_number(_first(payload, "altitude", "alt")),
        )
    return TrackPoint(
        timestamp=_required_datetime(payload, "timestamp", "time"),
        location=location,
        distance_m=_number(payload.get("distance")),
        speed_mps=_number(payload.get("speed")),
        heart_rate=_measurement(
            HeartRate, _first(payload, "heart-rate", "heart_rate", "hr"), "bpm"
        ),
        cadence=_measurement(Cadence, payload.get("cadence"), "rpm"),
        power=_measurement(Power, payload.get("power"), "watts"),
        temperature=_measurement(Temperature, payload.get("temperature"), "celsius"),
        extensions={key: value for key, value in payload.items() if key not in _POINT_KEYS},
    )


def _laps(
    raw_laps: Any,
    activity_start: datetime,
    activity_end: datetime,
    points: tuple[TrackPoint, ...],
) -> tuple[Lap, ...]:
    if not isinstance(raw_laps, Sequence) or isinstance(raw_laps, (str, bytes)):
        return ()
    laps: list[Lap] = []
    previous_end = activity_start
    for index, raw_lap in enumerate(raw_laps, start=1):
        if not isinstance(raw_lap, Mapping):
            raise ValueError(f"lap {index} must be an object")
        started_at = _optional_datetime(raw_lap, "start-time", "start_time") or previous_end
        seconds = _duration_seconds(raw_lap.get("duration"))
        ended_at = _optional_datetime(raw_lap, "end-time", "end_time")
        if ended_at is None:
            ended_at = (
                started_at + timedelta(seconds=seconds) if seconds is not None else activity_end
            )
        lap_points = tuple(point for point in points if started_at <= point.timestamp <= ended_at)
        laps.append(
            Lap(
                index=index,
                started_at=started_at,
                ended_at=ended_at,
                distance_m=_number(raw_lap.get("distance")),
                ascent_m=_number(raw_lap.get("ascent")),
                descent_m=_number(raw_lap.get("descent")),
                trackpoints=lap_points,
                extensions=dict(raw_lap),
            )
        )
        previous_end = ended_at
    return tuple(laps)


def _zones(payload: Mapping[str, Any]) -> Mapping[str, tuple[Zone, ...]]:
    zones: dict[str, tuple[Zone, ...]] = {}
    zone_fields = (
        ("heart-rate-zones", "heart_rate"),
        ("heart_rate_zones", "heart_rate"),
        ("power-zones", "power"),
        ("power_zones", "power"),
    )
    for source_key, name in zone_fields:
        raw_zones = payload.get(source_key)
        if not isinstance(raw_zones, Sequence) or isinstance(raw_zones, (str, bytes)):
            continue
        converted = tuple(_zone(raw) for raw in raw_zones if isinstance(raw, Mapping))
        if converted:
            zones[name] = converted
    return zones


def _zone(payload: Mapping[str, Any]) -> Zone:
    return Zone(
        lower_bound=_number(_first(payload, "lower-bound", "lower_bound", "min")),
        upper_bound=_number(_first(payload, "upper-bound", "upper_bound", "max")),
        duration_s=_duration_seconds(_first(payload, "duration", "time")),
    )


def _device(value: Any) -> Device | None:
    if isinstance(value, str) and value.strip():
        return Device(manufacturer="Polar", model=value.strip())
    if isinstance(value, Mapping):
        return Device(
            manufacturer=_string(_first(value, "manufacturer", "brand")) or "Polar",
            model=_string(_first(value, "model", "name")),
            serial_number=_string(_first(value, "serial-number", "serial_number", "serial")),
            software_version=_string(
                _first(value, "software-version", "software_version", "firmware-version")
            ),
        )
    return None


def _sport(value: Any) -> Sport:
    if not isinstance(value, str):
        return Sport.OTHER
    normalized = value.lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "road_cycling": Sport.CYCLING,
        "treadmill_running": Sport.RUNNING,
        "xc_skiing": Sport.CROSS_COUNTRY_SKIING,
    }
    try:
        return aliases.get(normalized, Sport(normalized))
    except ValueError:
        return Sport.OTHER


def _required_datetime(payload: Mapping[str, Any], *keys: str) -> datetime:
    value = _optional_datetime(payload, *keys)
    if value is None:
        raise ValueError(f"missing required timestamp ({', '.join(keys)})")
    return value


def _optional_datetime(payload: Mapping[str, Any], *keys: str) -> datetime | None:
    value = _first(payload, *keys)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _duration_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        raise ValueError("duration must be seconds or ISO-8601 duration")
    match = _DURATION.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid ISO-8601 duration: {value}")
    multipliers = (("days", 86_400), ("hours", 3_600), ("minutes", 60), ("seconds", 1))
    return sum(float(match.group(name) or 0) * multiplier for name, multiplier in multipliers)


def _measurement[T](model: type[T], value: Any, field: str) -> T | None:
    numeric = _number(value)
    return model(**{field: numeric}) if numeric is not None else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise ValueError("measurement must be numeric")


def _integer(value: Any) -> int | None:
    numeric = _number(value)
    return int(numeric) if numeric is not None else None


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    return next((payload[key] for key in keys if key in payload), None)
