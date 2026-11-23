# SPEC-004: Desktop integration contract

Status: Implemented
Created: 2026-11-24
Approval: 2026-11-24, requesting user approved the specification and its D decisions with the stated recommendations
Related: [SPEC-003](003-audit-performance.md), [parity matrix](../docs/desktop-parity-matrix.md)
Consumer: future TrainingHub (.NET 10 WPF + Blazor Hybrid). No GUI is built in this work.

This specification follows the [SDD workflow](README.md). It defines a stable, versioned,
machine-readable engine interface. The Python engine stays the only migration engine; the
CLI remains operational and unchanged for existing users.

## Problem

The desktop application must not parse human CLI output, must not require environment
variables, manual JSON editing or command-line OAuth, and must expose at least everything
the CLI reports. Today progress is Rich-rendered; credentials come from environment
variables; tokens are plaintext `.strava-tokens.json`; timezone configuration is hand
edited JSON.

## Principles

1. **One engine.** All behavior comes from `services/` and `strava/`; the contract layer
   (`integration/`, new) only adapts them. No reimplementation in C#.
2. **Parity.** Every field the CLI prints has a typed contract field (parity matrix).
   Nothing present in CLI reporting is dropped. Overlapping-category semantics, counts of
   activities vs attempts and the "local evidence only" labelling are preserved verbatim.
3. **Safety first.** The contract exposes only actions the recovery engine supports.
   Uncertain outcomes never become automatic retries; `reset` remains as protected today.
4. **No secrets on the wire.** Tokens, client secret, authorization codes and raw Strava
   response bodies never appear in events, results, errors, logs or exported reports.
5. **Unknown is explicit.** Missing rate-limit headers are `null` with `known:false`,
   never zero.

## Transport

`polar-to-strava-engine <command> --json` (new console script, same package) reads one
JSON request document from stdin (so no secrets in argv/env) and writes **JSON Lines** to
stdout, one envelope per line, UTF-8, flushed per line. Human text and diagnostics go to
stderr only and contain no secrets. Exit codes: 0 success, 1 domain failure (structured
`result`/`error` emitted), 2 invalid request, 3 workspace locked, 130 cancelled.

Envelope (all lines): `{"contract":"1.0","type":"event|result|error","seq":N,"time":"UTC ISO","command":"...","run_id":"..."}`.
`contract` follows semver: additive fields are minor; removals/renames are major; the host
checks the major version via `engine version`.

Cancellation: a `cancel` line on stdin, closing stdin when the request sets
`cancel_on_stdin_close` (amendment: closing stdin alone would cancel every `echo {} | engine ...`
pipeline), or CTRL_BREAK/SIGINT/SIGTERM triggers graceful shutdown: no new work is submitted, in-flight observations/audit tasks drain or persist as
today, durable intent is flushed, then a `result` with `status:"cancelled"` is emitted.
Hard kill is safe because of existing crash-window guarantees (SPEC-001) and atomic audit
outputs (SPEC-003 AC-07).

## Commands

| Command | Purpose | Network | Writes |
| --- | --- | --- | --- |
| `engine version` | contract/engine/tool versions, capabilities | no | no |
| `audit` | audit run with progress events and per-activity results | no | workspace outputs |
| `audit.dry_run` | discovery and eligibility preview without FIT generation | no | none |
| `upload.dry_run` | `strava upload --dry-run` equivalent (local evidence only) | no | none |
| `upload.run` | submissions and known-upload observations (`limit`/`activity_id`/`all`, date range, `max_in_flight`, `rate_limit_reserve`) | yes | recovery DB |
| `status` | `strava status [--details]` equivalent | no | none |
| `activity.list` / `activity.get` | per-activity detail with filters | no | none |
| `reset` | the protected existing reset, returns the safest local actions | no | recovery DB |
| `strava.connection` | connection state, athlete, granted scopes | optional | none |
| `strava.authorize.begin/complete` | OAuth helper (see Strava settings) | yes | token store |
| `strava.disconnect` | forget tokens (recovery history untouched) | no | token store |
| `timezone.list_unresolved` | activities with `requires_configuration` (filters) | no | none |
| `timezone.preview` | resulting UTC for selected offsets/IANA suggestions, validation | no | none |
| `timezone.apply` | write confirmed overrides to migration config, then rerun audit | no | config, audit outputs |
| `report.export` | sanitized report export | no | export file |

`upload.run` is single-writer per workspace (below). `audit.dry_run` and `timezone.preview`
never write.

## Workspace lock (one active uploader)

