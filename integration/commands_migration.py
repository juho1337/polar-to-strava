"""Status, upload, reset, activity and report commands over the existing recovery engine."""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from core.atomic import atomic_write_text
from core.errors import ConfigurationError
from integration.context import Context, make_client
from integration.contract import Emitter, EngineError, utc_now
from integration.lock import WorkspaceLock
from integration.requests import (
    ActivityGetRequest,
    ActivityListRequest,
    ReportExportRequest,
    ResetRequest,
    Selector,
    StatusRequest,
    UploadDryRunRequest,
    UploadRunRequest,
    WorkspaceRequest,
)
from integration.snapshots import (
    action_dict,
    activity_detail,
    audit_summary_dict,
    progress_dict,
    rate_dict,
    recovery_event_dict,
)
from strava.client import REQUIRED_SCOPE
from strava.models import MigrationManifest
from strava.progress import snapshot
from strava.rate_limit import RateLimitPolicy, RateWait
from strava.recovery import Action, Code, RecoveryEvent, RecoveryRecord
from strava.state import UploadStateStore
from strava.uploader import Uploader

NOTICE_UNCERTAIN = "uncertain_outcomes_are_never_retried_automatically"
EMIT_INTERVAL_S = 0.2


def state_path(workspace: Path) -> Path:
    return workspace / "migration-state.sqlite3"


