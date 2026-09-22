"""Filesystem boundary for FIT bytes."""

from pathlib import Path


class FITWriter:
    """Persist an already validated FIT document."""

    def write(self, content: bytes, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination
