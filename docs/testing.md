# Testing and validation

Tests protect observable migration behavior: source selection, supported measurements,
time interpretation, export semantics, manifest identity/eligibility and upload safety.
They do not prove that every possible Polar export is supported or that Strava will
display an activity in a particular way. See [architecture](architecture.md) for current
implementation limitations and [Python guidelines](python-guidelines.md) for conventions.

## Setup and required checks

Use Python 3.12+ and an activated project virtual environment, from the repository root:

```text
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
black --check .
mypy .
```

These commands are the completion checks, including for substantial documentation work.
Installation is setup, not something to repeat on every validation run. Pytest discovers
`tests/` with `-ra`; tool settings live in `pyproject.toml`. There are no tracked GitHub
Actions workflows enforcing these checks, and no configured coverage threshold or custom
test markers. Do not claim CI ran merely because local checks passed.

Without activation on Windows, invoke the same tools through the environment:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest
& '.\.venv\Scripts\ruff.exe' check .
& '.\.venv\Scripts\black.exe' --check .
& '.\.venv\Scripts\mypy.exe' .
```

Black supports `--workers 1` when an execution environment restricts worker processes.
Pytest `-p no:cacheprovider` disables its cache without dropping tests. Record any such
environment-specific adjustments and distinguish permission/environment failures from
code failures. Audit tests require local process creation for `ProcessPoolExecutor`.
Never remove those tests or silently replace a full run with a subset.

## Actual organization

Tests are a flat collection of modules, with local helpers and `tests/samples/` fixtures;
there are no separate unit/integration directory trees.

| Modules | Behavior covered |
| --- | --- |
| `test_domain_models.py` | Frozen values/extensions, measurements, aware timestamps, lap bounds/order and indexes |
| `test_parser.py`, `test_training_session.py` | Legacy/sample-stream parsing, missing fields, controlled errors, timezones, multiple exercises, explicit laps and HR preservation |
| `test_scanner.py`, `test_config.py` | Recursive case-insensitive selection/order, daily-file exclusion, YAML path resolution and unknown settings |
| `test_services.py` | Importer injection, structured results and safe validation findings |
| `test_tcx_serialization.py`, `test_tcx_integration.py` | Builder/writer separation, XML/XSD semantics, sport mapping, CLI conversion, overwrite protection and batch failure isolation |
| `test_fit_export.py` | Decoded FIT records/summaries, timestamps, missing measurements, altitude precedence, unresolved left power and format selection |
| `test_sprint81.py` | Route/session bounds, lap reconstruction, ambiguous time, summary-only exclusion and equal-time observations |
| `test_audit.py` | End-to-end local audit, reports, reruns, stable identity, timezone configuration, loss exclusion and progress/manifest independence |
| `test_audit_progress.py` | Nonterminal CLI phases, empty/failed audits and completion summaries |
| `test_strava_uploader.py` | Manifest selection, integrity, SQLite/reconciliation/reset, resume/duplicates, bounded retries, uncertain outcomes, OAuth, rate policy, pipeline capacity and local progress/status |

State, rate-limit and manifest tests currently live mainly in the uploader and audit
modules. CLI tests are distributed among relevant feature modules; there is no standalone
CLI suite. `test_cli_fit_and_tcx_formats` exercises `ConversionService` directly despite
its name. Tests include local end-to-end conversion/audit, not live end-to-end upload.

## Fixtures and isolation

Normal tests must not require personal exports, live credentials or real Strava requests,
and must never upload real activities. Existing fixtures are small representative JSON
structures, including sanitized training-session shapes and malformed inputs. Use
synthetic data or data explicitly sanitized for public distribution; a filename containing
`sanitized` alone is not evidence of privacy. Review timestamps, routes, IDs, devices,
metadata and paths before adding fixtures. Never copy a complete personal export.

Use `tmp_path` for generated JSON, FIT/TCX, manifests, tokens and SQLite state. Existing
uploader tests use `FakeClient`, `FakeClock`, injected sleep/clock functions and
`httpx.MockTransport`. `CliRunner` exercises commands without launching a real terminal.
Stub/lossy importers test service behavior and source-loss reporting. Synthetic token
strings test redaction; they are not real credentials.

Uploader fixture FIT bytes are deliberately placeholders: those tests establish file
hash/selection behavior, not FIT validity. Real generated FIT is decoded in exporter and
audit tests. TCX checks inspect XML and bundled schemas and compare HR timestamps/values.
Keep those semantic assertions; encoder success alone is insufficient.

## Regression workflow

For a bug, reproduce with the smallest focused automated test where practical, identify
the cause, implement the minimal fix, retain the regression, and run focused then full
validation. For new behavior, agree on observable behavior and affected boundaries
before implementation. Never weaken existing expectations just to make a change pass.
Documentation-only edits need no artificial tests.

Examples of focused runs:

```text
python -m pytest tests/test_training_session.py tests/test_sprint81.py
python -m pytest tests/test_fit_export.py tests/test_tcx_integration.py
python -m pytest tests/test_audit.py tests/test_audit_progress.py
python -m pytest tests/test_strava_uploader.py
```

Changes to manifests/versions, stable identity, FIT/TCX generation, eligibility, uploader
state, duplicate handling, retries, uncertain outcomes or rate policy need explicit
regression coverage. Test both success and refusal paths, including state retained after
failure. A passing suite does not establish untested guarantees: polling-failure recovery,
hard process termination, byte-identical duplicate sources and shared lap-boundary samples
need further coverage before changing their documented behavior.

## Documentation and completion review

Check relative links and referenced paths, CLI commands against actual help/configuration,
and Mermaid diagrams against the implementation. Separate syntax, semantic and remote
acceptance claims. Review the complete diff and `git status` for unintended files, private
paths, secrets, training data and generated artifacts. Report exact checks, outcomes and
limitations. Do not use a real corpus migration, OAuth, upload or destructive Git command
as routine validation.

See the official [pytest documentation](https://docs.pytest.org/) for fixtures and test
selection, and the [tool references](python-guidelines.md#official-reference-material)
for static-analysis/formatting details.
