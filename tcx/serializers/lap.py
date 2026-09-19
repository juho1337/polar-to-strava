"""Serialize one domain lap to Garmin TCX."""

from lxml import etree

from domain import Lap, TrackPoint
from serialization import Serializer
from tcx.serializers.trackpoint import tag, timestamp


class LapSerializer:
    def __init__(self, trackpoint_serializer: Serializer[TrackPoint, etree._Element]) -> None:
        self._trackpoint_serializer = trackpoint_serializer

    def serialize(self, lap: Lap) -> etree._Element:
        element = etree.Element(tag("Lap"), StartTime=timestamp(lap.started_at))
        etree.SubElement(element, tag("TotalTimeSeconds")).text = str(lap.duration.total_seconds())
        distance = lap.distance_m
        if distance is None:
            recorded = [
                point.distance_m for point in lap.trackpoints if point.distance_m is not None
            ]
            distance = max(recorded) - min(recorded) if recorded else 0.0
        etree.SubElement(element, tag("DistanceMeters")).text = str(distance)
        speeds = [point.speed_mps for point in lap.trackpoints if point.speed_mps is not None]
        if speeds:
            etree.SubElement(element, tag("MaximumSpeed")).text = str(max(speeds))
        etree.SubElement(element, tag("Calories")).text = "0"
        rates = [point.heart_rate.bpm for point in lap.trackpoints if point.heart_rate is not None]
        if rates:
            for name, value in (
                ("AverageHeartRateBpm", round(sum(rates) / len(rates))),
                ("MaximumHeartRateBpm", max(rates)),
            ):
                node = etree.SubElement(element, tag(name))
                etree.SubElement(node, tag("Value")).text = str(value)
        etree.SubElement(element, tag("Intensity")).text = "Active"
        cadences = [point.cadence.rpm for point in lap.trackpoints if point.cadence is not None]
        if cadences:
            etree.SubElement(element, tag("Cadence")).text = str(
                round(sum(cadences) / len(cadences))
            )
        etree.SubElement(element, tag("TriggerMethod")).text = "Manual"
        if lap.trackpoints:
            track = etree.SubElement(element, tag("Track"))
            for point in lap.trackpoints:
                track.append(self._trackpoint_serializer.serialize(point))
        return element
