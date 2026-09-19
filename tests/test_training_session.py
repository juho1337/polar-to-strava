import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml import etree

from domain import Sport
from polar import PolarImporter
from polar.errors import PolarValidationError
from polar.parser import parse_activity
from services import ActivityValidator
from tcx import TCXBuilder
from tcx.serializers.trackpoint import TCX_NAMESPACE
from tcx.validator import TCXValidator

SAMPLES = Path(__file__).parent / "samples"


def test_real_shape_preserves_time_and_missing_measurements() -> None:
    activity = PolarImporter().import_activity(SAMPLES / "training-session-real-shape.json")
    assert activity.started_at == datetime(2025, 5, 5, 18, 4, 22, 490000, tzinfo=UTC)
    assert activity.trackpoints[0].timestamp == datetime(2025, 5, 5, 18, 4, 22, 645000, tzinfo=UTC)
    assert activity.sport is Sport.PADEL
    assert activity.recorded_duration_s == 4.224
    assert activity.average_heart_rate_bpm == 125
    assert activity.maximum_heart_rate_bpm == 140
    assert activity.calories == 4
    assert len(activity.trackpoints) == 2
    assert all(point.location is None and point.speed_mps is None for point in activity.trackpoints)
    assert all(
        point.distance_m is None and point.temperature is None for point in activity.trackpoints
    )
    assert not ActivityValidator().validate(activity).issues
    content = TCXBuilder().build(activity)
    TCXValidator().validate_xml(content)
    root = etree.fromstring(content)
    assert (
        root.findtext(f".//{{{TCX_NAMESPACE}}}Activity/{{{TCX_NAMESPACE}}}Id")
        == "2025-05-05T18:04:22.490000Z"
    )
    assert len(root.findall(f".//{{{TCX_NAMESPACE}}}Trackpoint")) == 2
    assert root.findtext(f".//{{{TCX_NAMESPACE}}}TotalTimeSeconds") == "4.224"


def test_separate_streams_merge_by_timestamp_and_route() -> None:
    activity = parse_activity(SAMPLES / "training-session-sanitized.json")
    assert activity.started_at == datetime(2025, 1, 1, 7, 0, tzinfo=UTC)
    assert activity.sport is Sport.RUNNING
    assert activity.distance_m == 8.5
    assert activity.calories == 42
    assert len(activity.trackpoints) == 3
    first, middle, last = activity.trackpoints
    assert first.location is not None and first.location.latitude == 60.17
    assert middle.location is not None and middle.location.longitude == 24.95
    assert middle.location.altitude_m == 20
    assert middle.speed_mps == pytest.approx(3.0)
    assert middle.cadence is not None and middle.cadence.rpm == 82
    assert middle.power is not None and middle.power.watts == 250
    assert middle.temperature is not None and middle.temperature.celsius == 12.5
    assert last.heart_rate is not None and last.heart_rate.bpm == 150
    assert last.distance_m == 8.5
    content = TCXBuilder().build(activity)
    TCXValidator().validate_xml(content)
    root = etree.fromstring(content)
    tcx_activity = root.find(f".//{{{TCX_NAMESPACE}}}Activity")
    assert tcx_activity is not None
    assert tcx_activity.get("Sport") == "Running"
    assert len(root.findall(f".//{{{TCX_NAMESPACE}}}Trackpoint")) == 3


def test_multiple_exercises_have_deterministic_mixed_sport_and_order(tmp_path: Path) -> None:
    payload = json.loads((SAMPLES / "training-session-sanitized.json").read_text(encoding="utf-8"))
    second = json.loads(json.dumps(payload["exercises"][0]))
    second["sport"] = "CYCLING"
    second["startTime"] = "2025-01-01T10:00:04.000"
    second["stopTime"] = "2025-01-01T10:00:06.000"
    second["samples"] = {"heartRate": [{"dateTime": "2025-01-01T10:00:05.000", "value": 120}]}
    second.pop("recordedRoute")
    payload["exercises"].append(second)
    payload["stopTime"] = "2025-01-01T10:00:06.000"
    path = tmp_path / "training-session-multi.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    activity = parse_activity(path)
    assert activity.sport is Sport.OTHER
    assert activity.extensions["exercise_sports"] == ("RUNNING", "CYCLING")
    assert len(activity.laps) == 1
    assert len(activity.trackpoints) == 4
    assert activity.trackpoints[-1].timestamp > activity.trackpoints[-2].timestamp


def test_daily_tracking_is_never_a_workout() -> None:
    with pytest.raises(PolarValidationError, match="daily activity tracking"):
        parse_activity(SAMPLES / "activity-daily-tracking.json")


def test_explicit_exercise_laps_are_preserved(tmp_path: Path) -> None:
    payload = json.loads((SAMPLES / "training-session-sanitized.json").read_text(encoding="utf-8"))
    payload["exercises"][0]["laps"] = [
        {"startTime": "2025-01-01T10:00:00.000", "stopTime": "2025-01-01T10:00:00.500"},
        {"startTime": "2025-01-01T10:00:01.000", "stopTime": "2025-01-01T10:00:03.000"},
    ]
    path = tmp_path / "training-session-laps.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    activity = parse_activity(path)
    assert len(activity.laps) == 2
    assert [len(lap.trackpoints) for lap in activity.laps] == [1, 2]
    TCXValidator().validate_xml(TCXBuilder().build(activity))


def test_naive_session_timestamps_need_offset(tmp_path: Path) -> None:
    payload = json.loads((SAMPLES / "training-session-real-shape.json").read_text(encoding="utf-8"))
    payload.pop("timeZoneOffset")
    payload["exercises"][0].pop("timezoneOffset")
    path = tmp_path / "training-session-no-offset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PolarValidationError, match="timezone offset"):
        parse_activity(path)
