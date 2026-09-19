from pathlib import Path

import pytest
from lxml import etree
from typer.testing import CliRunner

from core.cli import app
from domain import Sport
from polar import PolarImporter
from services import ActivityValidator, ConversionService
from tcx import TCXBuilder
from tcx.serializers.activity import tcx_sport
from tcx.serializers.trackpoint import EXT_NAMESPACE, TCX_NAMESPACE
from tcx.validator import TCXValidationError, TCXValidator

SAMPLE = Path(__file__).parent / "samples" / "activity-complete.json"
NS = {"tcx": TCX_NAMESPACE, "ae": EXT_NAMESPACE}


@pytest.mark.parametrize(
    ("sport", "expected"),
    [(Sport.RUNNING, "Running"), (Sport.CYCLING, "Biking"), (Sport.YOGA, "Other")],
)
def test_sport_mapping(sport: Sport, expected: str) -> None:
    assert tcx_sport(sport) == expected


def test_polar_to_valid_tcx_semantics() -> None:
    activity, issues = ConversionService(PolarImporter(), ActivityValidator()).import_one(SAMPLE)
    assert not issues
    content = TCXBuilder().build(activity)
    TCXValidator().validate_xml(content)
    root = etree.fromstring(content)
    tcx_activity = root.find("tcx:Activities/tcx:Activity", NS)
    assert tcx_activity is not None
    assert tcx_activity.get("Sport") == "Running"
    assert tcx_activity.findtext("tcx:Id", namespaces=NS) == "2025-01-02T08:30:00Z"
    laps = tcx_activity.findall("tcx:Lap", NS)
    assert len(laps) == 1
    points = root.findall(".//tcx:Trackpoint", NS)
    assert len(points) == 2
    assert [point.findtext("tcx:Time", namespaces=NS) for point in points] == [
        "2025-01-02T08:30:01Z",
        "2025-01-02T08:30:02Z",
    ]
    point = points[0]
    assert point.findtext("tcx:Position/tcx:LatitudeDegrees", namespaces=NS) == "60.1699"
    assert point.findtext("tcx:AltitudeMeters", namespaces=NS) == "18.5"
    assert point.findtext("tcx:DistanceMeters", namespaces=NS) == "3.2"
    assert point.findtext("tcx:HeartRateBpm/tcx:Value", namespaces=NS) == "145"
    assert point.findtext("tcx:Cadence", namespaces=NS) == "82"
    assert point.findtext("tcx:Extensions/ae:TPX/ae:Speed", namespaces=NS) == "3.1"
    assert point.findtext("tcx:Extensions/ae:TPX/ae:Watts", namespaces=NS) == "250"
    assert laps[0].findtext("tcx:DistanceMeters", namespaces=NS) == "1000.0"


def test_generated_lap_for_polar_samples_without_laps(tmp_path: Path) -> None:
    source = tmp_path / "activity-no-laps.json"
    source.write_text(
        '{"start-time":"2025-01-01T00:00:00Z","duration":"PT10S",'
        '"samples":[{"data":[{"timestamp":"2025-01-01T00:00:01Z"}]}]}',
        encoding="utf-8",
    )
    activity = PolarImporter().import_activity(source)
    assert len(activity.laps) == 1
    assert len(etree.fromstring(TCXBuilder().build(activity)).findall(".//tcx:Lap", NS)) == 1


def test_invalid_tcx_reports_useful_error() -> None:
    with pytest.raises(TCXValidationError, match="root"):
        TCXValidator().validate_xml(b"<wrong />")


def test_convert_cli_file_and_no_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "converted.tcx"
    runner = CliRunner()
    assert runner.invoke(app, ["convert", str(SAMPLE), "--output", str(output)]).exit_code == 0
    assert output.exists()
    assert runner.invoke(app, ["convert", str(SAMPLE), "--output", str(output)]).exit_code == 1


def test_convert_directory_continues_after_failure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "activity-good.json").write_bytes(SAMPLE.read_bytes())
    (source / "activity-bad.json").write_text("{", encoding="utf-8")
    destination = tmp_path / "output"
    result = CliRunner().invoke(app, ["convert", str(source), "--output", str(destination)])
    assert result.exit_code == 1
    assert "Converted 1" in result.output
    assert "failures 1" in result.output
    assert len(list(destination.glob("*.tcx"))) == 1


def test_directory_names_remain_unique_for_duplicate_basenames(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "one").mkdir(parents=True)
    (source / "two").mkdir()
    for folder in ("one", "two"):
        (source / folder / "activity-same.json").write_bytes(SAMPLE.read_bytes())
    destination = tmp_path / "output"
    result = CliRunner().invoke(app, ["convert", str(source), "--output", str(destination)])
    assert result.exit_code == 0
    assert len(list(destination.glob("*.tcx"))) == 2
