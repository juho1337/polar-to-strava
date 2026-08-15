"""Polar implementation of the application importer port."""

from collections.abc import Iterable
from pathlib import Path

from domain import Activity
from polar.parser import parse_activity
from polar.scanner import iter_activity_files


class PolarImporter:
    """Imports Polar Flow JSON exports into domain activities."""

    def scan(self, directory: Path) -> Iterable[Path]:
        """Yield Polar activity files recursively."""
        return iter_activity_files(directory)

    def import_activity(self, path: Path) -> Activity:
        """Parse a single Polar export."""
        return parse_activity(path)
