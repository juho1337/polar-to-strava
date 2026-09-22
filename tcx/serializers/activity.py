"""Serialize an activity and map domain sports to TCX sports."""

from typing import cast

from lxml import etree

from domain import Activity, Lap, Sport
from serialization import Serializer
from tcx.serializers.trackpoint import EXT_NAMESPACE, TCX_NAMESPACE, tag, timestamp


def tcx_sport(sport: Sport) -> str:
    if sport in (Sport.RUNNING, Sport.TRAIL_RUNNING, Sport.TREADMILL_RUNNING):
        return "Running"
    if sport in (Sport.CYCLING, Sport.MOUNTAIN_BIKING, Sport.INDOOR_CYCLING):
        return "Biking"
    return "Other"


class ActivitySerializer:
    def __init__(self, lap_serializer: Serializer[Lap, etree._Element]) -> None:
        self._lap_serializer = lap_serializer

    def serialize(self, activity: Activity) -> etree._Element:
        root = etree.Element(
            tag("TrainingCenterDatabase"),
            nsmap=cast(dict[str, str], {None: TCX_NAMESPACE, "ae": EXT_NAMESPACE}),
        )
        activities = etree.SubElement(root, tag("Activities"))
        tcx_activity = etree.SubElement(
            activities, tag("Activity"), Sport=tcx_sport(activity.sport)
        )
        etree.SubElement(tcx_activity, tag("Id")).text = timestamp(activity.started_at)
        for lap in activity.laps:
            element = self._lap_serializer.serialize(lap)
            if len(activity.laps) == 1 and activity.recorded_duration_s is not None:
                total_time = element.find(tag("TotalTimeSeconds"))
                assert total_time is not None
                total_time.text = str(activity.recorded_duration_s)
            if len(activity.laps) == 1 and activity.calories is not None:
                calories = element.find(tag("Calories"))
                assert calories is not None
                calories.text = str(activity.calories)
            tcx_activity.append(element)
        if tcx_sport(activity.sport) == "Other":
            etree.SubElement(tcx_activity, tag("Notes")).text = (
                f"Original sport: {activity.sport.value}"
            )
        return root
