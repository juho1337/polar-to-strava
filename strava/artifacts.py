"""Artifact proof value only; snapshot creation belongs to SPEC-001 WP4."""

from dataclasses import dataclass, field
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    stream: BinaryIO = field(repr=False, compare=False)
    filename: str
    sha256: str
    size_bytes: int
    stable_activity_id: str
    revision: int
