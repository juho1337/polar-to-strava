# SPEC-002: Strava Duplicate Response Recognition & Polling Termination

Status: Approved
Created: 2026-09-30
Reviewed: 2026-09-30 (human decisions resolved and final SPEC-001 consistency review)
Approval: 2026-09-30 (requesting user explicitly approved Decisions 1–4 and the consistent specification)
Implementation: Not started
Implementation plan: Not created; separate document not required for this focused change
Extends: [SPEC-001](001-strava-uploader-recovery.md)
Investigation baseline: `5c5ceaa827c2fad87bdc0b6114272fe376b1f86c`

This specification follows the [SDD workflow](README.md). CURRENT describes inspected
code and separately identified user-reported live observations. TARGET is the behavior
approved on 2026-09-30, not implemented behavior. SPEC-001 remains unchanged and Verified
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
- A review-stopped job remains accounted for conservatively as retained remote work;
  it does not create extra submission capacity. Other already-active jobs progress.
  If capacity leaves selected fresh work unattempted, return with an explicit reason.
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
- **AC-05:** Mixed batches return when only stopped/deferred/capacity-blocked work
  remains. Review stops do not create unsafe capacity; genuine processing, transient
  failures, rate stops and interruption retain existing bounded recovery behavior.
- **AC-06:** CLI/progress/local preview distinguish resolved duplicates, duplicate
  review stops and actual observation; explain next wait, remaining budgets and
  unattempted selected work without exposing private payloads or changing --limit semantics.
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
| AC-05 | Mixed resolved/review/processing batch at capacity with pending candidates; lower-capacity restart; unchanged 60-GET and three-failure limits, backoff, short/daily/429 stops; Ctrl+C during GET/wait retains evidence |
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
Implementation has not started. A focused follow-up should retain existing module
boundaries and address matching context, structural recognition, evidence-based stop
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
4. **Approved — conservative capacity:** retain current conservative accounting and
   explain unattempted work. Any implementation-discovered direct contradiction must
   return to review rather than silently expanding scope into capacity redesign.

Final consistency review: the linked grammar explicitly extends SPEC-001's text-only
assertion; the persistent stop narrows its optional duplicate reobservation; reporting
distinguishes retained remote labels from active work. All broader SPEC-001 invariants
remain authoritative. No additional behavioral decision or approval blocker was found.

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

## Completion

- Artifact: Reviewed and Approved on 2026-09-30; not Implemented or Verified.
- Human Decisions 1–4 resolved; consistency review found no behavioral contradiction,
  remaining question or approval blocker. AC-01–AC-07 are unchanged.
- Implementation/per-AC satisfaction: not performed; verification scenarios proposed.
- Documentation review: links, scope, privacy, grammar/termination consistency and
  complete diff checked before commit; repository checks reported in delivery.
- Next action: implement SPEC-002 directly from this approved specification, then
  verify all seven acceptance criteria and perform a focused compliance review.
  No implementation or implementation plan was created in this approval task.
