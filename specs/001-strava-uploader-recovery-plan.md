# SPEC-001 Uploader Recovery Implementation Plan

Status: Approved
Specification: [SPEC-001](001-strava-uploader-recovery.md)
Specification status: Approved
Specification baseline: `49eded371fd25b01c3b854d537b296b01f4045bb`
Created: 2026-09-27
Human approval date: 2026-09-28
Human approval: Requesting user explicitly approved the implementation choices in plan commit c65c32b8890824567d4542d0062b420f14de5d8c.
Execution: WP1–WP3 implemented and verified (2026-09-28); WP4–WP8 incomplete; execution guards active

## Approved execution sequencing clarification (2026-09-28)

The requesting user authorized Sprint 10.3D (WP1–WP3) and explicitly approved this
sequencing clarification. WP1–WP3 replace interfaces consumed by the legacy scheduler;
running it during partial integration is unsafe. The temporary development guard
therefore applies at **both the CLI and service/uploader execution boundaries**, before
client use, OAuth, POST or GET. There is no legacy compatibility mode, environment
override, force bypass or test-only production escape hatch.

Orchestration-dependent tests may be individually deferred until WP4–WP5, retaining
their behavioral intent and historical reference. Foundation tests remain active.
All required deferred tests must be restored/replaced before guard removal; WP8 may
not remove it unless required orchestration tests and AC evidence are active and
passing. This changes sequencing only, not SPEC-001, its ACs or the approved design.

The seven narrowly scoped deferrals are:

| Test in `tests/test_strava_uploader.py` | Reason / owner | Required restored or replacement behavior |
| --- | --- | --- |
| `test_successful_async_upload_persists_and_does_not_repeat` | Requires connected POST/poll scheduler; WP4/WP5 | Persist completion, then zero repeat POST on restart |
| `test_retry_is_bounded` | Historical three-POST 503 expectation is forbidden; WP4 | One POST, uncertainty/review, zero POST after restart |
| `test_uncertain_network_outcome_is_not_retried` | Requires connected POST path; WP4 | Persist ambiguity and refuse resend |
| `test_rate_limit_stops_batch_without_retrying` | Requires scheduling after HTTP 429; WP4/WP5 | Stop batch, retain operation-specific evidence and ID |
| `test_bounded_pipeline_has_multiple_processing_uploads` | Requires remote-job scheduler; WP5 | Bound new submissions and retain all existing remote jobs |
| `test_daily_rate_limit_stops_uploader_with_pending_state` | Requires request preparation; WP4 | Daily reserve stops before intent/POST, preserving prior provenance |
| `test_keyboard_interrupt_preserves_resumable_state` | Requires request-boundary execution; WP4/WP5 | Preserve intent, known IDs and stronger terminal evidence |

Each deferred test has an individual SPEC-001/WP reason and its historical baseline
reference. No module is skipped. Active replacement guard tests cover direct execution,
CLI rejection, no client/auth/state access before CLI refusal, force non-bypass and
local synthetic operation. Existing non-scheduler tests are adapted to typed evidence;
known-ID selection is tested now, while actual scheduled GET resume remains WP5.

**Goal:** Implement the approved recovery contract without allowing observation
failure, erased history or uncertainty to authorize a new Strava upload.

**Architecture:** Retain the synchronous uploader and existing manifest/client/store
boundaries. Introduce typed action classification and a version-2 SQLite model with
independent submission evidence, per-attempt remote outcomes and local blockers.
Route new submissions through one final permission/intent gate, and observations
through an independent known-ID path.

**Stack:** Existing Python 3.12+, SQLite, HTTPX, Pydantic, Typer/Rich and pytest.
No new runtime dependency, workflow engine or manifest schema is planned.

For implementers: execute dependency-ordered packages with the linked approved spec
open. Checkbox steps are future work, not completed changes. Repository SDD and
optional test-first ordering govern execution; no particular agent tool is required.
This plan contains implementation choices, not permission to change AC-01–AC-18.

## Authority, constraints and review focus

Read [AGENTS.md](../AGENTS.md), [specification process](README.md), the approved spec,
[architecture](../docs/architecture.md), [testing](../docs/testing.md),
[Python conventions](../docs/python-guidelines.md), [contribution guidance](../CONTRIBUTING.md)
and [security policy](../SECURITY.md). Current implementation defines existing behavior;
SPEC-001 defines the target; this Approved plan records how to reach it. Stop and report any
unimplementable requirement rather than reinterpret the contract.

- Preserve source identity, manifest version 1, eligibility rules and domain isolation.
- POST requires positive durable permission, no older unresolved attempt, eligibility,
  fresh path/hash validation, rate permission and committed intent.
- Known ID means GET or review; uncertain no-ID and processing failures are review-only.
- No force-resend, remote search/reconciliation, new scopes/endpoints, workspace locks,
  OAuth/storage redesign, live migrations or exactly-once claims.
- Use synthetic temporary workspaces and mocked clients only during development.
- Keep credentials/raw HTTP payloads out of state, logs, events and fixtures.
- The implementation is scoped to a single uploader process and intact workspace
  history. A SQLite write transaction is not a cross-process migration lock.

Review these easily missed cases explicitly; tests are assigned below:

1. A legacy ID belongs to an earlier attempt while a later attempt is uncertain:
   observe that ID without erasing uncertainty (WP1/WP3/WP6).
2. Valid activity-ID completion without upload ID versus contradictory success:
   resolve only the attributable consistent response (WP2).
3. Removed manifest entry with no retained date: explicit-ID/`--all` can observe;
   date-filtered selection must explain missing metadata (WP3/WP7).
4. Token refresh or rate waiting after FIT validation would invalidate the intended
   ordering: complete these before snapshot validation (WP4).
5. Restoring a pre-upgrade backup after a remote effect can erase new evidence:
   fail closed and document why rollback is then unsafe (WP1/WP8).

## Inspected baseline and concrete delta

`strava/state.py` currently declares `SCHEMA_VERSION = 1`; metadata stores that value,
and `_create` only creates/rejects schemas, with no upgrade path. `set_status` retains
omitted IDs but overwrites the single status and latest errors. `reset` clears IDs.
`Uploader.select` iterates eligible manifest entries and verifies FITs even for GET.
`run` polls only processing+ID; its fallback queues POST. `_submit` repeats retryable
POSTs; `_poll_once` records generic failure and discards a job after a GET error.
`StravaClient.upload` loads/refreshes tokens and opens a path inside the client call.
`UploadStatus.upload_id` may raise after model validation; duplicate recognition is
a substring in `_handle_status`. `ProgressSnapshot` and CLI use state counts rather
than evidence/action classification. Tests are currently concentrated in
[test_strava_uploader.py](../tests/test_strava_uploader.py).

| Gap | Planned change | ACs |
| --- | --- | --- |
| Status label grants POST | One typed positive-permission decision and transactional intent gate | AC-01, AC-03, AC-04, AC-09 |
| Known-ID failures fall through | Separate submission/observation queues and permissions | AC-01, AC-02, AC-10 |
| Single status erases independent facts | Activity, attempt and blocker records; immutable evidence retained | AC-05, AC-06, AC-13, AC-14 |
| Schema 1 cannot describe safe recovery | Transactional version-2 upgrade with private backup and conservative mapping | AC-08, AC-14, AC-15 |
| Generic HTTP retry/malformed response | Operation-aware failure and partial trustworthy evidence parsing | AC-03, AC-04, AC-06, AC-15, AC-18 |
| FIT validated before waits/client reopen | Post-wait verified private snapshot sent through an already-open stream | AC-07, AC-09 |
| Reconcile/reset erases remote state | Local blocker updates and safest-action reset only | AC-05, AC-06, AC-07, AC-13 |
| Polling budget/interrupt relabels work | Durable observation deferral and monotonic evidence updates | AC-02, AC-08, AC-10, AC-11, AC-12 |
| Orphans and ambiguous rows disappear | State-driven selection and overlapping review/remote reporting | AC-14, AC-16 |
| Dry-run says would_upload for GET | Action-specific local preview and details | AC-16, AC-17 |
| Tests encode unsafe retries/reset | Replace forbidden expectations, retain useful coverage and add crash/migration cases | AC-01 through AC-18 |

