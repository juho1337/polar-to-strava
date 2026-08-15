from pathlib import Path

import pytest

from polar.scanner import iter_activity_files, scan_activities


def test_scan_activities_recursively_and_sorts(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "activity-z.json").write_text("{}", encoding="utf-8")
    (tmp_path / "nested" / "ACTIVITY-a.JSON").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")
    assert [path.name for path in scan_activities(tmp_path)] == [
        "ACTIVITY-a.JSON",
        "activity-z.json",
    ]


def test_scan_activities_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        scan_activities(tmp_path / "missing")


def test_iterator_is_lazy_and_handles_large_exports(tmp_path: Path) -> None:
    for index in range(10_000):
        (tmp_path / f"activity-{index:05}.json").touch()
    iterator = iter_activity_files(tmp_path)
    assert next(iterator).name == "activity-00000.json"
