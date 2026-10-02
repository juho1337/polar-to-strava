# Repository instructions

This project migrates personal Polar training history through local FIT artifacts to
Strava. Data integrity takes precedence over convenience. Inspect the existing
implementation before modifying it. Code and current architecture documentation describe
existing reality; an approved specification defines the intended target for its scoped
change. Investigate conflicts explicitly: expected code change, mistaken spec assumption,
or missed compatibility requirement. Do not silently let either side win.

## Read first

- [Architecture](docs/architecture.md): boundaries, data flow, invariants and limitations.
- [Python guidelines](docs/python-guidelines.md): project conventions and dependencies.
- [Testing](docs/testing.md): regression strategy and required validation commands.
- [Specifications](specs/README.md): SDD scope, authority, lifecycle, template and review.
  Read existing specifications relevant to the task.
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

Specification-Driven Development is preferred for substantial features, behavioral or
architecture changes, non-trivial semantic bugs and compatibility/safety-sensitive work.
Use the proportionality rules in [specs/README.md](specs/README.md); routine documentation,
formatting and other trivial nonsemantic work normally need no dedicated spec.

1. Understand requirements, inspect code/tests and read relevant architecture and specs.
   Determine whether a dedicated spec is needed; create/update it when appropriate.
2. Resolve material open questions, review architecture impact and define uniquely
   identified acceptance criteria. Obtain approval of intended behavior before substantial
   implementation, then create a proportional implementation plan.
3. Implement the smallest coherent change satisfying the approved spec. Follow existing
   typing/model/error conventions; justify dependencies and GPL-3.0-only compatibility.
   Add/update tests from acceptance criteria, edge cases and failure/compatibility/safety
   requirements. Tests may be written before, alongside or after a small implementation
   step; strict test-first development is not required. Never weaken coverage to pass.
4. Run focused checks, then the full checks in [Testing](docs/testing.md). Review the
   complete diff and `git status` for correctness, boundaries, error handling, API safety,
   resumability, compatibility, complexity and private/generated files.
5. Perform the [specification compliance review](specs/README.md#final-specification-compliance-review),
   update current architecture/user docs where behavior changed and record implementation
   references, per-criterion verification, deviations and follow-ups in the spec.
6. Report files/reasons, spec ID when applicable, architectural decisions, validation,
   compliance outcome, deviations, limitations, security/privacy and compatibility
   implications, and migration semantics affected or explicitly unchanged.

For a small bug with clear existing intended behavior: reproduce, find the root cause,
add a regression where practical, fix minimally and verify; a new spec is not necessarily
needed. Semantic or ambiguous bugs follow the specified workflow above.

Do not treat a user request as permission to invent missing behavioral requirements.
Investigate materially ambiguous behavior, surface the decision and update the spec.
If a spec proves incorrect, incomplete or impossible, stop affected implementation and
review/approve its revision before continuing. Tests are evidence, not a substitute for
the specification or permission to silently reinterpret acceptance criteria.

Keep Git changes scoped. Pushes, merges, releases, remote settings changes and destructive
actions require an explicit request; a request to edit code does not authorize them.

Repository specifications and project instructions take precedence over generic agent
workflows. External research, planning, debugging, testing and review skills may help
execute approved behavior; optional TDD must not replace SDD or impose strict test-first
ordering. Humans and different agents must be able to contribute without a particular
provider, IDE, plugin or proprietary workflow.
