# Polar altitude and power streams (Sprint 7.4)

The running training session inspected for this sprint has two different
altitude representations. `samples.altitude` has 2,007 values at timestamps
ending in `.384`, ranging from 136.4 to 164.289 m. The exercise's own
`altitude` summary reports min 136.399658, average 154.719238, and max
164.289200 m, matching that stream within summary precision. Its `ascent`
and `descent` summaries are 20.117 and 14.9354 m. In contrast,
`samples.recordedRoute` has 1,981 GPS points at timestamps ending in `.482`,
with route altitudes from -4 to 42 m. The export does not explain the route
altitude's reference or computation. These are distinct measurements; their
timestamps and ranges do not support treating them as interchangeable.

The importer now places populated `samples.altitude` values on independent
domain trackpoints, without requiring GPS. The separate altitude stream wins
for the whole exercise. Route altitude is used only when that exercise has no
populated standalone altitude stream, so values from the two different
altitude ranges are not mixed. FIT and TCX serialize the domain altitude at
the original sample timestamp. Existing domain activities that store altitude
on `Location` remain supported as a fallback. FIT enhanced altitude has 0.2 m
resolution; decoded values can differ from source by up to 0.1 m.

## Power remains unresolved

The same source has no `samples.power` stream. It has 2,007 entries under
`samples.leftPedalCrankBasedPower`; each entry has only `dateTime` and
`currentPower`. There are 1,948 nonzero readings, ranging from 0 to 179.
Representative readings: 0 at 21:37:13.384, 136 at 21:38:03.384, 159 at
21:38:53.384, 58 at 21:53:53.384, and 135 at 22:10:39.384. The exercise
`power` summary says average 252 and maximum 359. Twice the sample arithmetic
mean is about 251.84, close to the summary average; twice the sample maximum
is 358, close to the summary maximum. This is evidence of a relationship,
but the JSON does not specify whether `currentPower` is left-only watts, a
scaled total, or a different pedal metric. Polar's API also lists left- and
right-crank current power as distinct sample types without giving a conversion
for this export shape. See the [Polar AccessLink API profile](https://www.polar.com/polar-api-v4/).

The importer continues to map only an explicit `samples.power[].value`
stream to standard domain power. It leaves `leftPedalCrankBasedPower` out of
standard power and therefore out of FIT `record.power`. No value is doubled
or inferred from the summary. A matching Polar-exported FIT or authoritative
description of this JSON field would be needed to choose a defensible mapping.
