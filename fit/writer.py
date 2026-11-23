"""Filesystem boundary for FIT bytes."""

from pathlib import Path

from core.atomic import atomic_write_bytes


class FITWriter:
    """Persist an already validated FIT document atomically."""

    def write(self, content: bytes, destination: Path) -> Path:
        return atomic_write_bytes(destination, content)
