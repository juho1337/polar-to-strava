"""Synthetic end-to-end checks for the migration audit."""

import json
import shutil
from pathlib import Path

from polar import PolarImporter
from services.audit import MigrationAudit
from services.conversion import output_name
from services.validation import ActivityValidator

SAMPLE = Path(__file__).parent / "samples" / "training-session-sanitized.json"


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

    report = audit.run(source, output)
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

    rerun = audit.run(source, output)
    assert rerun["summary"]["skipped_existing"] == 2
    assert rerun["summary"]["validated"] == 2
    overwritten = audit.run(source, output, overwrite=True)
    assert overwritten["summary"]["converted"] == 2


def test_audit_reports_source_to_domain_loss_as_warning(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    payload["exercises"][0]["samples"]["heartRate"].append(
        {"dateTime": "2025-01-01T10:00:00.000", "value": 123}
    )
    path = source / "training-session-loss.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = MigrationAudit(PolarImporter(), ActivityValidator()).run(source, tmp_path / "audit")
    row = report["activities"][0]
    assert row["status"] == "converted"
    assert row["source_counts"]["hr"] > row["domain_counts"]["hr"]
    assert "source_hr_loss" in row["warnings"]
