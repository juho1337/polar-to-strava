"""Audit, audit preview and the timezone correction workflow."""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError as PydanticValidationError

from core.atomic import atomic_write_text
from integration.commands_migration import load_audit_rows
from integration.context import Context
from integration.contract import EngineError
from integration.lock import WorkspaceLock
from integration.requests import (
    AuditRequest,
    TimezoneApplyRequest,
    TimezoneChoice,
    TimezoneListRequest,
    TimezonePreviewRequest,
)
from integration.snapshots import audit_activity_dict, audit_progress_dict, audit_summary_dict
from polar import PolarImporter
from services.audit import MigrationAudit
from services.migration import (
    ActivityOverride,
    classify,
    load_migration_config,
    source_sha256,
    stable_activity_id,
)
from services.service_models import AuditPhase, AuditProgress
from services.validation import ActivityValidator
from strava.models import MigrationManifest

PROGRESS_INTERVAL_S = 0.25


class _AuditEvents:
    """Translates service callbacks into throttled contract events."""

    def __init__(self, context: Context) -> None:
        self.emit = context.emit
        self.started = time.monotonic()
        self.processing_started: float | None = None
        self.last_phase: AuditPhase | None = None
        self._last = 0.0

    def __call__(self, event: AuditProgress) -> None:
        now = time.monotonic()
        if event.phase is not self.last_phase:
            self.last_phase = event.phase
            self.emit.event("audit.phase", phase=event.phase.value, total=event.total)
        if event.phase is AuditPhase.PROCESSING_ACTIVITIES and self.processing_started is None:
            self.processing_started = now
        finished = event.completed >= event.total and event.total > 0
        if event.phase is AuditPhase.DISCOVERY_STARTED or (
            not finished and now - self._last < PROGRESS_INTERVAL_S and event.completed
        ):
            return
        self._last = now
        processing = now - self.processing_started if self.processing_started is not None else 0.0
        self.emit.event(
            "audit.progress", **audit_progress_dict(event, now - self.started, processing)
        )


def run_audit(
    context: Context,
    *,
    source: Path,
    workspace: Path,
    config: Path | None,
    overwrite: bool = False,
    incremental: bool = False,
    workers: int | None = None,
) -> dict[str, Any]:
    with WorkspaceLock(workspace, "audit"):
        report = MigrationAudit(PolarImporter(), ActivityValidator()).run(
            source,
            workspace,
            overwrite,
            config,
            _AuditEvents(context),
            workers=workers,
            incremental=incremental,
            should_cancel=context.cancel.is_set,
        )
    for row in report["activities"]:
        context.emit.event("audit.activity", **audit_activity_dict(row))
    return report


def audit(request: AuditRequest, context: Context) -> dict[str, Any]:
    report = run_audit(
        context,
        source=request.source,
        workspace=request.workspace,
        config=request.config,
        overwrite=request.overwrite,
        incremental=request.incremental,
        workers=request.workers,
    )
    return audit_summary_dict(
        report, str(request.workspace), str(request.workspace / "migration-audit.md")
    )


def audit_dry_run(request: AuditRequest, context: Context) -> dict[str, Any]:
    """Discovery plus a parse/validation preview. No FIT is built or verified; nothing is written."""
    from core.errors import AuditCancelledError, ConfigurationError

    importer, validator = PolarImporter(), ActivityValidator()
    context.emit.event("audit.phase", phase="discovery_started", total=0)
    paths = tuple(importer.scan(request.source))
    context.emit.event("audit.phase", phase="discovery_completed", total=len(paths))
    configuration = load_migration_config(request.config)
    counts: dict[str, int] = {}
    started = time.monotonic()
    for index, path in enumerate(paths, 1):
        if context.cancel.is_set():
            raise AuditCancelledError("audit cancelled")
        identifier = stable_activity_id(source_sha256(path))
        override = configuration.activity_overrides.get(identifier)
        status, reason = "would_convert", None
        try:
            activity = importer.import_activity(
                path, override.as_timezone() if override is not None else None
            )
            validation = validator.validate(activity)
            if not validation.is_valid:
                reason = "; ".join(issue.message for issue in validation.issues)
                status = classify({"warnings": [], "error": reason, "fit_valid": False}).value
        except ConfigurationError:
            raise
        except Exception as error:  # parse failures are findings, not command failures
            reason = str(error)
            status = classify({"warnings": [], "error": reason, "fit_valid": False}).value
        counts[status] = counts.get(status, 0) + 1
        context.emit.event(
            "audit.activity",
            stable_activity_id=identifier,
            source=path.relative_to(request.source).as_posix(),
            preview=True,
            preview_status=status,
            reason=reason,
        )
        if index == len(paths) or index % 25 == 0:
            context.emit.event(
                "audit.progress",
                completed=index,
                total=len(paths),
                percent=100 * index / len(paths),
                elapsed_s=time.monotonic() - started,
            )
    return {
        "discovered": len(paths),
        "preview_status_counts": dict(sorted(counts.items())),
        "fit_generation": "not_performed",
        "notice": "preview_only_no_fit_was_built_or_verified",
    }


