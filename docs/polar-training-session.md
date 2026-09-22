# Polar user-data training-session mapping

The inspected Polar user-data export is an `exportVersion: 2.6` training session.
It has top-level `name`, `deviceId`, `startTime`, `stopTime`,
`timeZoneOffset`, `duration`, `maximumHeartRate`, `averageHeartRate`,
`kiloCalories`, `physicalInformationSnapshot`, `exercises[]`, and
`periodData`. The inspected session has one `PADEL` exercise. That exercise
has `startTime`, `stopTime`, `timezoneOffset`, `duration`, `sport`,
`kiloCalories`, `heartRate` summary, `zones`, `samples`, and
`loadInformation`.

The observed sample streams are `heartRate`, `speed`, `distance`, and
`temperature`. Each is an array of objects carrying `dateTime` and, when
recorded, `value`. All four arrays contain 1,951 timestamps, but only
`heartRate` has values. The other streams have no `value` keys and therefore
do not create measurements. No `recordedRoute`, GPS, altitude, cadence, power,
or laps were present in this file. `periodData` identifies a training session;
no explicit pauses or stops were present beyond the session and exercise
boundaries.

The timestamps have no embedded UTC offset. `timeZoneOffset: 180` and
`timezoneOffset: 180` mean UTC+03:00, so a local `21:04:22.490` becomes
`18:04:22.490Z`. A sample with a timezone-aware timestamp retains its stated
offset. Relative `PT...S` sample times, if encountered, are anchored to the
exercise start. Samples without usable timestamps are rejected.

| Polar source | Domain field |
| --- | --- |
| session `startTime` / `stopTime` | `Activity.started_at` / `ended_at` |
| session `duration` | `Activity.recorded_duration_s` |
| session `kiloCalories` | `Activity.calories` |
| session average/maximum HR | `Activity.average_heart_rate_bpm` / `maximum_heart_rate_bpm` |
| exercise `sport` | `Activity.sport`; original exercise sports in extensions |
| `deviceId` | `Device.serial_number` |
| `samples.heartRate[].value` | `TrackPoint.heart_rate` |
| `samples.distance[].value` | `TrackPoint.distance_m` |
| `samples.speed[].value` | `TrackPoint.speed_mps`, converted from km/h |
| `samples.temperature[].value` | `TrackPoint.temperature` |
| `samples.cadence[].value` / `power[].value` | `TrackPoint.cadence` / `power` |
| `recordedRoute[]` latitude/longitude | `TrackPoint.location` |
| exercise `zones.heart_rate[]` | `Activity.zones["heart_rate"]` |

The session boundaries in the inspected file span 1958.265 seconds, while
the reported training duration and exercise boundaries span 1952.224 seconds.
Both are retained: `Activity.duration` is elapsed session time and
`recorded_duration_s` is Polar's reported duration. `inspect` prints both.
When TCX has one lap, its `TotalTimeSeconds` uses the recorded duration.

With one or more exercises, sample streams are merged by absolute timestamp and
sorted chronologically. Mixed exercise sports become `other`; original sports
remain in extensions. Explicit laps become domain laps. Without explicit laps,
one lap spans the session; no extra laps are inferred from exercise count.
Empty-valued streams are ignored. Unknown exercise metadata stays in extensions.

The real inspected file does not establish the exact shape of GPS routes,
explicit laps, cadence, power, or nonempty speed/distance streams in user-data
exports. Tests for these use small illustrative records following the observed
timestamped stream convention. A second real export containing those fields is
needed to verify their exact keys, units, and route timing.
