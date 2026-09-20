"""Encode and verify FIT activities using fit-tool."""

from __future__ import annotations

from datetime import UTC, datetime
from math import isclose
from zlib import crc32

from fit_tool.fit_file import FitFile  # type: ignore[import-untyped]
from fit_tool.fit_file_builder import FitFileBuilder  # type: ignore[import-untyped]
from fit_tool.profile.messages.activity_message import (  # type: ignore[import-untyped]
    ActivityMessage,
)
from fit_tool.profile.messages.event_message import EventMessage  # type: ignore[import-untyped]
from fit_tool.profile.messages.file_id_message import FileIdMessage  # type: ignore[import-untyped]
from fit_tool.profile.messages.lap_message import LapMessage  # type: ignore[import-untyped]
from fit_tool.profile.messages.record_message import RecordMessage  # type: ignore[import-untyped]
from fit_tool.profile.messages.session_message import SessionMessage  # type: ignore[import-untyped]
from fit_tool.profile.profile_type import (  # type: ignore[import-untyped]
    Event,
    EventType,
    FileType,
    Manufacturer,
    SubSport,
)
from fit_tool.profile.profile_type import (
    Sport as FITSport,
)

from core.errors import ExportError
from domain import Activity, Sport, TrackPoint

# The installed profile has PADEL as a racket sub-sport, not as a sport.
SPORTS: dict[Sport, tuple[FITSport, SubSport]] = {
    Sport.RUNNING: (FITSport.RUNNING, SubSport.GENERIC),
    Sport.TREADMILL_RUNNING: (FITSport.RUNNING, SubSport.TREADMILL),
    Sport.TRAIL_RUNNING: (FITSport.RUNNING, SubSport.TRAIL),
    Sport.WALKING: (FITSport.WALKING, SubSport.GENERIC),
    Sport.HIKING: (FITSport.HIKING, SubSport.GENERIC),
    Sport.CYCLING: (FITSport.CYCLING, SubSport.GENERIC),
    Sport.INDOOR_CYCLING: (FITSport.CYCLING, SubSport.INDOOR_CYCLING),
    Sport.MOUNTAIN_BIKING: (FITSport.CYCLING, SubSport.MOUNTAIN),
    Sport.SWIMMING: (FITSport.SWIMMING, SubSport.LAP_SWIMMING),
    Sport.OPEN_WATER_SWIMMING: (FITSport.SWIMMING, SubSport.OPEN_WATER),
    Sport.TRIATHLON: (FITSport.MULTISPORT, SubSport.TRIATHLON),
    Sport.STRENGTH_TRAINING: (FITSport.TRAINING, SubSport.STRENGTH_TRAINING),
    Sport.STRETCHING: (FITSport.TRAINING, SubSport.FLEXIBILITY_TRAINING),
    Sport.YOGA: (FITSport.TRAINING, SubSport.YOGA),
    Sport.SKIING: (FITSport.ALPINE_SKIING, SubSport.GENERIC),
    Sport.CROSS_COUNTRY_SKIING: (FITSport.CROSS_COUNTRY_SKIING, SubSport.GENERIC),
    Sport.ROWING: (FITSport.ROWING, SubSport.GENERIC),
    Sport.PADEL: (FITSport.RACKET, SubSport.PADEL),
    Sport.TENNIS: (FITSport.TENNIS, SubSport.GENERIC),
    Sport.OTHER_INDOOR: (FITSport.GENERIC, SubSport.GENERIC),
    Sport.OTHER_OUTDOOR: (FITSport.GENERIC, SubSport.GENERIC),
    Sport.OTHER: (FITSport.GENERIC, SubSport.GENERIC),
}


def fit_time(value: datetime) -> int:
    """UTC epoch milliseconds; FIT stores whole seconds, rounded half up."""
    return int(value.astimezone(UTC).timestamp() * 1000 + 500) // 1000 * 1000


