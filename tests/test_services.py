from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from domain import Activity, ActivitySource, Sport
from services import ActivityValidator, ConversionService, ValidationSeverity


class StubImporter:
    def __init__(self, paths: tuple[Path, ...], activity: Activity) -> None:
        self._paths = paths
        self._activity = activity

    def scan(self, directory: Path) -> Iterable[Path]:
        return iter(self._paths)

    def import_activity(self, path: Path) -> Activity:
        return self._activity


def activity() -> Activity:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return Activity(
        id="test",
        source=ActivitySource.MANUAL,
        sport=Sport.WALKING,
        started_at=now,
        ended_at=now,
    )


def test_conversion_service_uses_importer_port_and_returns_domain_objects(tmp_path: Path) -> None:
    source = tmp_path / "activity-1.json"
    service = ConversionService(StubImporter((source,), activity()), ActivityValidator())

    result = service.import_folder(tmp_path)

    assert result.scanned_paths == (source,)
    assert result.activities[0].id == "test"
    assert result.issues[0].severity is ValidationSeverity.WARNING


def test_validator_returns_issue_instead_of_throwing_for_broken_runtime_object() -> None:
    result = ActivityValidator().validate(object())  # type: ignore[arg-type]
    assert not result.is_valid
    assert result.issues[0].code == "validation.unexpected_error"
