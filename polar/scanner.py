"""Discovery of Polar activity JSON exports."""

from pathlib import Path


def scan_activities(export_directory: Path | str) -> list[Path]:
    """Recursively return deterministic paths matching ``activity-*.json``."""
    root = Path(export_directory)
    if not root.is_dir():
        raise NotADirectoryError(f"Polar export directory does not exist: {root}")
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.name.lower().startswith("activity-") and path.suffix.lower() == ".json"),
        key=lambda path: (path.name.lower(), str(path).lower()),
    )