def _record(point: TrackPoint) -> RecordMessage:
    record = RecordMessage()
    record.timestamp = fit_time(point.timestamp)
    if point.heart_rate is not None:
        record.heart_rate = point.heart_rate.bpm
    if point.location is not None:
        # fit-tool's public properties take degrees and convert to wire semicircles.
        record.position_lat = point.location.latitude
        record.position_long = point.location.longitude
    if point.recorded_altitude_m is not None:
        record.enhanced_altitude = point.recorded_altitude_m
    if point.distance_m is not None:
        record.distance = point.distance_m
    if point.speed_mps is not None:
        record.enhanced_speed = point.speed_mps
    if point.cadence is not None:
        record.cadence = round(point.cadence.rpm)
    if point.power is not None:
        record.power = point.power.watts
    if point.temperature is not None:
        record.temperature = round(point.temperature.celsius)
    return record


class FITBuilder:
    """Build FIT bytes and compare the decoded sensor stream with the source."""

    def build(self, activity: Activity) -> bytes:
        if not activity.laps or not activity.trackpoints:
            raise ExportError("FIT activity requires a lap with recorded points")
        builder = FitFileBuilder(auto_define=True)
        file_id = FileIdMessage()
        file_id.type = FileType.ACTIVITY
        file_id.manufacturer = Manufacturer.DEVELOPMENT.value
        file_id.product = 1  # PolarToStrava's local creator identifier, not a Polar product.
        file_id.serial_number = crc32(activity.id.encode()) or 1
        file_id.time_created = fit_time(activity.started_at)
        builder.add(file_id)

        start = EventMessage()
        start.timestamp = fit_time(activity.started_at)
        start.event = Event.TIMER
        start.event_type = EventType.START
        builder.add(start)

        for point in activity.trackpoints:
            builder.add(_record(point))

        stop = EventMessage()
        stop.timestamp = fit_time(activity.ended_at)
        stop.event = Event.TIMER
        stop.event_type = EventType.STOP
        builder.add(stop)

        for index, source_lap in enumerate(activity.laps):
            lap = LapMessage()
            lap.message_index = index
            lap.event = Event.LAP
            lap.event_type = EventType.STOP
            lap.start_time = fit_time(source_lap.started_at)
            lap.timestamp = fit_time(source_lap.ended_at)
            lap.total_elapsed_time = source_lap.duration.total_seconds()
            # The domain only has activity-level recorded duration.
            if len(activity.laps) == 1:
                lap.total_timer_time = (
                    activity.recorded_duration_s
                    if activity.recorded_duration_s is not None
                    else source_lap.duration.total_seconds()
                )
            elif activity.recorded_duration_s is None:
                lap.total_timer_time = source_lap.duration.total_seconds()
            if source_lap.distance_m is not None:
                lap.total_distance = source_lap.distance_m
            if source_lap.ascent_m is not None:
                lap.total_ascent = source_lap.ascent_m
            if source_lap.descent_m is not None:
                lap.total_descent = source_lap.descent_m
            builder.add(lap)

        session = SessionMessage()
        session.message_index = 0
        session.event = Event.SESSION
        session.event_type = EventType.STOP
        session.start_time = fit_time(activity.started_at)
        session.timestamp = fit_time(activity.ended_at)
        session.total_elapsed_time = activity.duration.total_seconds()
        session.total_timer_time = (
            activity.recorded_duration_s
            if activity.recorded_duration_s is not None
            else activity.duration.total_seconds()
        )
        session.sport, session.sub_sport = SPORTS[activity.sport]
        session.first_lap_index = 0
        session.num_laps = len(activity.laps)
        if activity.distance_m is not None:
            session.total_distance = activity.distance_m
        if activity.calories is not None:
            session.total_calories = activity.calories
        if activity.average_heart_rate_bpm is not None:
            session.avg_heart_rate = activity.average_heart_rate_bpm
        if activity.maximum_heart_rate_bpm is not None:
            session.max_heart_rate = activity.maximum_heart_rate_bpm
        if activity.ascent_m is not None:
            session.total_ascent = activity.ascent_m
        if activity.descent_m is not None:
            session.total_descent = activity.descent_m
        builder.add(session)

        summary = ActivityMessage()
        summary.timestamp = fit_time(activity.ended_at)
        summary.num_sessions = 1
        summary.total_timer_time = session.total_timer_time
        summary.event = Event.ACTIVITY
        summary.event_type = EventType.STOP
        builder.add(summary)

        try:
            content = builder.build_bytes()
            decoded = FitFile.from_bytes(content, check_crc=True)
            decoded.validate(raise_on_error=True)
            records = [
                item.message for item in decoded.records if isinstance(item.message, RecordMessage)
            ]
            if len(records) != len(activity.trackpoints):
                raise ExportError("FIT record count differs from source")
            for source, record in zip(activity.trackpoints, records, strict=True):
                if record.timestamp != fit_time(source.timestamp):
                    raise ExportError("FIT record timestamp differs from source")
                expected_hr = source.heart_rate.bpm if source.heart_rate is not None else None
                if record.heart_rate != expected_hr:
                    raise ExportError("FIT heart-rate stream differs from source")
                expected = {
                    "position_lat": source.location.latitude if source.location else None,
                    "position_long": source.location.longitude if source.location else None,
                    "enhanced_altitude": source.recorded_altitude_m,
                    "distance": source.distance_m,
                    "enhanced_speed": source.speed_mps,
                    "cadence": round(source.cadence.rpm) if source.cadence else None,
                    "power": source.power.watts if source.power else None,
                    "temperature": (
                        round(source.temperature.celsius) if source.temperature else None
                    ),
                }
                for field, value in expected.items():
                    actual = getattr(record, field)
                    tolerance = 0.101 if field == "enhanced_altitude" else 0.02
                    if value is None and actual is not None:
                        raise ExportError(f"FIT fabricated {field}")
                    if value is not None and (
                        actual is None or not isclose(actual, value, abs_tol=tolerance)
                    ):
                        raise ExportError(f"FIT {field} differs from source")
            for kind in (FileIdMessage, LapMessage, SessionMessage, ActivityMessage):
                if not any(isinstance(item.message, kind) for item in decoded.records):
                    raise ExportError(f"FIT missing {kind.__name__}")
            laps = [
                item.message for item in decoded.records if isinstance(item.message, LapMessage)
            ]
            sessions = [
                item.message for item in decoded.records if isinstance(item.message, SessionMessage)
            ]
            summaries = [
                item.message
                for item in decoded.records
                if isinstance(item.message, ActivityMessage)
            ]
            if len(laps) != len(activity.laps) or len(sessions) != 1 or len(summaries) != 1:
                raise ExportError("FIT lap, session or activity count differs from source")
            actual_session = sessions[0]
            if (
                actual_session.sport != SPORTS[activity.sport][0].value
                or actual_session.sub_sport != SPORTS[activity.sport][1].value
                or actual_session.num_laps != len(activity.laps)
                or actual_session.start_time != fit_time(activity.started_at)
                or actual_session.timestamp != fit_time(activity.ended_at)
                or actual_session.total_calories != activity.calories
            ):
                raise ExportError("FIT session differs from source")
            for field, expected_value in (
                ("total_elapsed_time", activity.duration.total_seconds()),
                (
                    "total_timer_time",
                    (
                        activity.recorded_duration_s
                        if activity.recorded_duration_s is not None
                        else activity.duration.total_seconds()
                    ),
                ),
                ("total_distance", activity.distance_m),
            ):
                actual_value = getattr(actual_session, field)
                if expected_value is None and actual_value is not None:
                    raise ExportError(f"FIT session fabricated {field}")
                if expected_value is not None and (
                    actual_value is None or not isclose(actual_value, expected_value, abs_tol=0.02)
                ):
                    raise ExportError(f"FIT session {field} differs from source")
            return bytes(content)
        except ExportError:
            raise
        except Exception as error:
            raise ExportError(f"Could not encode or validate FIT: {error}") from error
