"""Build and validate in-memory TCX documents."""

from lxml import etree

from domain import Activity
from serialization import Serializer
from tcx.serializers import ActivitySerializer, LapSerializer, TrackPointSerializer
from tcx.validator import TCXValidator


class TCXBuilder:
    def __init__(
        self,
        activity_serializer: Serializer[Activity, etree._Element] | None = None,
        validator: TCXValidator | None = None,
    ) -> None:
        self._activity_serializer = activity_serializer or ActivitySerializer(
            LapSerializer(TrackPointSerializer())
        )
        self._validator = validator or TCXValidator()

    def build(self, activity: Activity) -> bytes:
        self._validator.validate(activity)
        content = etree.tostring(
            self._activity_serializer.serialize(activity), encoding="utf-8", xml_declaration=True
        )
        self._validator.validate_xml(content)
        self._validator.validate_streams(activity, content)
        return content
