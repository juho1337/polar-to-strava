"""Filesystem boundary for already-built TCX bytes."""

from pathlib import Path


class TCXWriter:
    """Writes bytes to disk without importing or inspecting domain activities."""

    def write(self, content: bytes, destination: Path) -> Path:
        """Persist byte content and return its destination."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination
