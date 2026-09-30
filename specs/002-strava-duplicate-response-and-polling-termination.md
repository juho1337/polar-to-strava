# SPEC-002: Strava Duplicate Response Recognition & Polling Termination

Status: Verified
Created: 2026-09-30
Reviewed: 2026-09-30 (human decisions resolved and final SPEC-001 consistency review)
Approval: 2026-09-30 (requesting user explicitly approved Decisions 1–4 and the consistent specification)
Amendment approval: 2026-10-01 (requesting user explicitly approved persistent duplicate review-stop capacity exclusion)
Implementation: Prior revision implemented in 45a5ae1c842612101a9ca02adeed811088cb33d2; capacity amendment implemented in 851034963af9c45fbd55473cda482c7b802ee944
Prior revision verified: 2026-09-30; amended revision verified: 2026-10-01
Implementation plan: Not created; separate document not required for this focused change
Extends: [SPEC-001](001-strava-uploader-recovery.md)
Investigation baseline: `5c5ceaa827c2fad87bdc0b6114272fe376b1f86c`

This specification follows the [SDD workflow](README.md). CURRENT describes inspected
code and separately identified user-reported live observations. TARGET is the behavior
approved on 2026-09-30 and narrowly amended on 2026-10-01. Prior implementation
evidence is historical; the capacity amendment is implemented and synthetically verified. SPEC-001 remains unchanged and Verified
for its approved contract; SPEC-002 explicitly records the narrow extensions below.

## Problem

A real duplicate response identifies the existing activity in an HTML link rather
than the text grammar recognized by SPEC-001. Conservative rejection protects safety,
but the resulting review evidence remains pollable and can keep a command running
for many minutes without explaining its remaining wait.

## Motivation

Resolve only duplicates supported by narrow authoritative evidence. Return control
promptly when automatic observation cannot interpret duplicate evidence, retain all
recovery barriers, and explain genuine processing/backoff waits.

## Current behavior

The user reports a newly generated migration workspace prepared from 2,928 real
source activities: 2,874 valid FIT/ready, 39 needing configuration, 15 excluded, 54
failed and 3,584 warnings, with an audit duration of approximately 45 minutes.
These are reported audit categories, not assumed mutually exclusive partitions.
After one successful upload, a later `--limit 5` run displayed two resolved activities
and three both observing and requiring review for `duplicate_unrecognized`. It ran
well beyond five minutes before Ctrl+C; subsequent inspection reportedly retained
recovery evidence. The two resolved entries are workspace totals, not proof of two
new successes within that particular run. No workspace was opened in this analysis.

A previously authorized single diagnostic GET established the following response
shape. Identifiers and title below are synthetic; no raw private response is stored:

```json
{
  "id": 77,
  "id_str": "77",
  "activity_id": null,
  "status": "There was an error processing your activity.",
  "error": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.fit duplicate of <a href='/activities/99' target='_blank'>TITLE</a>",
  "external_id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.fit"
}
```

The earlier HTTP status was 200 and upload identity matched the requested ID. This
proves one real response shape, not that all three later jobs returned identical
bodies. The later run has no captured request timeline here; exact elapsed time,
request counts, rate waits and terminal responses cannot be reconstructed.

### Inspected cause and boundaries

- [responses.py](../strava/responses.py), `parse_upload_response`, strips HTML and
  matches a complete FIT basename followed by `duplicate of activity NUMBER`.
  The colon in `sha256:` fails its filename grammar. Independently, stripping the
  anchor loses the href identity and leaves a title, not `activity NUMBER`.
  The recognized error status and consistent upload IDs are not the failure here.
- [client.py](../strava/client.py), `_upload_response`, supplies operation/HTTP
  attribution. Unexpected HTTP cannot establish terminal resolution. Upload POST
  sends the stable activity identifier as `external_id`; the multipart filename is
  the artifact basename. The response's external identifier may include `.fit`.
  Echoed external text is not itself proof of expected submission identity.
- [state.py](../strava/state.py), `record_evidence`, retains the previous remote
  state when response evidence supplies no replacement, records the fixed diagnostic
  and adds a submission blocker. Thus `processing` can be historical, not the latest
  server status. Raw error/status text is intentionally not retained.
