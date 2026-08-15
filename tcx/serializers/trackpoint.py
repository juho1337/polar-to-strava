"""Serialization of one domain trackpoint to a TCX XML element."""

from datetime import datetime
from xml.etree.ElementTree import Element, SubElement

from domain import TrackPoint

TCX_NAMESPACE = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"


def tag(name: str) -> str:
    """Build a namespaced TCX element tag."""
    return f"{{{TCX_NAMESPACE}}}{name}"


def timestamp(value: datetime) -> str:
    """Format timezone-aware domain times as portable XML timestamps."""
    return value.isoformat().replace("+00:00", "Z")


class TrackPointSerializer:
    """Serializes optional point measurements without handling laps or activities."""

    def serialize(self, point: TrackPoint) -> Element:
        """Return an in-memory TCX Trackpoint element."""
        element = Element(tag("Trackpoint"))
        SubElement(element, tag("Time")).text = timestamp(point.timestamp)
        if point.location is not None:
            position = SubElement(element, tag("Position"))
            SubElement(position, tag("LatitudeDegrees")).text = str(point.location.latitude)
            SubElement(position, tag("LongitudeDegrees")).text = str(point.location.longitude)
            if point.location.altitude_m is not None:
                SubElement(element, tag("AltitudeMeters")).text = str(point.location.altitude_m)
        if point.distance_m is not None:
            SubElement(element, tag("DistanceMeters")).text = str(point.distance_m)
        if point.heart_rate is not None:
            heart_rate = SubElement(element, tag("HeartRateBpm"))
            SubElement(heart_rate, tag("Value")).text = str(point.heart_rate.bpm)
        if point.cadence is not None:
            SubElement(element, tag("Cadence")).text = str(round(point.cadence.rpm))
        return element
