from pathlib import Path

import pytest

from domain import ActivitySource, Sport
from polar.errors import PolarLoadError, PolarValidationError
from polar.parser import load_activity_json, parse_activity

SAMPLES = Path(__file__).parent / "samples"


def test_parse_polar_sample_preserves_supported_measurements() -> None:
    activity = parse_activity(SAMPLES / "activity-complete.json")
    point = activity.trackpoints[0]
    assert activity.source is ActivitySource.POLAR_FLOW
    assert activity.sport is Sport.RUNNING
    assert activity.name == "Morning Run"
    assert activity.distance_m == 1234.5
    assert activity.calories == 321
    assert (activity.ascent_m, activity.descent_m) == (43.0, 41.0)
    assert point.location is not None
    assert (point.location.latitude, point.location.altitude_m) == (60.1699, 18.5)
    assert (point.heart_rate.bpm, point.cadence.rpm, point.power.watts) == (145, 82.0, 250)
    assert point.temperature.celsius == 12.5
    assert activity.device.model == "Vantage V3"
    assert activity.zones["heart_rate"][0].duration_s == 120.0
    assert activity.laps[0].distance_m == 1000.0
    assert activity.extensions["running-index"] == 55


def test_missing_optional_values_are_supported() -> None:
    activity = parse_activity(SAMPLES / "activity-minimal.json")
    assert activity.distance_m is None
    assert activity.device is None
    assert activity.laps == ()


@pytest.mark.parametrize("filename", ["activity-invalid.json", "activity-invalid-schema.json"])
def test_malformed_activities_raise_controlled_errors(filename: str) -> None:
    with pytest.raises((PolarLoadError, PolarValidationError)):
        parse_activity(SAMPLES / filename)


def test_json_loader_rejects_non_object_root() -> None:
    with pytest.raises(PolarLoadError, match="root must be an object"):
        load_activity_json(SAMPLES / "activity-array.json")
