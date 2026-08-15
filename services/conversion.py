"""Application orchestration for importing and validating activities."""

from pathlib import Path

from core.contracts import ActivityImporter
from core.errors import ImportError
from services.service_models import ConversionResult
from services.validation import ValidationIssue, ValidationSeverity, Validator


class ConversionService:
    """Scans, imports, and validates activities without exporting or uploading."""

    def __init__(self, importer: ActivityImporter, validator: Validator) -> None:
        self._importer = importer
        self._validator = validator

    def scan_folder(self, directory: Path) -> tuple[Path, ...]:
        """Discover source files through the importer port."""
        try:
            return tuple(self._importer.scan(directory))
        except OSError as error:
            raise ImportError(f"Could not scan {directory}: {error}") from error

    def import_folder(self, directory: Path) -> ConversionResult:
        """Import valid activities and retain structured errors for bad files."""
        paths = self.scan_folder(directory)
        activities = []
        issues: list[ValidationIssue] = []
        for path in paths:
            try:
                activity = self._importer.import_activity(path)
            except ImportError as error:
                issues.append(
                    ValidationIssue(
                        code="import.failed",
                        message=str(error),
                        severity=ValidationSeverity.ERROR,
                        field=str(path),
                    )
                )
                continue
            validation = self._validator.validate(activity)
            issues.extend(validation.issues)
            if validation.is_valid:
                activities.append(activity)
        return ConversionResult(tuple(activities), tuple(issues), paths)
