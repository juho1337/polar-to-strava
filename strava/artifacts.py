"""Private, bounded-memory snapshots of the exact bytes authorized for submission."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, cast

from strava.models import ManifestActivity

if TYPE_CHECKING:
    from strava.recovery import Code


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    stream: BinaryIO = field(repr=False, compare=False)
    filename: str
    sha256: str
    size_bytes: int
    stable_activity_id: str
    revision: int


class ArtifactFailure(Exception):
    def __init__(self, code: Code) -> None:
        super().__init__(code.value)
        self.code = code


@contextmanager
def verified_snapshot(
    workspace: Path, activity: ManifestActivity, revision: int
) -> Iterator[VerifiedArtifact]:
    """Hash while copying, then retain the same open, rewound stream until transport ends."""
    from strava.recovery import Code

    if not activity.eligible or not activity.fit.valid or not activity.fit.relative_path:
        raise ArtifactFailure(Code.INVALID_ARTIFACT)
    with ExitStack() as resources:
        try:
            root = workspace.resolve()
            source = (root / activity.fit.relative_path).resolve()
            if not source.is_relative_to(root):
                raise ArtifactFailure(Code.INVALID_ARTIFACT)
            if not source.is_file():
                raise ArtifactFailure(Code.MISSING_FIT)
            # The OS temporary directory is deliberately outside the migration workspace.
            temporary_root = Path(tempfile.gettempdir()).resolve()
            if temporary_root.is_relative_to(root):
                raise ArtifactFailure(Code.INVALID_ARTIFACT)
            snapshot = resources.enter_context(
                tempfile.TemporaryFile(mode="w+b", dir=temporary_root)
            )
            with source.open("rb") as original:
                digest = hashlib.sha256()
                size = 0
                while chunk := original.read(1024 * 1024):
                    snapshot.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                if digest.hexdigest() != activity.fit.sha256:
                    raise ArtifactFailure(Code.FIT_HASH_MISMATCH)
                snapshot.seek(0)
                artifact = VerifiedArtifact(
                    cast(BinaryIO, snapshot),
                    source.name,
                    digest.hexdigest(),
                    size,
                    activity.stable_activity_id,
                    revision,
                )
        except FileNotFoundError:
            raise ArtifactFailure(Code.MISSING_FIT) from None
        except OSError:
            raise ArtifactFailure(Code.INVALID_ARTIFACT) from None
        yield artifact
