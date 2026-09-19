"""Validate domain exportability and generated XML against bundled Garmin schemas."""

from pathlib import Path

from lxml import etree

from core.errors import ExportError
from domain import Activity
from tcx.serializers.trackpoint import EXT_NAMESPACE, TCX_NAMESPACE, timestamp


class TCXValidationError(ExportError):
    """An activity or generated document cannot be exported as valid TCX."""


class TCXValidator:
    def validate(self, activity: Activity) -> None:
        if not activity.laps or not activity.trackpoints:
            raise TCXValidationError("TCX export requires at least one usable trackpoint.")
        if any(not lap.trackpoints for lap in activity.laps):
            raise TCXValidationError("TCX export cannot represent an empty lap.")
        if any(
            point.cadence is not None and round(point.cadence.rpm) > 254
            for point in activity.trackpoints
        ):
            raise TCXValidationError("TCX cadence must not exceed 254.")

    def validate_xml(self, content: bytes) -> None:
        try:
            root = etree.fromstring(
                content, parser=etree.XMLParser(resolve_entities=False, no_network=True)
            )
            if root.tag != f"{{{TCX_NAMESPACE}}}TrainingCenterDatabase":
                raise TCXValidationError("TCX document has an incorrect root element.")
            schema_dir = Path(__file__).parent
            base = etree.XMLSchema(etree.parse(str(schema_dir / "TrainingCenterDatabasev2.xsd")))
            extension = etree.XMLSchema(etree.parse(str(schema_dir / "ActivityExtensionv2.xsd")))
            if not base.validate(root):
                raise TCXValidationError(f"TCX schema validation failed: {base.error_log}")
            for tpx in root.iter(f"{{{EXT_NAMESPACE}}}TPX"):
                if not extension.validate(tpx):
                    raise TCXValidationError(
                        f"TCX extension validation failed: {extension.error_log}"
                    )
        except (etree.XMLSyntaxError, etree.XMLSchemaParseError, OSError) as error:
            raise TCXValidationError(f"Could not validate TCX XML: {error}") from error

    def validate_streams(self, activity: Activity, content: bytes) -> None:
        """Check that source heart-rate samples survived at trackpoint level."""
        expected = [
            (timestamp(point.timestamp), point.heart_rate.bpm)
            for point in activity.trackpoints
            if point.heart_rate is not None
        ]
        if not expected:
            return
        try:
            root = etree.fromstring(
                content, parser=etree.XMLParser(resolve_entities=False, no_network=True)
            )
            actual: list[tuple[str, int]] = []
            for point in root.iter(f"{{{TCX_NAMESPACE}}}Trackpoint"):
                heart_rate = point.find(f"{{{TCX_NAMESPACE}}}HeartRateBpm/{{{TCX_NAMESPACE}}}Value")
                if heart_rate is None:
                    continue
                time = point.find(f"{{{TCX_NAMESPACE}}}Time")
                if time is None or time.text is None or heart_rate.text is None:
                    raise TCXValidationError("TCX heart-rate trackpoint has no timestamp or value.")
                actual.append((time.text, int(heart_rate.text)))
            if actual != expected:
                raise TCXValidationError(
                    f"TCX heart-rate stream differs from source: expected {len(expected)} "
                    f"trackpoints, found {len(actual)}."
                )
        except (etree.XMLSyntaxError, ValueError) as error:
            raise TCXValidationError(
                f"Could not validate TCX heart-rate stream: {error}"
            ) from error