## File and responsibility map

Existing paths are linked. Paths marked **new** are proposed files, not current files.

| File | Planned responsibility |
| --- | --- |
| **new** `strava/recovery.py` | Frozen typed evidence/action values and pure submission/observation classification |
| [strava/state.py](../strava/state.py) | Version-2 store, atomic evidence updates, reconciliation/reset; remove generic runtime `set_status` mutation API |
| **new** `strava/state_migration.py` | Version detection, private backup and schema-1 mapping/atomic upgrade, separate from normal transitions |
| **new** `strava/responses.py` | Strict positive ID handling and conservative response classification with partial evidence |
| **new** `strava/artifacts.py` | Context-managed verified FIT snapshot; no FIT generation or source parsing |
| [strava/models.py](../strava/models.py) | Keep manifest/token/rate models; retire unsafe response-ID shortcut as callers move to evidence parsing |
| [strava/client.py](../strava/client.py) | Prepared authorization and stream-based POST/ID-based GET; report operation phase, no POST retry |
| [strava/uploader.py](../strava/uploader.py) | State-driven selection, separate action scheduling, ordered request preparation and persistence |
| [strava/rate_limit.py](../strava/rate_limit.py) | Preserve rate policy, limits/reserve/wait behavior; only adapt call sites if necessary |
| [strava/progress.py](../strava/progress.py), [core/cli.py](../core/cli.py) | Evidence-based summaries, details, dry-run and safe reset output |
| Existing `tests/test_strava_uploader.py` | Retain end-to-end mocked uploader/CLI/rate regressions, adapt fixture/client contracts |
| **new** `tests/test_strava_state.py` | Store, upgrade, legacy mapping, reconciliation/reset and persistence fault tests |
| **new** `tests/test_strava_recovery.py` | Pure action permission, independent blockers, orphan selection and crash/restart matrix |
| **new** `tests/test_strava_responses.py` | Response evidence, ID conflicts, duplicate grammar and redaction |
| **new** `tests/test_strava_artifacts.py` | Snapshot integrity, mutation during waits/copy, stream lifetime and cleanup |

New modules stay in the existing `strava` package, already included by setuptools
and mypy. No `domain`, Polar, exporter, manifest schema or dependency edits are needed.
No proposed new file is created by this planning sprint.

## Durable model recommendation

Use schema **2**, the next version after inspected schema 1. Keep `metadata` and add
three purpose-specific tables below, replacing the runtime use of `uploads`. An
attempt table is necessary: one activity can have a retained known ID and ambiguous
additional attempts. A single split-status row alone would still lose that distinction.
A general event store is unnecessary; retained attempts, typed evidence and inactive
blocker records provide the history required by the approved contract.

| Table | Proposed columns and meaning |
| --- | --- |
| `recovery_activities` | `stable_activity_id TEXT PRIMARY KEY`; `manifest_version INTEGER`; `present`, `eligible` checked booleans; `accepted_fit_sha256`, `current_fit_sha256`, `fit_relative_path`, `fit_size_bytes`; nullable `resolved_utc_start`, `sport`; `origin` = fresh/legacy_fresh/legacy_review; `revision INTEGER`; preserved `legacy_attempt_count`, `first_attempt_at`, `latest_attempt_at`, `legacy_status` from a fixed allowlist or unknown |
| `submission_attempts` | Local `attempt_id INTEGER PRIMARY KEY`; FK `stable_activity_id`; `kind` = submission/legacy_summary; `submission` = intent/not_submitted/uncertain/confirmed/legacy_unknown; `remote` = not_started/processing/deferred/processing_failed/completed/duplicate; nullable trusted `upload_id`, `activity_id`, `duplicate_activity_id`; `evidence_code`; `additional_attempts_unknown` boolean; validated conflicting numeric upload-ID set as JSON text; `intent_at`, `response_at`, `completed_at`; submitted `fit_sha256`; latest safe `http_status`, `error_code` |
| `recovery_blockers` | FK `stable_activity_id`; `scope TEXT` = activity or attempt:<local ID>; `code` from allowlist; `blocks_submission`, `blocks_observation` booleans; `active` boolean; first/last-seen and cleared timestamps; PK `(stable_activity_id, scope, code)` |

`origin=legacy_fresh` is positive provenance only under the fresh-row conditions in
the mapping below. For ordinary fresh rows, no attempts plus `origin=fresh` is the
initial positive proof. Preflight failures do not create fictional upload attempts.
For a real intent later proven not transmitted, retain that attempt as `not_submitted`
with a fixed proof code; do not delete it. No field named `retryable` grants POST.

An imported `legacy_summary` explicitly summarizes available evidence, not a fabricated
individual historical attempt. Preserve legacy attempt count separately. Set
`additional_attempts_unknown` when multiple attempts, reset/uncertain labels or
inconsistent provenance leave older/later submission unknown. Observing its trusted
ID may resolve that upload while the extra history remains a visible review condition.
No query should infer that one terminal upload resolves all unknown attempts.

Use SQL CHECKs for enums/booleans/nonnegative counters and FKs with no cascading
history deletion. Index attempts by activity and blockers by active/activity. Do
not make upload IDs globally unique across activities: legacy conflicts must remain
representable, then block unsafe interpretation. Validate blocker scopes against
their activity's attempts. Store IDs as canonical positive decimal strings without
32-bit truncation. JSON contains only validated conflicting IDs, never API payloads.

Frozen slotted dataclasses in `recovery.py` represent `RecoveryRecord` (activity fields,
attempt tuple and blocker tuple), `AttemptRecord`, `Blocker`, `ActionDecision` and
`RecoveryEvent`, `Permission`, `Operation` and `ResponseEvidence`. Define these shared
value types in WP1; WP2 implements parsing into them. `ActionDecision` has `kind`
(submit/observe/review/resolved), activity
ID, optional attempt ID/upload ID, and safe reason codes. An activity may have multiple
observe decisions plus separate review reasons; progress resolves/counts activities,
not jobs. No generic setter can downgrade a terminal fact or replace a trusted ID.

State API to implement (all typed, store owns its connection):

```text
load(identifier: str) -> RecoveryRecord
records() -> tuple[RecoveryRecord, ...]
reconcile(manifest: MigrationManifest, fingerprint: str) -> None
begin_submission(identifier: str, expected_revision: int,
                 artifact: VerifiedArtifact, rate_ready: bool) -> AttemptRecord
record_evidence(attempt_id: int, evidence: ResponseEvidence) -> None
defer_observation(attempt_id: int, reason: str, http_status: int | None) -> None
record_not_submitted(attempt_id: int, proof_code: str) -> None
record_uncertain(attempt_id: int, reason: str) -> None
set_blocker(identifier: str, scope: str, code: str, active: bool) -> None
reset(identifier: str) -> tuple[ActionDecision, ...]
```

`begin_submission` reloads and reuses the central permission logic inside its write
transaction, verifies expected revision/hash, inserts intent, updates counters/revision
and commits before returning. `record_evidence` atomically appends/retains trusted facts
and updates remote outcome; stale or contradictory evidence adds review conditions,
never overwrites trusted identity/terminal evidence. Terminal updates precede callbacks.
Store failures propagate to stop network scheduling, not to a clean-state fallback.

