"""TCX-specific pre-serialization validation."""

from core.errors import ExportError
from domain import Activity


class TCXValidationError(ExportError):
    """The activity cannot be represented by the current TCX serializer."""


class TCXValidator:
    """Checks the minimum data required to build a stable TCX document."""

    def validate(self, activity: Activity) -> None:
        """Raise a format-specific error only at the export boundary."""
        if not activity.laps:
            raise TCXValidationError("TCX export requires at least one lap.")
