"""Versioned, hash-keyed reuse of previously verified audit rows (opt-in, SPEC-003 AC-05).

Validation of a FIT artifact is a pure function of the source bytes, the timezone override,
the conversion/validation code and the FIT bytes. An entry is therefore keyed by the
relative source path, source SHA-256 and override, stored under a header holding the cache
format version, a fingerprint of the relevant code and library versions, and the importer and
validator identities. A hit additionally requires the FIT file to exist with the recorded
SHA-256 and size. Anything else (missing, corrupt, other version, other code) is a miss.
"""

from __future__ import annotations

import hashlib
import json
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from core.atomic import atomic_write_text

AUDIT_CACHE_VERSION = 1
CACHE_FILENAME = ".audit-cache.json"
_ROOT = Path(__file__).resolve().parent.parent
# Packages whose source defines parsing, domain semantics, FIT output or classification.
_CODE_PATTERNS = ("polar/*.py", "domain/*.py", "fit/*.py", "services/*.py", "core/errors.py")


def code_fingerprint() -> str:
    """Hash of the conversion/validation sources and library versions."""
    digest = hashlib.sha256()
    digest.update(f"cache={AUDIT_CACHE_VERSION};py={sys.version_info[:2]}".encode())
    for package in ("fit-tool", "pydantic"):
        try:
            digest.update(f";{package}={metadata.version(package)}".encode())
        except metadata.PackageNotFoundError:
            digest.update(f";{package}=?".encode())
    for pattern in _CODE_PATTERNS:
        for path in sorted(_ROOT.glob(pattern)):
            digest.update(path.relative_to(_ROOT).as_posix().encode() + b"\0")
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def entry_key(relative: str, source_sha256: str, timezone_offset: str | None) -> str:
    return f"{relative}|{source_sha256}|{timezone_offset or ''}"


class AuditCache:
    """In-memory view of ``.audit-cache.json`` for one importer/validator pair."""

    def __init__(self, directory: Path, importer: object, validator: object) -> None:
        self.path = directory / CACHE_FILENAME
        self.header = {
            "cache_version": AUDIT_CACHE_VERSION,
            "code": code_fingerprint(),
            "importer": f"{type(importer).__module__}.{type(importer).__qualname__}",
            "validator": f"{type(validator).__module__}.{type(validator).__qualname__}",
        }
        self.entries: dict[str, dict[str, Any]] = {}
        self.next_entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("header") == self.header and isinstance(payload.get("entries"), dict):
                self.entries = payload["entries"]
        except (OSError, UnicodeError, ValueError, AttributeError):
            self.entries = {}

    def lookup(self, key: str, fit_path: Path) -> dict[str, Any] | None:
        """Return a copy of the cached row when its FIT artifact is still byte-identical."""
        entry = self.entries.get(key)
        if not isinstance(entry, dict) or not entry.get("fit_valid") or not entry.get("fit_sha256"):
            return None
        try:
            if fit_path.stat().st_size != entry.get("fit_bytes"):
                return None
            digest = hashlib.sha256(fit_path.read_bytes()).hexdigest()
        except OSError:
            return None
        if digest != entry["fit_sha256"]:
            return None
        copy: dict[str, Any] = json.loads(json.dumps(entry))  # independent copy
        return copy

    def remember(self, key: str, row: dict[str, Any]) -> None:
        """Record a verified pre-classification row (copied via JSON)."""
        if row.get("fit_valid") and row.get("fit_sha256"):
            self.next_entries[key] = json.loads(json.dumps(row, default=dict))

    def save(self) -> None:
        """Atomically persist only entries seen in this run (drops removed sources)."""
        document = {"header": self.header, "entries": self.next_entries}
        atomic_write_text(self.path, json.dumps(document), newline="")