## Version detection, backup and atomic upgrade

Implement `ensure_schema(connection: sqlite3.Connection, path: Path) -> None` in
`state_migration.py`, called before reconcile, reset, status classification or client
construction. Inspect schema/version **before** normal DDL. Empty new database creates
v2 atomically; nonempty database without valid version/required tables is malformed,
not fresh. Version 2 is structurally validated; version 1 enters the path below;
anything else is refused with a safe diagnostic. Do not mutate newer versions.

1. Validate schema-1 structure, metadata integer version, readable rows and SQLite
   integrity. Structurally unreadable/corrupt databases stop without upgrade/network.
   Individually interpretable but inconsistent rows map to review; never skip a row.
2. Create a uniquely named private sibling backup with `sqlite3.Connection.backup`,
   not a byte copy that could omit journal/WAL contents. Check backup integrity,
   version and row count; close it before upgrading. Failure means no upgrade.
   Never overwrite an earlier backup or auto-delete it; tell the user its private
   local path without placing it in logs/public reports. No credentials are read.
3. Under one explicit `BEGIN IMMEDIATE`, create v2 tables, map every row, validate
   IDs/counters/relationships, compare source and destination activity counts, then
   remove the old runtime `uploads` table and update metadata version to 2 **last**.
   Use explicit transactional DDL/DML, not an `executescript` that can disrupt the
   intended transaction boundary. Manifest fingerprint is retained.
4. Commit once. Any exception rolls back the whole upgrade; restart sees schema 1
   and can retry with a new backup. A kill before commit must expose v1 after SQLite
   recovery; after commit it exposes fully validated v2. Unexpected v1+v2 partial
   tables or malformed v2 are refused, never interpreted as newly pending work.
5. Reopen and validate v2. Repeated opens do not repeat migration or duplicate rows.
   Old binaries see version 2 and refuse it. Provide no downgrade path or automatic
   restore command. New state has no old generic writable status compatibility layer.

The backup contains existing private state; keep it beside the private workspace,
outside version control. Do not copy arbitrary legacy error strings into v2: preserve
safe recognized codes, numeric IDs, outcome/provenance and times; unknown diagnostics
become a fixed review code. The backup preserves the original forensic record.

Rollback expectations: before any new external request, a human may recover the
pre-upgrade state for diagnosis if upgrade failed; do not automate replacement.
After any v2 network activity, restoring that backup could erase upload evidence and
is unsafe. Keep v2 and fix forward. Storage corruption/deletion/stale restores and
simultaneous uploader processes remain outside the approved guarantee.

## Concrete legacy mapping

Validate and retain trustworthy IDs before interpreting labels. A label alone never
clears conflicting evidence. Valid recorded completed/duplicate resolution survives
the upgrade, including the old broader duplicate recognizer; new API responses use
the stricter parser. Nonterminal rows with retained completion evidence are resolved
only when their provenance is consistent; otherwise preserve facts and review.
`additional_attempts_unknown`/blockers are orthogonal to the remote result below.

| Old evidence | New durable representation | Permitted action |
| --- | --- | --- |
| Pending, zero attempts, no IDs/timestamps/errors suggesting prior submission | legacy_fresh activity, no attempt, normal local gates | Submit candidate only if already eligible/present and integrity passes |
| Pending with attempt history | legacy_review; legacy_summary/legacy_unknown; history_unknown blocker | Review; independently trustworthy ID may be observed; no POST |
| Uploading without ID | legacy_summary/legacy_unknown, remote not_started, history_unknown | Review, no POST |
| Uploading with ID | summary confirmed for known ID, remote deferred; additional history unknown | Observe known ID plus review extra uncertainty |
| Processing with ID | summary confirmed, processing; additional history review when count/provenance warrants | Observe same ID |
| Processing without ID | summary legacy_unknown; missing_upload_id blocker | Review |
| Retryable failure with ID | summary confirmed, deferred; retain history uncertainty and safe cause code | Observe/review; never POST from label |
| Retryable failure without ID | summary legacy_unknown even when latest error says server/rate limit | Review; absence of ID/counter alone proves nothing |
| Uncertain with ID | summary confirmed for that ID, deferred plus additional history uncertainty | Observe trustworthy ID plus review |
| Uncertain without ID | summary uncertain, remote not_started | Review, no reconciliation |
| Permanent failure with ID and reliable processing_error provenance | summary confirmed, processing_failed | Review-only; no automatic GET loop or POST |
| Permanent failure with ID and reliable retrieval/access provenance | summary confirmed, deferred plus scoped access/retrieval blocker | Review/observe after safe reset/access repair |
| Permanent failure with ID but insufficient phase evidence | summary confirmed, deferred plus unknown_phase review | Preserve ID; bounded observation can recover facts, never POST |
| Permanent failure without ID | summary legacy_unknown and review | No POST; malformed success may have been accepted |
| Completed with valid activity evidence | summary confirmed, completed; retain upload ID if present, terminal provenance | Resolved; preserve separate local/history issues |
| Duplicate with consistent recorded duplicate status/evidence | summary confirmed, duplicate; legacy_duplicate evidence_code, optional duplicate activity ID | Resolved; preserve existing record, do not re-run new text grammar against it |
| Local-file-changed with reliable terminal evidence | retain completed/duplicate remote outcome plus artifact/manifest blocker | Resolved plus local review |
| Local-file-changed with trustworthy nonterminal ID | summary confirmed, deferred plus artifact blocker; keep uncertain history if applicable | Observe despite artifact blocker |
| Local-file-changed without remote evidence | legacy_review plus artifact blocker; summary legacy_unknown when history exists | Review; do not infer safety from absent ID. A reliably untouched zero-attempt preflight-only record may retain positive non-submission provenance |
| Skipped | preserve legacy skipped provenance and blocker, any remote facts retained independently | No new submission; observe only independently usable remote evidence |
| Unknown status/invalid counters/conflicting IDs or terminal fields | legacy_review; keep validated facts, malformed_state/history blocker | Review; GET only independently trustworthy identity, never POST |

Fresh mapping is deliberately stricter than counting attempts alone: timestamps or
remote evidence contradicting zero attempts prevent POST. For local-file-changed,
only an intact zero-attempt row with no IDs/times suggesting submission and an actual
known preflight category (missing_fit/fit_hash_mismatch) can establish pre-submission
provenance; generic manifest_changed cannot reconstruct erased history. All other
ambiguous cases review. No formerly blocked row becomes eligible because of upgrade:
artifact/eligibility blockers still require correction through normal recovery.

Do not fabricate absent start dates/sport/path. Populate retained metadata from the
current manifest only for matching identities during reconciliation; absent legacy
entries with no date report date-unavailable and remain selectable via explicit ID
or `--all`, as the spec requires.

## Authorization and action classification

Implement in `recovery.py`:

```text
classify_actions(record: RecoveryRecord) -> tuple[ActionDecision, ...]
submission_permission(record: RecoveryRecord, activity: ManifestActivity | None,
                      artifact: VerifiedArtifact | None, rate_ready: bool) -> Permission
observation_permission(record: RecoveryRecord, attempt_id: int,
                       rate_ready: bool) -> Permission
```

`Permission` contains allowed and fixed reason codes; it carries no credential.
Initial classification can report a submit candidate, but only the final permission
with artifact/rate proofs plus `begin_submission` can authorize POST. All POST callers
must hold the returned committed attempt; remove the old fallback queue/status check.
No secondary method may grant submission by clearing a status.

