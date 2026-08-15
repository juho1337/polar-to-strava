from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from domain import Activity, ActivitySource, Lap, Location, Sport, TrackPoint
from tcx import TCXBuilder, TCXWriter
from tcx.validator import TCXValidationError


def activity() -> Activity:
    started_at = datetime(2025, 1, 1, tzinfo=UTC)
    point = TrackPoint(
        timestamp=started_at,
        location=Location(latitude=60.17, longitude=24.94, altitude_m=20),
        distance_m=10,
    )
    lap = Lap(
        index=1,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=10),
        distance_m=10,
        trackpoints=(point,),
    )
    return Activity(
        id="test",
        source=ActivitySource.MANUAL,
        sport=Sport.RUNNING,
        started_at=started_at,
        ended_at=lap.ended_at,
        laps=(lap,),
    )


def test_builder_produces_xml_bytes_without_writing_files(tmp_path: Path) -> None:
    content = TCXBuilder().build(activity())
    assert content.startswith(b"<?xml")
    assert b"TrainingCenterDatabase" in content
    assert list(tmp_path.iterdir()) == []


def test_writer_accepts_only_prebuilt_bytes(tmp_path: Path) -> None:
    destination = TCXWriter().write(b"<xml />", tmp_path / "nested" / "activity.tcx")
    assert destination.read_bytes() == b"<xml />"


def test_builder_rejects_activities_without_laps() -> None:
    started_at = datetime(2025, 1, 1, tzinfo=UTC)
    empty = Activity(
        id="empty",
        source=ActivitySource.MANUAL,
        sport=Sport.WALKING,
        started_at=started_at,
        ended_at=started_at,
    )
    with pytest.raises(TCXValidationError, match="at least one lap"):
        TCXBuilder().build(empty)
