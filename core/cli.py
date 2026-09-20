"""Command line interface."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from config.loader import load_config
from core.errors import PolarToStravaError
from core.logging import configure_logging
from polar import PolarImporter
from services import ActivityValidator, ConversionService

app = typer.Typer(help="Convert Polar Flow exports to Strava-ready activities.")
console = Console()


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
