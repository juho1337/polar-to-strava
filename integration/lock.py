"""Exclusive per-workspace writer lock shared by the CLI and the desktop engine.

An OS-level advisory lock on ``<workspace>/.migration.lock`` is held for the lifetime of a
writing command. The operating system releases it when the process dies, so crashes never
leave a stale lock. The file holds only the holder's start time and command name.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from core.errors import PolarToStravaError

LOCK_FILENAME = ".migration.lock"


class WorkspaceLockedError(PolarToStravaError):
    """Another writer holds the workspace."""

    def __init__(self, holder_started_at: str | None = None) -> None:
        super().__init__("Another process is writing this workspace; only one writer is allowed.")
        self.holder_started_at = holder_started_at


def _try_lock(handle: int) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(handle, 0, os.SEEK_SET)
            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined,unused-ignore]
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(handle, 0, os.SEEK_SET)
            msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined,unused-ignore]
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass


class WorkspaceLock:
    """Context manager acquiring the writer lock or raising :class:`WorkspaceLockedError`."""

    def __init__(self, workspace: Path, command: str = "") -> None:
        self.path = workspace / LOCK_FILENAME
        self.command = command
        self._handle: int | None = None

    def acquire(self) -> bool:
        """Try to take the lock without raising; returns whether this instance holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
        if not _try_lock(handle):
            os.close(handle)
            return False
        self._handle = handle
        # Writing after locking: the first byte stays the locked region on Windows.
        os.lseek(handle, 1, os.SEEK_SET)
        os.ftruncate(handle, 1)
        info = json.dumps(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "command": self.command,
                "pid": os.getpid(),
            }
        )
        os.write(handle, info.encode())
        return True

    def holder_started_at(self) -> str | None:
        try:
            with self.path.open("rb") as stream:
                stream.seek(1)  # byte 0 is the locked region on Windows
                data = stream.read()
            started = json.loads(data.decode()).get("started_at")
            return started if isinstance(started, str) else None
        except (OSError, ValueError, AttributeError):
            return None

    def release(self) -> None:
        if self._handle is not None:
            _unlock(self._handle)
            os.close(self._handle)
            self._handle = None

    def __enter__(self) -> WorkspaceLock:
        if not self.acquire():
            raise WorkspaceLockedError(self.holder_started_at())
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
