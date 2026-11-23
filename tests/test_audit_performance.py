"""SPEC-003 regression coverage: optimized audit paths keep every integrity guarantee."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

import services.audit as audit_module
import services.audit_cache as cache_module
from core.atomic import atomic_write_bytes, remove_stale_temporaries
from core.errors import AuditCancelledError
from fit import FITWriter
from polar import PolarImporter
from services.audit import MigrationAudit, _process_one, default_workers
from services.audit_cache import CACHE_FILENAME
from services.migration import source_sha256
from services.service_models import AuditPhase, AuditProgress
from services.validation import ActivityValidator

SAMPLE = Path(__file__).parent / "samples" / "training-session-sanitized.json"
VOLATILE = ("elapsed_seconds", "activities_per_second")
OUTPUTS = (
    "migration-manifest.json",
    "migration-manifest.csv",
    "migration-config.template.json",
    "migration-audit.csv",
)


def make_source(root: Path, count: int = 3) -> Path:
    source = root / "source"
    source.mkdir(parents=True)
    for index in range(count):
        payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
        payload["name"] = f"variant {index}"  # distinct content, hence distinct identity
        (source / f"training-session-2025-01-0{index + 1}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    (source / "training-session-broken.json").write_text("{bad", encoding="utf-8")
    return source


def run(source: Path, output: Path, **options: Any) -> dict[str, Any]:
    return MigrationAudit(PolarImporter(), ActivityValidator()).run(source, output, **options)


def semantic(report: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(report, default=dict))
    for key in VOLATILE:
        clone["summary"].pop(key)
    for key in ("source_root", "output_root"):
        clone.pop(key)
    for row in clone["activities"]:
        row.pop("output_fit")
    return clone  # type: ignore[no-any-return]


def fingerprint(output: Path, portable: bool = False) -> dict[str, str]:
    """Hashes of deterministic outputs; ``portable`` drops files embedding the output path."""
    names = OUTPUTS[:3] if portable else OUTPUTS
    result = {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in names}
    for fit in sorted((output / "fits").glob("*.fit")):
        result[fit.name] = hashlib.sha256(fit.read_bytes()).hexdigest()
    return result


def test_worker_count_does_not_change_outputs_or_order(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    one = run(source, tmp_path / "one", workers=1)
    two = run(source, tmp_path / "two", workers=2)
    assert semantic(one) == semantic(two)
    assert fingerprint(tmp_path / "one", True) == fingerprint(tmp_path / "two", True)
    assert [row["source"] for row in one["activities"]] == sorted(
        (row["source"] for row in one["activities"]), key=lambda name: name.lower()
    )


def test_generated_fit_is_verified_once_and_hash_matches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "training-session-one.json"
    shutil.copyfile(SAMPLE, source)
    fits = tmp_path / "fits"

    def forbidden(*_: object) -> None:
        raise AssertionError("generated FIT must not be decoded a second time")

    monkeypatch.setattr(audit_module, "_decode_fit", forbidden)
    row = _process_one(
        source,
        tmp_path,
        fits,
        False,
        PolarImporter(),
        ActivityValidator(),
        source_sha256(source),
        None,
    )
    assert row["status"] == "converted" and row["fit_valid"] is True
    written = Path(row["output_fit"])
    assert row["fit_sha256"] == hashlib.sha256(written.read_bytes()).hexdigest()
    assert row["fit_bytes"] == written.stat().st_size


def test_file_differing_from_verified_bytes_fails_as_fit_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "training-session-one.json"
    shutil.copyfile(SAMPLE, source)

    def corrupting(self: FITWriter, content: bytes, destination: Path) -> Path:
        return atomic_write_bytes(destination, content[:-1] + bytes([content[-1] ^ 1]))

    monkeypatch.setattr(FITWriter, "write", corrupting)
    row = _process_one(
        source,
        tmp_path,
        tmp_path / "fits",
        False,
        PolarImporter(),
        ActivityValidator(),
        source_sha256(source),
        None,
    )
    assert row["status"] == "failed" and row["failure_stage"] == "fit_write"
    assert not row.get("fit_valid")


def test_existing_fit_is_still_fully_validated_without_cache(tmp_path: Path) -> None:
    source = make_source(tmp_path, 1)
    output = tmp_path / "out"
    run(source, output)
    fit = next((output / "fits").glob("*.fit"))
    fit.write_bytes(fit.read_bytes()[:-3])  # truncated: CRC/length no longer valid
    report = run(source, output)
    failed = [row for row in report["activities"] if row["status"] == "failed"]
    assert any(row["failure_stage"] == "fit_decode_validation" for row in failed)


def test_incremental_warm_run_matches_full_warm_run_and_skips_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(tmp_path)
    output = tmp_path / "out"
    run(source, output, incremental=True)  # cold run populates the cache
    assert (output / CACHE_FILENAME).exists()
    events: list[AuditProgress] = []
    cached = run(source, output, incremental=True, progress=events.append)
    final = [e for e in events if e.phase is AuditPhase.COMPLETE][-1]
    assert final.cache_hits == 3 and final.fit_generated == 0
    before = fingerprint(output)
    full = run(source, output)  # full verification of the same workspace
    assert semantic(cached) == semantic(full)
    assert fingerprint(output) == before


def test_cache_misses_when_fit_or_source_or_versions_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_source(tmp_path, 2)
    output = tmp_path / "out"
    run(source, output, incremental=True)

    def hits() -> int:
        events: list[AuditProgress] = []
        run(source, output, incremental=True, progress=events.append)
        return [e for e in events if e.phase is AuditPhase.COMPLETE][-1].cache_hits

    assert hits() == 2
    # Altered FIT bytes: no hit for that artifact, and full validation rejects it.
    fit = sorted((output / "fits").glob("*.fit"))[0]
    original = fit.read_bytes()
    fit.write_bytes(original + b"\0")
    assert hits() == 1
    fit.write_bytes(original)
    run(source, output, incremental=True)
    # Changed source content changes the identity and therefore misses.
    changed = next(source.glob("training-session-2025-01-01.json"))
    payload = json.loads(changed.read_text(encoding="utf-8"))
    payload["name"] = "edited"
    changed.write_text(json.dumps(payload), encoding="utf-8")
    assert hits() == 1
    # A different conversion-code fingerprint invalidates every entry.
    monkeypatch.setattr(cache_module, "code_fingerprint", lambda: "other-code")
    assert hits() == 0
    # Unknown cache version or corrupt file is a clean miss, never an error.
    (output / CACHE_FILENAME).write_text("not json", encoding="utf-8")
    monkeypatch.undo()
    assert hits() == 0


def test_incremental_is_bypassed_by_overwrite(tmp_path: Path) -> None:
    source = make_source(tmp_path, 1)
    output = tmp_path / "out"
    run(source, output, incremental=True)
    events: list[AuditProgress] = []
    run(source, output, incremental=True, overwrite=True, progress=events.append)
    final = [e for e in events if e.phase is AuditPhase.COMPLETE][-1]
    assert final.cache_hits == 0 and final.fit_generated == 1


def test_cancellation_writes_no_reports_and_leaves_no_temporaries(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    output = tmp_path / "out"
    with pytest.raises(AuditCancelledError):
        run(source, output, workers=1, should_cancel=lambda: True)
    assert not (output / "migration-manifest.json").exists()
    assert not (output / "migration-audit.json").exists()
    assert not list(output.rglob("*.tmp"))


def test_cancellation_midway_keeps_previous_manifest_intact(tmp_path: Path) -> None:
    source = make_source(tmp_path)
    output = tmp_path / "out"
    run(source, output)
    manifest = (output / "migration-manifest.json").read_bytes()
    calls = iter([False, False, True, True, True, True, True, True])
    with pytest.raises(AuditCancelledError):
        run(source, output, workers=2, should_cancel=lambda: next(calls, True))
    assert (output / "migration-manifest.json").read_bytes() == manifest


def test_atomic_write_preserves_old_content_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file.bin"
    target.write_bytes(b"old")

    def broken(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("core.atomic.os.replace", broken)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.tmp"))


def test_stale_temporaries_are_removed(tmp_path: Path) -> None:
    stale = tmp_path / ".migration-manifest.json.123.tmp"
    stale.write_bytes(b"partial")
    keep = tmp_path / "migration-manifest.json"
    keep.write_bytes(b"{}")
    remove_stale_temporaries(tmp_path)
    assert not stale.exists() and keep.exists()


def test_default_workers_are_cpu_and_memory_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    monkeypatch.setattr(audit_module, "_available_memory", lambda: None)
    assert default_workers(100) == 16
    assert default_workers(3) == 3
    assert default_workers(0) == 1
    monkeypatch.setattr(
        audit_module, "_available_memory", lambda: 3 * audit_module.WORKER_MEMORY_ESTIMATE
    )
    assert default_workers(100) == 3
    monkeypatch.setattr(audit_module, "_available_memory", lambda: 1)
    assert default_workers(100) == 1
