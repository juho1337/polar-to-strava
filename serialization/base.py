"""Generic in-memory serialization ports."""

from pathlib import Path
from typing import Protocol


class Serializer[InputT, OutputT](Protocol):
    """Transforms one value into another representation."""

    def serialize(self, value: InputT) -> OutputT: ...


class DocumentBuilder[InputT](Protocol):
    """Builds an in-memory document without knowing its destination."""

    def build(self, value: InputT) -> bytes: ...


class DocumentWriter(Protocol):
    """Persists bytes without knowing their domain source."""

    def write(self, content: bytes, destination: Path) -> Path: ...