Submission denies any trusted remote/terminal evidence, unresolved intent/uncertainty,
legacy_unknown/additional-history flag, active submission blocker, absent/ineligible
manifest entry, missing/stale artifact proof or unavailable rate permission. Allow
only fresh positive provenance or retained attempts all positively not_submitted,
with no unknown legacy history. Revision and expected hash bind final authorization
to the record that is committed. This is a correctness check, not a promised lock
against concurrent unsupported processes.

Observation requires a trustworthy retained upload ID associated with that attempt,
nonterminal processing/deferred outcome, and no applicable observation blocker.
Artifact, absence, eligibility and another attempt's uncertainty do not deny GET.
Processing_failed has no automatic retry; terminal completion/duplicate needs no GET.
Malformed identity on one attempt cannot supply a replacement ID for another.
Rate/access checks defer only that permitted operation; they never grant POST.

## Reconciliation and reset

Reconcile updates current manifest metadata and sets local blockers instead of
writing a remote status. Preserve accepted FIT hash and historical attempt hashes;
changed current hash/eligibility adds manifest_changed. Missing entries set present=0
and manifest_missing but retain all attempts, IDs, start time/sport and resolution.
New entries get fresh origin; repeated manifest load must not reset existing provenance.
Keep version-1 duplicate-ID rejection and identity semantics unchanged.

Reset reloads evidence and returns classification, never deletes attempts/IDs or
sets origin to fresh. For a corrected present artifact, validate against the current
manifest, acknowledge its local change and update accepted hash; clear only proved
corrected local blockers. This cannot make uncertain/failed/confirmed submission safe.
Missing/ineligible entries remain locally blocked while known-ID GET remains possible.
Auth/retrieval deferral may be cleared to permit a bounded GET retry after local auth
configuration is repaired; this is not proof of remote permission or non-submission.
Unknown history, response-identity conflicts and processing failure cannot be cleared
by ordinary reset. Terminal results remain terminal. No new reconciliation command.

Keep `--force` as an accepted **deprecated no-op modifier**, print that it cannot
override recovery protection, and execute exactly the same safe reset as without it.
This retains CLI argument compatibility without retaining force-resend semantics.
Tests must prove identical evidence/actions for both flag values in every category.

## Prepared requests, integrity and POST ordering

Choose a private `tempfile.TemporaryFile` snapshot, streamed and hashed while copying
from the workspace-contained source. Compare the copied bytes' SHA-256 to the expected
manifest hash, then rewind and send that same handle. This avoids unbounded whole-FIT
memory allocation and path reopening; a concurrent mutation during copy produces a
hash mismatch unless the copied bytes still match the authorized artifact. A same
source handle alone would still permit in-place modification while HTTP streams it.
Rehashing then reopening the path preserves that race. No workspace locking is added.

`VerifiedArtifact` in `artifacts.py` contains the private stream, original safe upload
filename, expected SHA-256 and byte count; use a context manager
`verified_artifact(workspace: Path, activity: ManifestActivity) -> Iterator[VerifiedArtifact]`.
Only uploader preparation writes the snapshot; the client consumes it read-only.
Delete/close on every exit; do not log paths or include snapshots in the workspace.
Document platform temporary-file cleanup limits, but no safety decision depends on
cleanup succeeding after a kill. Size mismatch is supplemental diagnostic; matching
size can never substitute for matching hash.

Split client preparation from transport so token refresh never intervenes after
final artifact validation. Extend its protocol to:

```text
prepare_access() -> PreparedAccess
upload(artifact: VerifiedArtifact, external_id: str,
       access: PreparedAccess) -> ResponseEvidence
get_upload(upload_id: str, access: PreparedAccess) -> ResponseEvidence
```

`PreparedAccess` holds the token and expiry only in memory with token excluded from
repr. Existing token refresh/storage remains in `client.py`; `upload/get_upload`
must not refresh, wait, reopen source paths or retry HTTP internally. Explicit
`RequestFailure` reports operation, phase (`not_sent`/`possibly_sent`/`observation`),
safe code and HTTP status. No exception string/body is automatically persisted.

Every actual POST follows this sequence in `Uploader._submit`:

1. Reload record and classify a safe candidate; otherwise return its allowed action.
2. Prepare access, apply existing rate policy, and repeat preparation/policy if a
   wait invalidates token readiness or OAuth refresh updates rate headers. Finish all
   such waits before snapshot validation. Daily reserve stops without an intent.
3. Reload/reconcile manifest and evidence as needed after waits; refuse malformed
   manifest rather than trust cached eligibility/hash. Recheck presence/eligibility.
4. Resolve contained path, open source, copy/hash into private snapshot and compare
   expected SHA-256; no network occurs during this step. Keep snapshot open.
5. Re-evaluate final permission with fresh snapshot and rate readiness. If another
   wait/preflight retry is needed, discard the snapshot and return to step 2.
6. `begin_submission` atomically validates revision/proofs and commits intent. Failure
   means zero upload POST. Emit no fallible callback between this commit and transport.
7. Call client POST once with prepared access and the verified stream. Configure no
   automatic transport retry; treat failure after entering the transport call as
   possibly_sent unless a narrowly typed client preflight refusal proves otherwise.
8. Parse evidence, including trustworthy partial IDs, and commit it before polling
   or notification. A valid unambiguous activity-ID completion can resolve without
   upload ID. Persistence failure stops the run, preserving the earlier intent barrier.
9. Notify only after evidence persistence; choose observe/resolved/review. Close
   snapshot on exit. Later callback/cleanup errors cannot downgrade the saved evidence.

Remove `_submit`'s generic `for attempt in range(max_retries)` POST retry loop. No
HTTP 503/429/4xx, malformed success or generic HTTPX transport exception authorizes
another POST. Proven pre-send preparation failures retain safe provenance and can
retry on a later selected run after repair, repeating all gates; they do not spin
in the current run. A typed refusal before HTTP invocation after intent can record
not_submitted and defer to later retry. Any doubt records uncertainty. The existing
max_retries setting is retained only as a bounded observation-failure budget (below),
not an upload retry promise; update its internal documentation/tests accordingly.

## Response evidence parsing

`responses.py` supplies:

```text
parse_upload_response(payload: object, *, operation: Operation,
                      expected_upload_id: str | None) -> ResponseEvidence
```

`Operation` is submit/observe. `ResponseEvidence` includes independently validated
upload/activity IDs, conflicting ID candidates, remote outcome or no authoritative
outcome, duplicate activity ID and fixed diagnostic codes. Do not rely on Pydantic
coercion of boolean/float/negative IDs. Accept integer IDs and positive decimal
`id_str`, normalize exactly without truncation; if both are present they must agree.
On GET require returned upload identity to match the requested ID; keep that original
trusted ID if malformed/conflicting fields arrive. On POST, an unambiguous usable
upload ID can survive unrelated malformed fields and lead to observation/review.
Conflicting candidate IDs do not become a trusted observation target.

Only an attributable, well-formed success with valid activity ID and no error or
contradictory fields completes. POST can complete without upload ID; GET missing
required correlation cannot. Valid processing ID plus no authoritative terminal
outcome leads to observation. An authoritative non-duplicate processing error keeps
the failed ID and review-only outcome. Unknown/ambiguous duplicate wording is review,
not completed/duplicate and not submission permission.

For new duplicate recognition, normalize documented HTML presentation, require the
complete positive assertion `<file name> duplicate of activity <positive activity ID>`,
processing-error status, trustworthy consistent upload identity and no activity-ID
completion conflict. Use an anchored recognizer, not substring/fuzzy matching.
Preserve only fixed duplicate recognition code and validated IDs, not private filename
or raw remote text. Test negation, prefixes/suffixes that are not the documented
assertion, unknown wording, invalid IDs and conflicting fields as review cases.
The approved spec's evidence contract is the authority; no undocumented error code.

