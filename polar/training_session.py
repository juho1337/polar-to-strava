"""Import Polar user-data training-session exports (exportVersion 2.x)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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

_STREAMS = {
    "heartRate": "heart_rate",
    "distance": "distance_m",
    "speed": "speed_mps",
    "cadence": "cadence",
    "power": "power",
    "temperature": "temperature",
    "altitude": "altitude_m",
}
_DURATION = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def _number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _duration(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"unsupported Polar duration: {value}")
    match = _DURATION.fullmatch(value)
    if match is None:
        raise ValueError(f"unsupported Polar duration: {value}")
    return sum(
        float(match.group(name) or 0) * multiplier
        for name, multiplier in (("days", 86400), ("hours", 3600), ("minutes", 60), ("seconds", 1))
    )


def _offset(exercise: Mapping[str, Any], session: Mapping[str, Any]) -> timezone:
    minutes = exercise.get(
        "timezoneOffset", exercise.get("timeZoneOffset", session.get("timeZoneOffset"))
    )
    if not isinstance(minutes, (int, float)) or abs(minutes) > 14 * 60:
        raise ValueError("training session requires a valid timezone offset in minutes")
    return timezone(timedelta(minutes=minutes))


def _time(value: Any, offset: timezone, label: str, anchor: datetime | None = None) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} requires a timestamp")
    if value.startswith("PT"):
        seconds = _duration(value)
        if seconds is None or anchor is None:
            raise ValueError(f"{label} requires an exercise start for relative time")
        return anchor + timedelta(seconds=seconds)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=offset) if parsed.tzinfo is None else parsed


def _sport(value: Any) -> Sport:
    if not isinstance(value, str):
        return Sport.OTHER
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    try:
        return Sport(normalized)
    except ValueError:
        return Sport.OTHER


def _stream_points(
    exercise: Mapping[str, Any], offset: timezone, start: datetime
) -> tuple[TrackPoint, ...]:
    samples = exercise.get("samples") or {}
    if not isinstance(samples, Mapping):
        raise ValueError("exercise samples must be an object of named streams")
    by_time: dict[datetime, dict[str, Any]] = {}
    for source_name, field in _STREAMS.items():
        stream = samples.get(source_name) or []
        if not isinstance(stream, Sequence) or isinstance(stream, (str, bytes)):
            raise ValueError(f"{source_name} samples must be an array")
        for item in stream:
            if not isinstance(item, Mapping):
                raise ValueError(f"{source_name} sample must be an object")
            value = _number(item.get("value"), source_name)
            if value is None:
                continue
            time_value = item.get("dateTime", item.get("time"))
            at = _time(time_value, offset, f"{source_name} sample", start)
            # Polar's published speed sample unit is km/h; domain speed is m/s.
            if field == "speed_mps":
                value /= 3.6
            by_time.setdefault(at, {})[field] = value
    # Distinct Polar altitude streams have different timestamps and, for real
    # exports, different elevation ranges. Never mix route elevation into a
    # session that has a populated standalone altitude stream.
    has_sensor_altitude = any("altitude_m" in values for values in by_time.values())
    route = exercise.get("recordedRoute", samples.get("recordedRoute", [])) or []
    if isinstance(route, Mapping):
        route = route.get("points", route.get("locations", []))
    if not isinstance(route, Sequence) or isinstance(route, (str, bytes)):
        raise ValueError("recordedRoute must be an array of points")
    for item in route:
        if not isinstance(item, Mapping):
            raise ValueError("recordedRoute point must be an object")
        at = _time(item.get("dateTime", item.get("time")), offset, "recordedRoute point", start)
        latitude = _number(item.get("latitude", item.get("lat")), "latitude")
        longitude = _number(item.get("longitude", item.get("lon")), "longitude")
        if latitude is None or longitude is None:
            raise ValueError("recordedRoute point requires latitude and longitude")
        fields = by_time.setdefault(at, {})
        fields["latitude"] = latitude
        fields["longitude"] = longitude
        altitude = _number(item.get("altitude"), "altitude")
        if altitude is not None and not has_sensor_altitude:
            fields["altitude_m"] = altitude
    points: list[TrackPoint] = []
    for at, values in sorted(by_time.items()):
        location = None
        if "latitude" in values:
            location = Location(
                latitude=values["latitude"],
                longitude=values["longitude"],
            )
        points.append(
            TrackPoint(
                timestamp=at,
                location=location,
                altitude_m=values.get("altitude_m"),
                distance_m=values.get("distance_m"),
                speed_mps=values.get("speed_mps"),
                heart_rate=(
                    HeartRate(bpm=round(values["heart_rate"])) if "heart_rate" in values else None
                ),
                cadence=Cadence(rpm=values["cadence"]) if "cadence" in values else None,
                power=Power(watts=round(values["power"])) if "power" in values else None,
                temperature=(
                    Temperature(celsius=values["temperature"]) if "temperature" in values else None
                ),
            )
        )
    return tuple(points)


def _zones(exercises: Sequence[Mapping[str, Any]]) -> dict[str, tuple[Zone, ...]]:
    if len(exercises) != 1:
        return {}
    raw = exercises[0].get("zones") or {}
    if not isinstance(raw, Mapping):
        return {}
    heart = raw.get("heart_rate") or []
    if not isinstance(heart, Sequence):
        return {}
    return {
        "heart_rate": tuple(
            Zone(
                lower_bound=_number(item.get("lowerLimit"), "zone lowerLimit"),
                upper_bound=_number(item.get("higherLimit"), "zone higherLimit"),
                duration_s=_duration(item.get("inZone")),
            )
            for item in heart
            if isinstance(item, Mapping)
        )
    }


def parse_training_session(payload: Mapping[str, Any], path: Path) -> Activity:
    """Map one user-data training session into a provider-neutral Activity."""
    raw_exercises = payload.get("exercises")
    if not isinstance(raw_exercises, list) or not raw_exercises:
        raise ValueError("training session requires a nonempty exercises array")
    if not all(isinstance(exercise, Mapping) for exercise in raw_exercises):
        raise ValueError("each exercise must be an object")
    exercises: list[Mapping[str, Any]] = raw_exercises
    session_offset = _offset(exercises[0], payload)
    started_at = _time(payload.get("startTime"), session_offset, "session startTime")
    stopped_at = _time(payload.get("stopTime"), session_offset, "session stopTime")
    all_points: list[TrackPoint] = []
    exercise_distances: list[float | None] = []
    lap_ranges: list[tuple[datetime, datetime, Mapping[str, Any]]] = []
    for exercise in exercises:
        offset = _offset(exercise, payload)
        exercise_start = _time(exercise.get("startTime"), offset, "exercise startTime")
        exercise_end = _time(exercise.get("stopTime"), offset, "exercise stopTime")
        if exercise_start < started_at or exercise_end > stopped_at:
            raise ValueError("exercise times must be within session startTime and stopTime")
        exercise_points = _stream_points(exercise, offset, exercise_start)
        exercise_distance = _number(exercise.get("distance"), "exercise distance")
        if exercise_distance is None:
            recorded_distances = [
                point.distance_m for point in exercise_points if point.distance_m is not None
            ]
            exercise_distance = max(recorded_distances) if recorded_distances else None
        exercise_distances.append(exercise_distance)
        for point in exercise_points:
            if point.timestamp < exercise_start or point.timestamp > exercise_end:
                raise ValueError("sample timestamp falls outside its exercise")
            all_points.append(point)
        raw_laps = exercise.get("laps") or []
        if not isinstance(raw_laps, list):
            raise ValueError("exercise laps must be an array")
        for raw_lap in raw_laps:
            if not isinstance(raw_lap, Mapping):
                raise ValueError("exercise lap must be an object")
            lap_start = _time(raw_lap.get("startTime"), offset, "lap startTime")
            lap_end = _time(raw_lap.get("stopTime"), offset, "lap stopTime")
            lap_ranges.append((lap_start, lap_end, raw_lap))
    points = tuple(sorted(all_points, key=lambda point: point.timestamp))
    if lap_ranges:
        lap_ranges.sort(key=lambda item: item[0])
        laps = tuple(
            Lap(
                index=index,
                started_at=lap_start,
                ended_at=lap_end,
                distance_m=_number(raw.get("distance"), "lap distance"),
                trackpoints=tuple(
                    point for point in points if lap_start <= point.timestamp <= lap_end
                ),
                extensions=dict(raw),
            )
            for index, (lap_start, lap_end, raw) in enumerate(lap_ranges, 1)
        )
        if any(
            not any(lap.started_at <= point.timestamp <= lap.ended_at for lap in laps)
            for point in points
        ):
            raise ValueError("sample timestamp falls outside all explicit laps")
    else:
        laps = (
            (Lap(index=1, started_at=started_at, ended_at=stopped_at, trackpoints=points),)
            if points
            else ()
        )
    sports = tuple(_sport(exercise.get("sport")) for exercise in exercises)
    sport = sports[0] if len(set(sports)) == 1 else Sport.OTHER
    distance = _number(payload.get("distance"), "session distance")
    if distance is None and all(value is not None for value in exercise_distances):
        distance = sum(value for value in exercise_distances if value is not None)
    calories = _number(payload.get("kiloCalories"), "session kiloCalories")
    device_id = payload.get("deviceId")
    return Activity(
        id=path.stem,
        source=ActivitySource.POLAR_FLOW,
        sport=sport,
        started_at=started_at,
        ended_at=stopped_at,
        name=payload.get("name") or None,
        distance_m=distance,
        calories=round(calories) if calories is not None else None,
        recorded_duration_s=_duration(payload.get("duration")),
        average_heart_rate_bpm=payload.get("averageHeartRate"),
        maximum_heart_rate_bpm=payload.get("maximumHeartRate"),
        device=Device(manufacturer="Polar", serial_number=str(device_id)) if device_id else None,
        laps=laps,
        zones=_zones(exercises),
        extensions={
            "exercise_sports": tuple(exercise.get("sport") for exercise in exercises),
            "session_timezone_offset_minutes": payload.get("timeZoneOffset"),
            "exercise_metadata": tuple(
                {
                    key: value
                    for key, value in exercise.items()
                    if key not in ("samples", "recordedRoute", "laps")
                }
                for exercise in exercises
            ),
        },
    )
