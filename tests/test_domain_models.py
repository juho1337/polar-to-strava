from datetime import UTC, datetime, timedelta

import pytest
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
)

NOW = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


def point(at: datetime = NOW) -> TrackPoint:
    return TrackPoint(
        timestamp=at,
        location=Location(latitude=60.1699, longitude=24.9384, altitude_m=18.5),
        distance_m=20.0,
        speed_mps=3.5,
        heart_rate=HeartRate(bpm=145),
        cadence=Cadence(rpm=82),
        power=Power(watts=250),
        temperature=Temperature(celsius=12.5),
    )


def lap() -> Lap:
    return Lap(index=1, started_at=NOW, ended_at=NOW + timedelta(minutes=1), distance_m=200.0, trackpoints=(point(), point(NOW + timedelta(seconds=30))))


def test_activity_preserves_exporter_data() -> None:
    activity = Activity(id="polar-123", source=ActivitySource.POLAR_FLOW, sport=Sport.RUNNING, started_at=NOW, ended_at=NOW + timedelta(minutes=1), distance_m=200.0, device=Device(manufacturer="Polar", model="Vantage V3"), laps=(lap(),), extensions={"running_index": 55})
    assert activity.trackpoints[0].location is not None
    assert activity.trackpoints[0].location.latitude == 60.1699
    assert activity.trackpoints[0].heart_rate.bpm == 145
    assert activity.trackpoints[0].power.watts == 250
    assert activity.duration == timedelta(minutes=1)
    assert activity.extensions == {"running_index": 55}


def test_models_are_frozen() -> None:
    model = point()
    with pytest.raises(ValidationError):
        model.distance_m = 50.0  # type: ignore[misc]


def test_extensions_are_recursively_immutable() -> None:
    model = point().model_copy(update={"extensions": {"nested": ["value"]}})
    frozen = TrackPoint.model_validate(model.model_dump())
    with pytest.raises(TypeError):
        frozen.extensions["new"] = "value"  # type: ignore[index]
    with pytest.raises(AttributeError):
        frozen.extensions["nested"].append("another")  # type: ignore[union-attr]


@pytest.mark.parametrize(("model", "value"), [(Location, {"latitude": 91, "longitude": 0}), (HeartRate, {"bpm": 301}), (Cadence, {"rpm": -1}), (Power, {"watts": -1}), (Temperature, {"celsius": 101})])
def test_measurement_validation(model: type[object], value: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        model(**value)  # type: ignore[operator]


def test_timestamp_must_include_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        TrackPoint(timestamp=datetime(2025, 1, 1))


def test_lap_requires_ordered_points_in_range() -> None:
    with pytest.raises(ValidationError, match="ordered"):
        Lap(index=1, started_at=NOW, ended_at=NOW + timedelta(minutes=1), trackpoints=(point(NOW + timedelta(seconds=30)), point()))


def test_activity_requires_consecutive_lap_indexes() -> None:
    invalid_lap = lap().model_copy(update={"index": 2})
    with pytest.raises(ValidationError, match="consecutive"):
        Activity(id="1", source=ActivitySource.MANUAL, sport=Sport.WALKING, started_at=NOW, ended_at=NOW + timedelta(minutes=1), laps=(invalid_lap,))


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        Device(manufacturer="Polar", unknown="field")  # type: ignore[call-arg]