- [recovery.py](../strava/recovery.py), `observation_permission`, does not include
  `duplicate_unrecognized` among observation blockers. `classify_actions` can emit
  OBSERVE and REVIEW simultaneously. This overlap is intentional for some evidence.
- [uploader.py](../strava/uploader.py), `_observe`, treats this expected-HTTP return
  as a successful request, records it and resets the consecutive transport-failure
  count. `_restore` keeps processing/deferred jobs; the loop polls until jobs resolve,
  become deferred, hit budgets, encounter a stop or are interrupted.
- Default per-job budget is 60 GET attempts; consecutive network/server failures
  defer after three. Poll intervals double from two seconds to a 30-second cap.
  Sixty fresh-job polls alone span 2+4+8+16+56*30 = 1,710 seconds (28.5 minutes),
  excluding request time and rate waits. Restored jobs start immediately, yielding
  1,708 seconds under the same idealized schedule. Jobs interleave; these durations
  must not simply be multiplied by three. This is bounded polling, not proof of an
  infinite loop. Repeated quota/preparation waits can extend wall-clock duration.
- `--limit` limits newly selected submissions, not elapsed time or existing GET work.
  Default capacity is three. Retained/deferred jobs occupy capacity conservatively;
  pending fresh candidates can remain unsent when no active jobs remain. The loop
  then returns rather than waiting forever merely because pending entries exist.
- [progress.py](../strava/progress.py) displays overlapping categories and the latest
  action/reason; it does not expose each poll's remaining budget or next due time.
  CLI rate-reserve waits already show a resume time. Routine backoff can appear idle.

The inspected control flow explains the reported symptom without asserting an exact
live replay. Existing interruption handling is not implicated by this evidence.
Relevant baseline verification lives in `test_strava_responses.py`,
`test_strava_attribution.py`, `test_strava_scheduler.py`, `test_strava_recovery.py`,
`test_strava_reporting.py` and `test_strava_execution.py` under `tests/`.

## Intended behavior and safety invariants

Recognize the additional linked assertion only under the contract below. Resolve it
durably and stop its observation. Unrecognized duplicate evidence must instead stop
automatic observation for that attempt, persist review, and prevent restart/reset from
silently re-entering the same loop. Do not generalize this to every REVIEW action:
independent local blockers and legacy uncertainty may still coexist with safe GET.

All SPEC-001 submission, identity, reset, artifact and persistence safeguards remain:
possible acceptance forbids resend; GET failures never permit POST; IDs and stronger
terminal evidence survive errors; malformed evidence cannot create resolution; local
artifact changes cannot erase remote evidence; no force-resend or raw-payload logging.

## Scope and non-goals

Scope: this response grammar, its attribution context, duplicate-review observation
termination, compatible persisted classification and useful waiting/final reporting.
Existing plain-text duplicate recognition remains supported unchanged.

Excluded: general uploader/state architecture redesign, OAuth/token storage, parallel
writers, exactly-once guarantees, unknown-upload reconciliation, hardware durability,
migration identity redesign, audit optimization, GUI and general API abstraction work.
No new live request, schema implementation or implementation plan belongs to this task.

## Terminology and architecture impact

**Expected identifier** means the identifier associated with this submission by trusted
local evidence, not a value accepted solely because the response echoes it. For this
extension it is exactly `sha256:<64 lowercase hexadecimal characters>.fit`, derived
from the matching stable submission identity with one `.fit` suffix. It is not the
FIT-content hash. If local provenance cannot establish this relationship, review.

**Duplicate review stop** means persistent refusal of automatic GET for the affected
attempt after unrecognized duplicate evidence; it does not declare a remote outcome.

Transport retains HTTP attribution; response interpretation validates the assertion;
persistence preserves evidence; recovery permissions govern both current scheduling
and restart; CLI renders typed safe reasons. Matching context must be available to
response interpretation without trusting mutable manifest filenames as past evidence.
Exact interfaces/storage choices remain future implementation details. No alternate
upload engine or network access from reporting is permitted.

## Detailed behavior: linked duplicate grammar

All conditions are mandatory for the new variant:

1. Expected successful HTTP for the operation (GET 200 or POST 201), attributable
   consistent positive upload identity, and for GET an exact match to its trusted
   requested ID. Existing unexpected-HTTP protections remain unchanged.