HTTP handling stays in client.py: capture rate headers before classification. Expected
201 POST/200 GET passes payload to the parser. POST unexpected HTTP/error responses
carry possibly_sent, with no code-only retry permission; capture any trustworthy ID
only if an attributable valid upload envelope exists, otherwise retain uncertainty.
GET failures preserve the requested ID independently of payload validity.
OAuth/preparation errors remain not_sent for the upload operation; no token body enters
`ResponseEvidence`. Raw bodies and arbitrary exception messages are not durable evidence.

## Scheduler, polling and interruption

Replace manifest-only selection with union of current manifest entries and retained
state records. `Uploader.select` returns typed decisions including review/resolved
entries for display, not an undifferentiated list of artifacts to submit. Explicit
ID and date filters apply to current/retained metadata; missing retained dates yield
a visible reason and advice to use explicit ID/`--all`, never a guessed date. `--limit`
counts submit candidates only. Artifact validation happens for POST preparation/local
preview, never as a prerequisite to constructing observation jobs.

Keep synchronous scheduling. `ProcessingJob` refers to activity ID, attempt ID and
trusted upload ID, not a required `ManifestActivity`. Restore all selected observable
jobs regardless of a lower capacity setting; hold new submissions until the number
of unresolved confirmed processing/deferred jobs drops below capacity. A deferred
GET job still represents remote work and must not free capacity as if it had failed
remotely. When no due work can advance capacity, end the run with a deferral summary
instead of spinning. Respect default 3/CLI 1–10 capacity; do not add worker threads.

New upload's first poll waits default 2 seconds (minimum 1); restored jobs are first
due immediately under rate policy. Backoff doubles up to 30 seconds. Reinitialize
in-memory timing/budget deliberately per run. Bound **all actual GETs** to default
60 per job per run, including failed GETs; use max_retries=3 consecutive transient
network/5xx failures before deferring the job. Successful pending response resets
that consecutive-error count. These are observation-only budgets, not POST retries.
HTTP 429 stops the batch immediately with ID retained; daily reserve stops before
request; short reserve waits using existing header/read-limit policy. Preserve safe
deferred evidence at budget end and allow later selected GET without POST.

GET authorization/404/permanent retrieval failures add a scoped observation blocker
for review/repair; malformed/conflicting response retains original ID and marks
review (bounded safe GET only when identity remains trustworthy). One activity's
failure does not relabel another. Stop the whole run on persistence failures that
prevent reliable evidence retention; throughput is secondary to safety.

Ctrl+C before intent leaves safe prior provenance. During submission, try to record
uncertainty only if that attempt still lacks stronger committed evidence; failed
cleanup leaves intent, which is already a no-POST barrier. After ID/terminal commit,
interrupt handling and callbacks retain it. During GET/wait, preserve ID and deferred
observation. Hard kill needs no handler: stale intent/no-ID goes to review; committed
ID goes to GET/review; committed terminal result remains resolved.

| Crash boundary from SPEC-001 | Planned durable protection and restart test |
| --- | --- |
| Before intent | Prior positive proof may permit a future gated attempt; no request yet |
| Intent committed, before POST | Intent means review on restart; do not assume transport was never entered |
| During POST transmission | Intent/uncertain forbids resend |
| Remote accepted, local response absent | Same no-ID review barrier, no remote search |
| ID in memory, not committed | Prior intent blocks; simulate response-write failure/reopen |
| ID committed | Same-ID observation, even with local artifact blocker |
| During GET | Known ID survives error/stop; only GET can repeat |
| Terminal received, not saved | Reobserve saved upload ID; if no saved ID, preserve intent uncertainty. Terminal saved before interruption stays resolved |

## Progress, status and dry-run integration

Build summaries from `RecoveryRecord` plus classifier, not SQL status totals. Extend
`ProgressSnapshot` with ready_to_submit, observing, needs_review, completed, duplicate,
local_blocked and outside_manifest counts. Current eligible denominator remains the
manifest eligible set; resolved numerator for its percentage uses only that same set.
Show a separate retained/outside-current-eligible section for orphan/ineligible work.
Count activities once per category, not once per attempt; needs_review/local_blocked
can overlap observing or resolved. Extra-attempt uncertainty remains visible.

`status --details` lists safe activity identifier, allowed action, safe reason codes,
remote/local dimensions and retained-date absence. Never dump raw SQL/errors/tokens.
`strava upload --dry-run` displays would_submit, would_observe or blocked/resolved;
it performs only local checks/upgrade/reconcile and creates no client/access token.
Live run callbacks become `RecoveryEvent(identifier, action, reason_codes)` so absent
manifest entries are supported; rate-wait callbacks remain separate. Renderers adapt
events, not decide permission. Local validation can report a FIT blocker on a known
upload without suppressing would_observe. Update reset output to the returned safe
action and explain deprecated force behavior.

## Dependency-ordered work packages

Each package includes semantic tests and a focused check; test-first ordering is
optional under repository SDD. Commit coherent reviewed increments on the isolated
implementation branch. Proposed file names below are future files from the map.

### WP1: Typed evidence and versioned persistence

Objective: represent all approved facts and safely open/upgrade existing workspaces.
Files: new recovery.py/state_migration.py; state.py; new test_strava_state.py.
ACs: AC-05, AC-06, AC-08, AC-09, AC-13, AC-14, AC-15, AC-17.
Dependencies: none. Produces shared evidence values and the persistence primitives
above. WP3 completes the public submission gate and reset/reconcile authorization.

- [x] Define typed enums/dataclasses and three tables with stated checks/indexes.
- [x] Implement schema detection/backup/atomic upgrade with every legacy mapping row.
- [x] Implement monotonic evidence writes, private transactional intent insertion and
  persisted blockers. Do not expose a callable submission gate until WP3 supplies its
  permission predicate; no permissive placeholder or alternate public insertion API.
- [x] Add `test_v1_mapping_matrix`, `test_upgrade_reopen_is_idempotent`,
  `test_upgrade_rollback_at_each_stage`, `test_newer_version_refused_without_writes`,
  `test_nonempty_missing_metadata_is_not_fresh`, `test_terminal_evidence_survives_updates`
  and `test_known_id_and_unknown_other_attempt_coexist`. Assertions: equal activity
  counts, retained IDs/counters, mapped evidence, no fabricated history, old-or-new
  complete schema after failures, no tokens/raw errors in new fields.
- [x] Run `python -m pytest tests/test_strava_state.py -q`; require all cases pass
  and isolated synthetic database snapshots show no unsafe candidate after failure.

Risk: schema-1 provenance is incomplete; conservative review is intentional, not a
migration failure to "repair" by guessing. Complete when every row/migration-fault
case has deterministic evidence and version handling. Do not activate partial store
integration for real work; apply the slicing guard below.

### WP2: Operation-aware client and response evidence

Objective: preserve trustworthy evidence without deriving POST permission from errors.
Files: client.py/models.py; new responses.py; new test_strava_responses.py; existing
uploader tests' client mocks. ACs: AC-03, AC-04, AC-06, AC-09, AC-15, AC-17, AC-18.
Depends on WP1 values; produces the ResponseEvidence parser, RequestFailure,
PreparedAccess and the new UploadClient protocol. Define the small VerifiedArtifact
value type in new artifacts.py here; WP4 adds snapshot creation/validation. Temporary
old callers remain disabled until integrated.

- [x] Implement strict independent ID extraction and response classification.
- [x] Implement anchored duplicate recognition and safe fixed diagnostics.
- [x] Split token preparation from transport, accept verified stream and disable
  POST retries/late token refresh; distinguish actual POST failure from preflight.
