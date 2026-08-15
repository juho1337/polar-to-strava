"""Application-wide exception hierarchy for user-facing failures."""


class PolarToStravaError(Exception):
    """Base exception that the CLI can safely present to a user."""


class ConfigurationError(PolarToStravaError):
    """Configuration could not be loaded or validated."""


class ImportError(PolarToStravaError):
    """An activity source could not be read or imported."""


class ValidationError(PolarToStravaError):
    """An activity failed application-level validation."""


class ExportError(PolarToStravaError):
    """An activity could not be exported."""


class UploadError(PolarToStravaError):
    """An activity could not be uploaded."""
