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
often than once per second. Limits and usage come from `X-RateLimit-Limit` and
`X-RateLimit-Usage`; the uploader does not assume an account's configured quota.

## Credentials and authorization

Register a Strava API application and set credentials in the local environment:

```powershell
$env:STRAVA_CLIENT_ID = "..."
$env:STRAVA_CLIENT_SECRET = "..."
python main.py strava auth C:\MigrationWorkspace --redirect-uri http://localhost
```

Open the displayed Strava URL, authorize the requested `activity:write` scope, and enter
the returned one-time code at the hidden prompt. Short-lived access tokens, the latest
rotating refresh token, expiry, and scope are stored in
`.strava-tokens.json` inside the workspace. The file is gitignored. Tokens and secrets
are never printed or stored in manifests, audit reports, or SQLite error messages.

## Review and upload

```powershell
python main.py strava status C:\MigrationWorkspace
python main.py strava upload C:\MigrationWorkspace --dry-run --limit 5
python main.py strava upload C:\MigrationWorkspace --limit 1
python main.py strava upload C:\MigrationWorkspace --activity-id sha256:...
python main.py strava upload C:\MigrationWorkspace --all
```

Exactly one of `--limit`, `--activity-id`, or `--all` is required. Optional `--from` and
`--to` dates filter the eligible manifest set. Dry-run loads and validates the manifest,
initializes state, verifies selected FIT existence and SHA-256, and performs no Strava
request. Immediately before every real POST the FIT hash is checked again.

State is stored transactionally in `migration-state.sqlite3` in the workspace. Schema
version 1 records pending, uploading, processing, completed, duplicate, retryable failure,
permanent failure, changed local file, uncertain outcome, and skipped states. Completed
and duplicate activities are not selected again. A processing activity with an upload ID
resumes polling. Retryable failures use bounded exponential backoff. A network failure
during POST becomes `uncertain` because Strava provides no idempotency key and the client
cannot know whether the request was accepted; it is not blindly resent.

Regenerated manifests are reconciled by stable activity ID. New activities are added.
Changed FIT hashes or eligibility and disappeared activities become
`local_file_changed`, including completed entries, while remote IDs are preserved for
manual review.

Local failure state can be reset explicitly:

```powershell
python main.py strava reset C:\MigrationWorkspace --activity-id sha256:...
```

Completed or duplicate state requires `--force`. Reset changes local state only and never
deletes a Strava activity.

## Operational limits

Strava uploads are asynchronous and duplicate detection is remote. Local SQLite state
provides restart safety but cannot guarantee remote idempotency across an unknown POST
outcome. A `429` or exhausted header budget stops the affected operation with durable
state. Processing errors and duplicate messages are stored in sanitized form. Keep the
workspace private because FIT files, tokens, and state contain personal information.
