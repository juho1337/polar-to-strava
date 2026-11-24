"""Contract 1.0: JSON Lines envelopes, structured errors and secret redaction (SPEC-004)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import IO, Any

CONTRACT_VERSION = "1.1"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_INVALID_REQUEST = 2
EXIT_LOCKED = 3
EXIT_CANCELLED = 130

REDACTED = "[redacted]"
# Keys whose values are never emitted, wherever they appear in a payload. Error ``code`` fields
# and OAuth nonces are not secrets; request secrets are additionally scrubbed by value.
SECRET_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_secret",
        "authorization_code",
        "authorization",
        "token",
        "tokens",
        "password",
        "secret",
    }
)


class EngineError(Exception):
    """A structured, user-presentable failure. ``details`` must never contain secrets."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        exit_code: int = EXIT_FAILURE,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.details = details or {}
        self.retryable = retryable
        self.exit_code = exit_code

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
            "details": self.details,
            "retryable": self.retryable,
        }


def redact(value: Any) -> Any:
    """Recursively replace values stored under secret-bearing keys."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if key in SECRET_KEYS else redact(item)) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class Emitter:
    """Serializes envelopes as one JSON object per line; thread-safe and flushed per line."""

    def __init__(
        self,
        stream: IO[str],
        command: str,
        run_id: str,
        secrets: Iterable[str] = (),
    ) -> None:
        self.stream = stream
        self.command = command
        self.run_id = run_id
        self._secrets = [value for value in secrets if len(value) >= 4]
        self._seq = 0
        self._lock = threading.Lock()

    def register_secret(self, value: str | None) -> None:
        if value and len(value) >= 4:
            self._secrets.append(value)

    def _write(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            envelope = {
                "contract": CONTRACT_VERSION,
                "type": kind,
                "seq": self._seq,
                "time": utc_now(),
                "command": self.command,
                "run_id": self.run_id,
                **redact(payload),
            }
            line = json.dumps(envelope, ensure_ascii=False, default=str)
            for secret in self._secrets:
                line = line.replace(secret, REDACTED)
            self.stream.write(line + "\n")
            self.stream.flush()

    def event(self, name: str, **payload: Any) -> None:
        self._write("event", {"event": name, **payload})

    def result(self, status: str, summary: dict[str, Any] | None = None, **extra: Any) -> None:
        self._write("result", {"status": status, "summary": summary or {}, **extra})

    def error(self, error: EngineError) -> None:
        self._write("error", {"error": error.as_dict()})
