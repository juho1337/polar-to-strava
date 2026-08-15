"""Command line interface."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from config.loader import load_config
from core.logging import configure_logging
from polar.scanner import scan_activities

app = typer.Typer(help="Convert Polar Flow exports to Strava-ready activities.")
console = Console()


@app.command()
def scan(config: Annotated[Path, typer.Option(exists=True, dir_okay=False)] = Path("config.yaml"), verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    """List every Polar activity JSON file in the configured export."""
    configure_logging(verbose)
    activities = scan_activities(load_config(config).polar_export)
    for activity in activities:
        console.print(activity)
    console.print(f"Found {len(activities)} activity file(s).")
