"""Reusable, exception-free validation of domain activities."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from domain import Activity


class ValidationSeverity(StrEnum):
    """Impact level of a validation issue."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A machine-readable validation finding."""

    code: str
    message: str
    severity: ValidationSeverity
    field: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validation outcome that never exposes exceptions to callers."""

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        """True when no error-severity issue was found."""
        return all(issue.severity is not ValidationSeverity.ERROR for issue in self.issues)


class Validator(Protocol):
    """A reusable validation component for importers and exporters."""

    def validate(self, activity: Activity) -> ValidationResult: ...


class ActivityValidator:
    """Checks cross-format constraints not enforced by immutable domain models."""

    def validate(self, activity: Activity) -> ValidationResult:
        """Validate safely; malformed runtime inputs become structured issues."""
        try:
            issues: list[ValidationIssue] = []
            if not activity.laps:
                issues.append(
                    ValidationIssue(
                        code="activity.no_laps",
                        message="Activity has no laps or trackpoints.",
                        severity=ValidationSeverity.WARNING,
                        field="laps",
                    )
                )
            if activity.distance_m is not None and activity.distance_m == 0:
                issues.append(
                    ValidationIssue(
                        code="activity.zero_distance",
                        message="Activity reports zero distance.",
                        severity=ValidationSeverity.WARNING,
                        field="distance_m",
                    )
                )
            return ValidationResult(tuple(issues))
        except Exception as error:
            return ValidationResult(
                (
                    ValidationIssue(
                        code="validation.unexpected_error",
                        message=f"Validation could not complete: {error}",
                        severity=ValidationSeverity.ERROR,
                    ),
                )
            )
