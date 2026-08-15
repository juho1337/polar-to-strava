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