def load_audit_rows(workspace: Path) -> dict[str, dict[str, Any]]:
    """Optional per-activity audit rows (validation issues, original timezone data)."""
    try:
        report = json.loads((workspace / "migration-audit.json").read_text(encoding="utf-8"))
        return {row["stable_activity_id"]: row for row in report["activities"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def load_state(
    workspace: Path,
) -> tuple[MigrationManifest, tuple[RecoveryRecord, ...], bool, str | None]:
    """Manifest and recovery records, reconciling as the CLI ``status`` does when no writer runs."""
    manifest, fingerprint = MigrationManifest.load(workspace)
    lock = WorkspaceLock(workspace, "read")
    reconciled = lock.acquire()
    try:
        with UploadStateStore(state_path(workspace)) as store:
            if reconciled:
                store.reconcile(manifest, fingerprint)
            backup = str(store.backup_path) if store.backup_path is not None else None
            return manifest, store.records(), reconciled, backup
    finally:
        lock.release()


def _details(
    manifest: MigrationManifest,
    records: tuple[RecoveryRecord, ...],
    audit_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {activity.stable_activity_id: activity for activity in manifest.activities}
    seen: set[str] = set()
    details: list[dict[str, Any]] = []
    for record in records:
        seen.add(record.stable_activity_id)
        details.append(
            activity_detail(
                by_id.get(record.stable_activity_id),
                record,
                audit_rows.get(record.stable_activity_id),
            )
        )
    for identifier, activity in by_id.items():
        if identifier not in seen:
            details.append(activity_detail(activity, None, audit_rows.get(identifier)))
    return details


def status(request: StatusRequest, context: Context) -> dict[str, Any]:
    manifest, records, reconciled, backup = load_state(request.workspace)
    summary = progress_dict(snapshot(manifest, records))
    summary["reconciled"] = reconciled
    summary["state_backup_path"] = backup
    if request.details:
        summary["activities"] = _details(manifest, records, load_audit_rows(request.workspace))
    return summary


def _matches(detail: dict[str, Any], request: ActivityListRequest) -> bool:
    migration = detail["migration"] or {}
    if request.migration_status and migration.get("status") not in request.migration_status:
        return False
    if request.sport and request.sport not in (
        detail["sport"]["polar"],
        detail["sport"]["domain"],
        detail["sport"]["fit"],
    ):
        return False
    state = (detail["upload"] or {}).get("state", "none")
    if request.upload_state and state not in request.upload_state:
        return False
    if request.outside_current_eligible is not None and (
        detail["outside_current_eligible"] != request.outside_current_eligible
    ):
        return False
    if request.reason:
        reasons = [migration.get("reason") or ""] + list(
            (detail["upload"] or {}).get("reason_codes", [])
        )
        if not any(request.reason.lower() in str(reason).lower() for reason in reasons):
            return False
    if request.from_date or request.to_date:
        started = detail["resolved_utc_start"] or ""
        try:
            day: date | None = datetime.fromisoformat(started).date()
        except ValueError:
            day = None
        if day is None:
            return False
        if request.from_date and day < request.from_date:
            return False
        if request.to_date and day > request.to_date:
            return False
    return True


def activity_list(request: ActivityListRequest, context: Context) -> dict[str, Any]:
    manifest, records, _, _ = load_state(request.workspace)
    details = [
        item
        for item in _details(manifest, records, load_audit_rows(request.workspace))
        if _matches(item, request)
    ]
    details.sort(key=lambda item: (item["resolved_utc_start"] or "", item["stable_activity_id"]))
    total = len(details)
    page = details[request.offset :]
    if request.limit is not None:
        page = page[: request.limit]
    return {"total": total, "offset": request.offset, "activities": page}


def activity_get(request: ActivityGetRequest, context: Context) -> dict[str, Any]:
    manifest, records, _, _ = load_state(request.workspace)
    for item in _details(manifest, records, load_audit_rows(request.workspace)):
        if item["stable_activity_id"] == request.activity_id:
            return {"activity": item}
    raise EngineError("unknown_activity", "Unknown activity.", field="activity_id")


def _select(uploader: Uploader, selector: Selector) -> list[Any]:
    return uploader.select(
        limit=selector.limit,
        activity_id=selector.activity_id,
        from_date=selector.from_date,
        to_date=selector.to_date,
    )


def upload_dry_run(request: UploadDryRunRequest, context: Context) -> dict[str, Any]:
    with WorkspaceLock(request.workspace, "upload.dry_run"):
        with UploadStateStore(state_path(request.workspace)) as store:
            uploader = Uploader(request.workspace, store)
            selected = _select(uploader, request.selector)
            actions = uploader.preview(selected)
    labels = {
        "submit": "would_submit",
        "observe": "would_observe",
        "review": "blocked/review",
        "resolved": "resolved",
    }
    return {
        "selected": len(selected),
        "actions": [{**action_dict(a), "label": labels[a.kind.value]} for a in actions],
        "notice": "local_preview_only",
    }


class _UploadMonitor:
    """Collects scheduler state and emits throttled progress/rate snapshots."""

    def __init__(
        self, request: UploadRunRequest, context: Context, store: UploadStateStore
    ) -> None:
        self.request, self.context, self.store = request, context, store
        self.emit: Emitter = context.emit
        self.uploader: Uploader | None = None
        self.client: Any = None
        self.wait: dict[str, Any] | None = None
        self.polling: dict[str, Any] | None = None
        self.batch: dict[str, Any] | None = None
        self.rate_limited = False
        self.in_flight = 0
        self._last = 0.0

    def rate(self) -> dict[str, Any]:
        return rate_dict(
            self.client.rate_limit if self.client is not None else None,
            reserve=self.request.rate_limit_reserve,
            max_in_flight=self.request.max_in_flight,
            in_flight=self.in_flight,
            wait=self.wait,
            polling=self.polling,
            observed_at=utc_now(),
        )

    def progress(self, current: str | None, total: int) -> dict[str, Any]:
        assert self.uploader is not None
        shot = snapshot(self.uploader.manifest, self.store.records())
        self.in_flight = shot.capacity_jobs
        return {**progress_dict(shot), "run_done": None, "run_total": total, "current": current}

    def on_event(self, event: RecoveryEvent, total: int) -> None:
        payload = recovery_event_dict(event)
        self.emit.event("upload.event", **payload)
        if Code.RATE_LIMIT in event.reason_codes:
            self.rate_limited = True
        if event.batch_unattempted is not None:
            self.batch = payload["batch"]
        self.polling = (
            {
                "next_get_at": payload["next_poll_at"],
                "polls_remaining": event.polls_remaining,
                "failures_remaining": event.failures_remaining,
                "backoff": event.wait_reason,
            }
            if event.next_poll_at is not None
            else None
        )
        if event.next_poll_at is None:
            self.wait = None
        now = time.monotonic()
        if event.batch_unattempted is not None or now - self._last >= EMIT_INTERVAL_S:
            self._last = now
            self.emit.event("upload.progress", **self.progress(event.identifier or None, total))
            self.emit.event("rate.snapshot", **self.rate())

    def on_wait(self, notice: RateWait, total: int) -> None:
        self.wait = {
            "reason": "short_window_reserve",
            "seconds": notice.seconds,
            "resume_at": notice.resume_at.isoformat().replace("+00:00", "Z"),
        }
        self.emit.event("rate.wait", **self.wait)
        self.emit.event("rate.snapshot", **self.rate())


def upload_run(request: UploadRunRequest, context: Context) -> dict[str, Any]:
    with WorkspaceLock(request.workspace, "upload.run"):
        with UploadStateStore(state_path(request.workspace)) as store:
            planner = Uploader(request.workspace, store)
            selected = _select(planner, request.selector)
            runnable = [a for a in selected if a.kind in {Action.SUBMIT, Action.OBSERVE}]
            if not runnable:  # as the CLI: report local state, make no request
                shot = snapshot(planner.manifest, store.records())
                return {
                    "selected": len(selected),
                    "network": False,
                    "actions": [action_dict(a) for a in selected],
                    "progress": progress_dict(shot),
                    "next": "status_details",
                }
            client = make_client(request, context)
            try:
                try:
                    scopes = client.token_store.load().scope.split()
                except ConfigurationError:
                    raise EngineError(
                        "not_authorized", "Strava is not connected; authorize first."
                    ) from None
                if REQUIRED_SCOPE not in scopes:
                    raise EngineError(
                        "insufficient_scope",
                        "Uploading requires the activity:write permission.",
                        details={"required": [REQUIRED_SCOPE], "granted": sorted(scopes)},
                    )
                monitor = _UploadMonitor(request, context, store)
                monitor.client = client
                total = len(runnable)
                uploader = Uploader(
                    request.workspace,
                    store,
                    client,
                    policy=RateLimitPolicy(
                        reserve=request.rate_limit_reserve,
                        sleep=context.interruptible_sleep,
                        on_wait=lambda notice: monitor.on_wait(notice, total),
                    ),
                    clock=context.clock,
                    on_event=lambda event: monitor.on_event(event, total),
                    max_in_flight=request.max_in_flight,
                    should_stop=context.cancel.is_set,
                )
                monitor.uploader = uploader
                uploader.run(selected)
                final = monitor.progress(None, total)
                context.emit.event("rate.snapshot", **monitor.rate())
                cancelled = context.cancel.is_set()
                stopped = bool(monitor.batch and monitor.batch["stopped"])
                stop_reason = (
                    "cancelled"
                    if cancelled
                    else (
                        "rate_limit"
                        if stopped and monitor.rate_limited
                        else "interrupted" if stopped else None
                    )
                )
                summary = {
                    "selected": len(selected),
                    "network": True,
                    "stopped": stopped or cancelled,
                    "stop_reason": stop_reason,
                    "batch": monitor.batch,
                    "progress": {k: v for k, v in final.items() if k not in {"current"}},
                    "rate": monitor.rate(),
                    "next": "status_details",
                    "notice": NOTICE_UNCERTAIN,
                }
                return {"status": "cancelled" if cancelled else "completed", **summary}
            finally:
                if context.http is None:
                    client.http.close()


def reset(request: ResetRequest, context: Context) -> dict[str, Any]:
    manifest, fingerprint = MigrationManifest.load(request.workspace)
    with WorkspaceLock(request.workspace, "reset"):
        with UploadStateStore(state_path(request.workspace)) as store:
            store.reconcile(manifest, fingerprint)
            actions = store.reset(request.activity_id, False)
            backup = str(store.backup_path) if store.backup_path is not None else None
    return {"actions": [action_dict(a) for a in actions], "state_backup_path": backup}


# --- sanitized report export -------------------------------------------------------------

CSV_COLUMNS = (
    "stable_activity_id",
    "local_start",
    "resolved_utc_start",
    "sport",
    "migration_status",
    "migration_reason",
    "upload_state",
    "reason_codes",
    "known_upload_ids",
    "strava_activity_ids",
    "outside_current_eligible",
)


def _csv_row(item: dict[str, Any]) -> dict[str, Any]:
    upload = item["upload"] or {}
    migration = item["migration"] or {}
    return {
        "stable_activity_id": item["stable_activity_id"],
        "local_start": item["local_start"],
        "resolved_utc_start": item["resolved_utc_start"],
        "sport": item["sport"]["polar"],
        "migration_status": migration.get("status"),
        "migration_reason": migration.get("reason"),
        "upload_state": upload.get("state", "none"),
        "reason_codes": ";".join(upload.get("reason_codes", [])),
        "known_upload_ids": ";".join(upload.get("known_upload_ids", [])),
        "strava_activity_ids": ";".join(upload.get("strava_activity_ids", [])),
        "outside_current_eligible": item["outside_current_eligible"],
    }


def report_export(request: ReportExportRequest, context: Context) -> dict[str, Any]:
    """Build a report only from typed snapshots; no tokens, secrets, responses or local paths."""
    manifest, records, _, _ = load_state(request.workspace)
    rows = load_audit_rows(request.workspace)
    details = _details(manifest, records, rows)
    progress = progress_dict(snapshot(manifest, records))
    audit = None
    try:
        report = json.loads(
            (request.workspace / "migration-audit.json").read_text(encoding="utf-8")
        )
        audit = audit_summary_dict(report, "<workspace>", "<workspace>/migration-audit.md")
    except (OSError, ValueError, KeyError):
        pass
    generated = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if request.format == "json":
        text = json.dumps(
            {
                "generated_at": generated,
                "progress": progress,
                "audit": audit,
                "activities": details,
            },
            indent=2,
            default=str,
        )
        newline: str | None = None
    elif request.format == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for item in details:
            writer.writerow(_csv_row(item))
        text, newline = buffer.getvalue(), ""
    else:
        lines = [
            "# Migration report",
            "",
            f"Generated: {generated}",
            "",
            f"- Eligible: {progress['eligible']}",
            f"- Resolved: {progress['resolved']} ({progress['percent']:.2f}%)",
            f"- Completed: {progress['completed']}; duplicates: {progress['duplicate']}",
            f"- Needs review / blocked: {progress['needs_review']}",
            "",
            "Categories count activities and may overlap.",
            "",
            "| Activity | Status | Upload |",
            "| --- | --- | --- |",
        ]
        for item in details:
            lines.append(
                f"| {item['stable_activity_id']} | {(item['migration'] or {}).get('status')} | "
                f"{(item['upload'] or {}).get('state', 'none')} |"
            )
        text, newline = "\n".join(lines) + "\n", None
    atomic_write_text(request.destination, text, newline=newline)
    return {
        "destination": str(request.destination),
        "format": request.format,
        "activities": len(details),
    }


def check_workspace(request: WorkspaceRequest) -> None:
    if not request.workspace.is_dir():
        raise EngineError(
            "workspace_not_found", "Workspace directory does not exist.", field="workspace"
        )