2. Status exactly `There was an error processing your activity.` after surrounding
   whitespace removal. No new status synonym is introduced. `activity_id` is null;
   a completion ID plus duplicate error is contradictory and remains review.
3. Error is a complete assertion consisting of the exact expected identifier, literal
   ` duplicate of `, and exactly one complete anchor. No surrounding semantic prose,
   speculative/negative modifiers, additional links, comments or nested elements.
4. Anchor has exactly one `href` whose value is exactly
   `/activities/<positive decimal ID>`, using canonical digits `[1-9][0-9]*`.
   No absolute/protocol-relative URL, other resource, query, fragment, trailing slash,
   percent-encoded route, sign, zero or alternate numeric representation is accepted.
   Single/double attribute quotes and attribute order are presentation differences.
   The only optional attribute is `target="_blank"`; duplicate/unknown attributes fail.
5. Link text is plain text (possibly HTML-escaped), treated solely as presentation.
   It is never parsed as identity, persisted or rendered from the remote response.
   Empty text may be accepted because identity comes exclusively from the validated
   href. Decode character references once for presentation; do not recursively decode
   content into new structural markup or repair malformed HTML.
6. Response `external_id` must be a string exactly equal to the expected identifier,
   also equal to the assertion prefix. Missing/mismatched context or echo means review.
   Do not accept arbitrary filenames merely to accommodate this sample.
7. Persist the trusted upload ID and href-derived duplicate activity ID as authoritative
   duplicate evidence only after all checks pass and prior evidence is consistent.
   Existing shared-ID and stronger-outcome conflict protections still apply.

The echoed identifier is a correlation check, not authorization to submit, an
idempotency key, or proof of identity without local submission context.

## Polling and termination semantics

- A recognized duplicate ends GET scheduling for its attempt immediately after durable
  terminal evidence is saved. Ordinary independent jobs continue under existing capacity
  and rate policy; resolved duplicates no longer occupy active remote capacity.
- An unrecognized duplicate response produces a duplicate review stop after that
  response is saved. No further automatic GET for this attempt occurs in the same run
  or a subsequent normal upload run. It never grants POST. The same rule applies to
  existing persisted `duplicate_unrecognized` evidence, even if older blocker flags
  allowed observation. Preserve the known ID and historical remote state.
- Reset, including force, does not clear this evidence-derived stop. A separately
  authorized read-only diagnostic remains possible outside normal orchestration;
  this spec introduces no override/reconciliation command or automatic diagnostic.
  Parser upgrade alone cannot resolve old records because the raw assertion is absent.
- A persistent `duplicate_unrecognized` review-stopped attempt consumes zero active
  submission-capacity slots, in both the current run and after reopening state. Preserve
  its upload ID, submission evidence, historical remote state, reason, review requirement
  and relevant timestamps/diagnostic codes; do not rewrite `processing` to free capacity.
  Submission permission remains denied, automatic observation remains denied, and the
  record remains unresolved and visible. Capacity exclusion is not resolution, duplicate
  recognition, blocker removal or proof of non-submission. Safe reset/force cannot clear
  the stop, restore POST/GET permission or restore its capacity occupancy.
- Derive this narrow exclusion from the persistent duplicate review-stop classification,
  not merely from a historical remote label or the absence of current GET permission.
  Genuine processing, transient observation deferrals, rate-limited/resumable work,
  temporary network failures and other retained jobs keep existing conservative capacity
  semantics. This does not release capacity for every blocked or temporarily inactive job.
- At capacity three, three persistent duplicate review stops occupy zero active slots;
  five independently eligible fresh candidates may progress through the normal scheduler
  with at most three active capacity-consuming remote jobs, not five simultaneous POSTs.
  Existing rate, artifact and submission-authorization gates still apply. If other
  retained capacity or a run stop leaves work unattempted, report that actual cause.
- Genuine processing and transient GET failures keep SPEC-001's existing per-run
  budgets, backoff and rate controls: default 60 GET attempts, three consecutive
  network/server failures, doubled intervals capped at 30 seconds, daily/429 stops.
  A run with finitely completing requests/waits returns when all selected work is
  resolved, review-stopped, budget-deferred, otherwise stopped or capacity-blocked.
  No wall-clock deadline is added; external repeated quota waits can extend duration.
