# SPEC-005: Strava activity lookup for duplicate detection

Status: Draft, awaiting owner approval
Created: 2026-11-24
Approval: Pending
Related: [SPEC-002](002-strava-duplicate-response-and-polling-termination.md), [SPEC-004](004-desktop-integration-contract.md)
Risk tier: safety-critical (duplicate safety, credentials, OAuth scopes, contract compatibility). See [change process](README.md).

## Problem and motivation

The JuhoRunLab desktop app classifies each Polar activity as no match, possible duplicate, strong
match, already migrated or check failed. In live mode it has no Strava activity data, so it must
treat **every** activity as `check failed` and the user has to decide each one by hand. The engine
can authorize and upload but has no command that reads the athlete's existing Strava activities.

## Current behavior

- OAuth commands exist (`strava.authorize.begin/complete`, `strava.connection`, `strava.disconnect`).
  Feature `sync` already maps to scope `activity:read_all` and reports `sync_ready`.
- Uploads use scope `activity:write` and a duplicate response from Strava is recognized after the
  fact (SPEC-002). There is no read of existing activities and no pre-upload duplicate check.
- The rate-limit policy already distinguishes read requests (`before_request(..., read=True)`).

## Intended behavior

A new **read-only** engine command `strava.activities.list` (contract 1.1, additive):

1. **Request:** `workspace`, `token_store`, optional `credentials`, `from` and `to` (UTC instants,
   `to` exclusive; at most 400 days per request), optional `max_pages`.
2. **Behavior:** pages through the athlete's activities in that window (Strava
   `GET /athlete/activities` with `after`/`before`, until an empty page), under the existing
   read-aware rate-limit policy. It never writes to Strava and never uploads, edits or deletes.
3. **Output:** one `strava.activities.page` event per page (progress) and a final result with
   `complete` (true only when every page of the whole window was read), `covered_from`,
   `covered_to` (the contiguous range actually read), `pages`, and `activities`. Each activity has
   only what duplicate matching needs: `strava_id`, `start_utc`, `start_local`, `timezone`,
   `sport_type`, `distance_m`, `moving_time_s`, `elapsed_time_s`, `average_hr` (optional),
   `private` and `external_id` (optional). No names, descriptions, routes, polylines, GPS or
   athlete profile data are returned.
4. **Scope:** requires `activity:read_all` (or `activity:read`/`read`, accepted but reported as
   `limited_visibility` because private activities are then invisible and a missing match proves
   nothing). Missing scope is `insufficient_scope`; the host asks the user to reconnect.
5. **Locking:** it takes the workspace lock (token refresh writes the token store), so it returns
   `workspace_locked` while an upload or audit runs.
6. **Failure semantics:** any stop (rate limit, daily limit, cancel, network or server error,
   not authorized) returns the pages read so far with `complete: false` and the error, never a
   silent partial success. The host must treat every part of the window outside
   `[covered_from, covered_to)` as unavailable (`check failed`), never as "no match".

Host behavior (JuhoRunLab, specified here for consistency, implemented in its repository):

- Looks up only the windows needed for the selected activities (their span, padded by the
  duplicate window), in chunks of at most 90 days, before building the preview. The classification
  code is unchanged; only its input (`StravaActivities`, `UnavailableRanges`, `LookupSucceeded`)
  becomes real.
- A failed or partial lookup yields `check failed` for the uncovered activities. Unresolved
  possible duplicates and failed checks still never upload.
- Strava connection screens: connect (browser with loopback redirect, state check), status
  (connected, scopes, athlete id/name), reconnect and disconnect. The client secret is entered
  per session and passed per request; tokens are stored only through the engine's DPAPI token
  store; neither is ever stored in app data, logged or exported.

## Scope and non-goals

In scope: the engine read command, the contract minor version, the host lookup and connection
screens, demo-mode simulation of the lookup.
Out of scope: writing, editing or deleting Strava activities, webhooks, background sync, fetching
streams/GPS/photos, activities of other athletes, changing duplicate thresholds.

## Data, state and compatibility impact

- Contract 1.0 → 1.1: one new command and one new event; hosts that ignore unknown events keep
  working. The `version` command reports 1.1. Existing commands and the CLI are unchanged.
