"""Typed, pure snapshots mapping CLI-visible values to contract fields (parity matrix)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from services.audit_metrics import audit_metrics
from services.service_models import AuditProgress
from strava.models import ManifestActivity, RateLimit
from strava.progress import ProgressSnapshot
from strava.recovery import (
    LOCAL_CODES,
    Action,
    ActionDecision,
    RecoveryEvent,
    RecoveryRecord,
    classify_actions,
)

PROGRESS_SEMANTICS = [
    "categories_count_activities_and_may_overlap",
    "capacity_and_review_stop_count_attempts",
    "workspace_counts_may_exceed_batch",
]


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


# --- audit -------------------------------------------------------------------------------


def audit_progress_dict(
    event: AuditProgress, elapsed_s: float, processing_elapsed_s: float
) -> dict[str, Any]:
    metrics = audit_metrics(event.completed, event.total, processing_elapsed_s)
    return {
        "completed": event.completed,
        "total": event.total,
        "percent": metrics.percent,
        "fit_valid": event.fit_valid,
        "fit_generated": event.fit_generated,
        "fit_reused": event.skipped_existing,
        "cache_hits": event.cache_hits,
        "warnings": event.warnings,
        "failed": event.failed,
        "skipped_existing": event.skipped_existing,
        "elapsed_s": elapsed_s,
        "rate_per_s": metrics.rate_per_s,
        "eta_s": metrics.eta_s,
    }


def audit_activity_dict(row: dict[str, Any]) -> dict[str, Any]:
    """One audit row; all fields the audit CSV/JSON carry, with stable names."""
    return {
        "stable_activity_id": row["stable_activity_id"],
        "source": row["source"],
        "source_filename": row["source_filename"],
        "status": row["status"],
        "failure_stage": row.get("failure_stage"),
        "error": row.get("error"),
        "migration_status": row.get("migration_status"),
        "migration_reason": row.get("migration_reason"),
        "warnings": list(row["warnings"]),
        "validation_issues": list(row.get("validation_issues", [])),
        "original_sport": row.get("original_sport"),
        "domain_sport": row.get("domain_sport"),
        "fit_sport": row.get("fit_sport"),
        "fit_sub_sport": row.get("fit_sub_sport"),
        "started_at": row.get("started_at"),
        "resolved_utc_start": row.get("resolved_utc_start"),
        "ended_at": row.get("ended_at"),
        "utc_offset_minutes": row.get("utc_offset_minutes"),
        "timezone_resolution_source": row.get("timezone_resolution_source"),
        "manual_override_used": bool(row.get("manual_override_used")),
        "elapsed_s": row.get("elapsed_s"),
        "timer_s": row.get("timer_s"),
        "distance_m": row.get("distance_m"),
        "calories": row.get("calories"),
        "activity_name": row.get("name"),
        "average_hr": row.get("average_hr"),
        "maximum_hr": row.get("maximum_hr"),
        "ascent_m": row.get("ascent_m"),
        "counts": {layer: row.get(f"{layer}_counts", {}) for layer in ("source", "domain", "fit")},
        "fit": {
            "valid": bool(row.get("fit_valid")),
            "sha256": row.get("fit_sha256"),
            "size_bytes": row.get("fit_bytes"),
            "relative_path": row.get("fit_relative_path") if row.get("fit_valid") else None,
            "reused": row["status"] == "skipped_existing",
        },
    }


def audit_summary_dict(report: dict[str, Any], workspace: str, report_path: str) -> dict[str, Any]:
    summary = report["summary"]
    statuses = summary["migration_status_counts"]
    ready = statuses.get("eligible", 0) + statuses.get("eligible_with_warnings", 0)
    excluded = sum(count for status, count in statuses.items() if status.startswith("excluded_"))
    return {
        "discovered": summary["discovered"],
        "processed": summary["discovered"],
        "parsed": summary["parsed"],
        "converted": summary["converted"],
        "validated": summary["validated"],
        "skipped_existing": summary["skipped_existing"],
        "fit_generated": summary["converted"],
        "fit_reused": summary["skipped_existing"],
        "ready_to_migrate": ready,
        "needs_configuration": statuses.get("requires_configuration", 0),
        "excluded": excluded,
        "failed": summary["failed"],
        "warnings_total": sum(summary["warning_counts"].values()),
        "warning_counts": summary["warning_counts"],
        "migration_status_counts": statuses,
        "failure_stages": summary["failure_stages"],
        "failure_categories": summary["failure_categories"],
        "elapsed_s": summary["elapsed_seconds"],
        "activities_per_second": summary["activities_per_second"],
        "total_fit_bytes": summary["total_fit_bytes"],
        "generated_fit_bytes": summary["generated_fit_bytes"],
        "earliest": summary["earliest"],
        "latest": summary["latest"],
        "per_year": summary["per_year"],
        "per_month": summary["per_month"],
        "sports": report["sports"],
        "sensors": report["sensors"],
        "duplicate_candidates": report["duplicate_candidates"],
        "workspace": workspace,
        "report_path": report_path,
    }


# --- migration progress ------------------------------------------------------------------


def progress_dict(progress: ProgressSnapshot) -> dict[str, Any]:
    return {
        "eligible": progress.eligible,
        "resolved": progress.resolved,
        "percent": progress.percent,
        "completed": progress.completed,
        "duplicate": progress.duplicate,
        "remaining": progress.remaining,
        "ready_to_submit": progress.ready_to_submit,
        "observing": progress.observing,
        "needs_review": progress.needs_review,
        "local_blocked": progress.local_blocked,
        "capacity_jobs": progress.capacity_jobs,
        "duplicate_review_stops": progress.duplicate_review_stops,
        "outside_current_eligible": progress.outside_current_eligible,
        "outside_manifest": progress.outside_manifest,
        "outside_resolved": progress.outside_resolved,
        "fit_bytes": progress.fit_bytes,
        "evidence": "local_only",
        "semantics": PROGRESS_SEMANTICS,
    }


def action_dict(action: ActionDecision) -> dict[str, Any]:
    return {
        "kind": action.kind.value,
        "stable_activity_id": action.stable_activity_id,
        "attempt_id": action.attempt_id,
        "upload_id": action.upload_id,
        "reason_codes": [code.value for code in action.reasons],
        "review_upload_ids": list(action.review_upload_ids),
    }


def recovery_event_dict(event: RecoveryEvent) -> dict[str, Any]:
    batch = None
    if event.batch_unattempted is not None:
        batch = {
            "unattempted": event.batch_unattempted,
            "review": event.batch_review,
            "observing": event.batch_observing,
            "capacity_jobs": event.batch_capacity_jobs,
            "review_stops": event.batch_review_stops,
            "stopped": event.batch_stopped,
        }
    return {
        "identifier": event.identifier,
        "action": event.action.value,
        "reason_codes": [code.value for code in event.reason_codes],
        "review_upload_ids": list(event.review_upload_ids),
        "wait_reason": event.wait_reason,
        "next_poll_at": _iso(event.next_poll_at),
        "polls_remaining": event.polls_remaining,
        "failures_remaining": event.failures_remaining,
        "batch": batch,
    }


# --- Strava API and scheduler ------------------------------------------------------------


def _window(usage: int | None, limit: int | None) -> dict[str, Any]:
    known = usage is not None and limit is not None
    return {
        "usage": usage if known else None,
        "limit": limit if known else None,
        "remaining": (limit - usage) if known and limit is not None and usage is not None else None,
        "known": known,
    }


def rate_dict(
    rate: RateLimit | None,
    *,
    reserve: int,
    max_in_flight: int,
    in_flight: int,
    wait: dict[str, Any] | None = None,
    polling: dict[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "overall": {
            "short": _window(
                rate.short_usage if rate else None, rate.short_limit if rate else None
            ),
            "daily": _window(
                rate.daily_usage if rate else None, rate.daily_limit if rate else None
            ),
        },
        "read": {
            "short": _window(
                rate.read_short_usage if rate else None, rate.read_short_limit if rate else None
            ),
            "daily": _window(
                rate.read_daily_usage if rate else None, rate.read_daily_limit if rate else None
            ),
        },
        "reserve": reserve,
        "max_in_flight": max_in_flight,
        "in_flight": in_flight,
        "wait": wait,
        "polling": polling,
        "observed_at": observed_at,
    }


# --- activity details --------------------------------------------------------------------


def _state(actions: tuple[ActionDecision, ...]) -> str:
    kinds = {action.kind for action in actions}
    for kind in (Action.REVIEW, Action.SUBMIT, Action.OBSERVE, Action.RESOLVED):
        if kind in kinds:
            return kind.value
    return "none"


def activity_detail(
    manifest: ManifestActivity | None,
    record: RecoveryRecord | None,
    audit_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identifier = (
        manifest.stable_activity_id
        if manifest is not None
        else (
            record.stable_activity_id
            if record is not None
            else (audit_row or {})["stable_activity_id"]
        )
    )
    started = None
    if manifest is not None and manifest.time.resolved_utc_start is not None:
        started = manifest.time.resolved_utc_start.isoformat()
    elif record is not None:
        started = record.resolved_utc_start
    date_available = False
    if started:
        try:
            date_available = datetime.fromisoformat(started).tzinfo is not None
        except ValueError:
            date_available = False
    upload: dict[str, Any] | None = None
    next_actions: list[str] = []
    if record is not None:
        actions = classify_actions(record)
        local = [b for b in record.blockers if b.active and b.code in LOCAL_CODES]
        upload = {
            "state": _state(actions),
            "actions": [action_dict(action) for action in actions],
            "reason_codes": sorted({code.value for action in actions for code in action.reasons}),
            "review_upload_ids": sorted(
                {upload_id for action in actions for upload_id in action.review_upload_ids}
            ),
            "remote": sorted({attempt.remote.value for attempt in record.attempts}),
            "submission_evidence": sorted(
                {attempt.submission.value for attempt in record.attempts}
            ),
            "known_upload_ids": sorted(
                {attempt.upload_id for attempt in record.attempts if attempt.upload_id}
            ),
            "strava_activity_ids": sorted(
                {
                    value
                    for attempt in record.attempts
                    for value in (attempt.activity_id, attempt.duplicate_activity_id)
                    if value
                }
            ),
            "blockers": [
                {
                    "scope": blocker.scope,
                    "code": blocker.code.value,
                    "blocks_submission": blocker.blocks_submission,
                    "blocks_observation": blocker.blocks_observation,
                    "local": blocker.code in LOCAL_CODES,
                }
                for blocker in record.blockers
                if blocker.active
            ],
            "error_codes": sorted(
                {
                    attempt.error_code.value
                    for attempt in record.attempts
                    if attempt.error_code.value != "none"
                }
            ),
            "attempts": len(record.attempts),
        }
        for kind in ("submit", "observe", "review", "resolved"):
            if any(action.kind.value == kind for action in actions):
                next_actions.append(kind)
        if local:
            next_actions.append("reset_available")
    if manifest is not None and manifest.migration.status == "requires_configuration":
        next_actions.append("configure_timezone")
    return {
        "stable_activity_id": identifier,
        "local_start": manifest.source.local_start if manifest is not None else None,
        "resolved_utc_start": started,
        "date_available": date_available,
        "sport": (
            {
                "polar": manifest.sport.polar,
                "domain": manifest.sport.domain,
                "fit": manifest.sport.fit,
                "fallback": manifest.sport.fallback,
            }
            if manifest is not None
            else {"polar": None, "domain": None, "fit": None, "fallback": False}
        ),
        "source": (
            {
                "relative_path": manifest.source.relative_path,
                "filename": manifest.source.filename,
                "sha256": manifest.source.sha256,
            }
            if manifest is not None
            else None
        ),
        "fit": (
            {
                "valid": manifest.fit.valid,
                "sha256": manifest.fit.sha256,
                "size_bytes": manifest.fit.size_bytes,
                "relative_path": manifest.fit.relative_path,
            }
            if manifest is not None
            else None
        ),
        "migration": (
            {
                "status": manifest.migration.status,
                "eligible": manifest.eligible,
                "reason": manifest.migration.reason,
                "warnings": sorted(manifest.migration.warnings),
                "timezone_resolution": manifest.time.resolution_source,
            }
            if manifest is not None
            else None
        ),
        "validation_issues": list((audit_row or {}).get("validation_issues", [])),
        "upload": upload,
        "outside_current_eligible": (
            record is not None and (not record.present or not record.eligible)
        )
        or (manifest is not None and not manifest.eligible and record is not None),
        "outside_manifest": record is not None and not record.present,
        "next_actions": next_actions,
    }


def status_counts(details: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter((item["migration"] or {}).get("status", "unknown") for item in details))
