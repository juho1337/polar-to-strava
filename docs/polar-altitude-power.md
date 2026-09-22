# Polar altitude and power streams

Some Polar training sessions contain separate `samples.altitude` and
`samples.recordedRoute` altitude measurements with different timestamps and ranges. The
export does not explain the route altitude's reference or computation. They are treated
as distinct measurements rather than being mixed.

The importer now places populated `samples.altitude` values on independent
domain trackpoints, without requiring GPS. The separate altitude stream wins
for the whole exercise. Route altitude is used only when that exercise has no
populated standalone altitude stream, so values from the two different
altitude ranges are not mixed. FIT and TCX serialize the domain altitude at
the original sample timestamp. Existing domain activities that store altitude
on `Location` remain supported as a fallback. FIT enhanced altitude has 0.2 m
resolution; decoded values can differ from source by up to 0.1 m.

## Power remains unresolved

Some exports contain `samples.leftPedalCrankBasedPower` entries with `dateTime` and
`currentPower`, but no explicit total-power stream. Relationships observed between this
stream and activity summaries do not establish whether `currentPower` is left-only
watts, a scaled total, or another pedal metric. Polar's API lists left- and right-crank
current power as distinct sample types without defining a conversion for this export
shape. See the [Polar AccessLink API profile](https://www.polar.com/polar-api-v4/).

The importer continues to map only an explicit `samples.power[].value`
stream to standard domain power. It leaves `leftPedalCrankBasedPower` out of
standard power and therefore out of FIT `record.power`. No value is doubled
or inferred from the summary. A matching Polar-exported FIT or authoritative
description of this JSON field would be needed to choose a defensible mapping.
