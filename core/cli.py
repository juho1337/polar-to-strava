"""Command line interface."""

import time
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live

from config.loader import load_config
from core.audit_progress import AuditProgressRenderer
from core.errors import PolarToStravaError
from core.logging import configure_logging
from polar import PolarImporter
from services import ActivityValidator, ConversionService
from services.audit import MigrationAudit
from strava.client import StravaClient, TokenStore, authorization_url, credentials
from strava.models import ManifestActivity, MigrationManifest
from strava.progress import print_status, progress_table, snapshot
from strava.rate_limit import RateLimitPolicy, RateWait
from strava.state import UploadStateStore
from strava.uploader import Uploader

app = typer.Typer(help="Convert Polar Flow exports to Strava-ready activities.")
strava_app = typer.Typer(help="Authenticate and upload a migration workspace safely.")
app.add_typer(strava_app, name="strava")
console = Console()


def _state_store(workspace: Path) -> UploadStateStore:
    return UploadStateStore(workspace / "migration-state.sqlite3")


@strava_app.command("auth")
def strava_auth(
    workspace: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    redirect_uri: Annotated[str, typer.Option("--redirect-uri")] = "http://localhost",
    code: Annotated[str | None, typer.Option("--code", hidden=True)] = None,
) -> None:
    """Authorize this local workspace using Strava OAuth."""
    client_id, client_secret = credentials()
    url, _ = authorization_url(client_id, redirect_uri)
    console.print("Open this URL and authorize activity:write access:")
    console.print(url)
    authorization_code = code or typer.prompt("Authorization code", hide_input=True)
    granted_scope = typer.prompt("Granted scope from the redirect URL")
    client = StravaClient(client_id, client_secret, TokenStore(workspace / ".strava-tokens.json"))
    client.exchange_code(authorization_code, granted_scope)
    console.print("Strava authorization saved locally.")


@strava_app.command("upload")
def strava_upload(
    workspace: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    limit: Annotated[int | None, typer.Option("--limit", min=1)] = None,
    activity_id: Annotated[str | None, typer.Option("--activity-id")] = None,
    all_activities: Annotated[bool, typer.Option("--all")] = False,
    from_date: Annotated[str | None, typer.Option("--from")] = None,
    to_date: Annotated[str | None, typer.Option("--to")] = None,
    max_in_flight: Annotated[int, typer.Option("--max-in-flight", min=1, max=10)] = 3,
    rate_limit_reserve: Annotated[int, typer.Option("--rate-limit-reserve", min=0)] = 10,
) -> None:
    """Upload eligible manifest activities with persistent resume state."""
    if sum((limit is not None, activity_id is not None, all_activities)) != 1:
        raise typer.BadParameter("Choose exactly one of --limit, --activity-id, or --all")
    try:
        parsed_from = date.fromisoformat(from_date) if from_date else None
        parsed_to = date.fromisoformat(to_date) if to_date else None
    except ValueError as error:
        raise typer.BadParameter("Dates must use YYYY-MM-DD") from error
    try:
        with _state_store(workspace) as store:
            client = None
            if not dry_run:
                client_id, client_secret = credentials()
                client = StravaClient(
                    client_id, client_secret, TokenStore(workspace / ".strava-tokens.json")
                )
            uploader = Uploader(workspace, store, client)
            selected = uploader.select(
                limit=limit,
                activity_id=activity_id,
                from_date=parsed_from,
                to_date=parsed_to,
            )
            initial = snapshot(uploader.manifest, store.summary())
            console.print(
                f"Manifest {len(uploader.manifest.activities)}; eligible {initial.eligible}; "
                f"selected {len(selected)}."
            )
            if dry_run:
                for index, item in enumerate(selected, 1):
                    assert item.time.resolved_utc_start is not None
                    console.print(
                        f"[{index}/{len(selected)}] {item.time.resolved_utc_start.date()} "
                        f"{item.sport.domain} would_upload"
                    )
            else:
                live: Live | None = None

                def rate_wait(wait: RateWait) -> None:
                    console.print(
                        f"API safety reserve reached; waiting until {wait.resume_at.isoformat()}."
                    )

                def update(activity: ManifestActivity | None, event: str) -> None:
                    current = None
                    if activity is not None:
                        current = f"{activity.sport.domain} - {event}"
                    current_progress = snapshot(uploader.manifest, store.summary())
                    if live is not None:
                        live.update(
                            progress_table(
                                current_progress,
                                run_done=current_progress.resolved - initial.resolved,
                                run_total=len(selected),
                                rate=client.rate_limit if client else None,
                                current=current,
                            )
                        )

                policy = RateLimitPolicy(
                    reserve=rate_limit_reserve, sleep=time.sleep, on_wait=rate_wait
                )
                uploader.max_in_flight = max_in_flight
                uploader.rate_policy = policy
                uploader.on_progress = update
                if console.is_terminal:
                    live = Live(
                        progress_table(initial, run_done=0, run_total=len(selected)),
                        console=console,
                        refresh_per_second=4,
                    )
                    with live:
                        uploader.run(selected)
                else:
                    uploader.run(selected)
                final = snapshot(uploader.manifest, store.summary())
                if not console.is_terminal:
                    print_status(console, final, details=True)
                if uploader.last_stop_reason:
                    console.print(f"Migration paused safely: {uploader.last_stop_reason}")
                    console.print(f'Resume with: python main.py strava upload "{workspace}" --all')
    except PolarToStravaError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error


