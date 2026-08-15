"""Serialization of one domain lap to a TCX XML element."""

from xml.etree.ElementTree import Element, SubElement

from domain import Lap, TrackPoint
from serialization import Serializer
from tcx.serializers.trackpoint import tag, timestamp


class LapSerializer:
    """Serializes lap metadata and delegates each point to a point serializer."""

    def __init__(self, trackpoint_serializer: Serializer[TrackPoint, Element]) -> None:
        self._trackpoint_serializer = trackpoint_serializer

    def serialize(self, lap: Lap) -> Element:
        """Return an in-memory TCX Lap element."""
        element = Element(tag("Lap"), {"StartTime": timestamp(lap.started_at)})
        SubElement(element, tag("TotalTimeSeconds")).text = str(lap.duration.total_seconds())
        SubElement(element, tag("DistanceMeters")).text = str(lap.distance_m or 0)
        track = SubElement(element, tag("Track"))
        for point in lap.trackpoints:
            track.append(self._trackpoint_serializer.serialize(point))
        return element
