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
- [Change process and specifications](specs/README.md): risk tiers and the spec template.
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

How much has to be written down before implementing depends on risk (details and the spec
template in [specs/README.md](specs/README.md)):

- **Routine changes** (clear bug fixes, refactors without semantic impact, docs, tests,
  tooling): implement, test, open a PR. No spec, no approval gate.
- **Normal features**: a GitHub issue with concise acceptance criteria, then
  implementation, tests and Codex review. No separate spec unless material behavior is
  still unresolved.
- **Safety-critical changes** (upload recovery and retries, duplicate safety, FIT/TCX
  integrity, timezones and timestamps, credentials, compatibility-sensitive changes):
  a written spec approved by the owner before implementation, and full verification.
  SPEC-001 to SPEC-004 stay in force.

Delivery is always: implement, test, PR, Codex review, fix, owner merges. Inspect the
existing code, tests and [architecture](docs/architecture.md) first; where code and an
approved spec disagree, decide explicitly which one is wrong instead of letting either
win silently. Do not invent missing behavioral requirements for safety-critical work;
surface the decision. Tests are evidence, not a replacement for intended behavior. Strict
test-first development is not required.

Run the checks in [Testing](docs/testing.md), review the full diff and `git status` for
private or generated files, and report what changed, what was validated, limitations,
and any security, privacy or compatibility implications. Update architecture or user
documentation when behavior changes.

Releases, remote settings changes and destructive actions require an explicit request.
Pushing a feature branch and opening or updating its pull request is part of the
[AI-assisted workflow](#ai-assisted-workflow-and-review) below; merging is never part of
it. Humans and different agents must be able to contribute without a particular provider,
IDE, plugin or proprietary workflow.

## AI-assisted workflow and review

Roles: **Claude Code** is the primary developer (architecture, implementation, tests,
documentation, issues, pull requests, addressing review findings, requesting re-reviews).
**Codex** is the independent reviewer. The **repository owner** gives final approval,
merges pull requests and authorizes real external operations (live Strava uploads, OAuth).
An agent must never merge its own pull request.

- Use feature branches. Prefer substantial, coherent pull requests over many small ones,
  each linked to its issue and filled in with the pull request template.
- Stack pull requests only when a real dependency requires it; document the dependency
  (base branch, required order) in the description. Do not change the same code in
  conflicting branches, and do not add unrelated work to an open pull request.
- Claude may keep implementing independent work while other pull requests are in review
  and does not need approval for routine implementation and testing decisions.
- Run the checks in [Testing](docs/testing.md) before opening or updating a pull request,
  and keep the description and test evidence current.
- Prefer substantial pull requests. Every substantial pull request needs an independent
  Codex review (automatic, or comment
  `@codex review`; use `@codex security review` for credential, OAuth, process or file
  handling changes). Do not request another review of unchanged code. An unreviewed pull
  request is not ready to merge.
- Address every confirmed critical and high (P0/P1) finding. Evaluate medium (P2) findings
  and record any justified exception in the pull request, replying to a rejected finding
  with the reason. Re-run tests afterwards and request another review when the change
  materially affects reviewed behavior.
- Automated tests use synthetic data, mock Strava and never perform live uploads. Add a
  regression test for each fix.
- What GitHub can and cannot enforce (branch protection, Codex review) is described in
  [GitHub settings](docs/github-settings.md).

## Review guidelines

For Codex pull request reviews. Report actionable findings with severity (P0 to P3), the
affected file and line, and the failure scenario. Do not report style preferences as
defects. Priorities, highest first:

1. FIT/TCX output correctness (fields, units, record ordering, lap and summary integrity).
2. Polar activity parsing (malformed or partial files, units, missing optional data).
3. Data integrity and timestamp preservation (no guessed timestamps or timezone offsets,
   no clamped or invented values, DST edges).
4. Performance regressions (audit throughput, memory, worker pool behavior).
5. Strava API behavior (scopes, rate limits, response handling, recovery states).
6. Duplicate and retry safety (uncertain uploads never blindly resent, state persisted
   around each request, no automatic merge of suspected duplicates).
7. JSON Lines engine compatibility (contract version, event shapes, stdout purity,
   cancellation, exit codes) with the JuhoRunLab desktop host.
8. CLI backward compatibility (commands, options, output, exit codes, config files).
9. Executable packaging (PyInstaller build reproducibility, bundled data, spawn safety).
10. Automated test coverage of new behavior, failure paths and edge cases.

Also check credential and personal-data handling (tokens or private exports in code, logs,
reports, fixtures or error messages), path traversal and malformed file input, and
data-loss or corruption risks. See [SECURITY.md](SECURITY.md).
