# Manual Strava heart-rate graph investigation

The real Padel training session was accepted by Strava after TCX upload.
Strava displayed the lap average and maximum heart rate, but the manual
upload did not display an HR graph. The local file
`C:\temp\polar-test.tcx` was inspected directly.

| Measurement | Polar sample stream | Written TCX |
| --- | ---: | ---: |
| Samples / trackpoints | 1,951 | 1,951 |
| Timestamped points | 1,951 | 1,951 |
| HR values at trackpoint level | 1,951 | 1,951 |
| Minimum HR | 78 bpm | 78 bpm |
| Arithmetic average HR | 125.13429010763711 bpm | 125.13429010763711 bpm |
| Maximum HR | 140 bpm | 140 bpm |
| Intervals | 1,950 × 1 second | 1,950 × 1 second |
| Unique, increasing timestamps | Yes | Yes |
| First sample | 21:04:22.645+03:00, 79 bpm | 18:04:22.645Z, 79 bpm |
| Last sample | 21:36:52.645+03:00, 127 bpm | 18:36:52.645Z, 127 bpm |

The samples span 1,950 seconds of Polar's 1,952.224-second recorded
duration. They fall within the exercise and session boundaries. The first
source `dateTime` is a local timestamp; the `timezoneOffset` of 180 minutes
is applied to form an absolute UTC instant. In the domain, the first point
is `2025-05-05T21:04:22.645000+03:00` with `HeartRate(bpm=79)`. The
serialized point is:

```xml
<Trackpoint xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Time>2025-05-05T18:04:22.645000Z</Time>
  <HeartRateBpm><Value>79</Value></HeartRateBpm>
</Trackpoint>
```

`Trackpoint`, `Time`, `HeartRateBpm`, and `Value` are all in the Garmin
Training Center Database v2 namespace. Heart rate is present per
trackpoint and in lap summaries. The TCX passes the bundled Garmin
Training Center v2 XSD. A new semantic validator also compares the
serialized trackpoint HR timestamps and values with the source domain
activity before returning the TCX bytes.

**Current conclusion:** the local Polar importer and TCX serializer did not
lose or misplace the HR stream. The cause of Strava's missing graph is not
established by the available files. A same-workout `Export Original` file
from the Polar-synced Strava activity, or evidence of the uploaded activity's
processed stream, is needed to distinguish a Strava import rule from a
display issue. We should not fabricate GPS or distance data to probe this.
