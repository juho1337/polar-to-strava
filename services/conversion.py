"""Application orchestration for importing and validating activities."""

from hashlib import sha256
from pathlib import Path

from core.contracts import ActivityImporter
from core.errors import ExportError, ImportError
from domain import Activity
from fit import FITBuilder, FITWriter
from services.service_models import ConversionResult
from services.validation import ValidationIssue, ValidationSeverity, Validator
from tcx import TCXBuilder, TCXWriter


def output_name(path: Path, root: Path, format: str) -> str:
    """Retain the session filename and disambiguate duplicate relative paths."""
    relative = path.relative_to(root).as_posix().lower()
    suffix = sha256(relative.encode()).hexdigest()[:10]
    return f"{path.stem}-{suffix}.{format}"


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

    def import_one(self, path: Path) -> tuple[Activity, tuple[ValidationIssue, ...]]:
        """Import and validate one file, retaining warnings."""
        activity = self._importer.import_activity(path)
        result = self._validator.validate(activity)
        if not result.is_valid:
            raise ImportError("; ".join(issue.message for issue in result.issues))
        return activity, result.issues

    def convert_file(
        self, source: Path, destination: Path, overwrite: bool = False, format: str = "tcx"
    ) -> tuple[ValidationIssue, ...]:
        """Convert one Polar JSON file to a validated TCX document."""
        if destination.exists() and not overwrite:
            raise ExportError(f"Output already exists: {destination}")
        activity, issues = self.import_one(source)
        if format not in ("tcx", "fit"):
            raise ExportError(f"Unsupported export format: {format}")
        if format == "tcx":
            TCXWriter().write(TCXBuilder().build(activity), destination)
        else:
            FITWriter().write(FITBuilder().build(activity), destination)
        return issues

    def convert_folder(
        self, source: Path, destination: Path, overwrite: bool = False, format: str = "tcx"
    ) -> tuple[tuple[Path, ...], tuple[ValidationIssue, ...]]:
        """Convert discovered activities independently, retaining failures."""
        outputs: list[Path] = []
        issues: list[ValidationIssue] = []
        for path in self.scan_folder(source):
            if format not in ("tcx", "fit"):
                raise ExportError(f"Unsupported export format: {format}")
            target = destination / output_name(path, source, format)
            if target.exists() and not overwrite:
                issues.append(
                    ValidationIssue(
                        code="conversion.skipped_existing",
                        message=f"Existing output left untouched: {target}",
                        severity=ValidationSeverity.WARNING,
                        field=str(path),
                    )
                )
                continue
            try:
                issues.extend(self.convert_file(path, target, overwrite, format))
                outputs.append(target)
            except Exception as error:
                issues.append(
                    ValidationIssue(
                        code="conversion.failed",
                        message=f"{path}: {error}",
                        severity=ValidationSeverity.ERROR,
                        field=str(path),
                    )
                )
        return tuple(outputs), tuple(issues)