- `--limit` remains a submission-selection limit, not a promise of that many successes
  or a time limit. Restart may resume permitted transient observations with fresh
  budgets, but not duplicate review stops. Ctrl+C retains existing durable semantics.

## Reporting expectations

Report recognized duplicates as resolved/duplicate. For duplicate review stops, show
review required and automatic observation stopped, including the safe reason and known
upload ID; do not count that attempt as actively observing solely because its retained
remote label says processing. Activity categories may still overlap for other attempts.

While waiting, expose whether the cause is processing backoff, transient failure or
rate reserve; show the next eligible observation time and remaining GET budget for the
waiting job, plus the applicable consecutive-failure budget. Distinguish scheduled
observation from deferred/review work. Final output explains unresolved/deferred and
unattempted selected work and returns control without claiming migration completion.
Local status/dry-run remain network-free and expose the same permission classification.
Distinguish retained duplicate review stops, active observations, capacity-consuming
remote work and fresh candidates. A review label does not itself imply slot occupancy.
Do not attribute unattempted work to duplicate review-stop capacity after this amendment;
`retained capacity or deferred work` is appropriate only when other work actually retains
capacity. No general reporting redesign is required.

## Data, persistence, restart and compatibility

Retain existing IDs, attempt history, evidence codes and independent blockers. No raw
response, activity title or arbitrary HTML is stored. Existing duplicate review stops
must be effective without deleting history, rewriting it as fresh or trusting a changed
manifest. Old recognized duplicate/completion evidence remains resolved. Absence of
expected identifier provenance prevents new linked recognition, not safe retention.

Prefer using existing evidence for the stop; no schema redesign is required by this
contract. If implementation discovers required additional durable context, its version
and upgrade implications require explicit review before implementation. Older binaries
will not honor the new stop; do not downgrade an affected workspace to resume polling.

## Error, integrity and security behavior

Unexpected HTTP, bad field types, identity conflicts, unrelated numeric IDs and
completion contradictions retain SPEC-001's conservative handling. Failed persistence
stops scheduling rather than pretending a result was saved. An unrecognized duplicate
is not authoritative processing failure or proof of non-submission.

HTML is untrusted data: do not follow links, interpret titles as IDs, accept arbitrary
URLs or log parser input. Diagnostics use fixed rejection reasons, not raw excerpts.
False-positive recognition could falsely resolve an unresolved activity and conceal
missing migration work; full attribution and grammar checks are therefore mandatory.
Synthetic fixtures must replace titles, hashes, account information and real paths.

## Relationship to SPEC-001

SPEC-001 remains unchanged. The following narrow extensions/clarifications are approved
through SPEC-002; they identify the affected SPEC-001 wording without silently rewriting it:

- **Authoritative response evidence / AC-06:** supplement the existing complete
  plain-text assertion with the linked grammar above. Current permission to normalize
  markup is insufficient: this variant gets identity from href, not numeric link text.
  Unknown variants still review; this is an explicit behavior extension.
- **Edge cases / error table GET malformed or conflicting / AC-15:** narrow the
  optional safe reobservation policy for `duplicate_unrecognized` to persistent review
  without automatic GET. Other trustworthy observations remain governed by SPEC-001.
- **AC-16 reporting:** clarify that historical processing is not active observation
  when a duplicate review stop applies; expose bounded-wait reasons/budgets.

- **2026-10-01 capacity amendment / SPEC-002 AC-05:** supersede this specification's
  original review-stop occupancy paragraph and the corresponding SPEC-001 implementation
  plan rule that all unresolved processing/deferred jobs retain slots, only for persistent
  `duplicate_unrecognized` review stops. Historical remote state is not rewritten; this
  is a scheduling exception, not a relaxation of SPEC-001 submission/observation safety.

No weakening of AC-03, positive POST permission, reset safety or identity invariants
is authorized. The SPEC-001 plan's observation-blocker mapping and scheduler/reporting
details require only the focused implementation delta described here, not a rewrite.

## Acceptance criteria

- **AC-01:** The exact linked grammar with trusted local context and attributable
  matching upload identity resolves a duplicate, retaining upload and href activity IDs.
