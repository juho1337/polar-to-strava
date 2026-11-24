# Testing and validation

Tests protect observable migration behavior: source selection, supported measurements,
time interpretation, export semantics, manifest identity/eligibility and upload safety.
They do not prove that every possible Polar export is supported or that Strava will
display an activity in a particular way. See [architecture](architecture.md) for current
implementation limitations and [Python guidelines](python-guidelines.md) for conventions.

For substantial behavioral work, the approved [specification](../specs/README.md) defines
intended observable behavior, implementation provides it, and tests verify it. Tests are
evidence, not a substitute for a behavioral specification. Acceptance criteria and the
spec's edge cases, failure behavior, compatibility, security and data-integrity requirements
drive coverage. Do not turn implementation details into requirements merely because tests
encode them. Important tests may cite spec/AC IDs; one test per criterion is not required.

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
`tests/` with `-ra`; tool settings live in `pyproject.toml`. The `CI` GitHub Actions
workflow (`.github/workflows/ci.yml`) runs the same four commands on pull requests and on
`main`. There is no configured coverage threshold or custom test markers. Do not claim CI
ran merely because local checks passed.

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
| `test_audit_performance.py` | SPEC-003: worker-count output equivalence, single-decode generation, written-vs-verified bytes, full validation of existing FITs, incremental cache hits/invalidation, cancellation, atomic writes, bounded workers |
| `test_integration_engine.py` | SPEC-004: contract envelopes against the JSON Schema, structured errors, CLI parity (status table, audit summary, details), fake-transport upload, cancellation and restart without a second POST, workspace lock (engine and CLI), secrets absence, OAuth state/scope handling and recovery preservation, DPAPI store, timezone list/preview/apply with historical DST, sanitized report export |
| `test_audit_progress.py` | Nonterminal CLI phases, empty/failed audits and completion summaries |
| `test_strava_uploader.py` | Manifest selection, integrity, SQLite/reconciliation/reset, resume/duplicates, bounded observation, uncertain outcomes, OAuth, rate policy, pipeline capacity and local progress/status |

Recovery tests additionally live in `test_strava_state`, `test_strava_recovery`,
`test_strava_responses`, `test_strava_attribution`, `test_strava_artifacts`,
`test_strava_scheduler`, `test_strava_crashes`, `test_strava_transport_integration`
and `test_strava_reporting`. They cover all eight crash windows, private snapshots,
legacy migration, durable uncertainty, bounded GET-only restart, safe reset and local
reporting with fail-fast network isolation. No orchestration test is deferred. CLI tests are distributed among relevant feature modules; there is no standalone
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

For a bug whose intended behavior is already clear: reproduce, find the root cause, add a
focused regression where practical, make the minimal fix and verify. No spec is needed.
If the correct behavior is ambiguous and the change is safety-critical (see
[change process](../specs/README.md)), settle the intended behavior first and do not infer
it solely from a failing test or the current implementation.

Strict test-first development is not required; write tests before, alongside or after the
implementation. Final automated coverage, retained regressions and the complete checks
remain required. Never weaken existing expectations just to pass; intentional changes to
expected behavior follow the issue or approved spec. Documentation-only edits need no
artificial tests.

Examples of focused runs:

```text
python -m pytest tests/test_training_session.py tests/test_sprint81.py
python -m pytest tests/test_fit_export.py tests/test_tcx_integration.py
python -m pytest tests/test_audit.py tests/test_audit_progress.py
python -m pytest tests/test_strava_uploader.py
python -m pytest tests/test_spec002.py tests/test_spec002_capacity.py tests/test_strava_responses.py tests/test_strava_attribution.py tests/test_strava_scheduler.py tests/test_strava_reporting.py
```

Changes to manifests/versions, stable identity, FIT/TCX generation, eligibility, uploader
state, duplicate handling, retries, uncertain outcomes or rate policy need explicit
regression coverage. Test both success and refusal paths, including state retained after
failure. Polling-failure recovery and abrupt process termination now have synthetic
coverage. Hardware power loss remains unproven; byte-identical duplicate sources and
shared lap-boundary samples remain separate limitations.

## Documentation and completion check

Safety-critical work is complete when every acceptance criterion in its spec is covered
by tests (or a justified manual check) and the spec's Completion note links the PR and
lists deviations and follow-ups. Ordinary changes need no per-criterion evidence: the PR
description, its tests and the Codex review are the record. Passing tests alone do not
prove intended behavior, so also look at unplanned behavior, boundaries, compatibility,
privacy and documentation.

Check relative links and referenced paths, CLI commands against actual help/configuration,
and Mermaid diagrams against the implementation. Separate syntax, semantic and remote
acceptance claims. Review the complete diff and `git status` for unintended files, private
paths, secrets, training data and generated artifacts. Report exact checks, outcomes and
limitations. Do not use a real corpus migration, OAuth, upload or destructive Git command
as routine validation.

See the official [pytest documentation](https://docs.pytest.org/) for fixtures and test
selection, and the [tool references](python-guidelines.md#official-reference-material)
for static-analysis/formatting details.