- [x] Add `test_activity_id_without_upload_id_completes`,
  `test_partial_valid_id_survives_malformed_fields`, `test_id_conflict_retains_original`,
  `test_duplicate_assertion_matrix`, `test_post_errors_never_grant_retry`,
  `test_get_error_preserves_target` and `test_response_diagnostics_redacted`.
  Assert returned evidence and exact MockTransport POST/GET counts, no exceptions
  that lose a trustworthy ID, no completion from contradictory fields.
- [x] Run `python -m pytest tests/test_strava_responses.py -q`; require the matrix
  and OAuth regression tests pass without live transport.

Risk: text duplicate evidence is narrow and service-dependent; unknown wording
reviews. Complete when transport and parsing can report safe partial evidence for
all approved failure categories without a generic resend flag.

### WP3: Action authorization, reconciliation and reset

Objective: make allowed actions explicit and preserve independent local/remote facts.
Files: recovery.py/state.py/uploader.py selection; new test_strava_recovery.py and
test_strava_state.py. ACs: AC-01, AC-04, AC-05, AC-06, AC-07, AC-13, AC-14, AC-15, AC-16, AC-18.
Depends on WP1/WP2; produces classifier/permissions and reset/reconcile API above.

- [x] Implement pure classification with independent per-attempt observation and
  activity review reasons; implement final positive submission predicate once.
- [x] Reuse that predicate in transactional `begin_submission`; reject stale revision.
- [x] Reconcile manifest changes into blockers without replacing remote outcomes.
- [x] Implement safe reset, retained metadata and state-union selection including
  explicit/date/limit cases and removed entries.
- [x] Add `test_submission_permission_matrix`, `test_observation_ignores_artifact_blocker`,
  `test_reset_matrix_preserves_evidence`, `test_reconcile_preserves_remote_dimensions`,
  `test_orphan_date_filter_requires_explicit_selection`, `test_processing_failure_stays_review`
  and `test_terminal_plus_unknown_history_keeps_review`. Assert no classifier path
  turns review/known ID into submit, and reset never removes history.
- [x] Run `python -m pytest tests/test_strava_state.py tests/test_strava_recovery.py -q`.

Risk: accidental restoration of safe origin after reset; completion requires negative
permission tests for every protected evidence category and both local/remote axes.

### WP4: Fresh verified artifact and single-POST path

Objective: connect authorization, waits, verification, durable intent and one transport
call in the prescribed sequence. Files: artifacts.py; new test_strava_artifacts.py;
uploader.py/client.py protocol; test_strava_uploader.py. ACs: AC-03, AC-04, AC-07, AC-08,
AC-09, AC-11, AC-12, AC-17. Depends on WP1–WP3.

- [ ] Implement context-managed private snapshot and authoritative SHA-256 check.
- [ ] Replace submission retry loop with ordered preparation/intent/one-POST path.
- [ ] Record narrowly proven not_sent versus uncertain, and persist response evidence
  before callbacks; preserve stronger evidence on interrupt/error.
- [ ] Add `test_hash_rechecked_after_rate_wait`, `test_snapshot_bytes_are_sent`,
  `test_snapshot_mutation_during_copy_blocks_post`, `test_oauth_prepares_before_validation`,
  `test_intent_commit_failure_prevents_post`, `test_response_commit_failure_blocks_restart`,
  `test_preflight_repair_allows_one_submission` and `test_503_does_not_resubmit`.
  Assert order from fake clock/store/transport trace and body hash, not just statuses.
- [ ] Run `python -m pytest tests/test_strava_artifacts.py tests/test_strava_uploader.py -q`.

Risk: hidden wait/refresh/path-open inside transport; completion requires every POST
call site to use the committed attempt and same verified stream, with no bypass.

### WP5: Observation scheduler and bounded resume

Objective: recover known uploads with GET only, retaining remote capacity/evidence.
Files: uploader.py, client.py, rate_limit.py only if adapters require; uploader/recovery
tests. ACs: AC-01, AC-02, AC-08, AC-10, AC-11, AC-12, AC-18. Depends on WP1–WP4.

- [ ] Schedule submit and observe decisions separately; ProcessingJob no longer
  requires manifest activity; restore all known IDs at lower configured capacity.
- [ ] Implement GET budget/backoff/deferral and scoped access/retrieval blockers.
- [ ] Preserve IDs at all rate/interrupt exits; stop safely on persistence failure.
- [ ] Add `test_poll_failure_restart_get_only`, `test_poll_budget_restart_get_only`,
  `test_get_429_stops_with_id`, `test_deferred_job_does_not_free_remote_capacity`,
  `test_lower_capacity_restores_all_ids`, `test_mixed_batch_failure_isolated` and
  `test_interrupt_at_each_request_boundary`. Assert zero extra POSTs, expected GET ID,
  budget limits, fake-clock backoff and unchanged unrelated activity evidence.
- [ ] Run `python -m pytest tests/test_strava_uploader.py tests/test_strava_recovery.py -q`.

Risk: capacity starvation/spinning on deferred remote work; completion requires a
bounded run exit with an actionable resume/review reason and no fallback submission.

### WP6: Crash, migration and recovery integration matrix

Objective: verify the composed contract across reopened stores and process loss.
Files: new state/recovery/response tests and existing uploader tests. ACs: AC-01
through AC-15, AC-17, AC-18. Depends on WP1–WP5.

- [ ] Parameterize all eight crash boundaries with injected exceptions and reopened
  stores; include caught Ctrl+C, ordinary exception and simulated abrupt exit.
- [ ] Add subprocess tests using synthetic paths and a fake transport barrier for
  kill after intent, after saved ID and during migration; no real API or credentials.
- [ ] Test all legacy rows through upgrade plus actual new scheduler operation;
  inspect sent POST/GET and retained blockers/IDs after reset/reconcile/restart.
- [ ] Add `test_backup_not_restored_after_remote_effect`,
  `test_upgrade_exception_after_commit_keeps_v2`, `test_old_reader_refuses_v2`,
  `test_orphan_get_with_missing_fit`, `test_known_id_with_unknown_extra_attempt` and
  `test_callback_failure_preserves_terminal_result`.
- [ ] Run all Strava-focused modules; require every crash/legacy row to have explicit
  evidence assertions and safe next-network-action assertions, not only final labels.

Risk: simulated faults do not prove hardware power-loss durability. Record that
limitation; completion requires no uncovered approved crash boundary.

### WP7: CLI, progress and local preview

Objective: expose safe actions through existing commands. Files: progress.py/core/cli.py,
uploader.py event signatures, tests/test_strava_uploader.py. ACs: AC-05, AC-06, AC-07,
AC-13, AC-14, AC-16, AC-17. Depends on WP3–WP6.

- [ ] Adapt snapshot/counts/detail rendering and identifier-based progress events.
- [ ] Route status/dry-run through local state classification without constructing
  network clients; render would_submit versus would_observe and independent blockers.
- [ ] Keep selector/capacity flags; deprecate force modifier as the safe no-op described.
- [ ] Add `test_status_dry_run_zero_network`, `test_resolution_and_review_counts_overlap`,
  `test_orphan_recovery_outside_eligible_denominator`, `test_force_reset_cannot_resend`,
  `test_cli_details_safe_reasons`, `test_dry_run_observes_changed_fit` and event tests.
- [ ] Run `python -m pytest tests/test_strava_uploader.py -q`; use CliRunner and
  transport methods that fail the test if any status/dry-run/auth request occurs.

Risk: aggregate totals hide orphan/unknown history. Complete when counts and detail
reasons agree with classification for eligible, ineligible and removed records.