- **AC-02:** Wrong identifier, malformed/ambiguous HTML, invalid path/ID, extra prose,
  negation/speculation, multiple links and contradictory fields never manufacture
  resolution. Unexpected HTTP attribution protections and old valid grammar remain intact.
- **AC-03:** Duplicate resolution is durable before observation ends; reopen/restart
  performs neither another POST nor unnecessary GET for that resolved attempt.
- **AC-04:** New and previously persisted unrecognized duplicate evidence stops
  automatic GET in the current and later runs; reset/force cannot erase that stop or
  permit POST. Known IDs and independent evidence survive.
- **AC-05:** Persistent `duplicate_unrecognized` review stops occupy zero active
  submission-capacity slots across persistence/restart/reset, while remaining visible,
  unresolved and prohibited from POST/automatic GET with evidence unchanged. Unrelated
  fresh candidates may progress within configured capacity and all existing gates.
  Other retained processing/transient/rate-deferred work keeps conservative occupancy.
  Mixed batches return when no permitted work can advance; existing polling/backoff,
  rate and interruption bounds remain unchanged.
- **AC-06:** CLI/progress/local preview distinguish resolved duplicates, duplicate
  review stops and actual observation; explain next wait, remaining budgets and
  unattempted selected work and actual capacity occupancy separately from review stops,
  without exposing private payloads or changing --limit semantics.
- **AC-07:** Missing historical context, artifact changes, conflicts and persistence
  faults fail conservatively; compatibility preserves existing terminal evidence and
  all SPEC-001 no-resend guarantees. No raw error/title is persisted for diagnostics.

## Verification

Propose synthetic verification, not live calls or mandatory test-first ceremony:

| Criteria | Evidence to produce during implementation verification |
| --- | --- |
| AC-01–03 | Synthetic GET 200/POST 201 linked assertions, matching context, quote/order variants, escaped plain title; client-to-store integration, reopen and exact zero repeat POST/GET; old plain grammar retained |
| AC-02 | Negation/speculation, unrelated link, `/athletes/123`, zero/negative/abc IDs, multiple links, extra prose, malformed hash/identifier, missing/mismatched echo/context, duplicate attributes, encoded/absolute/query paths, nested markup and completion/identity contradictions |
| AC-02,07 | Generic HTTP error with unrelated numeric ID; existing attribution, partial evidence and stronger-terminal regressions; no trust from anchor text |
| AC-03–05 | Fake-clock jobs resolve or stop after first applicable response; same unknown response cannot consume 60 GETs; old persisted stop with observation-permitting flags still makes zero GET; reset/force/reopen preserve barrier |
| AC-05 | Persist three confirmed historical-processing `duplicate_unrecognized` uploads, close/reopen SQLite, select five fresh eligible candidates at capacity three: zero POST/GET for stopped records, unchanged evidence and review visibility, zero stopped-record slot occupancy, permitted fresh progress and active remote concurrency never above three. Repeat after safe reset/force. Counterexamples retain slots for genuine processing, transient/network and rate deferrals; preserve lower-capacity behavior, 60-GET/three-failure bounds, backoff and interruption evidence. |
| AC-06 | CLI/service composition, waiting/final messages, actual budgets/times, selection limit and overlapping multiple-attempt categories; status/dry-run network bombs |
| AC-07 | Changed/missing artifact and orphan context, failed evidence commit, existing legacy terminal records, synthetic secret/title markers absent from database/events/output |

Run relevant focused and repository-required full checks after implementation, then
perform per-criterion compliance review. This specification's validation proves documentation
consistency only, not implementation or further live acceptance.

## Implementation notes

No implementation plan is created. The user approved the lighter workflow:
Specify -> Human review/approval -> Implement -> Verify -> Compliance review.
A separate detailed plan is not required unless implementation proves materially more
complex than currently understood. No such complexity was identified in this review.
Implementation is recorded in Completion. The focused change retains existing module
boundaries and addresses matching context, structural recognition, evidence-based stop
classification and reporting together. Do not merely widen a regex or lower all budgets.

## Open questions / human decisions

None. The requesting user explicitly resolved all four decisions on 2026-09-30:

1. **Approved — linked duplicate grammar:** all specified attribution, expected
   identifier, complete assertion and validated href conditions apply. Titles are
   presentation only; unsupported, speculative, negated or conflicting forms review.