@strava_app.command("status")
def strava_status(
    workspace: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    details: Annotated[bool, typer.Option("--details")] = False,
) -> None:
    """Show local upload state without accessing Strava."""
    manifest, fingerprint = MigrationManifest.load(workspace)
    with _state_store(workspace) as store:
        store.reconcile(manifest, fingerprint)
        current = snapshot(manifest, store.summary())
    print_status(console, current, details)


@strava_app.command("reset")
def strava_reset(
    workspace: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    activity_id: Annotated[str, typer.Option("--activity-id")],
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Reset selected local state; never delete a remote activity."""
    manifest, fingerprint = MigrationManifest.load(workspace)
    with _state_store(workspace) as store:
        store.reconcile(manifest, fingerprint)
        store.reset(activity_id, force)
    console.print("Local upload state reset.")


@app.command()
def audit(
    input: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o")],
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    config: Annotated[Path | None, typer.Option("--config", exists=True, dir_okay=False)] = None,
) -> None:
    """Convert workouts to FIT and write a complete migration audit."""
    if output.exists() and not output.is_dir():
        raise typer.BadParameter("Audit output must be a directory.")
    progress = AuditProgressRenderer(console, output)
    try:
        with progress:
            report = MigrationAudit(PolarImporter(), ActivityValidator()).run(
                input, output, overwrite, config, progress.update
            )
    except PolarToStravaError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error
    summary = report["summary"]
    progress.print_summary(report)
    if summary["failed"]:
        raise typer.Exit(code=1)


@app.command()
def scan(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path("config.yaml"),
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """List every Polar activity JSON file in the configured export."""
    configure_logging(verbose)
    try:
        settings = load_config(config)
        service = ConversionService(PolarImporter(), ActivityValidator())
        paths = service.scan_folder(settings.polar_export)
    except PolarToStravaError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error
    for path in paths:
        console.print(path)
    console.print(f"Found {len(paths)} activity file(s).")


@app.command()
def convert(
    input: Annotated[Path, typer.Argument(exists=True)],
    output: Annotated[Path, typer.Option("--output", "-o")],
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    format: Annotated[str, typer.Option("--format", help="tcx (default) or fit")] = "tcx",
) -> None:
    """Convert a Polar activity file or export directory to TCX or FIT."""
    if format not in ("tcx", "fit"):
        raise typer.BadParameter("Format must be tcx or fit.")
    service = ConversionService(PolarImporter(), ActivityValidator())
    try:
        if input.is_dir():
            if output.exists() and not output.is_dir():
                raise typer.BadParameter("Directory input requires an output directory.")
            paths, issues = service.convert_folder(input, output, overwrite, format)
            for path in paths:
                console.print(f"Converted: {path}")
            for issue in issues:
                console.print(f"{issue.severity.value}: {issue.message}")
            failures = sum(issue.severity.value == "error" for issue in issues)
            console.print(
                f"Converted {len(paths)}; warnings {len(issues) - failures}; failures {failures}."
            )
            if failures:
                raise typer.Exit(code=1)
        else:
            if output.exists() and output.is_dir():
                raise typer.BadParameter(f"File input requires a .{format} output file.")
            if output.suffix.lower() != f".{format}":
                raise typer.BadParameter(f"Output must have a .{format} extension.")
            issues = service.convert_file(input, output, overwrite, format)
            console.print(f"Converted: {output}")
            for issue in issues:
                console.print(f"{issue.severity.value}: {issue.message}")
    except PolarToStravaError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error


@app.command()
def inspect(input: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """Report measurements found in one Polar activity."""
    service = ConversionService(PolarImporter(), ActivityValidator())
    try:
        activity, issues = service.import_one(input)
    except PolarToStravaError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error
    points = activity.trackpoints
    counts = {
        "GPS": sum(point.location is not None for point in points),
        "heart rate": sum(point.heart_rate is not None for point in points),
        "cadence": sum(point.cadence is not None for point in points),
        "power": sum(point.power is not None for point in points),
        "altitude": sum(point.recorded_altitude_m is not None for point in points),
    }
    console.print(f"Date/time: {activity.started_at.isoformat()}")
    console.print(
        f"Sport: {activity.sport.value}; recorded duration: "
        f"{activity.recorded_duration_s if activity.recorded_duration_s is not None else activity.duration.total_seconds()} s; "
        f"elapsed duration: {activity.duration}; distance: {activity.distance_m}"
    )
    console.print(f"Laps: {len(activity.laps)}; trackpoints: {len(points)}")
    for name, count in counts.items():
        console.print(f"{name}: {count}")
    for issue in issues:
        console.print(f"{issue.severity.value}: {issue.message}")
