"""Pure audit progress calculations shared by the Rich renderer and machine-readable events."""

from __future__ import annotations

from dataclasses import dataclass

MIN_ETA_COMPLETED = 5
MIN_ETA_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class AuditMetrics:
    percent: float
    rate_per_s: float | None
    eta_s: float | None


def audit_metrics(completed: int, total: int, processing_elapsed: float) -> AuditMetrics:
    """Percentage, throughput and (only when reliable) remaining time."""
    percent = 100 * completed / total if total else 100.0
    rate = None
    eta = None
    if completed and processing_elapsed > 0:
        rate = completed / processing_elapsed
        if (
            completed >= MIN_ETA_COMPLETED
            and processing_elapsed >= MIN_ETA_SECONDS
            and total > completed
        ):
            eta = (total - completed) / rate
    return AuditMetrics(percent, rate, eta)