2. **Approved — persistent review stop:** `duplicate_unrecognized` stops automatic
   observation across restart/reset while retaining trusted IDs, durable submission
   evidence and the prohibition on POST. No blanket stop for unrelated review reasons.
3. **Approved — existing general polling budgets:** genuine processing retains its
   bounded polling/backoff policy; waiting and backoff must be visibly explained.
   No general scheduler redesign or new wall-clock deadline is introduced.
4. **Amended and approved 2026-10-01 — capacity exclusion:** persistent
   `duplicate_unrecognized` review stops occupy no active submission-capacity slot.
   Their POST/automatic GET permissions remain denied and all evidence remains intact.
   All other retained processing/transient/rate-deferred work keeps conservative
   accounting. This explicitly supersedes the 2026-09-30 decision only for these stops.

Final consistency review: the linked grammar explicitly extends SPEC-001's text-only
assertion; the persistent stop narrows its optional duplicate reobservation; reporting
distinguishes retained remote labels from active work. All broader SPEC-001 invariants
remain authoritative. The 2026-10-01 capacity exception is explicitly human-approved;
no additional behavioral decision or approval blocker remains.

## Future-work recommendation: audit performance

No established backlog/future-work document was found in README or docs. Record the
recommendation here rather than introducing a repository-wide planning system:
profile the reported 2,928-activity, approximately 45-minute audit before optimizing.
Investigate parsing, FIT generation/validation, filesystem I/O, repeated hashing,
redundant work, safe concurrency and reusable/incremental artifacts. Preserve
determinism, FIT validation semantics, manifest equivalence and progress reporting.
This is explicitly outside SPEC-002 implementation and has no approved solution.

## Decision log

- 2026-09-30: User requested specification-only follow-up for live duplicate grammar
  and apparent polling hang. Inspected baseline and existing tests; incorporated the
  previously collected sanitized response. No new API or workspace access performed.
- 2026-09-30: Proposed seven criteria and four explicit human decisions. None approved;
  no changes to SPEC-001, production code, tests or implementation plan.

- 2026-09-30: The requesting user approved Decisions 1–4. Recorded Reviewed after
  resolving the decisions and completing the SPEC-001 consistency review, then Approved
  in the same change under the user's explicit conditional approval. No blockers remain.
  The seven acceptance criteria and intended behavioral contract are unchanged.
  A separate implementation-plan document is not required for this focused change;
  implementation, verification and compliance review are subsequent work.

- 2026-09-30: Implemented the approved contract in `45a5ae1c842612101a9ca02adeed811088cb33d2`.
  After synthetic verification and focused compliance review, lifecycle advanced through
  Implemented to Verified. No behavioral amendment, schema change or plan was required.
  SPEC-001 and all seven SPEC-002 acceptance criteria remain unchanged.

- 2026-10-01: Controlled acceptance and prior read-only diagnosis established three
  persistent duplicate review stops occupied all three slots, leaving five fresh
  candidates unattempted with no intent/POST evidence. This matched the original policy;
  it was not an implementation defect or SPEC-001 recovery failure. The user explicitly
  approved excluding these stops from active capacity while retaining all evidence and
  denying POST/automatic GET. Amended AC-05 and narrowly clarified AC-06; IDs remain
  AC-01–AC-07. Other criteria, duplicate grammar and polling budgets are unchanged.
  Current revision returns to Approved pending implementation and fresh verification;
  the prior Verified result remains historical. No implementation, tests, workspace
  access, network requests or separate implementation plan belong to this amendment.

- 2026-10-01: Implemented the approved capacity amendment in
  `851034963af9c45fbd55473cda482c7b802ee944`. Full synthetic verification and independent
  focused compliance review passed. Advanced the amended revision through Implemented
  to Verified without changing the approved criteria or SPEC-001.

## Completion

Current amended revision: **Verified on 2026-10-01**. Amendment approval remains
2026-10-01; original approval and historical verification remain recorded separately.
The pre-existing approved amendment was preserved in
`bb3e75d58588420599ead000652769978616484c` before implementation.

### Verification of the 2026-10-01 capacity amendment

