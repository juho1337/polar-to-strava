# Repository instructions

This project migrates personal Polar training history through local FIT artifacts to
Strava. Data integrity takes precedence over convenience. Inspect the existing
implementation before modifying it; source code is authoritative when documentation
disagrees. Report discrepancies instead of silently changing migration semantics.

## Read first

- [Architecture](docs/architecture.md): boundaries, data flow, invariants and limitations.
- [Python guidelines](docs/python-guidelines.md): project conventions and dependencies.
- [Testing](docs/testing.md): regression strategy and required validation commands.
- Relevant feature documentation linked from the architecture guide.
- [SECURITY.md](SECURITY.md) for credentials, external APIs, security or personal data;
  [CONTRIBUTING.md](CONTRIBUTING.md) for contribution workflow.

## Repository map and boundaries

`core/cli.py` composes the CLI; `polar/` discovers and imports workouts; `domain/`
holds common activity values; `fit/` and `tcx/` build exports; `services/` handles
conversion, audit and manifest generation; `strava/` consumes the prepared workspace
and persists upload state. `config/` loads scanner YAML; `serialization/` defines
serialization protocols. `db/` is a placeholder, not the uploader database layer.

- Keep domain values independent of provider implementations, CLI, filesystem and HTTP.
- Exporters consume domain values; writers persist bytes. Strava upload must not parse
  Polar JSON or reconstruct source timestamps, samples, laps or eligibility.
- Preserve the manifest/workspace boundary, content-based identity and FIT integrity
  checks. The audit is currently Polar-aware; do not assume every service is generic.
- Keep audit events and uploader callbacks usable without terminal parsing. Reuse
  services for future frontends rather than creating another migration engine.
- Do not redesign working architecture or refactor unrelated code without task scope.

## Data and operational safeguards

Do not silently drop supported measurements, invent sensor values, guess timestamps or
timezone offsets, clamp invalid measurements, merge suspected duplicate activities,
substitute sensor streams without justified semantics, hide conversion failures, or
change source meaning merely to satisfy Strava. Preserve, report, require explicit
configuration, mark for review or exclude data according to existing semantics.
Distinguish malformed source, unsupported or ambiguous data, conversion bugs and API
failures. Existing format limitations are documented in the architecture guide.

Never blindly resend an upload with an uncertain outcome. Preserve durable state and
remote IDs; inspect the documented recovery limitations before changing retry logic.
Do not run real migrations, OAuth or uploads unless the user explicitly requests them.

Never commit or unnecessarily expose/log real exports, GPS tracks, training histories,
FIT/TCX outputs, private workspaces, migration state/configuration, personal paths,
credentials, authorization codes or access/refresh tokens. Use synthetic or explicitly
sanitized public-safe fixtures. Secrets must not appear in diagnostics, reports, tests,
documentation or commits. `.gitignore` is not a complete privacy boundary.

## Development workflow

1. Understand the request, inspect implementation and read the relevant documentation.
2. Identify affected boundaries and compatibility, migration, privacy and integrity risks.
3. For behavior changes, define observable behavior and add appropriate tests. For bugs,
   reproduce with a focused regression test where practical, find the cause and retain
   the test. Documentation-only edits need no artificial tests.
4. Make the smallest coherent change. Follow existing typing, model and exception
   conventions; justify new dependencies and check GPL-3.0-only compatibility.
5. Run focused checks while developing, then the full checks in [Testing](docs/testing.md).
   Never weaken tests to make a change pass.
6. Review the complete diff and `git status`: check correctness, boundaries, error
   handling, API safety, resumability, compatibility, unnecessary complexity and private
   or generated files. Report failures and limitations honestly.
7. Report files changed, reasons, architectural decisions, tests/checks and their results,
   limitations, security/privacy and compatibility implications, and migration semantics
   affected or explicitly unchanged.

Keep Git changes scoped. Pushes, merges, releases, remote settings changes and destructive
actions require an explicit request; a request to edit code does not authorize them.

Repository-specific rules govern architecture and safety. External planning, TDD,
debugging and review skills may guide how work is done, but must not redefine these
rules. Humans and different agents must be able to contribute without a particular
provider, IDE, plugin or proprietary workflow.
