"""Immutable values returned by application services."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
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


class AuditPhase(StrEnum):
    """Observable boundaries in the migration audit pipeline."""

    DISCOVERY_STARTED = "discovery_started"
    DISCOVERY_COMPLETED = "discovery_completed"
    PREPARING_ACTIVITIES = "preparing_activities"
    PROCESSING_ACTIVITIES = "processing_activities"
    GENERATING_REPORTS = "generating_reports"
    WRITING_MANIFEST = "writing_manifest"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class AuditProgress:
    """Framework-neutral snapshot emitted by the audit service."""

    phase: AuditPhase
    completed: int = 0
    total: int = 0
    fit_valid: int = 0
    warnings: int = 0
    failed: int = 0
    skipped_existing: int = 0


AuditProgressCallback = Callable[[AuditProgress], None]
