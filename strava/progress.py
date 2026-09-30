"""Evidence-based local summaries and rendering; never authorize requests."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from rich.console import Console
from rich.table import Table
from rich.text import Text

from strava.models import MigrationManifest, RateLimit
from strava.recovery import (
    LOCAL_CODES,
    Action,
    ActionDecision,
    Code,
    RecoveryEvent,
    RecoveryRecord,
    Remote,
    classify_actions,
    consumes_submission_capacity,
    duplicate_review_stop,
)

ACTION_LABELS = {
    Action.SUBMIT: "Ready to submit",
    Action.OBSERVE: "Observing",
    Action.REVIEW: "Needs review / blocked",
    Action.RESOLVED: "Resolved",
}
PREVIEW_LABELS = {
    Action.SUBMIT: "would_submit",
    Action.OBSERVE: "would_observe",
    Action.REVIEW: "blocked/review",
    Action.RESOLVED: "resolved",
}


def literal(value: str) -> Text:
    """Display identifiers literally, without terminal controls or Rich markup."""
    return Text("".join(character for character in value if character.isprintable()))


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    eligible: int
    fit_bytes: int
    completed: int
    duplicate: int
    resolved: int
    ready_to_submit: int
    observing: int
    needs_review: int
    local_blocked: int
    outside_current_eligible: int
    outside_manifest: int
    outside_resolved: int
    capacity_jobs: int = 0
    duplicate_review_stops: int = 0

    @property
    def remaining(self) -> int:
        return self.eligible - self.resolved

    @property
    def percent(self) -> float:
        return 100 * self.resolved / self.eligible if self.eligible else 100.0


def snapshot(manifest: MigrationManifest, records: Iterable[RecoveryRecord]) -> ProgressSnapshot:
    eligible = {a.stable_activity_id for a in manifest.activities if a.eligible}
    present = {a.stable_activity_id for a in manifest.activities}
    categories: dict[Action, set[str]] = {action: set() for action in Action}
    completed: set[str] = set()
    duplicates: set[str] = set()
    local: set[str] = set()
    identifiers: set[str] = set()
    capacity_jobs = 0
    review_stops = 0
    for record in records:
        capacity_jobs += sum(consumes_submission_capacity(record, a) for a in record.attempts)
        review_stops += sum(
            duplicate_review_stop(record, a)
            for a in record.attempts
            if a.remote in {Remote.PROCESSING, Remote.DEFERRED}
        )
        identifier = record.stable_activity_id
        identifiers.add(identifier)
        actions = classify_actions(record)
        for action in actions:
            categories[action.kind].add(identifier)
            if action.kind == Action.RESOLVED:
                attempt = next(a for a in record.attempts if a.attempt_id == action.attempt_id)
                if attempt.remote == Remote.COMPLETED:
                    completed.add(identifier)
                elif attempt.remote == Remote.DUPLICATE:
                    duplicates.add(identifier)
        if any(b.active and b.code in LOCAL_CODES for b in record.blockers):
            local.add(identifier)
    resolved = categories[Action.RESOLVED]
    return ProgressSnapshot(
        eligible=len(eligible),
        fit_bytes=sum(max(0, a.fit.size_bytes or 0) for a in manifest.activities if a.eligible),
        completed=len(completed & eligible),
        duplicate=len(duplicates & eligible),
        resolved=len(resolved & eligible),
        ready_to_submit=len(categories[Action.SUBMIT]),
        observing=len(categories[Action.OBSERVE]),
        needs_review=len(categories[Action.REVIEW]),
        local_blocked=len(local),
        outside_current_eligible=len(identifiers - eligible),
        outside_manifest=len(identifiers - present),
        outside_resolved=len(resolved - eligible),
        capacity_jobs=capacity_jobs,
        duplicate_review_stops=review_stops,
    )


def progress_table(
    progress: ProgressSnapshot,
    *,
    title: str = "Strava migration",
    run_done: int | None = None,
    run_total: int | None = None,
    rate: RateLimit | None = None,
    current: str | None = None,
) -> Table:
    table = Table(title=title, show_header=False, box=None)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Eligible activities", str(progress.eligible))
    table.add_row(
        "Resolved", f"{progress.resolved} / {progress.eligible} ({progress.percent:.2f}%)"
    )
    table.add_row("Completed (eligible)", str(progress.completed))
    table.add_row("Duplicates (eligible)", str(progress.duplicate))
    table.add_row("Remaining (eligible)", str(progress.remaining))
    for label, count in (
        ("Ready to submit", progress.ready_to_submit),
        ("Observing", progress.observing),
        ("Needs review / blocked", progress.needs_review),
        ("Capacity-consuming remote jobs (workspace)", progress.capacity_jobs),
        ("Duplicate review stops (no capacity)", progress.duplicate_review_stops),
        ("Local blockers", progress.local_blocked),
        ("Retained outside current eligible", progress.outside_current_eligible),
        ("Outside manifest", progress.outside_manifest),
        ("Resolved outside eligible", progress.outside_resolved),
    ):
        table.add_row(label, str(count))
    if run_done is not None and run_total is not None:
        table.add_row("This run", f"{run_done} / {run_total}")
    if rate is not None:
        table.add_row("API 15 min", f"{rate.short_usage} / {rate.short_limit}")
        table.add_row("API daily", f"{rate.daily_usage} / {rate.daily_limit}")
        if rate.read_short_limit is not None and rate.read_short_usage is not None:
            table.add_row("Read 15 min", f"{rate.read_short_usage} / {rate.read_short_limit}")
        if rate.read_daily_limit is not None and rate.read_daily_usage is not None:
            table.add_row("Read daily", f"{rate.read_daily_usage} / {rate.read_daily_limit}")
    if current:
        table.add_row("Current", literal(current))
    table.caption = "Recovery categories count activities and may overlap; capacity/review-stop counts are attempts. Workspace counts may exceed the selected batch."
    return table


def print_actions(
    console: Console, actions: Iterable[ActionDecision], *, preview: bool = False
) -> None:
    labels = PREVIEW_LABELS if preview else ACTION_LABELS
    for action in actions:
        console.print(literal(f"{action.stable_activity_id}: {labels[action.kind]}"))
        if action.reasons:
            console.print(
                "  Reasons: " + ", ".join(code.value for code in action.reasons), markup=False
            )
        if Code.DUPLICATE_UNRECOGNIZED in action.reasons:
            console.print("  Human review required; automatic observation stopped.")
            for upload_id in action.review_upload_ids:
                console.print(literal(f"  Review-stopped upload ID: {upload_id}"))
        if Code.DATE_UNAVAILABLE in action.reasons:
            console.print(
                "  Date unavailable: use an explicit --activity-id or --all without date filters."
            )


def print_details(console: Console, records: Iterable[RecoveryRecord]) -> None:
    for record in records:
        print_actions(console, classify_actions(record))
        remote = ", ".join(sorted({a.remote.value for a in record.attempts})) or "not_started"
        phases = ", ".join(sorted({a.submission.value for a in record.attempts})) or "no_attempt"
        local = (
            ", ".join(
                sorted(
                    {b.code.value for b in record.blockers if b.active and b.code in LOCAL_CODES}
                )
            )
            or "none"
        )
        reasons = {a.error_code for a in record.attempts} - {Code.NONE}
        console.print(f"  Remote: {remote}; submission evidence: {phases}", markup=False)
        console.print(f"  Local blockers: {local}", markup=False)
        if reasons:
            console.print(
                "  Observation/evidence reasons: "
                + ", ".join(sorted(code.value for code in reasons)),
                markup=False,
            )
        outside = not record.present or not record.eligible
        console.print(
            f"  Outside current eligible: {'yes' if outside else 'no'}; outside manifest: {'no' if record.present else 'yes'}"
        )
        started = None
        try:
            if record.resolved_utc_start:
                parsed = datetime.fromisoformat(record.resolved_utc_start)
                if parsed.tzinfo:
                    started = parsed.date().isoformat()
        except ValueError:
            pass
        console.print(
            "  Activity date: "
            + (started or "unavailable; use explicit --activity-id or --all without date filters.")
        )


def print_status(
    console: Console,
    progress: ProgressSnapshot,
    details: bool = False,
    *,
    records: Iterable[RecoveryRecord] = (),
) -> None:
    console.print(progress_table(progress))
    console.print(f"FIT data: {progress.fit_bytes / 1_000_000:.1f} MB")
    console.print("Local evidence only; no Strava request was made.")
    if details:
        print_details(console, records)


class RecoveryProgressRenderer:
    """Bounded current-event adapter; rendering never mutates evidence or authorizes I/O."""

    def __init__(
        self, manifest: MigrationManifest, records: Callable[[], tuple[RecoveryRecord, ...]]
    ) -> None:
        self.manifest, self.records = manifest, records
        self.current: RecoveryEvent | None = None

    def update(self, event: RecoveryEvent) -> None:
        self.current = event

    def render(self) -> Table:
        current = None
        if self.current:
            event = self.current
            reasons = ", ".join(code.value for code in event.reason_codes)
            current = f"{event.identifier}: {ACTION_LABELS[event.action]}" + (
                f" ({reasons})" if reasons else ""
            )
            if Code.DUPLICATE_UNRECOGNIZED in event.reason_codes:
                current += "; automatic observation stopped"
                current += "; upload IDs: " + ", ".join(event.review_upload_ids)
            if event.next_poll_at is not None:
                due = datetime.fromtimestamp(event.next_poll_at, UTC).isoformat()
                current += (
                    f"; waiting: {event.wait_reason}; next GET at {due}; "
                    f"GET budget remaining {event.polls_remaining}; "
                    f"consecutive-failure budget remaining {event.failures_remaining}"
                )
            if event.batch_unattempted is not None:
                cause = (
                    "run stopped" if event.batch_stopped else "retained capacity or deferred work"
                )
                current += (
                    f"; Batch finished: {event.batch_review} need review; "
                    f"{event.batch_observing} observations retained/deferred; "
                    f"{event.batch_capacity_jobs} capacity-consuming jobs; "
                    f"{event.batch_review_stops} duplicate review stops (no capacity); "
                    f"{event.batch_unattempted} selected submissions unattempted"
                )
                if event.batch_unattempted:
                    current += f" ({cause})"
        return progress_table(snapshot(self.manifest, self.records()), current=current)
