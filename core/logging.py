"""Logging configuration for command line use."""

import logging
from rich.logging import RichHandler


def configure_logging(verbose: bool = False) -> None:
    """Configure predictable console logging for the application."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s", datefmt="[%X]", handlers=[RichHandler(rich_tracebacks=True)], force=True)
