"""FIT output is checked through decoded messages, not just encoder success."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fit_tool.fit_file import FitFile  # type: ignore[import-untyped]
from fit_tool.profile.messages.record_message import RecordMessage  # type: ignore[import-untyped]
from fit_tool.profile.messages.session_message import SessionMessage  # type: ignore[import-untyped]
from fit_tool.profile.profile_type import (  # type: ignore[import-untyped]
    Sport as FITSport,
)
from fit_tool.profile.profile_type import SubSport
from lxml import etree

from domain import (
    Activity,
    ActivitySource,
    Cadence,
    HeartRate,
    Lap,
    Location,
    Power,
    Sport,
    Temperature,
    TrackPoint,
)
from fit import FITBuilder
from polar import PolarImporter
from services import ActivityValidator, ConversionService
from tcx import TCXBuilder
from tcx.serializers.trackpoint import TCX_NAMESPACE


def messages(activity: Activity, kind: type[Any]) -> list[Any]:
    decoded = FitFile.from_bytes(FITBuilder().build(activity), check_crc=True)
    assert not decoded.validate().has_errors
    return [item.message for item in decoded.records if isinstance(item.message, kind)]


def test_indoor_hr_only_round_trip() -> None:
    activity = PolarImporter().import_activity(
        Path(__file__).parent / "samples" / "training-session-real-shape.json"
    )
    records = messages(activity, RecordMessage)
    assert len(records) == 2
    assert [point.heart_rate for point in records] == [79, 80]
    assert [point.timestamp for point in records] == [1746468263000, 1746468267000]
    assert all(point.position_lat is None and point.distance is None for point in records)
    session = messages(activity, SessionMessage)[0]
    assert (session.sport, session.sub_sport) == (FITSport.RACKET.value, SubSport.PADEL.value)
    assert session.total_elapsed_time == activity.duration.total_seconds()
    assert session.total_timer_time == 4.224
    assert session.total_calories == 4


def test_gps_multisensor_and_timezone_round_trip() -> None:
    offset = timezone(timedelta(hours=3))
    start = datetime(2025, 5, 5, 21, 4, 22, 645000, tzinfo=offset)
    point = TrackPoint(
        timestamp=start,
        location=Location(latitude=60.123456, longitude=24.123456, altitude_m=32.4),
        distance_m=100.12,
        speed_mps=3.456,
        heart_rate=HeartRate(bpm=120),
        cadence=Cadence(rpm=88),
        power=Power(watts=215),
        temperature=Temperature(celsius=16),
    )
    activity = Activity(
        id="gps-fit-test",
        source=ActivitySource.POLAR_FLOW,
        sport=Sport.RUNNING,
        started_at=start,
        ended_at=start + timedelta(seconds=5),
        distance_m=100.12,
        calories=2,
        laps=(
            Lap(
                index=1,
                started_at=start,
                ended_at=start + timedelta(seconds=5),
                distance_m=100.12,
                trackpoints=(point,),
            ),
        ),
    )
    record = messages(activity, RecordMessage)[0]
    assert record.timestamp == 1746468263000  # 18:04:23 UTC, rounded from .645
    assert abs(record.position_lat - 60.123456) < 0.000001
    assert abs(record.position_long - 24.123456) < 0.000001
    assert abs(record.enhanced_altitude - 32.4) < 0.01
    assert abs(record.distance - 100.12) < 0.01
    assert abs(record.enhanced_speed - 3.456) < 0.01
    assert (record.heart_rate, record.cadence, record.power, record.temperature) == (
        120,
        88,
        215,
        16,
    )
    assert messages(activity, SessionMessage)[0].total_distance == 100.12


def test_cli_fit_and_tcx_formats(tmp_path: Path) -> None:
    source = Path(__file__).parent / "samples" / "training-session-real-shape.json"
    service = ConversionService(PolarImporter(), ActivityValidator())
    fit_path = tmp_path / "pilot.fit"
    service.convert_file(source, fit_path, format="fit")
    assert FitFile.from_file(str(fit_path)).validate().has_errors is False
    service.convert_file(source, tmp_path / "pilot.tcx")
    assert (tmp_path / "pilot.tcx").exists()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / source.name).write_bytes(source.read_bytes())
    outputs, issues = service.convert_folder(source_dir, tmp_path / "output", format="fit")
    assert not issues
    assert len(outputs) == 1 and outputs[0].suffix == ".fit"
    assert FitFile.from_file(str(outputs[0])).validate().has_errors is False


def test_independent_polar_altitude_and_unsupported_left_power(tmp_path: Path) -> None:
    payload = json.loads(
        (Path(__file__).parent / "samples" / "training-session-sanitized.json").read_text(
            encoding="utf-8"
        )
    )
    exercise = payload["exercises"][0]
    exercise["samples"].pop("power")
    exercise["samples"]["altitude"] = [
        {"dateTime": "2025-01-01T10:00:00.384", "value": 136.4},
        {"dateTime": "2025-01-01T10:00:01.384", "value": 164.289},
    ]
    exercise["samples"]["leftPedalCrankBasedPower"] = [
        {"dateTime": "2025-01-01T10:00:01.384", "currentPower": 135}
    ]
    exercise["power"] = {"avg": 252, "max": 359}
    exercise["recordedRoute"][0]["dateTime"] = "2025-01-01T10:00:00.482"
    exercise["recordedRoute"][1]["dateTime"] = "2025-01-01T10:00:01.482"
    exercise["recordedRoute"][1]["altitude"] = -4
    path = tmp_path / "training-session-altitude.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    activity = PolarImporter().import_activity(path)
    altitude_points = [point for point in activity.trackpoints if point.altitude_m is not None]
    assert [point.altitude_m for point in altitude_points] == [136.4, 164.289]
    assert all(point.location is None for point in altitude_points)
    assert [point.timestamp.microsecond for point in altitude_points] == [384000, 384000]
    assert sum(point.location is not None for point in activity.trackpoints) == 2
    assert all(
        point.location.altitude_m is None for point in activity.trackpoints if point.location
    )
    assert all(point.power is None for point in activity.trackpoints)

    records = messages(activity, RecordMessage)
    fit_altitude = [
        point.enhanced_altitude for point in records if point.enhanced_altitude is not None
    ]
    assert fit_altitude == pytest.approx([136.4, 164.2])  # FIT resolution is 0.2 m
    assert all(point.power is None for point in records)
    assert sum(point.position_lat is not None for point in records) == 2
    tcx = etree.fromstring(TCXBuilder().build(activity))
    altitude_nodes = tcx.findall(f".//{{{TCX_NAMESPACE}}}AltitudeMeters")
    assert [node.text for node in altitude_nodes] == ["136.4", "164.289"]


def test_route_altitude_fallback_and_missing_altitude(tmp_path: Path) -> None:
    payload = json.loads(
        (Path(__file__).parent / "samples" / "training-session-sanitized.json").read_text(
            encoding="utf-8"
        )
    )
    exercise = payload["exercises"][0]
    exercise["samples"].pop("altitude")
    route_path = tmp_path / "route-altitude.json"
    route_path.write_text(json.dumps(payload), encoding="utf-8")
    activity = PolarImporter().import_activity(route_path)
    assert [p.altitude_m for p in activity.trackpoints if p.altitude_m is not None] == [19]
    assert [
        p.enhanced_altitude
        for p in messages(activity, RecordMessage)
        if p.enhanced_altitude is not None
    ] == [19]

    exercise["recordedRoute"][0].pop("altitude")
    missing_path = tmp_path / "missing-altitude.json"
    missing_path.write_text(json.dumps(payload), encoding="utf-8")
    missing_activity = PolarImporter().import_activity(missing_path)
    assert all(p.recorded_altitude_m is None for p in missing_activity.trackpoints)
    assert all(p.enhanced_altitude is None for p in messages(missing_activity, RecordMessage))
