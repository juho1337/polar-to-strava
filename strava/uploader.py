"""Local recovery selection foundation. Execution is disabled until integration."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from core.errors import ValidationError
from strava.models import MigrationManifest
from strava.recovery import Action, ActionDecision, Code, classify_actions
from strava.state import UploadStateStore

INTEGRATION_INCOMPLETE = "recovery integration incomplete"


class Uploader:
    def __init__(
        self,
        workspace: Path,
        store: UploadStateStore,
        client: object | None = None,
        **options: object,
    ) -> None:
        self.workspace, self.store = workspace, store
        # Never inspect/construct/use an injected client during foundation work.
        self.manifest, fingerprint = MigrationManifest.load(workspace)
        store.reconcile(self.manifest, fingerprint)

    def select(
        self,
        *,
        limit: int | None = None,
        activity_id: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[ActionDecision]:
        if limit is not None and limit < 1:
            raise ValidationError("Selection limit must be positive")
        selected: list[ActionDecision] = []
        new = 0
        records = self.store.records()
        if activity_id is not None and not any(
            r.stable_activity_id == activity_id for r in records
        ):
            raise ValidationError("Unknown recovery activity")
        for record in records:
            if activity_id is not None and record.stable_activity_id != activity_id:
                continue
            if from_date or to_date:
                started = None
                try:
                    if record.resolved_utc_start:
                        parsed = datetime.fromisoformat(record.resolved_utc_start)
                        if parsed.tzinfo:
                            started = parsed.date()
                except ValueError:
                    pass
                if started is None:
                    selected.append(
                        ActionDecision(
                            Action.REVIEW,
                            record.stable_activity_id,
                            reasons=(Code.DATE_UNAVAILABLE,),
                        )
                    )
                    continue
                if from_date and started < from_date or to_date and started > to_date:
                    continue
            for action in classify_actions(record):
                if action.kind == Action.SUBMIT:
                    if limit is not None and new >= limit:
                        continue
                    new += 1
                selected.append(action)
        return selected

    def run(self, activities: list[ActionDecision], dry_run: bool = False) -> dict[str, int]:
        # Authoritative guard: no escape hatch, client access, callback or scheduler.
        if not dry_run:
            raise ValidationError(INTEGRATION_INCOMPLETE)
        return self.store.summary()
