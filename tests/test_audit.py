"""Synthetic end-to-end checks for the migration audit."""

import hashlib
import json
import shutil
from collections.abc import Iterable
from pathlib import Path

import pytest

from core.errors import ConfigurationError
from domain import Activity
from polar import PolarImporter
from services.audit import MigrationAudit
from services.conversion import output_name
from services.migration import source_sha256, stable_activity_id
from services.service_models import AuditPhase, AuditProgress
from services.validation import ActivityValidator

SAMPLE = Path(__file__).parent / "samples" / "training-session-sanitized.json"


class LossyImporter:
    """A controlled importer defect used to check audit loss detection."""

    def scan(self, directory: Path) -> Iterable[Path]:
        return PolarImporter().scan(directory)

    def import_activity(self, path: Path) -> Activity:
        activity = PolarImporter().import_activity(path)
        lap = activity.laps[0]
        first = lap.trackpoints[0].model_copy(update={"heart_rate": None})
        return activity.model_copy(
            update={
                "laps": (lap.model_copy(update={"trackpoints": (first, *lap.trackpoints[1:])}),)
            }
        )


def test_audit_accounts_for_every_source_and_reruns(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    first = source / "training-session-2025-01-01-one.json"
    second = source / "nested" / "training-session-2025-01-01-two.json"
    broken = source / "training-session-broken.json"
    shutil.copyfile(SAMPLE, first)
    shutil.copyfile(SAMPLE, second)
    broken.write_text("{bad", encoding="utf-8")
    (source / "activity-2025-01-01.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "audit"
    audit = MigrationAudit(PolarImporter(), ActivityValidator())
    events: list[AuditProgress] = []

    report = audit.run(source, output, progress=events.append)
    summary = report["summary"]
    assert summary["discovered"] == 3
    assert summary["parsed"] == 2
    assert summary["converted"] == summary["validated"] == 2
    assert summary["failed"] == 1
    assert summary["failure_stages"] == {"import": 1}
    assert sum(group["count"] for group in summary["failure_categories"].values()) == 1
    assert len(report["duplicate_candidates"]) == 1
    assert report["sports"]["RUNNING"]["activities"] == 2
    assert report["sensors"]["hr"]["fit"]["samples"] == 4
    assert report["sensors"]["gps"]["fit"]["samples"] == 4
    assert {row["status"] for row in report["activities"]} == {"converted", "failed"}
    assert all((output / f"migration-audit.{ext}").exists() for ext in ("json", "csv", "md"))
    assert len(list((output / "fits").glob("*.fit"))) == 2
    assert output_name(first, source, "fit") != output_name(second, source, "fit")
    manifest = json.loads((output / "migration-manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 1
    assert manifest["summary"]["total"] == 3
    assert sum(manifest["summary"]["status_counts"].values()) == 3
    assert len(manifest["activities"]) == 3
    eligible = next(item for item in manifest["activities"] if item["fit"]["valid"])
    fit_path = output / eligible["fit"]["relative_path"]
    assert fit_path.exists()
    assert eligible["fit"]["sha256"] == hashlib.sha256(fit_path.read_bytes()).hexdigest()
    assert not Path(eligible["fit"]["relative_path"]).is_absolute()
    assert "trackpoints" not in json.dumps(eligible)
    assert (output / "migration-manifest.csv").exists()
    assert (output / "migration-config.template.json").exists()
    assert events[0].phase is AuditPhase.DISCOVERY_STARTED
    assert events[-1].phase is AuditPhase.COMPLETE
    assert events[-1].completed == events[-1].total == 3
    assert events[-1].fit_valid == 2
    assert events[-1].failed == 1
    assert [event.phase for event in events if event.phase is AuditPhase.GENERATING_REPORTS]
    assert [event.phase for event in events if event.phase is AuditPhase.WRITING_MANIFEST]

    rerun = audit.run(source, output)
    assert rerun["summary"]["skipped_existing"] == 2
    assert rerun["summary"]["validated"] == 2
    overwritten = audit.run(source, output, overwrite=True)
    assert overwritten["summary"]["converted"] == 2


def test_progress_callback_does_not_change_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    shutil.copyfile(SAMPLE, source / SAMPLE.name)
    events: list[AuditProgress] = []

    MigrationAudit(PolarImporter(), ActivityValidator()).run(
        source, tmp_path / "with-progress", progress=events.append
    )
    MigrationAudit(PolarImporter(), ActivityValidator()).run(source, tmp_path / "without-progress")

    with_progress = json.loads(
        (tmp_path / "with-progress" / "migration-manifest.json").read_text(encoding="utf-8")
    )
    without_progress = json.loads(
        (tmp_path / "without-progress" / "migration-manifest.json").read_text(encoding="utf-8")
    )
    assert with_progress == without_progress
    assert events[-1].phase is AuditPhase.COMPLETE


def test_audit_reports_source_to_domain_loss_as_warning(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    path = source / "training-session-loss.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = MigrationAudit(LossyImporter(), ActivityValidator()).run(source, tmp_path / "audit")
    row = report["activities"][0]
    assert row["status"] == "converted"
    assert row["source_counts"]["hr"] > row["domain_counts"]["hr"]
    assert "source_hr_loss" in row["warnings"]
    assert row["migration_status"] == "excluded_unresolved"


def test_stable_identity_is_independent_of_absolute_root(tmp_path: Path) -> None:
    first = tmp_path / "one" / SAMPLE.name
    second = tmp_path / "two" / SAMPLE.name
    first.parent.mkdir()
    second.parent.mkdir()
    shutil.copyfile(SAMPLE, first)
    shutil.copyfile(SAMPLE, second)
    assert stable_activity_id(source_sha256(first)) == stable_activity_id(source_sha256(second))


def test_timezone_override_resolves_ambiguous_activity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    payload.pop("timeZoneOffset")
    payload["exercises"][0].pop("timezoneOffset")
    activity_path = source / "training-session-ambiguous.json"
    activity_path.write_text(json.dumps(payload), encoding="utf-8")
    first_output = tmp_path / "first"
    first = MigrationAudit(PolarImporter(), ActivityValidator()).run(source, first_output)
    assert first["activities"][0]["migration_status"] == "requires_configuration"
    template = json.loads(
        (first_output / "migration-config.template.json").read_text(encoding="utf-8")
    )
    identifier = first["activities"][0]["stable_activity_id"]
    template["activity_overrides"][identifier]["timezone_offset"] = "+02:00"
    config = tmp_path / "migration-config.json"
    config.write_text(json.dumps(template), encoding="utf-8")
    second = MigrationAudit(PolarImporter(), ActivityValidator()).run(
        source, tmp_path / "second", config=config
    )
    row = second["activities"][0]
    assert row["timezone_resolution_source"] == "manual_override"
    assert row["source_local_start"] == payload["startTime"]
    assert row["resolved_utc_start"].startswith("2025-01-01T08:00:00")


@pytest.mark.parametrize("offset", ["2:00", "+15:00", "+14:01"])
def test_invalid_timezone_override_is_rejected(tmp_path: Path, offset: str) -> None:
    config = tmp_path / "migration-config.json"
    config.write_text(
        json.dumps(
            {
                "config_version": 1,
                "activity_overrides": {"sha256:" + "0" * 64: {"timezone_offset": offset}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        MigrationAudit(PolarImporter(), ActivityValidator()).run(
            tmp_path, tmp_path / "output", config=config
        )


def test_timezone_override_for_unknown_id_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "migration-config.json"
    config.write_text(
        json.dumps(
            {
                "config_version": 1,
                "activity_overrides": {"sha256:" + "0" * 64: {"timezone_offset": "+02:00"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="unknown stable activity"):
        MigrationAudit(PolarImporter(), ActivityValidator()).run(
            tmp_path, tmp_path / "output", config=config
        )


def test_timezone_override_conflicting_with_source_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    activity_path = source / SAMPLE.name
    shutil.copyfile(SAMPLE, activity_path)
    identifier = stable_activity_id(source_sha256(activity_path))
    config = tmp_path / "migration-config.json"
    config.write_text(
        json.dumps(
            {
                "config_version": 1,
                "activity_overrides": {identifier: {"timezone_offset": "+02:00"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="conflicts"):
        MigrationAudit(PolarImporter(), ActivityValidator()).run(
            source, tmp_path / "output", config=config
        )
