"""Local migration progress calculations and compact Rich rendering."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from strava.models import MigrationManifest, RateLimit


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    eligible: int
    fit_bytes: int
    completed: int
    duplicate: int
    pending: int
    uploading: int
    processing: int
    retryable_failure: int
    permanent_failure: int
    local_file_changed: int
    uncertain: int
    skipped: int

    @property
    def resolved(self) -> int:
        return self.completed + self.duplicate

    @property
    def active(self) -> int:
        return self.uploading + self.processing

    @property
    def needs_attention(self) -> int:
        return self.permanent_failure + self.local_file_changed + self.uncertain

    @property
    def failed(self) -> int:
        return self.retryable_failure + self.needs_attention

    @property
    def remaining(self) -> int:
        return self.eligible - self.resolved

    @property
    def percent(self) -> float:
        return 100 * self.resolved / self.eligible if self.eligible else 100.0


def snapshot(manifest: MigrationManifest, states: dict[str, int]) -> ProgressSnapshot:
    eligible = [activity for activity in manifest.activities if activity.eligible]
    return ProgressSnapshot(
        eligible=len(eligible),
        fit_bytes=sum(activity.fit.size_bytes or 0 for activity in eligible),
        completed=states.get("completed", 0),
        duplicate=states.get("duplicate", 0),
        pending=states.get("pending", 0),
        uploading=states.get("uploading", 0),
        processing=states.get("processing", 0),
        retryable_failure=states.get("retryable_failure", 0),
        permanent_failure=states.get("permanent_failure", 0),
        local_file_changed=states.get("local_file_changed", 0),
        uncertain=states.get("uncertain", 0),
        skipped=states.get("skipped", 0),
    )


def progress_table(
    progress: ProgressSnapshot,
    *,
    title: str = "Strava migration",
    run_done: int | None = None,
    run_total: int | None = None,
    processing: int | None = None,
    rate: RateLimit | None = None,
    current: str | None = None,
) -> Table:
    table = Table(title=title, show_header=False, box=None)
    table.add_column(style="bold")
    table.add_column()
    table.add_row(
        "Resolved", f"{progress.resolved} / {progress.eligible} ({progress.percent:.2f}%)"
    )
    table.add_row("Migrated", str(progress.completed))
    table.add_row("Duplicates", str(progress.duplicate))
    table.add_row("Remaining", str(progress.remaining))
    table.add_row("Processing", str(progress.active if processing is None else processing))
    table.add_row("Retryable", str(progress.retryable_failure))
    table.add_row("Needs review", str(progress.needs_attention))
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
        table.add_row("Current", current)
    return table


def print_status(console: Console, progress: ProgressSnapshot, details: bool = False) -> None:
    console.print(progress_table(progress))
    console.print(f"FIT data: {progress.fit_bytes / 1_000_000:.1f} MB")
    state = (
        "Needs attention"
        if progress.needs_attention
        else ("Migration resolved" if progress.remaining == 0 else "Ready to continue")
    )
    console.print(f"State: {state}")
    if details:
        console.print(
            f"pending={progress.pending}; uploading={progress.uploading}; "
            f"processing={progress.processing}; retryable_failure={progress.retryable_failure}; "
            f"permanent_failure={progress.permanent_failure}; "
            f"local_file_changed={progress.local_file_changed}; uncertain={progress.uncertain}; "
            f"skipped={progress.skipped}"
        )