- No change to the manifest, the recovery database or workspace layout. No new files in the
  workspace. The command does not touch recovery state.
- Host caching: the host may keep the returned minimal fields in memory for the session. Whether it
  persists them is an open question below.

## Error and recovery behavior

- `rate.wait` and `rate.snapshot` events are emitted as for uploads; a daily limit stops the lookup
  immediately (`complete: false`). The command never sleeps past the policy's wait limit.
- Cancel (stdin `cancel` line, signal) stops between requests and returns `complete: false`.
- HTTP 401/403 after a refresh attempt: `not_authorized`/`insufficient_scope`, no retry loop.
- Network or 5xx: bounded retries per page (existing policy), then stop with `complete: false`.
- Malformed API response: `invalid_response`, no partial page is trusted.

## Data integrity, security and privacy

- Read-only; the token store is read (and refreshed through the existing client) only.
- Tokens, authorization codes and the client secret never appear in events, results, logs or
  errors (existing secret registration applies).
- Strava activity data is private user data: only the minimal fields above are returned, tests use
  synthetic API responses, and no real API is called in automated tests.
- A lookup never changes duplicate classification rules and never lowers a safety rule: an
  incomplete lookup can only make more activities need an explicit decision.

## Edge cases

- Window with no activities (complete, empty); exactly one page; many pages (pagination end).
- Activities at the window edges and with start times equal to the boundaries (`after`/`before`
  semantics, inclusive/exclusive).
- Private activities with `activity:read` only; trainer and manual activities; missing HR.
- Rate limit hit on page N; daily limit; cancel between pages; token expiring mid-lookup.
- Window longer than 400 days is rejected (`validation_failed`) rather than truncated.
- Duplicate pages or ids across pages (de-duplicated by `strava_id`).

## Acceptance criteria

- AC-01: `strava.activities.list` returns the activities in the window with exactly the specified minimal fields and no names, routes or profile data.
- AC-02: `complete` is true only when pagination reached an empty page; otherwise `complete` is false and `covered_from`/`covered_to` describe the contiguous range read.
- AC-03: Rate-limit, daily-limit, cancel, network and server stops return the pages read so far with `complete: false` and the error code; none is reported as success.
- AC-04: Missing read scope returns `insufficient_scope`; `activity:read` only returns activities with `limited_visibility: true`.
- AC-05: The command performs no writing request to Strava and never changes recovery state, the manifest or the workspace files other than the token store refresh.
- AC-06: It holds the workspace lock and returns `workspace_locked` while another writer runs.
- AC-07: A window over 400 days or with `to <= from` is rejected without any request.
- AC-08: Tokens, codes and the client secret never appear in any output (checked with sentinel values).
- AC-09: The `version` command reports contract 1.1; contract 1.0 hosts are unaffected and the schema/examples/docs list the command and event.
- AC-10: Host: a failed or partial lookup produces `check failed` for uncovered activities; a successful lookup feeds the existing duplicate classification; an unresolved possible duplicate or failed check never uploads.
- AC-11: Host: connection screens never store, log or export the client secret or tokens; disconnect and reconnect work; `workspace_locked` is shown as "an upload is running".
- AC-12: Demo mode simulates the lookup, including a partial result, without any network access, labelled as simulated.
- AC-13: All automated tests use synthetic API responses and mocked transport; no real Strava call is made.

## Open questions for the owner

1. **Scope:** request `activity:read_all` (private activities included, reliable duplicate detection; one consent together with `activity:write`) or `activity:read` only? Proposal: `activity:read_all`, falling back to a clearly marked limited mode.
2. **Persistence:** keep looked-up Strava activity data only in memory per session, or cache the minimal fields (no names) in the app data folder to avoid repeated lookups, with a "clear" action in Settings? Proposal: memory only for the first version.
3. **Client credentials:** Strava requires the user's own API application (client id and secret). Enter the secret per session (nothing stored, proposal), or store it through the OS credential facility?

## Decision log

- 2026-11-24: Draft written from the merged engine contract and the app's current live-mode limitation.

## Completion

Filled in with the implementation PRs (engine and app).
