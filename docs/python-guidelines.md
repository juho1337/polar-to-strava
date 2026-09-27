# Python development guidelines

These are project conventions, grounded in the existing code. Read the
[architecture](architecture.md) before changing boundaries and [testing guide](testing.md)
for validation. `pyproject.toml` is authoritative for supported Python, dependencies,
packaging and tool settings.

## Runtime, typing and tooling

Python **3.12 or newer** is required; the code uses 3.12 generic syntax. Black and Ruff
target `py312` with line length 100. Ruff enables `E`, `F`, `I`, `UP`, `B` and ignores
`E501`. Mypy is strict, targeting Python 3.12. Its configured package list omits
`serialization` and tests, whereas the documented `mypy .` command supplies the whole
checkout explicitly. Do not assume running bare `mypy` checks the same scope.

Annotate public functions, service/protocol boundaries, domain models and API values.
Use `Path`, `datetime`, enums and specific models rather than unstructured strings
where those types represent the contract. Existing source parsing and audit reporting
use `Mapping[str, Any]`/`dict[str, Any]`; narrow untrusted values before use instead of
pretending those payloads are statically validated. Avoid spreading `Any` into new
interfaces. Keep justified, narrow suppressions such as fit-tool's `import-untyped`;
do not disable checks globally to hide errors.

## Models and composition

- Pydantic domain models are frozen with `extra="forbid"`; tuples and validated frozen
  mappings protect observations from accidental mutation during conversion. Preserve
  that behavior. `model_copy(update=...)` does not validate new values, so it must not
  be used to bypass validation of external data.
- Configuration models reject unknown keys. Manifest/API envelopes generally ignore
  extra fields for compatibility, while token storage accepts only the minimal token
  fields. These distinct policies are intentional boundaries, not one universal default.
- Frozen slotted dataclasses represent results, validation issues and progress values.
  Mutable dataclasses such as `ProcessingJob` hold scheduler state. Use `StrEnum` for
  established states/phases and `Protocol` for injectable importer, validator, serializer
  and upload-client contracts. There is no established `TypedDict` convention here.
- Keep units cohesive and responsibilities explicit; compose existing builders, writers
  and services. Prefer deterministic functions for mapping, hashing and classification,
  with I/O at named boundaries. Do not introduce speculative abstractions or arbitrary
  limits on function length. Inject clients, clocks and sleep functions for testability.

## Files and persistence

Use `pathlib.Path` and explicit UTF-8 text encoding. Resolve paths according to the
existing interface: YAML paths relative to the YAML file; manifest FIT paths relative
to the selected workspace. Never hard-code a user's filesystem paths. Keep generated
artifacts in explicitly chosen output locations and source data unchanged.

Preserve explicit overwrite behavior at service/CLI boundaries. Writers currently
overwrite bytes; report generation is not transactional. Do not describe those writes
as atomic. Token persistence uses temporary-file replacement; SQLite uses parameterized
statements and transactions in `UploadStateStore`. Use that store for upload state,
not ad hoc database edits. Retain IDs/history when reconciling manifests, and preserve
state ordering around external effects: `uploading` before POST, upload ID and
`processing` before polling. Review [recovery limitations](architecture.md#uploader-and-persistence)
before altering retries or reset behavior.

## Time and measurements

Domain timestamps must be timezone-aware. Apply verified Polar fixed offsets or explicit
per-activity configuration; never use the machine timezone, GPS location or a DST guess
to fill ambiguity. Preserve source local time separately from the resolved instant.
Use UTC conversion at export/API boundaries; distinguish recorded timer duration from
elapsed start/end duration. Protect offset resolution, relative times, duplicate
timestamps and precision changes with regressions.

Preserve `None` for missing measurements; zero is a value. Respect units and the existing
ordinal stream merge, altitude precedence and unresolved power policy. FIT whole-second
rounding and sensor scaling are explicit format constraints. Do not silently interpolate,
clamp, discard or manufacture samples. Existing TCX required-summary fallbacks are
documented format behavior, not permission to invent sensor observations.

## Exceptions and diagnostics

Use `core.errors` and Polar-specific errors at established boundaries. Distinguish
load/parse errors, invalid or unsupported source semantics, export failures and API
failures. `ActivityValidator` returns structured issues; TCX export validation raises
`TCXValidationError`. Not all validators have an exception-free contract.

Catch specific errors where practical, preserve causes and useful safe context, and
avoid silent broad exception swallowing. Existing broad catches in batch conversion and
audit isolate individual failures into structured results; preserve that reporting when
working there. `StravaAPIError` is a separate exception with category, retryability and
HTTP status; it is not a subclass of the application's base error. CLI error handling
does not uniformly catch every possible failure.

Never include secrets in public-facing exceptions, logs, reports or test fixtures. OAuth
errors intentionally suppress payload details. Stripping HTML from remote error text is
not general secret redaction. Source errors and report paths may contain private data;
sanitize reproductions before sharing.

## HTTP, retries and output

Keep HTTP behind `StravaClient` and its injectable HTTPX client. The default timeout is
30 seconds. Preserve bounded retry behavior and the distinction between known retryable
errors, permanent errors and uncertain POST outcomes. Never automatically resend an
uncertain non-idempotent operation. Respect observed overall/read limits and reserve
policy; do not hard-code personal quotas. OAuth refresh is part of client access-token
handling, not a separate CLI upload engine.

Use service progress events/callbacks for application integration. Keep Rich user output,
logging diagnostics and durable reports conceptually separate; terminal text is not an
API. Avoid a line per activity during large audits. Audit's nonterminal renderer already
bounds output by phase. Follow existing progress tests when altering presentation.

## Dependencies and packaging

Prefer existing capabilities, then the standard library where suitable; use a mature
maintained library when it materially simplifies the task. A new dependency needs a
reason, maintenance review and license compatibility review for this **GPL-3.0-only**
project. Avoid dependencies for trivial helpers. Do not treat package license metadata
as a legal conclusion about an external protocol.

Current runtime roles: Pydantic validates values, PyYAML loads scanner settings,
Typer/Rich provide the CLI, lxml builds/validates TCX, fit-tool encodes/decodes FIT, and
HTTPX handles Strava HTTP. SQLite, hashing and process workers use the standard library.
Install with `python -m pip install -e ".[dev]"` for development. `requirements.txt` is
a legacy list and currently omits fit-tool; it is not a complete mirror of project
metadata. Setuptools lists packages explicitly and bundles TCX XSD files: new packages
or runtime resources require packaging review. Avoid unrelated tool-setting changes.

## Official reference material

Project rules above are sufficient to understand the conventions; these official sources
provide further detail rather than replacing repository policy:

- [PEP 8](https://peps.python.org/pep-0008/) and Python
  [typing](https://docs.python.org/3/library/typing.html),
  [pathlib](https://docs.python.org/3/library/pathlib.html),
  [datetime](https://docs.python.org/3/library/datetime.html),
  [exceptions](https://docs.python.org/3/tutorial/errors.html) and
  [sqlite3](https://docs.python.org/3/library/sqlite3.html).
- [PyPA pyproject guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/).
- [Pydantic](https://docs.pydantic.dev/) and [HTTPX](https://www.python-httpx.org/).
- [pytest](https://docs.pytest.org/), [mypy](https://mypy.readthedocs.io/),
  [Ruff](https://docs.astral.sh/ruff/) and [Black](https://black.readthedocs.io/).
