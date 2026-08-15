# PolarToStrava

PolarToStrava converts Polar Flow JSON exports into Garmin TCX files, with planned support for the official Strava Upload API.

## Milestone 1

This foundation release loads configuration, recursively discovers `activity-*.json` files, and parses Polar activities into strongly typed Pydantic models. TCX generation and Strava uploading are planned for later milestones.

## Requirements and installation

Python 3.12 or newer is required.

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -e ".[dev]"
```

## Configure and scan

Edit `config.yaml`, then run:

```bash
python main.py scan --config config.yaml
```

Relative paths in configuration resolve relative to the configuration file. Add `--verbose` for debug logging.

## Test

```bash
pytest
```

## Layout

- `polar/` — Polar Flow discovery and parsing
- `tcx/` — future TCX generation
- `strava/` — future Strava Upload API client
- `db/` — future SQLite persistence
- `core/` — CLI and logging
- `config/` — configuration loading
- `tests/` — automated tests
