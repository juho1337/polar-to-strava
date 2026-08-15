"""Serialization of one activity and its laps into a TCX XML document tree."""

from xml.etree.ElementTree import Element, SubElement, register_namespace

from domain import Activity, Lap
from serialization import Serializer
from tcx.serializers.trackpoint import TCX_NAMESPACE, tag, timestamp


class ActivitySerializer:
    """Builds the document hierarchy and delegates every lap element."""

    def __init__(self, lap_serializer: Serializer[Lap, Element]) -> None:
        self._lap_serializer = lap_serializer

    def serialize(self, activity: Activity) -> Element:
        """Return the root TCX XML element for one activity."""
        register_namespace("", TCX_NAMESPACE)
        root = Element(tag("TrainingCenterDatabase"))
        activities = SubElement(root, tag("Activities"))
        tcx_activity = SubElement(activities, tag("Activity"), {"Sport": activity.sport.value})
        SubElement(tcx_activity, tag("Id")).text = timestamp(activity.started_at)
        for lap in activity.laps:
            tcx_activity.append(self._lap_serializer.serialize(lap))
        return root
