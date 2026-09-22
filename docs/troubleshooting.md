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

Start with the local command:

```powershell
python main.py strava status "C:\path\to\migration-workspace" --details
```

- `local_file_changed`: the FIT file, eligibility, or manifest changed after state was
  recorded. Recreate or review the workspace rather than bypassing its hash check.
- `retryable_failure`: a temporary failure can be selected in a later run.
- `permanent_failure`: inspect the stored category and message before deciding what to do.
- `duplicate`: Strava authoritatively identified an existing activity. It is resolved and
  will not be uploaded again.
- `uncertain`: the POST outcome could not be determined. **Do not blindly reset and
  re-upload it**; Strava may already have accepted the activity. Check Strava and compare
  timestamps before taking an explicit action.

`strava reset --activity-id ID` resets local state only. It never removes a Strava
activity. Completed and duplicate rows require `--force`; use that only after confirming
the remote situation. Never edit or delete the SQLite database to clear an error.

### The uploader is waiting for a rate limit

Short-window reserves cause an automatic pause until the next Strava window. Leave the
process running or stop it with Ctrl+C and resume later.

### The daily API budget was reached

The uploader stops instead of sleeping for many hours. After the reported midnight UTC
reset, run the same `strava upload "<workspace>" --all` command.

### The migration was interrupted

Run `strava status --details`, then resume with `strava upload "<workspace>" --all`.
Accepted uploads with known IDs resume polling without another POST. An interruption
during an unknown POST outcome remains `uncertain` for manual review.
