import json
from pathlib import Path
import pytest
from polar.parser import PolarParseError, parse_activity


def test_parse_activity_preserves_available_measurements(tmp_path: Path) -> None:
    path = tmp_path / "activity-1.json"
    path.write_text(json.dumps({"id": "123", "title": "Morning Run", "start-time": "2025-01-02T10:30:00+02:00", "distance": 1234.5, "samples": [{"recording-rate": 1, "data": [{"timestamp": "2025-01-02T10:30:01+02:00", "latitude": 60.1699, "longitude": 24.9384, "altitude": 18.5, "heart-rate": 145, "cadence": 82, "power": 250, "distance": 3.2}]}], "laps": [{"start-time": "2025-01-02T10:30:00+02:00", "distance": 1000}]}), encoding="utf-8")
    activity = parse_activity(path)
    point = activity.trackpoints[0]
    assert activity.title == "Morning Run"
    assert activity.distance == 1234.5
    assert (point.heart_rate, point.latitude, point.power, activity.laps[0].distance) == (145, 60.1699, 250, 1000)


def test_parse_activity_wraps_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "activity-invalid.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(PolarParseError, match="Could not read"):
        parse_activity(path)
