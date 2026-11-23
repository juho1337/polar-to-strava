"""Crash-safe file replacement: write a sibling temporary file, then ``os.replace``."""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path


def atomic_write_bytes(destination: Path, content: bytes) -> Path:
    """Write ``content`` so ``destination`` is either the old or the complete new file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary, destination)
                break
            except PermissionError:  # transient sharing violation on Windows
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
    return destination


def atomic_write_text(destination: Path, text: str, *, newline: str | None = None) -> Path:
    """Atomic UTF-8 text write; ``newline`` follows ``open()`` semantics (None = platform)."""
    if newline is None:
        data = text.replace("\n", os.linesep).encode("utf-8")
    else:
        data = text.replace("\n", newline).encode("utf-8") if newline else text.encode("utf-8")
    return atomic_write_bytes(destination, data)


def remove_stale_temporaries(directory: Path) -> None:
    """Delete leftover ``.<name>.<pid>.tmp`` files from an interrupted run."""
    for path in directory.glob(".*.tmp"):
        with contextlib.suppress(OSError):
            path.unlink()
