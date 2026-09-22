# Corpus compatibility policy

This document records the source-shape evidence behind compatibility rules. Personal
source files, FIT files, audit reports, and corpus-specific counts remain outside the
repository.

## Route and exercise bounds

Observed `sample timestamp falls outside its exercise` failures came from
`recordedRoute` in Polar export version 2.6. A common shape has an exercise duration
consistent with its start and stop while route points continue within the enclosing
session. Another shape has route points outside the session and remains invalid.

The importer accepts route points after exercise stop only when they remain inside session
start and stop, and records their count in the Activity extension
`route_points_outside_exercise`. Other sensor streams still require exercise bounds.
When explicit source laps end before a session-bounded route, the importer uses one
session-level lap to retain all points, preserves original laps in `source_laps`, and
emits `source_laps_unapplied`. It does not extend an explicit source lap.

## Timezone

Some source files have naive timestamps and no `timeZoneOffset`, `timezoneOffset`, or
explicit timestamp offset. Their instants cannot be determined from the source, so they
require manual configuration. No machine timezone, location, or daylight-saving guess is
used. Explicit exercise or session offsets remain authoritative.

## Laps without absolute timestamps

Some files contain ordered laps with cumulative `splitTime` and individual `duration`,
but no `startTime` or `stopTime`. The importer derives boundaries from exercise start and
the previous and current cumulative split. It requires positive ordered durations,
consistent deltas, sequential lap numbers when supplied, and a final split matching
exercise duration. Inconsistent or incomplete splits remain errors.

## Duplicate timestamps and stream merge

Duplicate timestamps occur in some ordered `recordedRoute` streams. The importer retains
one slot per observation ordinal at each timestamp. Different streams join by ordinal, so
duplicates do not multiply each other. Slots sort by timestamp and retain source order
within equal timestamps. No artificial millisecond offsets are added.

FIT rounds timestamps to whole seconds and can contain multiple ordered record messages
with the same timestamp. Regression fixtures verify that GPS and altitude observation
counts match the source and that FIT output passes decode, CRC, and semantic validation.

## Summary-only workouts

A summary-only source can contain no sample streams, route, laps, distance, calories, or
heart-rate summary. The FIT validator requires a record message, so the exporter excludes
such a workout rather than fabricating a sensor record.

## Remaining review items

Left-crank power remains omitted from domain and FIT. Generic sport mappings and possible
duplicate sources remain review items. Audit reports distinguish source observation
counts, populated domain trackpoints, decoded FIT fields, and failed imports.
