# Resumable Strava uploader

The uploader starts at a completed migration workspace. It reads
`migration-manifest.json` and workspace-relative FIT paths; it never reads Polar JSON or
changes migration eligibility.

## Strava API contract

The implementation follows Strava's official [authentication](https://developers.strava.com/docs/authentication/),
[upload](https://developers.strava.com/docs/uploads/), [API reference](https://developers.strava.com/docs/reference/),
and [rate limit](https://developers.strava.com/docs/rate-limits/) documentation. Uploading
requires only `activity:write`. FIT is sent as multipart form data to `POST /api/v3/uploads`.
The returned upload ID is persisted and polled through `GET /api/v3/uploads/{id}` no more
often than once per second. Limits and usage come from `X-RateLimit-Limit`,
`X-RateLimit-Usage`, `X-ReadRateLimit-Limit`, and `X-ReadRateLimit-Usage`; the uploader
does not assume an account's configured quota.

## Credentials and authorization

Follow the [Strava setup guide](strava-setup.md) to register a Strava API application,
configure `localhost` as its callback domain, and set credentials in the local environment:

```powershell
$env:STRAVA_CLIENT_ID = "YOUR_CLIENT_ID"
$env:STRAVA_CLIENT_SECRET = "YOUR_CLIENT_SECRET"
python main.py strava auth "<workspace>" --redirect-uri http://localhost
```

Open the displayed Strava URL and authorize the requested `activity:write` scope. The CLI
does not run a callback server, so a localhost connection error is expected. Copy only
the returned `code` query value into the hidden prompt, then enter the returned `scope`
query value. Short-lived access tokens, the latest rotating refresh token, expiry, and
scope are stored in
`.strava-tokens.json` inside the workspace. The file is gitignored. Tokens and secrets
are never printed or stored in manifests, audit reports, or SQLite error messages.

## Review and upload

```powershell
python main.py strava status "<workspace>"
python main.py strava status "<workspace>" --details
python main.py strava upload "<workspace>" --dry-run --limit 5
python main.py strava upload "<workspace>" --limit 5
python main.py strava upload "<workspace>" --activity-id sha256:...
python main.py strava upload "<workspace>" --all
```

Exactly one of `--limit`, `--activity-id`, or `--all` is required. `--limit N` selects at
most N new or retryable activities and also resumes every matching activity already in
Strava processing. Optional `--from` and `--to` dates filter the eligible manifest set.
Dry-run loads and validates the manifest,
initializes state, verifies selected FIT existence and SHA-256, and performs no Strava
request. Immediately before every real POST the FIT hash is checked again.

Uploads use a bounded asynchronous pipeline. The default allows three submitted uploads
to be processing at once, polls them in turn, and applies bounded polling backoff. Set a
smaller or larger bound with `--max-in-flight`, up to 10. Progress reports migrated and
authoritative duplicate activities as resolved. Pending, processing, retryable, and
uncertain outcomes remain unresolved; permanent, changed-file, and uncertain outcomes
remain visible for review. `strava status` calculates this only from the local manifest,
FIT metadata, and SQLite state and makes no Strava request.

The status meanings are:

- `completed`: Strava created the activity; it counts as migrated and resolved.
- `duplicate`: Strava authoritatively reported a duplicate; it counts as resolved.
- `pending`, `uploading`, and `processing`: work remains active or available.
- `retryable_failure`: a later selected run may retry it.
- `permanent_failure`, `local_file_changed`, and `uncertain`: user review is required.
- `skipped`: the uploader did not migrate it and it does not count as resolved.

State is stored transactionally in `migration-state.sqlite3` in the workspace. Schema
version 1 records pending, uploading, processing, completed, duplicate, retryable failure,
permanent failure, changed local file, uncertain outcome, and skipped states. Completed
and duplicate activities are not selected again. A processing activity with an upload ID
resumes polling. Retryable failures use bounded exponential backoff. Pressing Ctrl+C
stops scheduling and leaves the last durable state available for a later `--all` resume.
A network failure
during POST becomes `uncertain` because Strava provides no idempotency key and the client
cannot know whether the request was accepted; it is not blindly resent.

Regenerated manifests are reconciled by stable activity ID. New activities are added.
Changed FIT hashes or eligibility and disappeared activities become
`local_file_changed`, including completed entries, while remote IDs are preserved for
manual review.

Local failure state can be reset explicitly:

```powershell
python main.py strava reset "<workspace>" --activity-id sha256:...
```

Completed or duplicate state requires `--force`. Reset changes local state only and never
deletes a Strava activity.

## Operational limits

Strava uploads are asynchronous and duplicate detection is remote. Local SQLite state
provides restart safety but cannot guarantee remote idempotency across an unknown POST
outcome. Each response updates observed overall 15-minute and daily usage from Strava's
rate headers. Before another request, the uploader preserves a configurable safety
reserve (`--rate-limit-reserve`, default 10). Reaching the short-window reserve waits
until the next natural 15-minute boundary and shows the resume time. Reaching the daily
reserve stops cleanly and reports the midnight UTC reset. Missing or malformed headers
do not invent a quota. HTTP `429` also stops scheduling with durable state. Processing
errors and duplicate messages are stored in sanitized form. Keep the workspace private
because FIT files, tokens, and state contain personal information. Never delete
`migration-state.sqlite3` during a migration; it is the record that prevents completed
uploads from being selected again. A large migration can span multiple days when the
daily safety reserve is reached; rerun the same `--all` command after the reported UTC
reset to continue from durable state.
