"""Ports reserved for the future TCX exporter implementation."""

from pathlib import Path
from typing import Protocol

from domain import Activity


class TCXBuilder(Protocol):
    """Builds TCX bytes in memory from a domain activity."""

    def build(self, activity: Activity) -> bytes: ...


class TCXWriter(Protocol):
    """Persists TCX bytes; storage is deliberately outside the builder."""

    def write(self, content: bytes, destination: Path) -> None: ...
