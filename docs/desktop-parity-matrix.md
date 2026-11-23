# CLI-to-GUI reporting parity matrix

Status: Draft, part of [SPEC-004](../specs/004-desktop-integration-contract.md). Derived from
`core/audit_progress.py`, `services/audit.py`, `strava/progress.py`, `strava/rate_limit.py`,
`strava/models.py` and `core/cli.py`. Every CLI field must have a contract field; nothing
is dropped. Contract field names are proposed (contract `1.0`).

Semantics preserved verbatim: recovery categories count **activities and may overlap**;
`capacity_jobs` and `duplicate_review_stops` count **attempts**; workspace counts may exceed the
selected batch; `status` and `--dry-run` use local evidence only (no Strava request).

## `audit`: live progress (`AuditProgressRenderer`)

| CLI field | Meaning | Source | Contract field |
| --- | --- | --- | --- |
| Phase label | discovery, preparing, processing, reports, manifest, complete | `AuditPhase` | `audit.phase.phase` |
| Training sessions | discovered source files | `AuditProgress.total` | `audit.progress.total` |
| Processed N / M (%) | finished sources | `completed/total` | `completed`, `total`, `percent` |
| FIT valid | sources with valid FIT | `fit_valid` | `fit_valid` |
| Warnings | total warning count (not activities) | `warnings` | `warnings` |
| Failed | failed sources | `failed` | `failed` |
| Skipped existing | existing FIT reused after validation | `skipped_existing` | `skipped_existing` |
| Elapsed | wall time since start | renderer clock | `elapsed_s` |
| Rate | activities/s since processing began | renderer | `rate_per_s` |
| Estimated left | only if completed>=5, processing>=2 s, remaining>0 | renderer | `eta_s` (null otherwise) |
| (new) generated / reused | FIT generated vs reused | row `status` | `fit_generated`, `fit_reused` |

## `audit`: final summary and report

| CLI field | Meaning | Source | Contract field |
| --- | --- | --- | --- |
| Activities discovered / Processed | rows | `summary.discovered` | `summary.discovered`, `processed` |
| FIT valid | validated FIT | `summary.validated` | `summary.validated` |
| Ready to migrate | `eligible` + `eligible_with_warnings` | `migration_status_counts` | `summary.ready_to_migrate` |
| Needs configuration | `requires_configuration` | same | `summary.needs_configuration` |
| Excluded | sum of `excluded_*` | same | `summary.excluded` and full `migration_status_counts` |
| Failed | failed rows | `summary.failed` | `summary.failed` |
| Warnings | sum of warning counts | `warning_counts` | `summary.warnings_total`, `warning_counts` |
| Duration | elapsed | `summary.elapsed_seconds` | `summary.elapsed_s` |
| Workspace / Review path | output directory / `migration-audit.md` | args | `summary.workspace`, `summary.report_path` |
| (report only) failure stages / categories | failures by stage/message | `failure_stages`, `failure_categories` | `summary.failure_stages`, `failure_categories` |
| (report only) sports, sensors, per year/month, bytes | inventory | `report` | `summary.sports`, `sensors`, `per_year`, `per_month`, `total_fit_bytes`, `generated_fit_bytes`, `earliest`, `latest` |
| (report only) duplicate candidate pairs | start within 60 s, same sport, similar duration/distance | `_duplicates` | `duplicate_candidates[]` |
| (report only) per-activity row | every audit CSV column | `activities[]` | `audit.activity` (all columns, plus `migration_status`, `migration_reason`) |

## `strava status` / progress table (`ProgressSnapshot`)

| CLI label | Meaning | Source | Contract field |
| --- | --- | --- | --- |
| Eligible activities | manifest `eligible` | manifest | `eligible` |
| Resolved N / M (%) | resolved within eligible | `snapshot` | `resolved`, `percent` |
| Completed (eligible) | resolved by completed upload | `Remote.COMPLETED` | `completed` |
| Duplicates (eligible) | resolved by authoritative duplicate | `Remote.DUPLICATE` | `duplicate` |
| Remaining (eligible) | eligible minus resolved | derived | `remaining` |
| Ready to submit | classify_actions SUBMIT | recovery | `ready_to_submit` |
| Observing | OBSERVE (active submissions/observations, deferred) | recovery | `observing` |
| Needs review / blocked | REVIEW | recovery | `needs_review` |
| Capacity-consuming remote jobs (workspace) | attempts consuming capacity | `consumes_submission_capacity` | `capacity_jobs` |
| Duplicate review stops (no capacity) | attempts stopped for review | `duplicate_review_stop` | `duplicate_review_stops` |
| Local blockers | active local-code blockers | `LOCAL_CODES` | `local_blocked` |
| Retained outside current eligible | records not currently eligible | records minus eligible | `outside_current_eligible` |
| Outside manifest | records absent from manifest | records minus present | `outside_manifest` |
| Resolved outside eligible | resolved but not eligible | resolved minus eligible | `outside_resolved` |
| FIT data MB | eligible FIT bytes | manifest | `fit_bytes` |
| "Local evidence only" | no request made | constant | `evidence: "local_only"` |

