"""Rich CLI adapter for framework-neutral migration audit progress events."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from services.service_models import AuditPhase, AuditProgress

PHASE_LABELS = {
    AuditPhase.DISCOVERY_STARTED: "Discovering training sessions",
    AuditPhase.DISCOVERY_COMPLETED: "Training-session discovery complete",
    AuditPhase.PREPARING_ACTIVITIES: "Preparing activities",
    AuditPhase.PROCESSING_ACTIVITIES: "Processing activities",
    AuditPhase.GENERATING_REPORTS: "Generating migration reports",
    AuditPhase.WRITING_MANIFEST: "Writing migration manifest",
    AuditPhase.COMPLETE: "Complete",
}


def _duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


class AuditProgressRenderer:
    """Render audit events live on a terminal or as bounded static phase messages."""

    def __init__(
        self,
        console: Console,
        output: Path,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.console = console
        self.output = output
        self.clock = clock
        self.started_at = 0.0
        self.processing_started_at: float | None = None
        self.current = AuditProgress(AuditPhase.DISCOVERY_STARTED)
        self.live: Live | None = None
        self.last_static_phase: AuditPhase | None = None

    def __enter__(self) -> AuditProgressRenderer:
        self.started_at = self.clock()
        self.console.print("[bold]Analyzing Polar export[/bold]")
        if self.console.is_terminal:
            self.live = Live(
                self._render(), console=self.console, refresh_per_second=4, transient=True
            )
            self.live.start()
        return self

    def __exit__(self, *_: object) -> None:
        if self.live is not None:
            self.live.stop()

    def update(self, event: AuditProgress) -> None:
        if event.phase is AuditPhase.PROCESSING_ACTIVITIES and self.processing_started_at is None:
            self.processing_started_at = self.clock()
        self.current = event
        if self.live is not None:
            self.live.update(self._render())
        else:
            self._print_static_phase(event)

    def _print_static_phase(self, event: AuditProgress) -> None:
        if event.phase is self.last_static_phase:
            return
        self.last_static_phase = event.phase
        if event.phase is AuditPhase.DISCOVERY_STARTED:
            self.console.print("Discovering training sessions...")
        elif event.phase is AuditPhase.DISCOVERY_COMPLETED:
            self.console.print(f"Discovered {event.total} training session(s).")
        elif event.phase is not AuditPhase.COMPLETE:
            self.console.print(f"{PHASE_LABELS[event.phase]}...")

    def _render(self) -> RenderableType:
        event = self.current
        phase = PHASE_LABELS[event.phase]
        items: list[RenderableType] = []
        if event.phase in {
            AuditPhase.DISCOVERY_STARTED,
            AuditPhase.PREPARING_ACTIVITIES,
            AuditPhase.GENERATING_REPORTS,
            AuditPhase.WRITING_MANIFEST,
        }:
            items.append(Spinner("dots", text=phase))
        else:
            items.append(
                Text(
                    f"Complete: {phase}"
                    if event.phase is not AuditPhase.PROCESSING_ACTIVITIES
                    else phase
                )
            )
        if event.phase is not AuditPhase.DISCOVERY_STARTED:
            items.append(Text(f"Training sessions: {event.total}"))
        if event.phase in {
            AuditPhase.PROCESSING_ACTIVITIES,
            AuditPhase.GENERATING_REPORTS,
            AuditPhase.WRITING_MANIFEST,
            AuditPhase.COMPLETE,
        }:
            items.append(
                ProgressBar(
                    total=max(1, event.total),
                    completed=event.completed if event.total else 1,
                    width=48,
                )
            )
            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column(style="bold")
            table.add_column(justify="right")
            percentage = 100 * event.completed / event.total if event.total else 100.0
            table.add_row("Processed", f"{event.completed} / {event.total} ({percentage:.1f}%)")
            table.add_row("FIT valid", str(event.fit_valid))
            table.add_row("Warnings", str(event.warnings))
            table.add_row("Failed", str(event.failed))
            table.add_row("Skipped existing", str(event.skipped_existing))
            elapsed = self.clock() - self.started_at
            table.add_row("Elapsed", _duration(elapsed))
            processing_elapsed = (
                self.clock() - self.processing_started_at
                if self.processing_started_at is not None
                else 0.0
            )
            if event.completed and processing_elapsed > 0:
                rate = event.completed / processing_elapsed
                table.add_row("Rate", f"{rate:.2f} activities/s")
                if (
                    event.completed >= 5
                    and processing_elapsed >= 2
                    and event.total > event.completed
                ):
                    table.add_row(
                        "Estimated left", f"~{_duration((event.total - event.completed) / rate)}"
                    )
            items.append(table)
        return Group(*items)

    def print_summary(self, report: dict[str, Any]) -> None:
        summary = report["summary"]
        statuses = summary["migration_status_counts"]
        ready = statuses.get("eligible", 0) + statuses.get("eligible_with_warnings", 0)
        excluded = sum(
            count for status, count in statuses.items() if status.startswith("excluded_")
        )
        table = Table(title="Polar migration audit complete", show_header=False, box=None)
        table.add_column(style="bold")
        table.add_column(justify="right")
        table.add_row("Activities discovered", str(summary["discovered"]))
        table.add_row("Processed", str(summary["discovered"]))
        table.add_row("FIT valid", str(summary["validated"]))
        table.add_row("Ready to migrate", str(ready))
        table.add_row("Needs configuration", str(statuses.get("requires_configuration", 0)))
        table.add_row("Excluded", str(excluded))
        table.add_row("Failed", str(summary["failed"]))
        table.add_row("Warnings", str(sum(summary["warning_counts"].values())))
        table.add_row("Duration", _duration(float(summary["elapsed_seconds"])))
        self.console.print(table)
        self.console.print(f"Workspace: {self.output}")
        self.console.print(f"Review: {self.output / 'migration-audit.md'}")
