"""Immutable values returned by application services."""

from dataclasses import dataclass
from pathlib import Path

from domain import Activity
from services.validation import ValidationIssue


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Successful activities and non-fatal import or validation issues."""

    activities: tuple[Activity, ...]
    issues: tuple[ValidationIssue, ...]
    scanned_paths: tuple[Path, ...]

    @property
    def is_successful(self) -> bool:
        """Whether every scanned activity was imported and validated."""
        return not self.issues
