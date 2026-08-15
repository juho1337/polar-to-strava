"""In-memory TCX document builder."""

from typing import cast
from xml.etree.ElementTree import Element, tostring

from domain import Activity
from serialization import Serializer
from tcx.serializers import ActivitySerializer, LapSerializer, TrackPointSerializer
from tcx.validator import TCXValidator


class TCXBuilder:
    """Orchestrates validation and serializers; it never writes a filesystem path."""

    def __init__(
        self,
        activity_serializer: Serializer[Activity, Element] | None = None,
        validator: TCXValidator | None = None,
    ) -> None:
        self._activity_serializer = activity_serializer or ActivitySerializer(
            LapSerializer(TrackPointSerializer())
        )
        self._validator = validator or TCXValidator()

    def build(self, activity: Activity) -> bytes:
        """Serialize one domain activity into UTF-8 XML bytes."""
        self._validator.validate(activity)
        return cast(
            bytes,
            tostring(
                self._activity_serializer.serialize(activity),
                encoding="utf-8",
                xml_declaration=True,
            ),
        )
