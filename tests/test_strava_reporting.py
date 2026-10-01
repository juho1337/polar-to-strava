"""SPEC-001 WP7: local action previews, overlapping populations and safe rendering."""

from dataclasses import replace
from io import StringIO
from pathlib import Path

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

import core.cli as cli
from strava.client import FailurePhase, RequestFailure, StravaClient, TokenStore
from strava.models import MigrationManifest, RateLimit
from strava.progress import RecoveryProgressRenderer, print_status, snapshot
from strava.recovery import Action, Code, Operation, RecoveryEvent, Remote, ResponseEvidence
from strava.state import UploadStateStore
from strava.uploader import Uploader
from tests.test_strava_scheduler import Clock, Pipeline, engine
from tests.test_strava_state import legacy
from tests.test_strava_uploader import multi_workspace, start_attempt, workspace


@pytest.mark.parametrize("reason", [Code.RATE_LIMIT, Code.NETWORK, Code.POLL_BUDGET])
def test_progress_reports_persisted_observation_deferral(tmp_path: Path, reason: Code) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())

    def observe(upload_id: str) -> ResponseEvidence:
        if reason == Code.POLL_BUDGET:
            return ResponseEvidence(upload_id=upload_id, remote=Remote.PROCESSING)
        raise RequestFailure(Operation.OBSERVE, FailurePhase.OBSERVATION, reason)

    client.observe = observe
    events: list[RecoveryEvent] = []
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        run = engine(root, store, client, polls=1 if reason == Code.POLL_BUDGET else 60)
        run.on_event = events.append
        run.run(uploader.select())
        assert store.load(identifier).attempts[0].error_code == reason
        assert events[-1].action == Action.OBSERVE and reason in events[-1].reason_codes
        renderer = RecoveryProgressRenderer(uploader.manifest, store.records)
        renderer.update(events[-1])
        output = StringIO()
        Console(file=output, width=150).print(renderer.render())
        assert reason.value in output.getvalue()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Local reporting attempted client/auth/network access")

    monkeypatch.setattr(cli, "StravaClient", forbidden)
    monkeypatch.setattr(cli, "credentials", forbidden)
    monkeypatch.setattr(StravaClient, "__init__", forbidden)
    for name in ("prepare_access", "access_token", "upload", "get_upload"):
        monkeypatch.setattr(StravaClient, name, forbidden)
    monkeypatch.setattr(TokenStore, "load", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)


def invoke(root: Path, command: str, *args: str) -> str:
    result = CliRunner().invoke(cli.app, ["strava", command, str(root), *args])
    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.parametrize(
    "command,args", [("status", []), ("status", ["--details"]), ("upload", ["--all", "--dry-run"])]
)
def test_local_commands_zero_network(
    tmp_path: Path, no_network: None, command: str, args: list[str]
) -> None:
    root, identifier = workspace(tmp_path)
    output = invoke(root, command, *args)
    assert ("would_submit" if command == "upload" else "Ready to submit") in output
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        assert store.load(identifier).attempts == ()


