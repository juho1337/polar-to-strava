"""Strongly typed models for useful portions of a Polar activity export."""

from datetime import datetime
from typing import Any
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class TrackPoint(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    timestamp: datetime = Field(validation_alias=AliasChoices("timestamp", "time"))
    latitude: float | None = Field(default=None, validation_alias=AliasChoices("latitude", "lat"))
    longitude: float | None = Field(default=None, validation_alias=AliasChoices("longitude", "lon", "lng"))
    altitude: float | None = None
    heart_rate: int | None = Field(default=None, validation_alias=AliasChoices("heart-rate", "heart_rate", "hr"))
    cadence: int | None = None
    power: int | None = None
    distance: float | None = None
    speed: float | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_timezone_aware(cls, value: Any) -> Any:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value


class Lap(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    start_time: datetime | None = Field(default=None, validation_alias=AliasChoices("start-time", "start_time"))
    duration: str | float | int | None = None
    distance: float | None = None


class SampleGroup(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    recording_rate: int | None = Field(default=None, validation_alias=AliasChoices("recording-rate", "recording_rate"))
    data: list[TrackPoint] = Field(default_factory=list)


class PolarActivity(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    id: str | int | None = None
    title: str | None = None
    sport: str | None = Field(default=None, validation_alias=AliasChoices("sport", "detailed-sport-info", "detailed_sport_info"))
    start_time: datetime = Field(validation_alias=AliasChoices("start-time", "start_time"))
    duration: str | float | int | None = None
    distance: float | None = None
    samples: list[SampleGroup] = Field(default_factory=list)
    laps: list[Lap] = Field(default_factory=list)

    @property
    def trackpoints(self) -> list[TrackPoint]:
        return [point for group in self.samples for point in group.data]