# --- timezone workflow -------------------------------------------------------------------

REASON_NO_OFFSET = "no_timezone_offset"


def _config_path(workspace: Path, config: Path | None) -> Path:
    return config if config is not None else workspace / "migration-config.json"


def _local_start(manifest_start: str | None) -> tuple[datetime | None, bool]:
    """Naive local start and whether the source timestamp itself carries a zone."""
    if not manifest_start:
        return None, False
    try:
        parsed = datetime.fromisoformat(manifest_start.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None), True
    return parsed, False


def _unresolved(workspace: Path) -> list[dict[str, Any]]:
    manifest, _ = MigrationManifest.load(workspace)
    rows = load_audit_rows(workspace)
    items: list[dict[str, Any]] = []
    for activity in manifest.activities:
        if activity.migration.status != "requires_configuration":
            continue
        row = rows.get(activity.stable_activity_id, {})
        local, has_zone = _local_start(activity.source.local_start)
        items.append(
            {
                "stable_activity_id": activity.stable_activity_id,
                "source": activity.source.relative_path,
                "date": local.date().isoformat() if local else None,
                "local_start": activity.source.local_start,
                "sport": activity.sport.polar,
                "original_timezone": {
                    "source_offset_minutes": row.get("source_timezone_offset_minutes"),
                    "timestamp_has_zone": has_zone,
                },
                "reason_code": REASON_NO_OFFSET,
                "reason": activity.migration.reason,
            }
        )
    return items


def timezone_list(request: TimezoneListRequest, context: Context) -> dict[str, Any]:
    overrides = load_migration_config(
        _config_path(request.workspace, request.config)
        if _config_path(request.workspace, request.config).exists()
        else None
    ).activity_overrides
    items = []
    for item in _unresolved(request.workspace):
        if request.sport and request.sport != item["sport"]:
            continue
        if request.reason and request.reason.lower() not in (
            f"{item['reason_code']} {item['reason'] or ''}".lower()
        ):
            continue
        day = date.fromisoformat(item["date"]) if item["date"] else None
        if request.from_date and (day is None or day < request.from_date):
            continue
        if request.to_date and (day is None or day > request.to_date):
            continue
        override = overrides.get(item["stable_activity_id"])
        item["configured_offset"] = override.timezone_offset if override else None
        items.append(item)
    return {"total": len(items), "activities": items}


def _offset_text(delta: timedelta) -> str | None:
    if delta.total_seconds() % 60:
        return None
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _iana_offset(local: datetime, zone: str) -> tuple[str | None, dict[str, str] | None]:
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None, {
            "code": "unknown_timezone",
            "message": f"Time zone '{zone}' is unknown or the time zone database is unavailable.",
        }
    first = local.replace(tzinfo=tz, fold=0)
    second = local.replace(tzinfo=tz, fold=1)
    if first.utcoffset() != second.utcoffset():
        roundtrip = first.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
        code = "nonexistent_local_time" if roundtrip != local else "ambiguous_local_time"
        return None, {
            "code": code,
            "message": "The local time is ambiguous or does not exist in this zone on that date "
            "(daylight-saving change); choose the offset explicitly.",
        }
    offset = first.utcoffset()
    assert offset is not None
    text = _offset_text(offset)
    if text is None:
        return None, {
            "code": "unsupported_offset_precision",
            "message": "The zone's historical offset has seconds; choose a whole-minute offset.",
        }
    return text, None


