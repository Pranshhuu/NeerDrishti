"""
API schemas for FlowSight weather endpoints (Phase 2A)

This module defines the normalized response contracts for live meteorological
data. Clients never see the raw Open-Meteo payload shape directly; every
response is translated into these models by app.services.weather_service
before it reaches the API layer.

Boundaries:
- Upstream request construction and response parsing belong to
  app.services.weather_service.WeatherService. Nothing in this module talks
  to Open-Meteo or performs HTTP requests.
- Configuration (default coordinates, timezone, base URL) belongs to
  app.core.config.Settings. This module holds no defaults of its own beyond
  what Pydantic Field requires for validation bounds.

Data semantics (important):
    Open-Meteo's precipitation and rain values are ECMWF IFS HRES model
    forecast output, not rain-gauge observations. Field and docstring naming
    throughout this module says "forecast" deliberately, and callers should
    not describe this data as "observed" or "measured" rainfall.

Usage:
    from app.schemas.weather import WeatherResponse

    def handler(response: WeatherResponse) -> None:
        print(response.source.provider, response.current.precipitation_mm)
"""

from datetime import datetime
from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "WeatherLocation",
    "CurrentWeather",
    "HourlyForecast",
    "WeatherSource",
    "WeatherResponse",
]


def _require_timezone_aware(value: datetime) -> datetime:
    """
    Reject naive datetimes.

    A naive timestamp cannot be safely compared or displayed without
    ambiguity about which zone it belongs to, so every timestamp in this
    module is required to carry tzinfo.

    Args:
        value: Datetime to check.

    Returns:
        The value unchanged.

    Raises:
        ValueError: If the value has no timezone.
    """
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class WeatherLocation(BaseModel):
    """The coordinates and timezone a weather response describes."""

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(
        ge=-90.0,
        le=90.0,
        description="Latitude in decimal degrees.",
    )
    longitude: float = Field(
        ge=-180.0,
        le=180.0,
        description="Longitude in decimal degrees.",
    )
    timezone: str = Field(
        min_length=1,
        description=(
            "IANA timezone name used to interpret the timestamps in this "
            "response, e.g. 'Asia/Kolkata'."
        ),
    )


class CurrentWeather(BaseModel):
    """
    Current meteorological conditions for one location.

    precipitation_mm and rain_mm are ECMWF IFS HRES forecast values for the
    current interval, not a rain-gauge reading.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(
        description="Timezone-aware timestamp of the current interval.",
    )
    precipitation_mm: float = Field(
        ge=0.0,
        description="Forecast total precipitation for the current interval, in millimeters.",
    )
    rain_mm: float = Field(
        ge=0.0,
        description="Forecast liquid rain component for the current interval, in millimeters.",
    )
    weather_code: int = Field(
        ge=0,
        description="WMO weather interpretation code reported by the provider.",
    )

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)


class HourlyForecast(BaseModel):
    """
    One hour of the short-range precipitation forecast.

    precipitation_mm and rain_mm are the ECMWF IFS HRES model's forecast for
    that hour, not an observation.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(
        description="Timezone-aware timestamp identifying this forecast hour.",
    )
    precipitation_mm: float = Field(
        ge=0.0,
        description="Forecast total precipitation for this hour, in millimeters.",
    )
    rain_mm: float = Field(
        ge=0.0,
        description="Forecast liquid rain component for this hour, in millimeters.",
    )

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)


class WeatherSource(BaseModel):
    """
    Provenance of a weather response.

    Every WeatherResponse carries one of these so a client (or a developer
    reading a log) can always see which provider and model produced the
    numbers, rather than the source being implicit.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, description="Data provider name, e.g. 'Open-Meteo'.")
    model: str = Field(
        min_length=1,
        description="Underlying forecast model, e.g. 'ECMWF IFS HRES 9km'.",
    )
    source_url: str = Field(
        min_length=1,
        description="Base URL of the upstream API this response was fetched from.",
    )


class WeatherResponse(BaseModel):
    """
    Normalized live meteorological response for one location.

    Combines current conditions and a short-range hourly forecast in a single
    envelope, since Open-Meteo returns both from a single upstream call. A
    request for "current only" still populates hourly (typically with one
    entry); a request for "forecast only" still populates current. Nothing
    here is fabricated: every field is either read from the upstream response
    or is process metadata (fetched_at, status).
    """

    model_config = ConfigDict(extra="forbid")

    location: WeatherLocation
    current: CurrentWeather
    hourly: List[HourlyForecast] = Field(
        default_factory=list,
        description="Short-range hourly forecast, ordered chronologically.",
    )
    source: WeatherSource
    fetched_at: datetime = Field(
        description="Timezone-aware timestamp of when FlowSight fetched this data.",
    )
    status: str = Field(
        default="ok",
        min_length=1,
        description="Outcome of the fetch. 'ok' for a successful, fully parsed response.",
    )

    @field_validator("fetched_at")
    @classmethod
    def _fetched_at_is_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value)