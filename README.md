# PolarToStrava

Convert Polar Flow user-data training-session JSON exports to Garmin TCX files for manual import to Strava. Python 3.12 or newer is required.

## Install

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Commands

```powershell
python main.py scan --config config.yaml
python main.py inspect C:\PolarExport\training-session-123.json
python main.py convert C:\PolarExport\training-session-123.json --output C:\Converted\training-session-123.tcx
python main.py convert C:\PolarExport --output C:\Converted
```

`scan` discovers `training-session-*.json` recursively from the configured `polar_export` directory. Polar user-data exports contain several categories: `training-session-*.json` holds recorded workouts, while `activity-*.json` holds daily activity tracking and is excluded from workout conversion. `inspect` reports source counts and measurements for one activity. `convert` accepts one JSON file or a directory. Directory conversion processes each file independently and gives each output a deterministic name based on its relative path, with a short hash to prevent filename collisions. An existing output is preserved unless `--overwrite` is supplied. A directory run reports successes, warnings, failures, and totals; its exit code is 1 if any activity fails.

The importer supports Polar's user-data training-session structure (`startTime`, `stopTime`, `timeZoneOffset`, `exercises[]`, and timestamped sample streams) and retains support for the earlier direct activity JSON fixture format. Naive sample timestamps use the exercise's timezone offset in minutes; streams are joined by timestamp, not array position. For multiple exercises, one session Activity is produced, with a single generated lap when no explicit laps exist. Mixed exercise sports map to domain `other`, with each original sport retained in extensions. Missing sensor values remain absent.

## TCX behavior and limits

The importer creates one lap covering the activity when Polar samples exist but explicit Polar laps do not. Activities with no usable trackpoints are rejected. The exporter maps running and trail running to TCX `Running`, cycling and mountain biking to `Biking`, and all other sports to `Other`; for `Other`, the domain sport is retained in TCX Notes.

TCX requires lap Calories, Intensity, and TriggerMethod even when Polar provides no per-lap values. The exporter writes `0`, `Active`, and `Manual` respectively as schema-required defaults. Missing lap distance is derived from recorded point distances when possible, otherwise written as `0`. Maximum speed and heart-rate/cadence summaries are derived from points when present. Trackpoint speed and watts use Garmin ActivityExtension v2. Temperature has no field in the chosen base TCX and ActivityExtension v2 schemas and is retained in the domain only. Other Polar fields can also remain in domain extensions without a TCX equivalent.

The bundled [TrainingCenterDatabasev2.xsd](tcx/TrainingCenterDatabasev2.xsd) and [ActivityExtensionv2.xsd](tcx/ActivityExtensionv2.xsd) were retrieved from Garmin's published schemas at `https://www8.garmin.com/xmlschemas/`. Generated XML is checked against both schemas locally; export has no runtime network dependency.

## Manual Strava verification

1. Run `inspect` on a real Polar training-session file and note its date, sport, recorded and elapsed duration, distance, and sensor counts.
2. Convert that JSON file to TCX and check the command succeeds.
3. In Strava, manually upload the TCX as an activity file.
4. Compare start time, sport, elapsed time, distance, route, altitude, heart rate, cadence, and power against the Polar activity. Record any differences and the Polar JSON structure that produced them.

Strava API uploading, OAuth, migration state, and duplicate detection are not implemented.