## `strava status --details` (per activity)

| CLI line | Meaning | Source | Contract field |
| --- | --- | --- | --- |
| id: label | action kind (Ready / Observing / Needs review / Resolved) | `classify_actions` | `ActivityDetail.upload.state` |
| Reasons | reason codes | `ActionDecision.reasons` | `upload.reason_codes[]` |
| Human review required, Review-stopped upload ID | duplicate evidence not recognized | `review_upload_ids` | `upload.review_upload_ids[]` |
| Remote / submission evidence | attempt remote states / phases | attempts | `upload.remote[]`, `submission_evidence[]` |
| Local blockers | active local blockers | blockers | `upload.blockers[]` |
| Observation/evidence reasons | attempt error codes | attempts | `upload.error_codes[]` |
| Outside current eligible / outside manifest | flags | record | `outside_current_eligible`, `outside_manifest` |
| Activity date / unavailable hint | UTC date or note to use explicit id | `resolved_utc_start` | `resolved_utc_start`, `date_available` |
| (new) known Strava IDs | upload and activity IDs known | attempts | `known_upload_ids[]`, `strava_activity_ids[]` |

## `strava upload --dry-run`, `--limit`, `--all`, `--activity-id`

| CLI behavior | Contract |
| --- | --- |
| Per-activity `would_submit / would_observe / blocked/review / resolved` with reasons | `upload.dry_run` result `actions[]` (same labels as codes) |
| "Local preview only ... does not guarantee Strava acceptance" | `result.notice: "local_preview_only"` |
| Selection (`--limit`/`--activity-id`/`--all`, `--from`/`--to`) | request fields; selected count in result |
| Live table "This run N / M" (the CLI renderer never supplies it today) | `upload.progress.run_done` (null) and `run_total` (selected runnable activities) |
| "Current: id: label (reasons)" | `upload.event` (`identifier, action, reason_codes`) |
| Duplicate stop "automatic observation stopped; upload IDs" | `upload.event.review_upload_ids` + reason code |
| Waiting line: reason, next GET time, GET budget remaining, consecutive-failure budget remaining | `upload.event.wait_reason`, `next_poll_at`, `polls_remaining`, `failures_remaining` |
| Batch finished: need review, observations retained/deferred, capacity jobs, review stops, selected unattempted, cause (run stopped vs retained capacity/deferred) | `upload.event.batch{review, observing, capacity_jobs, review_stops, unattempted, stopped}` |
| "Rate reserve: waiting until T" | `rate.wait{seconds, resume_at, reason: "short_window_reserve"}` |
| Daily reserve reached (`DailyLimitReached`, resumes after midnight UTC) | `error.code: daily_limit_reached`, `details.resume_after` |
| `--max-in-flight`, `--rate-limit-reserve` | request fields, echoed in `RateSnapshot` |
| "Run finished. Inspect status --details" | `result.next: "status_details"` |

## Strava API and scheduler (`RateLimit`, `RateLimitPolicy`)

| CLI row | Meaning | Source | Contract field |
| --- | --- | --- | --- |
| API 15 min usage / limit | overall short window | `X-RateLimit` headers | `rate.overall.short{usage,limit,remaining}` |
| API daily usage / limit | overall daily | headers | `rate.overall.daily{...}` |
| Read 15 min / Read daily | read-specific, shown only when present | headers | `rate.read.short`, `rate.read.daily` (`null`, `known:false` when absent) |
| Reserve | safety reserve (default 10) | policy | `rate.reserve` |
| Concurrency | max in-flight (1-10, default 3) and active | uploader | `rate.max_in_flight`, `rate.in_flight` |
| Wait reason / resume time | reserve wait until next 15-min boundary +1 s | `RateWait` | `rate.wait{reason,resume_at,seconds}` |
| Polling/backoff | next GET, budgets | `RecoveryEvent` | `rate.polling{next_get_at, polls_remaining, failures_remaining}` |

## Recovery actions (`strava reset`)

| CLI | Contract |
| --- | --- |
| "Safest local action(s)" list | `reset` result `actions[]`; `--force` deprecated, no override |
| Private schema-1 backup path | `result.state_backup_path` (null if none) |

## Implementation status

All rows above are implemented in `integration/snapshots.py` and enforced by
`tests/test_integration_engine.py` (`test_status_parity_*`, `test_audit_summary_parity`,
`test_status_details_cover_cli_detail_lines`). The gaps below were closed by SPEC-004.

## Gaps closed (CLI fields that had no structured form)

- Audit progress is a dataclass (`AuditProgress`) with no generated/reused split and no ETA field.
- `RecoveryEvent` carries budgets and batch counters but no rate snapshot; `RateLimit` is read via a supplier.
- There is no structured per-activity upload detail with known Strava IDs outside rendering.
- `Remote`/`Code` enums are the source for stable string codes; the contract will freeze their values.
