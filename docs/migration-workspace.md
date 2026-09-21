# Migration workspace

Run the reusable audit against a Polar user-data export:

```powershell
python main.py audit C:\path\to\polar-export --output C:\path\to\workspace
```

The workspace contains `fits/`, the JSON/CSV/Markdown audit, version 1 JSON and CSV
manifests, and `migration-config.template.json`. FIT is the preferred Strava migration
format based on manual upload tests; TCX remains supported by `convert --format tcx`.

## Manifest version 1

`migration-manifest.json` has one entry for every discovered `training-session-*.json`.
Each entry contains source identity and summary metadata, resolved time, sport mapping,
a workspace-relative FIT path, FIT size and SHA-256, warnings, and one migration status.
It deliberately contains no sensor streams or GPS track.

The stable activity ID is `sha256:<digest>`, where the digest covers the exact bytes of
the source JSON file. Moving or re-extracting unchanged source files does not change the
ID. FIT SHA-256 is separate and detects changed generated output. FIT paths resolve from
the workspace root, so the complete workspace can be moved.

Statuses are `eligible`, `eligible_with_warnings`, `requires_configuration`,
`excluded_summary_only`, `excluded_invalid_source`, and `excluded_unresolved`.
Only entries with a known UTC instant, valid FIT and hash, and no unexplained supported
sensor loss are eligible. Warnings remain visible and may require human review without
preventing upload. Summary-only workouts receive no fabricated records. Routes outside
session bounds remain excluded instead of changing bounds or discarding points. Altitude
that cannot be represented truthfully by the current FIT writer remains unresolved; it
is never clamped or replaced.

## Timezone configuration

An activity whose local timestamps have no explicit Polar offset is
`requires_configuration`. Copy the generated template to a private configuration file,
fill an offset such as `+02:00` under its stable ID, then rerun:

```powershell
python main.py audit C:\path\to\polar-export --output C:\path\to\workspace `
  --config C:\private\migration-config.json --overwrite
```

Configuration version 1 supports only per-activity `timezone_offset`. Offsets must be
between `-14:00` and `+14:00`. Unknown IDs, unsupported versions, extra fields, and an
override that conflicts with an explicit Polar timezone are rejected. The original naive
local timestamp stays in the manifest while the resolved UTC instant records
`manual_override` as its source. Never edit the Polar export.

Review manifest statuses and warnings, update configuration, and rerun until the desired
set is eligible. Keep the workspace and configuration private because summaries and FIT
files contain personal activity data. Existing FITs are validated on rerun; use
`--overwrite` after configuration changes.

## Uploader boundary

A future uploader will consume the manifest, eligible FIT files, and their hashes. It
will not parse Polar JSON, infer timezones, merge streams, reconstruct laps, select
altitude sources, or decide compatibility rules. Those decisions are finalized before
the manifest boundary. Polar exports can contain additional shapes that still require a
new compatibility rule or an explicit exclusion.
