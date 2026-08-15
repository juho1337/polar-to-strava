"""Discovery of Polar activity JSON exports."""

from collections.abc import Iterator
from pathlib import Path


def iter_activity_files(export_directory: Path | str) -> Iterator[Path]:
    """Yield activity JSON files lazily in deterministic order."""
    root = Path(export_directory)
    if not root.is_dir():
        raise NotADirectoryError(f"Polar export directory does not exist: {root}")
    paths = (
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name.lower().startswith("activity-")
        and path.suffix.lower() == ".json"
    )
    yield from sorted(paths, key=lambda path: (path.name.lower(), str(path).lower()))


def scan_activities(export_directory: Path | str) -> list[Path]:
    """Recursively return deterministic paths matching ``activity-*.json``."""
    return list(iter_activity_files(export_directory))