### WP8: Compliance, documentation and release readiness

Objective: demonstrate all approved behavior before exposing the new upload path.
Files: architecture/uploader/troubleshooting/workspace/testing docs, README if needed,
SPEC-001 Completion evidence after implementation; version metadata only in separately
authorized release preparation. ACs: all 18. Depends on WP1–WP7.

- [ ] Run required focused/full verification and inspect all POST call sites for the
  authorization/intent requirement; audit no alternate legacy upload path remains.
- [ ] Update user/current-architecture docs listed below, including schema backup,
  downgrade refusal, reset change and review-only legacy rows.
- [ ] Perform complete diff/privacy review and explicit per-AC compliance review;
  record evidence and limitations in SPEC-001 Completion without altering its contract.
- [ ] Remove the temporary development safety guard only when every preceding package
  and compliance check passes; verify release-facing CLI uses the integrated safe path.
- [ ] Commit final integrated result; no push/merge/release without separate request.

Risk: enabling partially integrated code. Completion requires all packages and all
AC evidence, not merely a passing unit suite or schema migration alone.

## Existing tests and concrete replacement obligations

Keep semantic coverage for successful async upload/no repeat, processing-ID resume,
manifest version rejection, dry-run isolation, scope/token redaction, rate parsing,
short/daily/read reserves, explicit selector, pipeline bound and progress resolution.
Adapt helpers/FakeClient to PreparedAccess, stream and ResponseEvidence; use valid
synthetic numeric IDs instead of `unused` where strict parsing is exercised.

Change these existing expectations deliberately, citing SPEC-001:

- `test_retry_is_bounded`: replace three POSTs on 503 with one POST, uncertain/review,
  and no POST after restart. Preserve bounded-retry coverage in GET/preflight tests.
- `test_reset_protects_completed_state`: force no longer resets to pending; assert
  terminal evidence and action unchanged with/without force.
- `test_manifest_change_is_detected_without_overwriting_history`: assert separate
  artifact blocker **and** retained completed outcome, not a replacement status.
- `test_duplicate_and_permanent_states_are_not_reselected`: use full documented
  positive duplicate assertion with consistent status/IDs; add a separate unknown
  wording review case instead of accepting the old shortened substring fixture.
- `test_http_failures_are_classified`: parameterize operation and phase; POST 503/429
  is not safe retry permission, whereas GET can defer/retry with original ID.
- `test_rate_limit_stops_batch_without_retrying`: retain one request/stop; POST 429
  now conservatively preserves uncertainty rather than a resubmittable label.
- `test_local_fit_change_blocks_upload`/`test_ineligible_manifest_entry_is_ignored`:
  preserve new-POST exclusion, add known-ID observation independent of those conditions.
- `test_keyboard_interrupt_preserves_resumable_state` and progress/status tests:
  assert evidence/next operation/overlapping counts instead of old status strings.

Do not delete useful old assertions without replacing their behavioral protection.
Do not assert a new schema by duplicating implementation SQL in every test; build
minimal schema-1 synthetic fixtures reflecting the inspected historical schema and
verify externally meaningful actions, evidence and rollback as well as constraints.

## AC traceability

Every criterion has an implementation path and proposed evidence. Test names refer
to package steps above; one matrix may cover several criteria.

| AC | Implementation areas and change | Verification |
| --- | --- | --- |
| AC-01 | WP3/WP5 classifier and independent known-ID jobs | Permission matrix; poll-failure restart has unchanged POST count |
| AC-02 | WP1/WP5 durable observation deferral | Poll error/budget/429 reopen and same-ID GET tests |
| AC-03 | WP2/WP4 conservative POST failures | 5xx/timeout/malformed/missing-ID/kill; zero POST on restart |
| AC-04 | WP3/WP4 single positive gate and proven not_sent | Fresh/preflight repair gives permitted POST; old uncertainty always denies |
| AC-05 | WP1/WP3 terminal retention and blockers | Completed across reset/reconcile/missing artifact/reopen |
| AC-06 | WP2 strict duplicate parsing; WP1 legacy preservation | Positive/negative grammar/conflict matrix plus legacy duplicate upgrade |
| AC-07 | WP4 verified snapshot; WP3/WP5 artifact-independent GET | Wait/copy mutation/body hash; missing/ineligible/orphan GET with blocker retained |
| AC-08 | WP1/WP4/WP5/WP6 commit ordering | All eight crash windows and reopened-store network counts |
| AC-09 | WP1/WP4 atomic intent/response evidence | Intent-write failure zero POST; response-write failure no resend |
| AC-10 | WP5 per-attempt scheduling/capacity | Mixed jobs, lowered capacity, deferred-job and cross-activity isolation tests |
| AC-11 | WP4/WP5 existing policy at each request | Retained fake-clock reserve/read/daily tests and new GET/POST stop cases |
| AC-12 | WP4/WP5 handlers never erase facts | Ctrl+C, abrupt exit and post-terminal callback failure |
| AC-13 | WP3/WP7 safest-action reset, no-op force | Complete category/reset matrix and identical force/non-force evidence |
| AC-14 | WP1 versioned model/upgrade; WP6 integration | Every legacy row, interrupted upgrade, reopen, old/new version refusal |
| AC-15 | WP1/WP2/WP3 malformed evidence fails closed | Corrupt/version/ID/response conflict tests with no manufactured outcome/POST |
| AC-16 | WP3 selection and WP7 summaries/details/preview | Orphans, dates/limits, overlapping review, would_submit/would_observe; zero network |
| AC-17 | WP1/WP2/WP4/WP7 minimal safe evidence | Synthetic credential markers, auth repair, snapshot cleanup and diagnostics tests |
| AC-18 | WP2/WP3 processing-failed review-only | Failure plus reset/restart retains ID, no new POST or automatic failure polling loop |

## Slicing and activation safety

Recommend three ordered implementation sprints on one unmerged integration branch:

1. **Persistence/evidence foundation:** WP1–WP3, typed APIs and isolated synthetic tests.
2. **Network-safe orchestration:** WP4–WP6, request sequence and complete recovery matrix.
3. **User integration/compliance:** WP7–WP8, local reporting, docs and release readiness.

Before the first runtime integration edit, add a temporary development guard in CLI
composition rejecting non-dry-run upload before client/network construction with a
fixed "recovery integration incomplete" message. No user flag/environment escape
hatch. Unit/integration tests call injected services with mocks. Keep partially
integrated migrations/status/reset restricted to synthetic workspaces on the branch;
do not distribute or merge it. Remove the guard only in WP8 after full compliance.
This modest guard plus branch-only sequencing avoids shipping a half-safe new schema;
it does not authorize real migrations during development. Each package can be committed
and reviewed independently, but no intermediate package is a usable migration release.

## Failure handling, compatibility and release recommendation

If upgrade transaction fails or process exits: preserve original database and private
backup, reopen to validate old-or-new complete schema, and refuse ambiguous partial
state. If an implementation exception occurs after committed upgrade: preserve v2
and all evidence; stop unsafe network scheduling and fix forward. If interpretation
is incomplete for an individual legacy row: review/block that row rather than invent
history; unrelated trustworthy observations remain possible if store integrity holds.

First use of the new version opens/upgrades schema 1 before network work, then shows
conservative actions. Previously unsafe retryable/reset paths intentionally become
blocked or observation-only. No upload becomes newly eligible just because software
was upgraded. Existing manifest and CLI selectors remain; internal API/structured
callback/status-output changes must be documented. `--force` parses but cannot erase
evidence. Do not tell users to regenerate/delete state to get past a review block.

