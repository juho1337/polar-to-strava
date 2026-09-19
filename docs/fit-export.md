# Experimental FIT export (Sprint 7.3)

PolarToStrava supports TCX and FIT. TCX remains the default. Use `--format fit`
with a `.fit` output path to create an experimental FIT activity for manual
Strava upload. FIT was added because Strava accepted our valid TCX with all
1,951 heart-rate points but did not display its heart-rate graph. **A generated
FIT has not yet been manually accepted and checked in Strava.**

FIT output uses `fit-tool==0.9.16`. Its installed package metadata declares
Python 3.12 support and BSD-3-Clause licensing. The package writes a FIT
header and CRC and can decode and validate its output. This states package
facts only; it is not a legal conclusion about the FIT protocol. This
repository currently has no declared project license.

The exporter writes `file_id` (manufacturer `development`, a local product
identifier and a deterministic identifier derived from the activity ID), timer
start/stop events, one standard `record` per domain trackpoint, one `lap` per
domain lap, one `session`, and one `activity` summary. It does not claim to be
Polar hardware or write a `device_info` message for the original device.
Missing sensor values stay absent. fit-tool accepts latitude and longitude in
degrees and converts them to FIT wire semicircles. The source's timezone-aware
timestamps become UTC instants; FIT's whole-second timestamps are rounded to
the nearest second, with half-second ties rounded upward. Distinct source
samples less than one second apart may share a FIT timestamp; samples are
retained in source order. Sensor field precision is limited by FIT scaling.
Cadence and temperature are rounded to integers.

| Domain sport | FIT sport / sub-sport |
| --- | --- |
| Running, treadmill running, trail running | running / generic, treadmill, trail |
| Cycling, indoor cycling, mountain biking | cycling / generic, indoor cycling, mountain |
| Walking, hiking | walking / generic; hiking / generic |
| Swimming, open-water swimming | swimming / lap swimming, open water |
| Triathlon | multisport / triathlon |
| Strength training, stretching, yoga | training / strength training, flexibility training, yoga |
| Skiing, cross-country skiing | alpine skiing, cross-country skiing / generic |
| Rowing | rowing / generic |
| Padel | racket / padel |
| Tennis | tennis / generic |
| Other indoor, other outdoor, other | generic / generic |

Unknown Polar sports fall back to domain `other` and FIT generic. The domain
has activity-level recorded time but no recorded time per lap; for multiple
laps with an activity-level recorded duration, the exporter leaves individual
lap timer durations absent.
Activity and session timer duration use recorded duration when supplied.
The FIT exporter requires at least one lap and one trackpoint.

The exporter decodes its bytes with CRC verification and checks FIT conformance,
message counts, record timestamps, heart rate and sensor values, and session
summaries before writing. This local validation does not predict Strava's
display behavior. The manual acceptance step is to upload the generated FIT
in Strava and compare its heart-rate graph and summary with the Polar-synced
original activity.
