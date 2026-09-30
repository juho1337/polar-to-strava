# Troubleshooting

## Installation

### Python is not found or is too old

Install Python 3.12 or newer from [python.org](https://www.python.org/downloads/). On
systems where `python` names an older interpreter, use `python3` or the full path to the
virtual-environment interpreter.

### The virtual environment is not active

The prompt usually shows `(.venv)` after activation. You can always call its interpreter
directly: `.\.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on macOS/Linux.

### A dependency is missing

From the repository root with the intended environment active, run:

```text
python -m pip install -e ".[dev]"
```

## Polar discovery and audit

### The export path is not found

Extract the Polar download first and pass the extracted directory, not the ZIP file.
Quote paths that contain spaces.

### No training sessions are discovered

Confirm that the extracted tree contains `training-session-*.json`. Daily
`activity-*.json` records are deliberately ignored because they are not workouts.

### An activity requires timezone configuration

Review its source context and `migration-config.template.json`. Add a verified
`timezone_offset` to a private configuration file and rerun audit with `--config` and
`--overwrite`. Do not infer an offset merely to make the item eligible.

### An activity is summary-only, invalid, or unresolved

The audit excludes records it cannot represent truthfully. Inspect `migration-audit.md`
and the detailed JSON or CSV report. Summary-only entries have no usable samples; invalid
entries violate source or timing rules; unresolved entries can contain supported data
that would be lost or cannot be encoded safely.

## Strava authorization

### Client ID or Client Secret is missing

Set `STRAVA_CLIENT_ID` and `STRAVA_CLIENT_SECRET` in the same shell that launches the
command. Do not place real credentials in repository files.

### Localhost shows “connection refused”

This is expected in the current manual OAuth flow because the tool does not start a local
web server. Copy only the `code` query value from the browser address and enter it when
prompted, followed by the returned `scope` value.

### The authorization code fails

Codes are short-lived and usable once. Run `strava auth` again and use the new code.
Ensure the API application's callback domain is `localhost`.

### `activity:write` was not granted

Authorize again and leave the requested upload permission selected. The uploader refuses
to store or use authorization that lacks `activity:write`.

## Upload state

Begin with `python main.py strava status "<workspace>" --details`, then use
`strava upload "<workspace>" --all --dry-run` for local artifact checks. Both are
network-free, but can upgrade/reconcile state. Production execution is enabled after
SPEC-001 synthetic verification; controlled live acceptance remains separately authorized.

| Finding | Safest next action |
| --- | --- |
| Needs review / uncertain submission | Preserve state. An earlier POST may have succeeded. No-ID uncertainty has no automatic search, resend or reconciliation. Inspect the remote situation privately and request diagnosis; checking Strava does not itself authorize resend. |
| Unrecognized duplicate | `duplicate_unrecognized` preserves the known upload ID but stops automatic GET and POST across restart/reset. Historical `processing` is not active polling. Inspect local details for the ID; preserve state and request separately authorized read-only diagnosis. Upgrading the parser does not resolve old records without their discarded response evidence. |
| Processing failure | Preserve the failed upload ID and result. Ordinary reset, artifact repair and reauthorization cannot authorize another POST. |
| Missing/changed FIT | Review or regenerate the artifact through the audit workflow, then use safe reset to verify the correction. Known-ID observation remains independent of that file. |
| Orphaned retained record | Use its stable ID or unfiltered `--all`; it remains visible outside the eligible denominator. Date filters cannot select a record with no reliable retained date. |
| Authorization/retrieval blocker | Repair OAuth configuration for the intended account, then safe reset may allow a bounded GET retry. Account changes do not prove non-submission or clear uncertain history. |
| Completed/duplicate plus local blocker | Remote resolution remains recorded. Correct the local issue without erasing remote evidence. |
| Malformed database or conflicting identity | Stop and preserve files for diagnosis; do not recreate a clean database or guess an identity. |

`strava reset "<workspace>" --activity-id ID` rechecks corrected local conditions and
reports allowed actions. It preserves attempts, IDs, terminal outcomes and unknown
history. `--force` is deprecated and changes nothing. Never delete/edit SQLite, erase
evidence, restore an old backup, or use force as a resend mechanism.

### The uploader is waiting for a rate limit

Processing/transient backoff reports the next eligible GET and remaining budgets.
Unrecognized duplicates require review and do not consume the remaining polling budget.
A finished batch may leave selected submissions unattempted because retained remote work
occupies capacity; the final summary explains this. Persistent duplicate review stops
are excluded from capacity even after restart/reset, while genuine processing and
temporary network/rate deferrals remain capacity-consuming. Exclusion does not authorize
POST or automatic GET for the stopped activity and does not remove its evidence.

Short-window reserves wait to the next natural quarter-hour plus one
second. Ctrl+C stops scheduling while preserving committed evidence. HTTP 429 stops
the batch. Neither event grants POST permission for an earlier attempt.

### The daily API budget was reached

Daily/read reserves stop before the next affected request. Resume permitted work after
midnight UTC; large migrations can span days. GET budgets may defer observations for a
later run, without authorizing new submissions or treating remote processing as failed.

### The migration was interrupted

Inspect local status/details before resuming. After separately authorized controlled
live acceptance, repeating `strava upload "<workspace>" --all` restores
permitted GETs of known IDs and submits only positively safe candidates. An intent with
no saved ID remains review-only even if interruption might have preceded transmission.
Saved completion/duplicate results survive restart and reset.

### An older version opened upgraded state

An archived reader can create an empty legacy table before refusing version 2. Current
code then refuses the mixed layout; v2 recovery evidence remains intact. Preserve the
database and request diagnosis. Do not downgrade or manually remove tables. A pre-upgrade
backup cannot safely replace state after later remote effects. See the
[uploader guide](strava-uploader.md#persistence-and-upgrade).