| Criterion | Current synthetic evidence | Result |
| --- | --- | --- |
| AC-01 | Existing linked grammar positives and HTTP composition tests pass unchanged | PASS |
| AC-02 | Existing negative grammar, conflicts, context and unexpected HTTP tests pass unchanged | PASS |
| AC-03 | Existing terminal evidence, write-failure, reset and restart tests pass unchanged | PASS |
| AC-04 | Existing persistent stop and obsolete-blocker tests; reopened stops receive no POST/GET | PASS |
| AC-05 | `test_three_persisted_stops_allow_five_fresh_with_capacity_three` (normal/reset/force); retained processing/network/rate cases in `test_actionable_retained_jobs_still_hold_lower_restart_capacity`; `test_capacity_does_not_follow_generic_observation_refusal` | PASS |
| AC-06 | Updated `test_wait_and_capacity_reporting`; new snapshot and final-event capacity/review-stop assertions; existing local CLI, wait budgets and reporting tests pass | PASS |
| AC-07 | Existing privacy, provenance, persistence and attribution tests pass unchanged; no new schema or raw response persistence | PASS |

The mandatory regression closes and reopens SQLite after persisting three confirmed
processing attempts with `duplicate_unrecognized`. All five fresh activities complete
with exactly five synthetic POSTs and five GETs, peak active capacity three. Stopped
upload IDs receive zero POST/GET; their attempt and blocker evidence remains equal.
Another restart issues no additional requests. Normal reset and force-reset variants
preserve these results. Three genuinely processing, network-deferred or rate-deferred
jobs retain all three slots after reopening at a lower configured capacity of one;
no fresh POST is admitted. Generic observation denial does not release capacity.

Submission and observation permissions are unchanged. The separate pure
`consumes_submission_capacity` predicate excludes only persistent duplicate review
stops from retained processing/deferred jobs. Status shows workspace attempt counts
separately from activity categories; final batch summaries report selected-batch
capacity jobs, review stops and unattempted submissions.

Focused verification: **123 passed**. Full suite: **421 passed**, zero skips/failures
(24.38 seconds). Ruff passed; Black checked **77 files** unchanged; mypy passed
**77 source files**. Local Markdown links/anchors, complete diffs and whitespace were
checked. No diagram changed. The seven added parametrized regression cases supplement
the original 414 tests; only superseded capacity expectations in the existing
SPEC-002 reporting test changed.

Independent read-only compliance review found no blocking defect and required no
fixes. It covered capacity/permission separation, persistent evidence, restart/reset,
temporary deferrals, concurrency and reporting privacy. The reviewer did not run tests;
the executor ran the checks above. No approved behavioral deviation remains.
SPEC-001 text and invariants, response grammar, schemas, dependencies and version are
unchanged. Operational, architecture, troubleshooting and testing guidance were updated.

Limitations: releasing scheduler capacity does not resolve old stopped uploads or
reconstruct discarded response payloads. They remain review-only. Synthetic evidence
does not establish live acceptance. Genuine processing and quota waits retain existing
budgets; no wall-clock SLA or concurrent-writer guarantee is introduced.

No real workspace, credentials, Strava/OAuth or network access occurred. No real reset,
resend, push, merge, tag, release or version change occurred. Next action, requiring
separate live authorization (not executed here):

```powershell
python main.py strava upload "C:\temp\polar-live-acceptance-clean" --limit 5
```

### Historical verification of the 2026-09-30 revision

The following results apply to the prior capacity policy. They do not verify the
2026-10-01 amendment; especially the former AC-05 test deliberately retained review-stop
capacity. Prior release/acceptance recommendations below are historical.


- Implementation: `45a5ae1c842612101a9ca02adeed811088cb33d2`.
- Lifecycle: Verified on 2026-09-30; controlled live acceptance has not been performed
  for this implementation. Human approval metadata and approved behavior are preserved.

