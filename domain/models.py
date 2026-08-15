"""Immutable, provider-neutral models for recorded sporting activities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _freeze_extension(value: Any) -> Any:
    """Recursively freeze integration-neutral extension data."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_extension(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_extension(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_extension(item) for item in value)
    return value


class Sport(StrEnum):
    """Sports that can be represented by PolarToStrava exporters."""

    RUNNING = "running"
    TRAIL_RUNNING = "trail_running"
    WALKING = "walking"
    HIKING = "hiking"
    CYCLING = "cycling"
    MOUNTAIN_BIKING = "mountain_biking"
    SWIMMING = "swimming"
    OPEN_WATER_SWIMMING = "open_water_swimming"
    TRIATHLON = "triathlon"
    STRENGTH_TRAINING = "strength_training"
    YOGA = "yoga"
    SKIING = "skiing"
    CROSS_COUNTRY_SKIING = "cross_country_skiing"
    ROWING = "rowing"
    OTHER = "other"


class ActivitySource(StrEnum):
    """Origin of an activity, independent of any importer implementation."""

    POLAR_FLOW = "polar_flow"
    GARMIN = "garmin"
    STRAVA = "strava"
    MANUAL = "manual"
    IMPORTED_FILE = "imported_file"
    UNKNOWN = "unknown"


class DomainModel(BaseModel):
    """Base class for immutable domain values."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ExtensibleDomainModel(DomainModel):
    """An immutable domain model that retains immutable exporter extensions."""

    extensions: Mapping[str, Any] = Field(default_factory=dict, validate_default=True)

    @field_validator("extensions")
    @classmethod
    def freeze_extensions(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return cast(Mapping[str, Any], _freeze_extension(dict(value)))


class Location(DomainModel):
    """Geographic position in WGS84 coordinates and optional elevation."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float | None = None
    horizontal_accuracy_m: float | None = Field(default=None, ge=0)

    @field_validator("latitude", "longitude", "altitude_m", "horizontal_accuracy_m")
    @classmethod
    def finite(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("measurement must be finite")
        return value


class HeartRate(DomainModel):
    """Instantaneous heart rate in beats per minute."""

    bpm: int = Field(ge=0, le=300)


class Cadence(DomainModel):
    """Instantaneous cadence in revolutions or steps per minute."""

    rpm: float = Field(ge=0, le=300)

    @field_validator("rpm")
    @classmethod
    def finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("cadence must be finite")
        return value


class Power(DomainModel):
    """Instantaneous power in watts."""

    watts: int = Field(ge=0, le=5_000)


class Temperature(DomainModel):
    """Instantaneous ambient temperature in degrees Celsius."""

    celsius: float = Field(ge=-100, le=100)

    @field_validator("celsius")
    @classmethod
    def finite(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("temperature must be finite")
        return value


class Device(DomainModel):
    """Device provenance retained by all exporters where the format allows it."""

    manufacturer: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    serial_number: str | None = Field(default=None, min_length=1, max_length=200)
    software_version: str | None = Field(default=None, min_length=1, max_length=100)


class TrackPoint(ExtensibleDomainModel):
    """One timestamped recording, with all optional sensor readings preserved."""

    timestamp: datetime
    location: Location | None = None
    distance_m: float | None = Field(default=None, ge=0)
    speed_mps: float | None = Field(default=None, ge=0)
    heart_rate: HeartRate | None = None
    cadence: Cadence | None = None
    power: Power | None = None
    temperature: Temperature | None = None

    @field_validator("timestamp")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("distance_m", "speed_mps")
    @classmethod
    def finite(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("measurement must be finite")
        return value


class Lap(ExtensibleDomainModel):
    """A contiguous section of an activity, including its recorded points."""

    index: int = Field(ge=1)
    started_at: datetime
    ended_at: datetime
    distance_m: float | None = Field(default=None, ge=0)
    trackpoints: tuple[TrackPoint, ...] = ()

    @field_validator("started_at", "ended_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator("distance_m")
    @classmethod
    def finite_distance(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("distance must be finite")
        return value

    @model_validator(mode="after")
    def valid_time_range_and_points(self) -> Lap:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        if any(point.timestamp < self.started_at or point.timestamp > self.ended_at for point in self.trackpoints):
            raise ValueError("trackpoint timestamps must be within the lap time range")
        if any(
            later.timestamp < earlier.timestamp
            for earlier, later in zip(self.trackpoints, self.trackpoints[1:], strict=False)
        ):
            raise ValueError("trackpoints must be ordered by timestamp")
        return self

    @property
    def duration(self) -> timedelta:
        """Elapsed lap duration."""
        return self.ended_at - self.started_at


class Activity(ExtensibleDomainModel):
    """A complete activity, independent of its source or target export format."""

    id: str = Field(min_length=1, max_length=255)
    source: ActivitySource
    sport: Sport
    started_at: datetime
    ended_at: datetime
    name: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    distance_m: float | None = Field(default=None, ge=0)
    calories: int | None = Field(default=None, ge=0)
    device: Device | None = None
    laps: tuple[Lap, ...] = ()

    @field_validator("started_at", "ended_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value

    @field_validator("distance_m")
    @classmethod
    def finite_distance(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("distance must be finite")
        return value

    @model_validator(mode="after")
    def valid_time_range_and_laps(self) -> Activity:
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        expected_indexes = tuple(range(1, len(self.laps) + 1))
        if tuple(lap.index for lap in self.laps) != expected_indexes:
            raise ValueError("laps must use consecutive indexes starting at one")
        if any(lap.started_at < self.started_at or lap.ended_at > self.ended_at for lap in self.laps):
            raise ValueError("laps must be within the activity time range")
        return self

    @property
    def duration(self) -> timedelta:
        """Elapsed activity duration."""
        return self.ended_at - self.started_at

    @property
    def trackpoints(self) -> tuple[TrackPoint, ...]:
        """All lap trackpoints in their recorded order."""
        return tuple(point for lap in self.laps for point in lap.trackpoints)
