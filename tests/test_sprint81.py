"""Compatibility regressions derived from the Sprint 8 corpus audit."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fit_tool.fit_file import FitFile  # type: ignore[import-untyped]
from fit_tool.profile.messages.lap_message import LapMessage  # type: ignore[import-untyped]
from fit_tool.profile.messages.record_message import RecordMessage  # type: ignore[import-untyped]

from core.errors import ExportError
from fit import FITBuilder
from polar import PolarImporter
from polar.errors import PolarValidationError
from services.audit import _domain_counts, _source_info, _warnings

SAMPLE = Path(__file__).parent / "samples" / "training-session-sanitized.json"


def source(
    tmp_path: Path, payload: dict[str, Any], name: str = "training-session-case.json"
) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def sample() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(SAMPLE.read_text(encoding="utf-8")))


def test_session_bounded_route_overrun_is_retained(tmp_path: Path) -> None:
    payload = sample()
    payload["stopTime"] = "2025-01-01T10:00:05.000"
    payload["exercises"][0]["recordedRoute"].append(
        {"dateTime": "2025-01-01T10:00:04.000", "latitude": 60.19, "longitude": 24.96}
    )
    path = source(tmp_path, payload)
    activity = PolarImporter().import_activity(path)
    assert activity.extensions["route_points_outside_exercise"] == 1
    assert [point.location.latitude for point in activity.trackpoints if point.location] == [
        60.17,
        60.18,
        60.19,
    ]
    assert "route_outside_exercise_inside_session" in _warnings(
        activity, _source_info(path)[1], _domain_counts(activity)
    )


def test_session_bounded_route_with_explicit_laps_keeps_points_and_source_laps(
    tmp_path: Path,
) -> None:
    payload = sample()
    payload["stopTime"] = "2025-01-01T10:00:05.000"
    payload["exercises"][0]["recordedRoute"].append(
        {"dateTime": "2025-01-01T10:00:04.000", "latitude": 60.19, "longitude": 24.96}
    )
    payload["exercises"][0]["laps"] = [
        {"startTime": "2025-01-01T10:00:00.000", "stopTime": "2025-01-01T10:00:03.000"}
    ]
    activity = PolarImporter().import_activity(source(tmp_path, payload))
    assert activity.extensions["source_laps_unapplied"] is True
    assert len(activity.laps) == 1
    assert activity.laps[0].ended_at == activity.ended_at
    assert sum(point.location is not None for point in activity.trackpoints) == 3
    assert FITBuilder().build(activity)


def test_route_extension_does_not_hide_other_explicit_lap_gaps(tmp_path: Path) -> None:
    payload = sample()
    payload["stopTime"] = "2025-01-01T10:00:05.000"
    payload["exercises"][0]["recordedRoute"].append(
        {"dateTime": "2025-01-01T10:00:04.000", "latitude": 60.19, "longitude": 24.96}
    )
    payload["exercises"][0]["laps"] = [
        {"startTime": "2025-01-01T10:00:00.000", "stopTime": "2025-01-01T10:00:00.500"},
        {"startTime": "2025-01-01T10:00:02.000", "stopTime": "2025-01-01T10:00:03.000"},
    ]
    with pytest.raises(PolarValidationError, match="outside all explicit laps"):
        PolarImporter().import_activity(source(tmp_path, payload))


def test_route_outside_session_remains_rejected(tmp_path: Path) -> None:
    payload = sample()
    payload["exercises"][0]["recordedRoute"].append(
        {"dateTime": "2025-01-01T10:00:04.000", "latitude": 60.19, "longitude": 24.96}
    )
    with pytest.raises(PolarValidationError, match="outside its session"):
        PolarImporter().import_activity(source(tmp_path, payload))


def test_nonroute_sample_outside_exercise_remains_rejected(tmp_path: Path) -> None:
    payload = sample()
    payload["stopTime"] = "2025-01-01T10:00:05.000"
    payload["exercises"][0]["samples"]["heartRate"].append(
        {"dateTime": "2025-01-01T10:00:04.000", "value": 120}
    )
    with pytest.raises(PolarValidationError, match="outside its exercise"):
        PolarImporter().import_activity(source(tmp_path, payload))


def test_missing_timezone_remains_ambiguous(tmp_path: Path) -> None:
    payload = sample()
    payload.pop("timeZoneOffset")
    payload["exercises"][0].pop("timezoneOffset")
    with pytest.raises(PolarValidationError, match="timezone offset"):
        PolarImporter().import_activity(source(tmp_path, payload))


def test_cumulative_lap_splits_reconstruct_without_overlap(tmp_path: Path) -> None:
    payload = sample()
    payload["exercises"][0]["laps"] = [
        {"lapNumber": 0, "splitTime": "PT1.5S", "duration": "PT1.5S"},
        {"lapNumber": 1, "splitTime": "PT3S", "duration": "PT1.5S"},
    ]
    activity = PolarImporter().import_activity(source(tmp_path, payload))
    assert len(activity.laps) == 2
    assert activity.laps[0].ended_at == activity.laps[1].started_at
    decoded = FitFile.from_bytes(FITBuilder().build(activity), check_crc=True)
    assert not decoded.validate().has_errors
    assert sum(isinstance(item.message, LapMessage) for item in decoded.records) == 2


def test_inconsistent_lap_split_remains_rejected(tmp_path: Path) -> None:
    payload = sample()
    payload["exercises"][0]["laps"] = [
        {"lapNumber": 0, "splitTime": "PT1S", "duration": "PT1S"},
        {"lapNumber": 1, "splitTime": "PT3S", "duration": "PT1S"},
    ]
    with pytest.raises(PolarValidationError, match="inconsistent"):
        PolarImporter().import_activity(source(tmp_path, payload))


def test_summary_only_activity_remains_unconverted_without_fake_records(tmp_path: Path) -> None:
    payload = sample()
    exercise = payload["exercises"][0]
    exercise["samples"] = {}
    exercise.pop("recordedRoute")
    activity = PolarImporter().import_activity(source(tmp_path, payload))
    assert not activity.trackpoints
    with pytest.raises(ExportError, match="recorded points"):
        FITBuilder().build(activity)


def test_equal_timestamp_route_observations_preserve_order_and_fit_count(tmp_path: Path) -> None:
    payload = sample()
    exercise = payload["exercises"][0]
    exercise["samples"]["heartRate"].append({"dateTime": "2025-01-01T10:00:00.000", "value": 131})
    exercise["recordedRoute"] = [
        {
            "dateTime": "2025-01-01T10:00:00.000",
            "latitude": 60.17,
            "longitude": 24.94,
            "altitude": 19,
        },
        {
            "dateTime": "2025-01-01T10:00:00.000",
            "latitude": 60.18,
            "longitude": 24.95,
            "altitude": 20,
        },
        {
            "dateTime": "2025-01-01T10:00:00.000",
            "latitude": 60.18,
            "longitude": 24.95,
            "altitude": 20,
        },
    ]
    path = source(tmp_path, payload)
    activity = PolarImporter().import_activity(path)
    points = [point for point in activity.trackpoints if point.location]
    assert [point.location.latitude for point in points if point.location] == [60.17, 60.18, 60.18]
    assert [point.heart_rate.bpm if point.heart_rate else None for point in points] == [
        130,
        131,
        None,
    ]
    decoded = FitFile.from_bytes(FITBuilder().build(activity), check_crc=True)
    records = [item.message for item in decoded.records if isinstance(item.message, RecordMessage)]
    assert len(records) == len(activity.trackpoints)
    assert [
        record.position_lat for record in records if record.position_lat is not None
    ] == pytest.approx([60.17, 60.18, 60.18], abs=1e-6)
    assert len({record.timestamp for record in records if record.position_lat is not None}) == 1
    assert _source_info(path)[1]["gps"] == _domain_counts(activity)["gps"] == 3