| Criterion | Implementation and synthetic evidence | Result |
| --- | --- | --- |
| AC-01 | `responses.linked_duplicate_id`, client expected-context propagation, stored native-attempt context; `test_linked_grammar_positive`, `test_http_composition_reset_restart` including immediate POST duplicate | PASS |
| AC-02 | Full assertion/attribute/path checks before href identity, existing attribution/conflict gates; `test_linked_grammar_negative`, `test_linked_context_and_conflicts`, `test_missing_or_invalid_expected_context`, `test_linked_unexpected_http_cannot_resolve`; all existing response/attribution regressions retained | PASS |
| AC-03 | Existing transactional terminal evidence; HTTP composition/reopen/reset cases assert duplicate IDs and zero repeated POST/GET; `test_linked_result_write_failure_preserves_get_only_restart` verifies failure/recovery | PASS |
| AC-04 | `recovery.duplicate_review_stop` gates GET from retained codes/blockers, independent of old active/observation flags; `test_old_duplicate_evidence_ignores_obsolete_blocker_flags`, HTTP unknown/reset/reopen cases | PASS |
| AC-05 | Existing scheduler restoration/capacity and budgets, with stopped jobs inactive; `test_five_selected_batch_terminates`, `test_wait_and_capacity_reporting`; existing scheduler and crash tests retain genuine-processing, rate, interruption and restart guarantees | PASS |
| AC-06 | Typed review-ID/wait/budget/batch events, live renderer and local action output; `test_wait_and_capacity_reporting`, `test_transient_wait_reports_both_remaining_budgets`, `test_stopped_duplicate_local_cli_is_network_free`; existing reporting and CLI execution cases | PASS |
| AC-07 | No schema or raw-payload persistence additions; native context survives orphan/artifact removal while legacy context is refused; `test_orphaned_native_attempt_retains_matching_context`, `test_legacy_attempt_cannot_invent_linked_identifier_context`, write-failure test and private-title/database assertions; existing persistence/attribution/privacy tests | PASS |

New `tests/test_spec002.py`: **61 passed**. Final complete suite: **414 passed,
zero skips/failures** (22.60 seconds). Ruff passed; Black left **76 files** unchanged;
mypy passed **76 source files**. Existing tests retain their assertions; only two fake
transport signatures gained optional expected-identifier context. No test was deferred.
Local Markdown links/anchors, whitespace, complete diff and privacy/scope checks passed.
No diagrams were changed and no renderer was run.

Focused review covered grammar, expected identity, attribution/conflicts, persistent
stops, reset/restart, mixed batches, genuine processing, reporting/privacy and SPEC-001
invariants. An independent read-only reviewer found a missing known-upload-ID/local-stop
reporting requirement. Fixed by carrying safe IDs through typed decisions/events and
displaying explicit stop details; new local/network-isolation and event assertions pass.
The reviewer did not run tests or repeat the review. Executor inspection and verification
confirmed the fix. Additional review-requested evidence covers linked unexpected HTTP,
legacy/orphan context, evidence-write failure and transient remaining budgets. Existing
rate-wait tests support unchanged rate behavior; no new rate-policy design is claimed.

Initial focused verification also caught a batch-summary regression hiding the last
activity reason; the summary now retains that reason. Existing reporting tests pass
unchanged. No unresolved compliance blocker or behavioral deviation remains.

README, uploader operations, troubleshooting, architecture, testing guidance and the
specification index were updated. No SPEC-001 behavioral text was changed. State schema,
general polling budgets, backoff, capacity policy, dependencies and version are unchanged.

Limitations: old review-stopped records cannot become resolved simply by upgrading;
their raw response was deliberately discarded. Missing trusted legacy context reviews.
New server variants remain conservative. There is no wall-clock SLA across quota waits,
no exactly-once guarantee and no support for concurrent writers or erased history.
Audit-performance optimization remains future work above.

No real credentials, Strava requests, OAuth or real migration workspace access occurred.
No reset/resend of real activities, push, merge, tag, release or version change occurred.

Next separately authorized action: controlled live acceptance. First privately back up
the real workspace, then review local status and dry-run for explicit selected IDs
(these local commands may reconcile state). Existing `duplicate_unrecognized` rows must
remain review-only with zero automatic POST/GET; do not reset them to exercise recognition.
To exercise linked recognition, authorize a separate GET-only diagnostic of a trusted
known upload with locally established native context, using a valid token and no refresh,
retry, redirect or persistence unless separately requested. Use the new parser in memory;
check retained upload identity and href-derived duplicate result. If a full normal-path
upload is desired, separately approve one known safe fresh activity after preview, then
check terminal state and zero repeat submission on a later explicit selection. Stop on
unexpected evidence. No such acceptance operation is authorized by this completion record.
