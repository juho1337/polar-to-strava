# Sprint 8.1 corpus compatibility evidence and policy

The Sprint 8 audit covered 2,928 Polar `training-session-*.json` files. This
document records the source evidence used for the compatibility rules. Personal
source files, FIT files, and audit reports remain outside the repository.

## Route and exercise bounds

All 153 `sample timestamp falls outside its exercise` failures came from
`recordedRoute` in export version 2.6. No other supported sample stream caused
this failure. Every affected exercise's `duration` agrees with its own start and
stop timestamps. In 140 files, every route point outside the exercise remains
inside the enclosing session. In 13 files, route points extend beyond the
session, in some cases by hours. The largest exercise overrun observed was
45,952.27 seconds. Representative session-bounded source:
`training/1/training-session-2019-08-08-3744481431-f1f73a69-deee-4529-88c0-d1a687ce106f.json`.

The importer accepts route points after exercise stop only when they remain
inside session start and stop, and records their count in the Activity extension
`route_points_outside_exercise`. Other sensor streams still require exercise
bounds. Route points outside the session remain errors. For eight of the 140
session-bounded files, explicit source laps end before the route. The importer
uses one session-level lap to retain all points and preserves the original lap
objects in `source_laps`; `source_laps_unapplied` produces an audit warning. It
does not extend an explicit source lap beyond its stated stop time.

## Timezone

All 39 timezone failures have naive timestamps, no `timeZoneOffset` or
`timezoneOffset` field at session or exercise level, and no timestamp string
with an explicit UTC offset. A `speedCalibrationOffset` field in one file is
unrelated to timezone. Their instants cannot be determined from the source, so
all 39 remain failed for manual review. Existing explicit exercise/session
offset handling is unchanged; no machine timezone, location, or DST guess is
used.

## Laps without absolute timestamps

All 37 affected files have ordered laps with cumulative `splitTime` and
individual `duration`, but neither `startTime` nor `stopTime`. Every adjacent
split difference equals the next lap's duration within 0.01 second, and every
last split matches exercise elapsed duration within 0.01 second. The importer
derives each boundary from exercise start plus the previous/current cumulative
split. It requires positive, ordered durations, consistent deltas, sequential
lap numbers when supplied, and a final split matching exercise duration.
Inconsistent or incomplete splits remain errors. FIT multi-lap messages use
each explicit lap duration for their required `total_timer_time` field.

## Duplicate timestamps and stream merge

Across the real corpus, duplicate timestamps occur in three `recordedRoute`
streams and no other populated sample stream. Together they contain 7,102 route
rows beyond one row per timestamp. Two sessions contain large runs of repeated
coordinate/altitude values at one timestamp; the third contains two distinct
positions at one timestamp. All three route arrays are ordered by timestamp.
The importer now retains one slot per observation ordinal at each timestamp.
Different streams join by ordinal, so duplicate streams do not multiply each
other. Slots are sorted by timestamp, then retain source order within equal
timestamps. Every route row becomes a TrackPoint GPS observation, even if its
timestamp and coordinate equal another row. The domain does not require unique
timestamps. FIT rounds timestamps to whole seconds and may likewise contain
multiple ordered record messages with the same timestamp; the existing accepted
Running FIT and the regression tests validate that behavior. No artificial
millisecond offsets are added.

The three known route cases now import **4,007/4,157/5,310** GPS observations,
matching the source counts exactly. Their altitude counts also match, and
their FIT files pass local decode, CRC, and semantic validation.

## Summary-only workout

`training/1/training-session-2025-01-21-8038300381-cb4deb94-cc0b-4891-b00d-dc4a41a378e3.json`
is a Treadmill Running session lasting 1,529.819 seconds. It has no sample
streams, route, laps, distance, calories, or HR summary. The installed fit-tool
validator rejects an activity FIT file without a record message. The exporter
therefore keeps this workout failed rather than fabricating a sensor record.

## Scope

Left-crank power remains omitted from domain/FIT and reported separately.
Generic sport mappings and duplicate source candidates remain review items.
The audit reports source observation counts separately from populated domain
TrackPoint and decoded FIT field counts; failed imports are included only in
the source totals.