@pytest.mark.parametrize("change", ["changed", "missing", "orphan", "ineligible"])
def test_dry_run_known_id_keeps_observation_and_local_blocker(
    tmp_path: Path, no_network: None, change: str
) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        store.record_evidence(
            start_attempt(store, uploader, identifier),
            ResponseEvidence(upload_id="11", remote=Remote.PROCESSING),
        )
        before = store.load(identifier).attempts
    manifest, _ = MigrationManifest.load(root)
    if change == "missing":
        (root / "fits/activity.fit").unlink()
    elif change == "changed":
        (root / "fits/activity.fit").write_bytes(b"changed!!")
    else:
        if change == "orphan":
            manifest.activities.clear()
        else:
            manifest.activities[0].migration.status = "excluded"
        (root / "migration-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    output = invoke(root, "upload", "--activity-id", identifier, "--dry-run")
    assert "would_observe" in output and "blocked/review" in output
    assert "would_submit" not in output.split("Local preview only")[0]
    with UploadStateStore(path) as store:
        assert store.load(identifier).attempts == before
    details = invoke(root, "status", "--details")
    assert "Observing" in details and "Local blockers:" in details


@pytest.mark.parametrize(
    "state,label",
    [
        ("fresh", "would_submit"),
        ("uncertain", "blocked/review"),
        ("completed", "resolved"),
        ("duplicate", "resolved"),
        ("bad_fit", "blocked/review"),
    ],
)
def test_preview_actions_and_no_intent(
    tmp_path: Path, no_network: None, state: str, label: str
) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        if state not in {"fresh", "bad_fit"}:
            attempt = start_attempt(store, uploader, identifier)
            if state == "uncertain":
                store.record_uncertain(attempt)
            else:
                store.record_evidence(
                    attempt,
                    ResponseEvidence(
                        upload_id="11",
                        activity_id="22" if state == "completed" else None,
                        duplicate_activity_id="22" if state == "duplicate" else None,
                        remote=Remote.COMPLETED if state == "completed" else Remote.DUPLICATE,
                    ),
                )
        before = store.load(identifier).attempts
    if state == "bad_fit":
        (root / "fits/activity.fit").write_bytes(b"wrong-fit")
    output = invoke(root, "upload", "--all", "--dry-run").split("Local preview only")[0]
    assert label in output
    if state != "fresh":
        assert "would_submit" not in output
    with UploadStateStore(path) as store:
        assert store.load(identifier).attempts == before


def test_overlapping_categories_and_eligible_population(tmp_path: Path) -> None:
    root, ids = multi_workspace(tmp_path, 4)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        for index, identifier in enumerate(ids[:3]):
            store.record_evidence(
                start_attempt(store, uploader, identifier),
                ResponseEvidence(
                    upload_id=str(index + 1), activity_id=str(index + 10), remote=Remote.COMPLETED
                ),
            )
            store.set_blocker(identifier, "activity", Code.FIT_HASH_MISMATCH)
        manifest = uploader.manifest.model_copy(deep=True)
        manifest.activities.pop(2)
        manifest.activities[1].migration.status = "excluded"
        store.reconcile(manifest, "synthetic")
        result = snapshot(manifest, store.records())
        assert result.eligible == 2 and result.resolved == 1 and result.percent == 50
        assert result.completed == 1 and result.ready_to_submit == 1
        assert result.needs_review == 3 and result.local_blocked == 3
        assert result.outside_current_eligible == 2 and result.outside_manifest == 1
        assert result.outside_resolved == 2
        output = StringIO()
        print_status(Console(file=output, width=140), result, True, records=store.records())
        rendered = output.getvalue()
        assert "may overlap" in rendered and "1 / 2 (50.00%)" in rendered
        assert ids[2] in rendered and "Outside current eligible: yes" in rendered


def test_multiple_attempts_count_activity_once(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.record_evidence(
            start_attempt(store, uploader, identifier),
            ResponseEvidence(activity_id="22", remote=Remote.COMPLETED),
        )
        record = store.load(identifier)
        first = record.attempts[0]
        second = replace(
            first,
            attempt_id=2,
            upload_id="11",
            activity_id=None,
            duplicate_activity_id="33",
            remote=Remote.DUPLICATE,
        )
        result = snapshot(uploader.manifest, [replace(record, attempts=(first, first, second))])
        assert result.resolved == 1 and result.percent == 100
        assert result.completed == 1 and result.duplicate == 1


def test_limit_and_missing_date_selection(tmp_path: Path, no_network: None) -> None:
    root, ids = multi_workspace(tmp_path, 3)
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.record_evidence(
            start_attempt(store, uploader, ids[2]),
            ResponseEvidence(upload_id="11", remote=Remote.PROCESSING),
        )
    output = invoke(root, "upload", "--limit", "1", "--dry-run").split("Local preview only")[0]
    assert output.count("would_submit") == 1 and output.count("would_observe") == 1
    manifest, _ = MigrationManifest.load(root)
    manifest.activities[0].time.resolved_utc_start = None
    (root / "migration-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    # Use fresh state for a genuinely unavailable date, not a retained valid old date.
    other = tmp_path / "dateless"
    other.mkdir()
    other_root, identifier = workspace(other)
    manifest, _ = MigrationManifest.load(other_root)
    manifest.activities[0].time.resolved_utc_start = None
    (other_root / "migration-manifest.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    filtered = invoke(other_root, "upload", "--all", "--from", "2025-01-01", "--dry-run")
    assert "date_unavailable" in filtered and "without date filters" in " ".join(filtered.split())
    assert "would_submit" not in filtered.split("Local preview only")[0]
    assert "would_submit" in invoke(other_root, "upload", "--activity-id", identifier, "--dry-run")


@pytest.mark.parametrize(
    "state", ["uncertain", "processing", "completed", "duplicate", "corrected"]
)
def test_force_reset_preserves_evidence_and_action(
    tmp_path: Path, no_network: None, state: str
) -> None:
    root, identifier = workspace(tmp_path)
    path = root / "migration-state.sqlite3"
    with UploadStateStore(path) as store:
        uploader = Uploader(root, store)
        if state == "corrected":
            store.set_blocker(identifier, "activity", Code.MISSING_FIT)
        else:
            attempt = start_attempt(store, uploader, identifier)
            if state == "uncertain":
                store.record_uncertain(attempt)
            else:
                store.record_evidence(
                    attempt,
                    ResponseEvidence(
                        upload_id="11",
                        activity_id="22" if state == "completed" else None,
                        duplicate_activity_id="22" if state == "duplicate" else None,
                        remote=(
                            Remote.COMPLETED
                            if state == "completed"
                            else Remote.DUPLICATE if state == "duplicate" else Remote.PROCESSING
                        ),
                    ),
                )
        before = store.load(identifier).attempts
    plain = invoke(root, "reset", "--activity-id", identifier)
    forced = invoke(root, "reset", "--activity-id", identifier, "--force")
    assert plain == forced[forced.index("Safest local action(s):") :]
    assert "cannot override recovery protection" in forced
    with UploadStateStore(path) as store:
        assert store.load(identifier).attempts == before


def test_private_legacy_diagnostics_never_rendered(tmp_path: Path, no_network: None) -> None:
    root, identifier = workspace(tmp_path)
    marker = "SYNTHETIC_PRIVATE_RESPONSE_TOKEN"
    legacy(
        root / "migration-state.sqlite3",
        stable_activity_id=identifier,
        status="uncertain",
        attempt_count=1,
        last_error_message=marker,
    )
    output = invoke(root, "status", "--details") + invoke(root, "upload", "--all", "--dry-run")
    assert marker not in output
    assert "history_unknown" in output
    assert "not_started" in output and "uncertain" in output


def test_dateless_orphan_preview_and_status(tmp_path: Path, no_network: None) -> None:
    root, identifier = workspace(tmp_path)
    manifest, _ = MigrationManifest.load(root)
    manifest.activities[0].time.resolved_utc_start = None
    (root / "migration-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        store.record_evidence(
            start_attempt(store, uploader, identifier),
            ResponseEvidence(upload_id="11", remote=Remote.PROCESSING),
        )
    manifest.activities.clear()
    (root / "migration-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    (root / "fits/activity.fit").unlink()
    filtered = invoke(root, "upload", "--all", "--from", "2025-01-01", "--dry-run").split(
        "Local preview only"
    )[0]
    assert "date_unavailable" in filtered and "would_observe" not in filtered
    assert "would_observe" in invoke(root, "upload", "--all", "--dry-run")
    details = " ".join(invoke(root, "status", "--details").split())
    assert "Activity date: unavailable" in details
    assert "Outside current eligible: yes; outside manifest: yes" in details


def test_reporting_does_not_expose_private_metadata_or_markup(tmp_path: Path) -> None:
    root, identifier = workspace(tmp_path)
    marker = "SYNTHETIC_PRIVATE_TOKEN_BODY_PATH"
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        record = replace(
            store.load(identifier),
            fit_relative_path=marker,
            sport=marker,
            resolved_utc_start=marker,
        )
        output = StringIO()
        console = Console(file=output, width=140)
        print_status(console, snapshot(uploader.manifest, [record]), True, records=[record])
        renderer = RecoveryProgressRenderer(uploader.manifest, lambda: (record,))
        renderer.update(
            RecoveryEvent("[bold]literal-id[/bold]\x1b", Action.REVIEW, (Code.HISTORY_UNKNOWN,))
        )
        console.print(renderer.render())
        assert marker not in output.getvalue()
        assert "[bold]literal-id[/bold]" in output.getvalue()
        assert "\x1b" not in output.getvalue()


@pytest.mark.parametrize(
    "outcome", ["completed", "duplicate", "review", "artifact", "orphan", "rate"]
)
def test_progress_renderer_with_mocked_orchestration(tmp_path: Path, outcome: str) -> None:
    root, identifier = workspace(tmp_path)
    client = Pipeline(Clock())
    with UploadStateStore(root / "migration-state.sqlite3") as store:
        uploader = Uploader(root, store)
        renderer = RecoveryProgressRenderer(uploader.manifest, store.records)
        candidate = uploader.select()[0]
        renderer.update(RecoveryEvent(identifier, candidate.kind, candidate.reasons))
        assert renderer.current and renderer.current.action == Action.SUBMIT
        run = engine(root, store, client)
        events: list[RecoveryEvent] = []

        def update(event: RecoveryEvent) -> None:
            events.append(event)
            renderer.update(event)

        run.on_event = update
        if outcome == "duplicate":
            client.observe = lambda i: ResponseEvidence(
                upload_id=i, duplicate_activity_id="22", remote=Remote.DUPLICATE
            )
        elif outcome == "review":
            client.observe = lambda i: ResponseEvidence(
                upload_id=i, remote=Remote.PROCESSING_FAILED, code=Code.PROCESSING_ERROR
            )
        elif outcome == "artifact":
            (root / "fits/activity.fit").unlink()
        elif outcome == "rate":
            client.rate_limit = RateLimit(
                short_limit=100, short_usage=0, daily_limit=100, daily_usage=95
            )
        elif outcome == "orphan":
            run.submit(identifier)
            manifest = uploader.manifest.model_copy(deep=True)
            manifest.activities.clear()
            (root / "migration-manifest.json").write_text(
                manifest.model_dump_json(), encoding="utf-8"
            )
            uploader = Uploader(root, store)
            renderer.manifest = manifest
        run.run(uploader.select())
        assert events and all(event.identifier == identifier for event in events)
        if outcome == "rate":
            assert any(Code.RATE_LIMIT in e.reason_codes for e in events)
        rendered = StringIO()
        Console(file=rendered, width=150).print(renderer.render())
        assert identifier in rendered.getvalue()
        assert "synthetic" not in rendered.getvalue().lower()


def test_renderer_reads_current_rate_snapshot_through_final_render() -> None:
    manifest = MigrationManifest(manifest_version=1, activities=[])
    rate: RateLimit | None = None
    renderer = RecoveryProgressRenderer(manifest, lambda: (), rate_supplier=lambda: rate)

    def rendered() -> str:
        output = StringIO()
        Console(file=output, width=200).print(renderer.render())
        return output.getvalue()

    assert "API 15 min" not in rendered()
    rate = RateLimit(
        short_limit=600,
        daily_limit=30000,
        short_usage=12,
        daily_usage=345,
        read_short_limit=300,
        read_daily_limit=15000,
        read_short_usage=3,
        read_daily_usage=44,
    )
    text = rendered()
    for expected in (
        "API 15 min",
        "API daily",
        "Read 15 min",
        "Read daily",
        "12 / 600",
        "345 / 30000",
        "3 / 300",
        "44 / 15000",
    ):
        assert expected in text
    rate = rate.model_copy(update={"short_usage": 14, "daily_usage": 347})
    assert "14 / 600" in rendered() and "12 / 600" not in rendered()
    renderer.update(RecoveryEvent("synthetic", Action.RESOLVED, (), batch_unattempted=0))
    text = rendered()
    assert "Batch finished" in text and "347 / 30000" in text
    rate = None
    assert "API 15 min" not in rendered() and "Read daily" not in rendered()


def test_renderer_without_rate_supplier_preserves_missing_rate_output() -> None:
    renderer = RecoveryProgressRenderer(
        MigrationManifest(manifest_version=1, activities=[]), lambda: ()
    )
    output = StringIO()
    Console(file=output).print(renderer.render())
    assert "API 15 min" not in output.getvalue()
    assert "API daily" not in output.getvalue()
