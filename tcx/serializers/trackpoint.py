"""Serialize trackpoints with Garmin TCX and ActivityExtension v2."""

from datetime import UTC, datetime

from lxml import etree

from domain import TrackPoint

TCX_NAMESPACE = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
EXT_NAMESPACE = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"


def tag(name: str) -> str:
    return f"{{{TCX_NAMESPACE}}}{name}"


def ext_tag(name: str) -> str:
    return f"{{{EXT_NAMESPACE}}}{name}"


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class TrackPointSerializer:
    def serialize(self, point: TrackPoint) -> etree._Element:
        element = etree.Element(tag("Trackpoint"))
        etree.SubElement(element, tag("Time")).text = timestamp(point.timestamp)
        if point.location is not None:
            position = etree.SubElement(element, tag("Position"))
            etree.SubElement(position, tag("LatitudeDegrees")).text = str(point.location.latitude)
            etree.SubElement(position, tag("LongitudeDegrees")).text = str(point.location.longitude)
        if point.recorded_altitude_m is not None:
            etree.SubElement(element, tag("AltitudeMeters")).text = str(point.recorded_altitude_m)
        if point.distance_m is not None:
            etree.SubElement(element, tag("DistanceMeters")).text = str(point.distance_m)
        if point.heart_rate is not None:
            heart_rate = etree.SubElement(element, tag("HeartRateBpm"))
            etree.SubElement(heart_rate, tag("Value")).text = str(point.heart_rate.bpm)
        if point.cadence is not None:
            etree.SubElement(element, tag("Cadence")).text = str(round(point.cadence.rpm))
        if point.speed_mps is not None or point.power is not None:
            extensions = etree.SubElement(element, tag("Extensions"))
            tpx = etree.SubElement(extensions, ext_tag("TPX"))
            if point.speed_mps is not None:
                etree.SubElement(tpx, ext_tag("Speed")).text = str(point.speed_mps)
            if point.power is not None:
                etree.SubElement(tpx, ext_tag("Watts")).text = str(point.power.watts)
        return element
