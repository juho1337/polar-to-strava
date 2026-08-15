"""Explicit errors raised while importing Polar Flow exports."""

from pathlib import Path


class PolarImportError(ValueError):
    """Base error for an activity that cannot be imported."""

    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


class PolarLoadError(PolarImportError):
    """The source file cannot be opened or decoded as a JSON object."""


class PolarValidationError(PolarImportError):
    """The source file is JSON but cannot form a valid domain activity."""
