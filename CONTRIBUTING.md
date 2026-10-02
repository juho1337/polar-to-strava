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

Python 3.12 or newer is required. Before changing code, read the
[architecture](docs/architecture.md), [Python guidelines](docs/python-guidelines.md) and
[testing strategy](docs/testing.md). AI contributors should also follow the root
[AGENTS.md](AGENTS.md). Read [SECURITY.md](SECURITY.md) when handling credentials,
external APIs or personal data.

For substantial behavioral, compatibility, safety or architecture changes, follow
[Specification-Driven Development](specs/README.md): specify intended behavior and
acceptance criteria, review architecture impact and obtain approval, plan, implement,
verify and review specification compliance. Keep plans proportional; routine nonsemantic
corrections and clear small bugs normally need no new spec. Tests remain required
verification, but strict test-first ordering is optional. Link the spec and record
deviations/follow-up work where applicable.

Keep the change scoped, preserve migration and format compatibility, and add focused
regressions for behavioral changes. Review the complete diff and Git status for personal
data, secrets and generated artifacts before committing. Summarize changes, validation
and limitations; call out architecture, manifest, migration or security implications.
Tests must not call the real Strava API or use real credentials. Do not push, merge,
release or modify remote settings unless explicitly requested by the maintainer/user.

## Licensing status

The project is licensed under the GNU General Public License version 3. By submitting a
contribution, you agree that it is provided under the project's GPLv3 license. The project
does not require a contributor license agreement or copyright assignment.

## Recovery changes

SPEC-001 recovery verification must assert durable evidence and actual mocked POST/GET
counts and identities after reopen, not just final labels. Keep the eight crash windows,
legacy routing, local-command network isolation and privacy regressions active. A
passing synthetic suite does not demonstrate live acceptance or hardware power-loss
durability. CI setup remains a separate future task. See the
[release checklist](docs/release.md) for versioning and release validation.