Inspected project metadata is version `0.1.0`; local tags include `v.0.1.0` and
`v0.1.1` (the latter also has `0.1.0` metadata). Recommend a **0.2.0 minor release**
after complete implementation/compliance because schema, retry, reset and reporting
compatibility change. Reconcile tag/package version in separately authorized release
preparation, not this sprint. No current release machinery is assumed or redesigned.

Required post-implementation documentation:

- [Architecture](../docs/architecture.md): actual evidence model/version and ordering.
- [Uploader guide](../docs/strava-uploader.md): safe resume/reset, no force-resend,
  observation independence, new status/dry-run semantics and upgrade behavior.
- [Troubleshooting](../docs/troubleshooting.md): blocked legacy/no-ID cases, auth
  repair, absence of remote reconciliation and why database deletion/old backup restore is unsafe.
- [Workspace guide](../docs/migration-workspace.md): private v2 state/backup artifacts,
  retained removed entries and unchanged identity/manifest boundary.
- [Testing guide](../docs/testing.md): new test modules and verified recovery coverage.
- README only for changed user workflows; approved spec Completion for per-AC evidence,
  implementation commits, limitations and final compliance outcome. Do not rewrite ACs.

## Security, residual risks and manual acceptance

New state accepts fixed reason/proof codes and validated identifiers, not raw HTTP,
OAuth or arbitrary legacy error strings. Snapshot and backup files remain private,
excluded from Git; inspect ignore patterns for these exact future artifacts during
implementation without treating ignore rules as privacy enforcement. Do not serialize
PreparedAccess or log response payloads. Test malicious synthetic secret markers in
errors and fields, not real user records. Backups may contain private historical
diagnostics already present in v1; document this rather than expose them in reports.

Residual risks: incomplete legacy history, private backup/temp-file management,
text-based duplicate recognition, credential context not bound to historical athlete,
unsupported concurrent processes, stale restores and hardware loss. These are handled
by explicit review/limitations within SPEC-001, not new behavioral choices. No conflict
with the approved specification or additional behavior needing human decision was
identified. The requesting user explicitly approved these implementation choices on 2026-09-28.

After automated checks and compliance, a separately authorized human-controlled
acceptance may inspect a **private backup/copy** locally with network disabled first,
compare row counts/known IDs and review status/dry-run actions. Any later real GET/POST
requires a separate explicit request, confirmed account/workspace and reviewed selected
actions; it is not an automated test or a step to run during implementation development.
Do not exercise uncertain resends to demonstrate safety. If no live acceptance is
authorized, report that limitation without blocking local automated verification.

## Definition of implementation complete and plan validation

- [ ] WP1–WP8 complete with no bypass POST caller and no partially active old schema path.
- [ ] Versioned upgrade, conservative legacy mapping, reset and all eight crash
  boundaries verified with reopened synthetic state and exact network action evidence.
- [ ] AC-01 through AC-18 each have recorded evidence; no unexplained deviations.
- [ ] Focused tests and full `python -m pytest`, `ruff check .`, `black --check .`,
  `mypy .` pass using the repository virtual environment.
- [ ] Current architecture/user docs updated, full diff/links/privacy/artifact review
  passed, and independent final specification compliance review recorded.
- [ ] SPEC-001 Completion updated only with actual implementation/verification evidence;
  lifecycle progresses only when its requirements are met. No automatic release/push.

Planning completeness review: all 18 ACs map above; schema/legacy/permission, reset,
reconciliation, rate, integrity, observability and privacy paths are assigned. The
five review-focus cases have owning packages/tests. Types/signatures are shared through
the file/API sections; later tasks must not invent alternate authorization APIs.
This planning sprint changes only this plan, leaves the approved spec untouched, and
runs the repository-required checks plus link/path/reference/privacy checks. Results
are recorded in the delivery report, not as evidence that the future behavior exists.

Next SDD action: human review of the WP1–WP3 foundation. WP4–WP6 require separate
authorization; keep both development guards active and do not merge or release.


## Sprint 10.3D execution evidence (2026-09-28)

Implementation commit: `1d77ad4944a44f81ee22ad1f125e782eccad8ac4`
Prepared client protocol: `2db8a8569b640d4372eef7f407bd8bd0c70e342b`
Branch: `spec-001-foundation` (unmerged).

WP1–WP3 are implemented. SPEC-001 remains Approved, not Implemented or Verified;
this is foundation evidence, not complete AC satisfaction. WP4–WP8 remain incomplete.
The coupled store, client and selection interfaces, their guards and regression tests
were committed together so the runtime replacement does not expose an intermediate
compatibility path. Sequencing clarification and execution evidence form a separate
reviewable documentation commit. No squash, push, merge or release was performed.
The planned protocol declaration was added in a small separately checked follow-up.

- WP1: frozen evidence values; three schema-2 tables with checks/indexes/FKs; validated
  v1 upgrade, unique SQLite backup, explicit transaction/version-last commit, rollback
  and reopen; conservative legacy mapping and monotonic evidence writes.
- WP2: strict independent ID/response evidence parsing, conservative FIT-basename
  duplicate assertions, operation/phase failures, memory-only PreparedAccess, stream
  transport without implicit refresh or retry. Unknown filename/prose shapes review.
- WP3: one positive submission predicate reused inside the intent transaction;
  independent GET permission; evidence-preserving reconciliation/reset; deprecated
  no-op force; manifest/state-union selection with date and submission-only limits.
- Artifact implementation is the proof value only. Snapshot creation, request
  preparation ordering, connected POST and observation scheduling are not implemented.
- Both CLI non-dry-run execution and Uploader.run refuse before client use with the
  fixed integration-incomplete diagnostic. Legacy scheduler entry points and generic
  status mutation are removed, not retained behind a compatibility switch.
- Local preview and the existing status projection remain transitional. Full overlapping
  categories, orphan reporting layouts and would_submit/would_observe rendering remain
  WP7. They are not claimed complete by these foundation checks.

Validation: full `python -m pytest` reported **220 active tests passed, 7 individually
  deferred orchestration tests** (227 collected). `ruff check .`, `black --check .`
  (67 files) and `mypy .` (67 files) passed. Focused modules passed: state 57,
  response/client 37, authorization/guards 30, and affected uploader 26 active plus
  the seven documented deferrals. Tests use synthetic temporary workspaces and
  HTTPX mock transports only; no real migration workspace or real Strava request.
Checklist test labels are covered by equivalent parameterized tests where appropriate
(for example, reopen/idempotency is asserted in every legacy-mapping case).

Migration fault injection covers validation, backup, table creation, row mapping,
post-mapping validation, version update and post-commit failure. Reopen yields complete
v1 or validated v2, never a clean submission candidate from uncertain history. Separate
intent/response-write failures verify rollback and retained intent barriers. Backups
preserve original private v1 diagnostics; new logical v2 records contain fixed codes,
validated IDs and minimal evidence, not raw response bodies or arbitrary old errors.
No downgrade or automatic stale-backup restore is implemented.

A fresh read-only review identified four important gaps: unsupported terminal claims
in malformed v2, cross-activity upload-ID attribution, lost parser conflicts on HTTP
errors, and modal/prefix duplicate wording. All were corrected with regression tests;
stronger existing outcomes remain retained while conflicting new attribution reviews.
The executor reviewed the complete changes and the final regression results. There
was no second reviewer pass, and passing mocks do not establish live API behavior or
hardware power-loss durability. Concurrent writers and external stale restores remain
outside the approved guarantees.

SPEC-001/AC-01–AC-18, dependencies, manifest/domain schemas and approved plan design
are unchanged. The only plan changes are the explicitly approved sequencing note,
WP1–WP3 checkmarks and execution/lifecycle evidence. No new behavioral decision.
The next step is human review; do not automatically proceed to WP4.
