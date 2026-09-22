"""Provider-neutral ports used by application services."""

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from domain import Activity


class ActivityImporter(Protocol):
    """Reads source files and returns provider-neutral activities."""

    def scan(self, directory: Path) -> Iterable[Path]: ...

    def import_activity(self, path: Path) -> Activity: ...


class ActivityExporter(Protocol):
    """Builds an in-memory representation; it never writes files itself."""

    def build(self, activity: Activity) -> bytes: ...


class ActivityUploader(Protocol):
    """Uploads a prepared activity representation to a remote provider."""

    def upload(self, activity: Activity, payload: bytes) -> str: ...