def _preview_one(
    choice: TimezoneChoice, default_zone: str | None, unresolved: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    result: dict[str, Any] = {
        "stable_activity_id": choice.stable_activity_id,
        "ok": False,
        "offset": None,
        "offset_source": None,
        "iana_zone": None,
        "resulting_utc": None,
        "local_start": None,
        "errors": errors,
        "warnings": warnings,
    }
    item = unresolved.get(choice.stable_activity_id)
    if item is None:
        errors.append(
            {
                "code": "not_unresolved",
                "message": "The activity is unknown or does not require timezone configuration.",
            }
        )
        return result
    result["local_start"] = item["local_start"]
    local, has_zone = _local_start(item["local_start"])
    original = item["original_timezone"]
    if has_zone or original["source_offset_minutes"] is not None:
        errors.append(
            {
                "code": "authoritative_timezone",
                "message": "The source carries authoritative timezone data; it cannot be overridden.",
            }
        )
        return result
    if local is None:
        errors.append(
            {"code": "no_local_start", "message": "The activity has no usable local start."}
        )
        return result
    offset = choice.offset
    zone = choice.iana_zone or (default_zone if offset is None else None)
    if offset is not None:
        result["offset_source"] = "explicit"
    elif zone is not None:
        offset, problem = _iana_offset(local, zone)
        result["offset_source"], result["iana_zone"] = "iana", zone
        if problem:
            errors.append(problem)
            return result
        warnings.append(
            {
                "code": "suggestion_requires_confirmation",
                "message": f"Offset suggested from the chosen zone {zone} on the activity date.",
            }
        )
    else:
        errors.append({"code": "no_offset", "message": "Provide an offset or an IANA zone."})
        return result
    try:
        override = ActivityOverride(timezone_offset=offset)
    except PydanticValidationError as error:
        errors.append(
            {
                "code": "invalid_offset",
                "message": error.errors()[0]["msg"].removeprefix("Value error, "),
            }
        )
        return result
    tzinfo = override.as_timezone()
    assert isinstance(tzinfo, timezone)
    result["offset"] = offset
    result["resulting_utc"] = (
        local.replace(tzinfo=tzinfo).astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
    result["ok"] = True
    return result


def _preview(request: TimezonePreviewRequest) -> list[dict[str, Any]]:
    unresolved = {item["stable_activity_id"]: item for item in _unresolved(request.workspace)}
    return [_preview_one(choice, request.iana_zone, unresolved) for choice in request.choices]


def timezone_preview(request: TimezonePreviewRequest, context: Context) -> dict[str, Any]:
    items = _preview(request)
    return {
        "items": items,
        "valid": sum(item["ok"] for item in items),
        "invalid": sum(not item["ok"] for item in items),
    }


def timezone_apply(request: TimezoneApplyRequest, context: Context) -> dict[str, Any]:
    if not request.confirm:
        raise EngineError(
            "confirmation_required",
            "Timezone corrections are applied only after explicit confirmation.",
            field="confirm",
        )
    items = _preview(request)
    if not all(item["ok"] for item in items):
        raise EngineError(
            "validation_failed",
            "Some corrections are invalid; nothing was changed.",
            field="choices",
            details={"items": [item for item in items if not item["ok"]]},
        )
    path = _config_path(request.workspace, request.config)
    with WorkspaceLock(request.workspace, "timezone.apply"):
        existing = load_migration_config(path if path.exists() else None)
        overrides = {
            key: {"timezone_offset": value.timezone_offset}
            for key, value in existing.activity_overrides.items()
        }
        for item in items:
            overrides[item["stable_activity_id"]] = {"timezone_offset": item["offset"]}
        document = {
            "config_version": existing.config_version,
            "activity_overrides": overrides,
            "review_requirements": existing.review_requirements,
        }
        atomic_write_text(path, json.dumps(document, indent=2))
        load_migration_config(path)  # the written file must round-trip through the engine's schema
    report = run_audit(
        context,
        source=request.source,
        workspace=request.workspace,
        config=path,
        incremental=request.incremental,
        workers=request.workers,
    )
    rows = {row["stable_activity_id"]: row for row in report["activities"]}
    return {
        "applied": len(items),
        "config_path": str(path),
        "activities": [
            {
                "stable_activity_id": item["stable_activity_id"],
                "offset": item["offset"],
                "resulting_utc": item["resulting_utc"],
                "status_before": "requires_configuration",
                "status_after": rows.get(item["stable_activity_id"], {}).get("migration_status"),
                "reason_after": rows.get(item["stable_activity_id"], {}).get("migration_reason"),
            }
            for item in items
        ],
        "audit": audit_summary_dict(
            report, str(request.workspace), str(request.workspace / "migration-audit.md")
        ),
        "notice": "no_upload_was_started_or_retried",
    }
