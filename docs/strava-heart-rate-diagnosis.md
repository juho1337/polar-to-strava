# TCX heart-rate compatibility finding

During TCX acceptance testing, Strava displayed lap average and maximum heart rate but
did not display the trackpoint heart-rate graph. Direct inspection confirmed that every
source sample was present in the generated TCX with the expected UTC timestamp and value.

| Measurement | Polar sample stream | Written TCX |
| --- | --- | --- |
| Samples / trackpoints | All source samples | Same count |
| Timestamped points | All source timestamps | Same instants in UTC |
| HR values at trackpoint level | Present | Present |
| Unique, increasing timestamps | Yes | Yes |

A source `dateTime` with a valid Polar `timezoneOffset` becomes an absolute UTC instant.
A serialized point has this shape:

```xml
<Trackpoint xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Time>2025-01-01T10:00:00Z</Time>
  <HeartRateBpm><Value>120</Value></HeartRateBpm>
</Trackpoint>
```

`Trackpoint`, `Time`, `HeartRateBpm`, and `Value` are all in the Garmin Training Center
Database v2 namespace. Heart rate is present per trackpoint and in lap summaries. TCX
passes the bundled Garmin Training Center v2 XSD, and semantic validation compares the
serialized trackpoint timestamps and heart-rate values with the domain activity.

The local importer and TCX serializer did not lose or misplace the stream. FIT was adopted
as the preferred Strava migration format after FIT acceptance testing preserved the
continuous heart-rate graph. TCX remains a supported conversion format.
