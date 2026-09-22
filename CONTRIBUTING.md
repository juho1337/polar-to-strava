# Contributing

Contributions are welcome for discussion and review. Before sending a change:

1. Open an issue for significant behavior or format changes.
2. Never attach real Polar exports, FIT histories, Strava tokens, credentials, or other
   personal training data.
3. Use synthetic or thoroughly sanitized fixtures.
4. Keep changes focused and document user-visible behavior.
5. Run the quality checks below.

## Development setup

```text
python -m venv .venv
python -m pip install -e ".[dev]"
pytest
ruff check .
black --check .
mypy .
```

Python 3.12 or newer is required. Follow the existing domain, importer, validation,
serialization, and manifest boundaries. Tests must not call the real Strava API or use
real credentials.

## Licensing status

The project is licensed under the GNU General Public License version 3. By submitting a
contribution, you agree that it is provided under the project's GPLv3 license. The project
does not require a contributor license agreement or copyright assignment.