A lock file `workspace/.migration.lock` is held with an OS-level exclusive lock for the
duration of any writing command (`audit`, `upload.run`, `reset`, `timezone.apply`) and
by the CLI as well, so the CLI and the engine exclude each other. A second writer exits
with code 3 and a structured `workspace_locked` error (including the holder's start time,
never user data). Read-only commands do not take the lock. Stale locks from dead
processes are released by the OS.

## Recovery database

The existing workspace `migration-state.sqlite3` (schema, history, backups, reconciliation)
is unchanged and remains CLI-compatible. The desktop application's own data (settings,
UI state, cached reports, secret references) lives in a **separate** application database
owned by the host; the engine never reads it and the host never writes the recovery DB
directly: it uses engine commands only.

## Events and results

Event `type` values and payloads mirror the CLI exactly:

- `audit.phase` (`discovery_started|discovery_completed|preparing_activities|processing_activities|generating_reports|writing_manifest|complete`), `audit.progress` with
  `completed,total,fit_valid,warnings,failed,skipped_existing, fit_generated, fit_reused, elapsed_s, rate_per_s, eta_s|null` (ETA only under the CLI's reliability rule: >=5 completed and >=2 s).
- `audit.activity` per-activity result row (stage, status, migration status and reason,
  warnings, validation issues, counts per layer, FIT hashes/size).
- `upload.progress` = `ProgressSnapshot` fields (below) plus `run_done,run_total,current`.
- `upload.event` = `RecoveryEvent` (action, reason codes, review upload IDs, next poll,
  polls remaining, failures remaining, batch summary fields).
- `rate.wait` (`seconds`, `resume_at`, `reason`).
- `rate.snapshot` (below).
- `result` per command with a `summary` object and `error` (`code`, `message`, `field`, `details` without secrets).

### Typed snapshots

`ProgressSnapshot`: `eligible, fit_bytes, completed, duplicate, resolved, remaining,
percent, ready_to_submit, observing, needs_review, local_blocked, outside_current_eligible,
outside_manifest, outside_resolved, capacity_jobs, duplicate_review_stops` plus a
`semantics` string array restating overlap/attempt-vs-activity rules. No mutually exclusive
totals are introduced.

`RateSnapshot`: `overall{short{usage,limit,remaining}, daily{...}}`, `read{short, daily}`
each field `null` when unavailable, `known` flags, `reserve`, `max_in_flight`,
`in_flight`, `wait{reason,resume_at}|null`, `polling{next_get_at,polls_remaining,
failures_remaining,backoff}|null`, `observed_at`.

`ActivityDetail`: `stable_activity_id, local_start, resolved_utc_start, sport(polar,
domain,fit), source{path,sha256}, fit{valid,sha256,size,relative_path,reused}, migration{status,reason,
warnings}, validation_issues[], upload{state,remote,submission_evidence,known_upload_ids[],
strava_activity_ids[],blockers[],error_codes[]}, outside_current_eligible,
outside_manifest, date_available, next_actions[]` where `next_actions` is derived only
from `classify_actions`/reset rules and has fixed codes (`submit|observe|review|resolved|reset_available`).

## Strava settings and OAuth (host + engine)

Host owns UX, secure storage and the loopback listener; engine owns protocol logic.

- Client ID/secret are stored by the host with Windows user-scoped DPAPI
  (`ProtectedData`, CurrentUser) and passed to the engine **per request via stdin**.
  The engine accepts credentials from request documents (new) or, unchanged, from the
  environment (CLI). Neither is ever echoed.
- `strava.authorize.begin` returns the authorize URL, a random `state`, requested scopes
  and redirect URI (`http://localhost:<port>/...`); the host opens the browser and runs the
  loopback listener. The host **verifies `state`** before passing `code` to
  `strava.authorize.complete`, which exchanges it and returns athlete identity and granted
  scopes (never tokens). Granted scopes are validated: write operations require
  `activity:write` (existing `REQUIRED_SCOPE`); read-only sync features request only
  `read`/`activity:read[_all]` as enabled. Scope upgrade = reauthorization.
- Token refresh stays inside the engine/token store. Token storage becomes a pluggable
  `TokenStore` protocol; the default plaintext workspace file remains for the CLI.
  The host supplies a DPAPI-protected store. Reauthorization/reconnect/disconnect never
  touch the recovery DB or history.
- The in-app guide links to https://www.strava.com/settings/api and explains creating an
  application and setting the callback domain to `localhost`.

## Timezone correction workflow

- `timezone.list_unresolved`: activities with `migration.status == requires_configuration`
  with date, local start, sport, original timezone information, unresolved reason, filters
  (date range, sport, reason).
- `timezone.preview`: input `{stable_activity_id, offset | iana_zone}` list. Output per
  activity: selected offset (`+HH:MM`, range -14:00..+14:00, existing semantics), resulting
  UTC, warnings/errors. IANA suggestions use `zoneinfo` for the **activity's date** (historical
  DST), requires the user to have explicitly chosen the zone, is never derived from the
  host location, and is refused (`authoritative_timezone`) for activities whose source has
  authoritative timezone data (same rule as the audit's conflict check).
- `timezone.apply`: requires `confirm:true` and the previewed offsets; writes a config
  compatible with `config_version 1` atomically (temp file + replace, existing overrides
  merged, unknown IDs rejected as today), then re-runs the audit for the workspace,
  regenerating needed FITs and the manifest. Stable IDs, source data, upload history and
  recovery state are unchanged; no upload is triggered or retried.

## Reporting export

`report.export` writes JSON/CSV/Markdown built from the same typed snapshots with a
secret-scrubbing allowlist (no token/secret/code/state, no raw responses, no personal
absolute paths unless the user opts in).

## Acceptance criteria

- **AC-01** Contract `1.0` JSONL is documented with JSON Schemas under `docs/contract/`,
  and a schema-conformance test validates every emitted envelope type.
- **AC-02** Parity: every row of the parity matrix has a contract field; a test fails when
  a CLI field lacks a mapping.
- **AC-03** `audit`, `audit.dry_run`, `upload.dry_run`, `upload.run` (fake transport),
  `status`, `activity.*` produce the specified events/results with CLI-equal numbers on the
  same fixture workspace.
- **AC-04** Unknown/missing rate-limit values serialize as `null`/`known:false`.
- **AC-05** Cancellation: audit and fake-transport upload stop gracefully, emit
  `cancelled`, leave no truncated files and no uncertain upload becomes a retry.
- **AC-06** A second writer fails with code 3 `workspace_locked`; CLI and engine exclude
  each other; read-only commands run concurrently.
- **AC-07** No secrets: tests inject sentinel secrets via request/env/token store and
  assert absence from stdout, stderr, exported reports and error details.
- **AC-08** Recovery DB compatibility: existing schema/backup behavior and all current
  recovery/crash tests pass unchanged; the engine writes the DB only via `Uploader`/`UploadStateStore`.
- **AC-09** Strava connection and OAuth commands: state is generated/verified by the
  host contract, scope validation rejects insufficient scope, token/secret never output,
  reauthorization preserves recovery records (fake HTTP only).
- **AC-10** Timezone commands: listing/filters, preview with historical DST (e.g.
  Europe/Helsinki winter vs summer), range validation, authoritative-source refusal,
  confirmation required, config round-trips through `load_migration_config`, rerun
  updates status from `requires_configuration` to eligible, stable IDs unchanged.
- **AC-11** CLI behavior and output unchanged (existing tests pass).
- **AC-13** No loss of existing CLI operational visibility: for the same fixture
  workspace, a parity test renders the CLI output (audit progress/summary, `status`,
  `status --details`, `upload --dry-run`, upload progress/batch summary, rate-wait lines)
  and asserts that every number, reason code and identifier shown has an equal value in
  the structured events/results. CLI text is unchanged (existing tests).
- **AC-12** Packaging evaluation documented (size, cold start, licensing) with a
  reproducible build script; not required to ship.

## Implementation approach (reporting parity)

- Reporting fields are identified from code and tabulated in the
  [parity matrix](../docs/desktop-parity-matrix.md); gaps listed there are closed by
  adding fields, never by removing CLI output.
- Calculations are separated from Rich rendering: `strava.progress.snapshot` and
  `classify_actions` are already pure; the audit renderer's rate/ETA/percentage and the
  `RecoveryEvent` wait/batch text move into pure functions returning typed values used by
  both the Rich renderer and the JSONL adapter. Rendered CLI text stays identical.
- `AuditProgress` gains `fit_generated`/`fit_reused`; a `RateSnapshot` is produced from
  `RateLimit` + policy + uploader state. Existing callbacks keep their signatures
  (additive fields with defaults).
- The parity test (AC-13) is table driven from the matrix so a CLI field without a
  contract field fails the build.
- Examples of audit, upload, wait, error and final-result events:
  [contract examples](../docs/desktop-contract-examples.md).

## UI component mapping (future, informative)

| View | Components | Contract sources |
| --- | --- | --- |
| Overview dashboard | outcome cards (Completed, Duplicates, Unresolved separate), resolved progress bar with overlap footnote | `status`, `ProgressSnapshot.semantics` |
| Live progress | phase stepper, bar, counters, rate/ETA (hidden when `eta_s` null), current activity, wait banner | `audit.*`, `upload.*`, `rate.wait` |
| Activity table | virtualized grid, filters, "Why?" drill-down | `activity.list/get` |
| Review queue | blocked, duplicate-review and needs-configuration items; only `next_actions` buttons | `ActivityDetail.next_actions`, `timezone.*`, `reset` |
| API and scheduler diagnostics | quota gauges with "unknown" state, reserve, concurrency, polling budgets, collapsible advanced section | `RateSnapshot`, `upload.event` |
| History and results | past runs (host DB), final summaries, sanitized export | `result`, `report.export` |

## UI specification (future, informative)

Views: (1) migration overview, (2) live audit/upload progress, (3) activity table with
filters and "why" drill-down, (4) review queue, (5) Strava API/scheduler diagnostics with
collapsible advanced section, (6) history and final results, plus Settings -> Integrations
-> Strava and the timezone review workflow. Completed, duplicate and unresolved outcomes
are visually distinct; overlapping categories are labelled as overlapping.

## Decisions requiring approval

- **D1** Add `integration/` package and `polar-to-strava-engine` console script. Recommended: yes.
- **D2** Credentials per request via stdin plus `TokenStore` protocol (CLI default unchanged).
- **D3** Lock file shared by CLI and engine (small CLI behavior addition: refuses concurrent writers).
- **D4** Package the engine as a Windows executable (PyInstaller one-folder preferred, see
  packaging evaluation). Requires adding `tzdata` for IANA on Windows.
- **D5** Contract versioning policy as above.

## Out of scope

GUI, C# code, changing uploader/recovery semantics, Strava read-sync features beyond
connection/scope handling.

## Completion

Implementation: branch `feat/desktop-integration-contract` (PR linked from the pull request). Stacked on
the SPEC-003 branch because it uses `AuditCancelledError`, the audit progress additions and the cache.
Status is *Implemented*; the final compliance review is for the maintainer.

| Criterion | Evidence |
| --- | --- |
| AC-01 | `docs/contract/contract-1.0.schema.json`; `validate()` in `tests/test_integration_engine.py` checks every envelope of audit, upload and timezone runs, including gap-free `seq` |
| AC-02, AC-13 | `test_status_parity_every_cli_row_has_a_contract_field` (fails when a CLI row has no mapping), `test_audit_summary_parity`, `test_status_details_cover_cli_detail_lines`; matrix in `docs/desktop-parity-matrix.md` |
| AC-03 | audit, `audit.dry_run`, `status`, `activity.*`, `upload.dry_run`, `upload.run` (fake `httpx.MockTransport`) tests |
| AC-04 | `test_upload_run_with_fake_transport_...` asserts unknown read windows are `null`/`known:false` |
| AC-05 | audit cancel tests; `test_upload_cancel_mid_request_then_restart_does_not_post_again`; `test_upload_run_never_retries_uncertain_outcomes` |
| AC-06 | `test_second_writer_is_refused_and_readers_continue` (engine and CLI) |
| AC-07 | sentinel secret assertions in upload, OAuth, report export and invalid-request tests |
| AC-08 | recovery database code unchanged; full existing suite passes; OAuth test compares records before and after |
| AC-09 | `test_authorize_*`, `test_connection_*`, DPAPI test (Windows) |
| AC-10 | `test_timezone_*` (list filters, Europe/Helsinki winter/summer, DST gap/overlap refusal, range, authoritative refusal, confirmation, atomic config, rerun, unchanged IDs) |
| AC-11 | existing CLI tests pass; `AuditProgressRenderer` refactor verified by `test_audit_progress.py` |
| AC-12 | `docs/packaging.md`, `packaging/build_engine.ps1`; built and run (45 MB, 0.6-0.8 s start-up) |

Amendments and deviations (to be confirmed at review):

- `cancel_on_stdin_close` is opt-in rather than always on (an EOF would cancel `echo {} | engine ...`).
- `strava status` and activity reads reconcile only when no writer is active (`reconciled: false`
  otherwise); the CLI `status` is unchanged and unlocked.
- `upload.dry_run` takes the writer lock because the existing `preview` records local blockers.
- The engine stores tokens through a new `TokenStoreLike` protocol; `exchange_code` gained
  `required_scopes`, `authorization_url` gained `scopes`, `Uploader` gained `should_stop`; all additive
  with CLI-compatible defaults.
- `run_done` stays `null` because the CLI renderer never populated "This run".
