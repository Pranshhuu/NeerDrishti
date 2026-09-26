"""
Live meteorological ingestion service for FlowSight (Phase 2A)

WeatherService fetches current conditions and a short-range precipitation
forecast from Open-Meteo's public forecast API and normalizes the response
into app.schemas.weather models.

Boundaries:
- Upstream HTTP access is the only thing this service does. It does not
  compute flood risk, does not couple to drainage, and does not run any
  model beyond what Open-Meteo already returns.
- Configuration (base URL, default coordinates, timezone, timeout) comes
  from app.core.config.Settings, the same settings object every other
  service reads from. No second configuration system is introduced here.
- The HTTP client is injectable so tests never reach the real network: pass
  any object exposing an async `get(url, params=..., timeout=...)` method
  that returns something with `.status_code` and a synchronous `.json()`,
  matching the subset of httpx.AsyncClient / httpx.Response this service
  actually uses.

Data semantics (read this before changing wording elsewhere):
    Open-Meteo's precipitation and rain fields are ECMWF IFS HRES 9 km model
    forecast output. They are NOT rain-gauge observations. This service, its
    docstrings, and its error messages consistently say "forecast", not
    "observed" or "measured", and callers should preserve that distinction.

Later phases (flood risk, drainage coupling, ML nowcasting, routing) are not
represented here. This is ingestion and normalization only.

Usage:
    from app.services.weather_service import WeatherService

    service = WeatherService()
    response = await service.get_weather(latitude=19.0760, longitude=72.8777)
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.config import Settings, settings as default_settings
from app.core.logging import get_logger
from app.schemas.weather import (
    CurrentWeather,
    HourlyForecast,
    WeatherLocation,
    WeatherResponse,
    WeatherSource,
)

logger = get_logger(__name__)

# Provenance reported on every response. Kept as module constants rather than
# configuration because they describe what the code actually does (which
# fields it requests, how it interprets them), not an operator-tunable value.
WEATHER_PROVIDER_NAME = "Open-Meteo"
WEATHER_MODEL_NAME = "ECMWF IFS HRES 9km"

# Open-Meteo variable lists this service requests. Fixed, not configurable:
# the parsing logic below is written against exactly these fields.
_CURRENT_VARIABLES = "precipitation,rain,weather_code"
_HOURLY_VARIABLES = "precipitation,rain"

MIN_FORECAST_HOURS = 1
MAX_FORECAST_HOURS = 24

# Providers this service knows how to talk to. Phase 2A supports only
# Open-Meteo; this is an allowlist check, not a provider abstraction, and
# should not grow into a factory/strategy pattern until a second provider is
# actually being integrated (e.g. IMD).
SUPPORTED_WEATHER_PROVIDERS = frozenset({"open_meteo"})


class WeatherServiceError(Exception):
    """
    Base exception for weather ingestion failures.

    Upstream and validation errors are translated into this type so callers
    depend on one service-level contract rather than on httpx's exception
    hierarchy. The original exception is always chained so the cause is not
    lost.
    """

    pass


class WeatherRequestError(WeatherServiceError):
    """Raised when the caller supplied invalid coordinates or parameters."""

    pass


class WeatherUpstreamError(WeatherServiceError):
    """
    Raised when Open-Meteo could not be reached, returned an error status,
    or returned a response this service could not parse.
    """

    pass


class WeatherTimeoutError(WeatherServiceError):
    """Raised when the request to Open-Meteo exceeded the configured timeout."""

    pass


class WeatherService:
    """
    Fetch and normalize live meteorological data from Open-Meteo.

    Attributes:
        provider: Configured provider identifier, currently always
            "open_meteo" per Settings.WEATHER_PROVIDER.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        http_client: Optional[Any] = None,
    ) -> None:
        """
        Create a weather service.

        Args:
            settings: Optional Settings instance. Defaults to the shared
                application settings object used everywhere else.
            http_client: Optional object with an async `get(url, params=,
                timeout=)` method. When omitted, a fresh httpx.AsyncClient is
                created and closed per request. Tests should always supply a
                fake client here so no automated test reaches the network.

        Raises:
            WeatherServiceError: If settings.WEATHER_PROVIDER is not a
                provider this service supports.
        """
        self._settings: Settings = settings or default_settings

        if self._settings.WEATHER_PROVIDER not in SUPPORTED_WEATHER_PROVIDERS:
            raise WeatherServiceError(
                f"Unsupported WEATHER_PROVIDER '{self._settings.WEATHER_PROVIDER}'. "
                f"This service currently supports only: "
                f"{', '.join(sorted(SUPPORTED_WEATHER_PROVIDERS))}."
            )

        self._http_client = http_client
        self.provider: str = self._settings.WEATHER_PROVIDER

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_weather(
        self,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        forecast_hours: int = 3,
    ) -> WeatherResponse:
        """
        Fetch current conditions and an hourly forecast for one location.

        This is the one method that actually talks to Open-Meteo;
        get_current_weather and get_forecast are thin wrappers around it,
        since a single Open-Meteo call already returns both current and
        hourly data together.

        Args:
            latitude: Optional latitude in decimal degrees. Must be supplied
                together with longitude, or both omitted to use the
                configured default location.
            longitude: Optional longitude in decimal degrees.
            forecast_hours: Number of hourly forecast entries to request.
                Must be between 1 and 24 inclusive. Defaults to 3.

        Returns:
            A normalized WeatherResponse.

        Raises:
            WeatherRequestError: If coordinates or forecast_hours are invalid.
            WeatherTimeoutError: If the upstream request timed out.
            WeatherUpstreamError: If the upstream request failed, returned a
                non-200 status, or returned a response this service could
                not parse.
        """
        if (latitude is None) != (longitude is None):
            raise WeatherRequestError(
                "latitude and longitude must both be supplied, or both "
                "omitted to use the default location."
            )

        resolved_latitude = (
            latitude if latitude is not None else self._settings.WEATHER_DEFAULT_LATITUDE
        )
        resolved_longitude = (
            longitude if longitude is not None else self._settings.WEATHER_DEFAULT_LONGITUDE
        )

        self._validate_coordinates(resolved_latitude, resolved_longitude)
        self._validate_forecast_hours(forecast_hours)

        params = {
            "latitude": resolved_latitude,
            "longitude": resolved_longitude,
            "current": _CURRENT_VARIABLES,
            "hourly": _HOURLY_VARIABLES,
            "forecast_hours": forecast_hours,
            "timezone": self._settings.WEATHER_DEFAULT_TIMEZONE,
        }

        payload = await self._fetch(params)
        return self._normalize(
            payload,
            resolved_latitude,
            resolved_longitude,
            self._settings.WEATHER_DEFAULT_TIMEZONE,
        )

    async def get_current_weather(
        self,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> WeatherResponse:
        """
        Fetch current conditions for one location.

        The response includes the current conditions and the minimum hourly
        forecast window returned by the weather service. Callers that only
        need current conditions should use the `current` field.

        Args:
            latitude: Optional latitude in decimal degrees.
            longitude: Optional longitude in decimal degrees.

        Returns:
            A normalized WeatherResponse.

        Raises:
            WeatherRequestError: If coordinates are invalid.
            WeatherTimeoutError: If the upstream request timed out.
            WeatherUpstreamError: If the upstream request failed or could not
                be parsed.
        """
        return await self.get_weather(
            latitude=latitude, longitude=longitude, forecast_hours=MIN_FORECAST_HOURS
        )

    async def get_forecast(
        self,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        forecast_hours: int = 3,
    ) -> WeatherResponse:
        """
        Fetch a short-range hourly precipitation forecast for one location.

        The response's `current` field is still populated, since Open-Meteo
        returns it in the same call; callers that only need the hourly array
        can read `response.hourly` and ignore `response.current`.

        Args:
            latitude: Optional latitude in decimal degrees.
            longitude: Optional longitude in decimal degrees.
            forecast_hours: Number of hourly entries to request, 1-24.

        Returns:
            A normalized WeatherResponse.

        Raises:
            WeatherRequestError: If coordinates or forecast_hours are invalid.
            WeatherTimeoutError: If the upstream request timed out.
            WeatherUpstreamError: If the upstream request failed or could not
                be parsed.
        """
        return await self.get_weather(
            latitude=latitude, longitude=longitude, forecast_hours=forecast_hours
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_coordinates(latitude: float, longitude: float) -> None:
        """
        Validate that coordinates are within their physical range.

        Args:
            latitude: Latitude in decimal degrees.
            longitude: Longitude in decimal degrees.

        Raises:
            WeatherRequestError: If either value is out of range.
        """
        if not isinstance(latitude, (int, float)) or isinstance(latitude, bool):
            raise WeatherRequestError(
                f"latitude must be numeric, got {type(latitude).__name__}."
            )
        if not isinstance(longitude, (int, float)) or isinstance(longitude, bool):
            raise WeatherRequestError(
                f"longitude must be numeric, got {type(longitude).__name__}."
            )
        if not (-90.0 <= latitude <= 90.0):
            raise WeatherRequestError(
                f"latitude must be between -90 and 90, got {latitude}."
            )
        if not (-180.0 <= longitude <= 180.0):
            raise WeatherRequestError(
                f"longitude must be between -180 and 180, got {longitude}."
            )

    @staticmethod
    def _validate_forecast_hours(forecast_hours: int) -> None:
        """
        Validate that forecast_hours is within the supported range.

        Args:
            forecast_hours: Requested number of hourly entries.

        Raises:
            WeatherRequestError: If the value is out of range or not an
                integer.
        """
        if isinstance(forecast_hours, bool) or not isinstance(forecast_hours, int):
            raise WeatherRequestError(
                f"forecast_hours must be an integer, got {type(forecast_hours).__name__}."
            )
        if not (MIN_FORECAST_HOURS <= forecast_hours <= MAX_FORECAST_HOURS):
            raise WeatherRequestError(
                f"forecast_hours must be between {MIN_FORECAST_HOURS} and "
                f"{MAX_FORECAST_HOURS}, got {forecast_hours}."
            )

    # ------------------------------------------------------------------
    # Upstream request
    # ------------------------------------------------------------------

    async def _fetch(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Request data from Open-Meteo and return the parsed JSON body.

        Only the HTTP status code is logged, never the response body: the
        body is user-relevant weather data, not a secret, but logging every
        payload would flood the logs for no diagnostic benefit.

        Args:
            params: Query parameters for the forecast endpoint.

        Returns:
            The parsed JSON response body.

        Raises:
            WeatherTimeoutError: If the request timed out.
            WeatherUpstreamError: If the request failed, returned a non-200
                status, or returned a body that is not valid JSON.
        """
        timeout = self._settings.WEATHER_REQUEST_TIMEOUT_SECONDS
        base_url = self._settings.WEATHER_API_BASE_URL

        logger.info(
            "Requesting weather data: lat=%s lon=%s forecast_hours=%s",
            params.get("latitude"),
            params.get("longitude"),
            params.get("forecast_hours"),
        )

        try:
            if self._http_client is not None:
                response = await self._http_client.get(
                    base_url, params=params, timeout=timeout
                )
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(base_url, params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise WeatherTimeoutError(
                f"Weather provider did not respond within {timeout}s."
            ) from exc
        except httpx.RequestError as exc:
            raise WeatherUpstreamError(
                f"Cannot reach weather provider: {exc}"
            ) from exc

        if response.status_code != 200:
            raise WeatherUpstreamError(
                f"Weather provider returned HTTP {response.status_code}."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise WeatherUpstreamError(
                "Weather provider returned malformed JSON."
            ) from exc

        logger.debug("Weather provider responded with HTTP %s", response.status_code)
        return payload

    # ------------------------------------------------------------------
    # Response normalization
    # ------------------------------------------------------------------

    def _normalize(
        self,
        payload: Any,
        latitude: float,
        longitude: float,
        requested_timezone: str,
    ) -> WeatherResponse:
        """
        Convert a raw Open-Meteo response into a WeatherResponse.

        Args:
            payload: Parsed JSON body from Open-Meteo.
            latitude: Latitude that was requested.
            longitude: Longitude that was requested.
            requested_timezone: Timezone name that was requested, used as a
                fallback if the response does not echo one back.

        Returns:
            A validated WeatherResponse.

        Raises:
            WeatherUpstreamError: If the payload is not an object, or if
                required fields are missing or malformed.
        """
        if not isinstance(payload, dict):
            raise WeatherUpstreamError(
                "Weather provider returned a response that is not a JSON object."
            )

        resolved_timezone = payload.get("timezone")
        if not isinstance(resolved_timezone, str) or not resolved_timezone:
            resolved_timezone = requested_timezone

        location = WeatherLocation(
            latitude=latitude, longitude=longitude, timezone=resolved_timezone
        )
        current = self._parse_current(payload, resolved_timezone)
        hourly = self._parse_hourly(payload, resolved_timezone)
        source = WeatherSource(
            provider=WEATHER_PROVIDER_NAME,
            model=WEATHER_MODEL_NAME,
            source_url=self._settings.WEATHER_API_BASE_URL,
        )

        return WeatherResponse(
            location=location,
            current=current,
            hourly=hourly,
            source=source,
            fetched_at=datetime.now(timezone.utc),
            status="ok",
        )

    def _parse_current(self, payload: Dict[str, Any], timezone_name: str) -> CurrentWeather:
        """
        Parse the 'current' block of an Open-Meteo response.

        precipitation and rain are validated by _validate_finite_non_negative,
        and weather_code is range-checked, before CurrentWeather is
        constructed, so an invalid upstream value is always reported as
        WeatherUpstreamError rather than surfacing as a raw
        pydantic.ValidationError from the schema's own constraints.

        Args:
            payload: The full parsed response body.
            timezone_name: Timezone to attach to the parsed timestamp.

        Returns:
            A validated CurrentWeather.

        Raises:
            WeatherUpstreamError: If 'current' is missing, not an object, any
                required field is missing, or precipitation/rain/weather_code
                have an invalid value.
        """
        current = payload.get("current")
        if not isinstance(current, dict):
            raise WeatherUpstreamError(
                "Weather provider response is missing 'current' data."
            )

        try:
            raw_time = current["time"]
            raw_precipitation = current["precipitation"]
            raw_rain = current["rain"]
            raw_weather_code = current["weather_code"]
        except KeyError as exc:
            raise WeatherUpstreamError(
                f"Weather provider 'current' data is missing field: {exc}."
            ) from exc

        timestamp = self._parse_timestamp(raw_time, timezone_name)
        precipitation_mm = self._validate_finite_non_negative(
            raw_precipitation, "current.precipitation"
        )
        rain_mm = self._validate_finite_non_negative(raw_rain, "current.rain")

        try:
            weather_code = int(raw_weather_code)
        except (TypeError, ValueError) as exc:
            raise WeatherUpstreamError(
                f"Weather provider 'current.weather_code' has an invalid value: {exc}"
            ) from exc

        if weather_code < 0:
            raise WeatherUpstreamError(
                f"Weather provider 'current.weather_code' must not be "
                f"negative, got {weather_code!r}."
            )

        return CurrentWeather(
            timestamp=timestamp,
            precipitation_mm=precipitation_mm,
            rain_mm=rain_mm,
            weather_code=weather_code,
        )

    def _parse_hourly(
        self, payload: Dict[str, Any], timezone_name: str
    ) -> List[HourlyForecast]:
        """
        Parse the 'hourly' block of an Open-Meteo response.

        Args:
            payload: The full parsed response body.
            timezone_name: Timezone to attach to each parsed timestamp.

        Returns:
            An ordered list of validated HourlyForecast entries.

        Raises:
            WeatherUpstreamError: If 'hourly' is missing, its arrays are
                missing, empty, mismatched in length, or contain an invalid
                value.
        """
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise WeatherUpstreamError(
                "Weather provider response is missing 'hourly' data."
            )

        times = hourly.get("time")
        precipitation = hourly.get("precipitation")
        rain = hourly.get("rain")

        if not isinstance(times, list) or not times:
            raise WeatherUpstreamError(
                "Weather provider 'hourly.time' array is missing or empty."
            )
        if not isinstance(precipitation, list):
            raise WeatherUpstreamError(
                "Weather provider 'hourly.precipitation' array is missing."
            )
        if not isinstance(rain, list):
            raise WeatherUpstreamError(
                "Weather provider 'hourly.rain' array is missing."
            )
        if not (len(times) == len(precipitation) == len(rain)):
            raise WeatherUpstreamError(
                "Weather provider 'hourly' arrays have mismatched lengths: "
                f"time={len(times)}, precipitation={len(precipitation)}, "
                f"rain={len(rain)}."
            )

        forecasts: List[HourlyForecast] = []
        for index, (time_value, precip_value, rain_value) in enumerate(
            zip(times, precipitation, rain)
        ):
            timestamp = self._parse_timestamp(time_value, timezone_name)
            precipitation_mm = self._validate_finite_non_negative(
                precip_value, f"hourly.precipitation[{index}]"
            )
            rain_mm = self._validate_finite_non_negative(
                rain_value, f"hourly.rain[{index}]"
            )
            forecasts.append(
                HourlyForecast(
                    timestamp=timestamp,
                    precipitation_mm=precipitation_mm,
                    rain_mm=rain_mm,
                )
            )

        return forecasts

    @staticmethod
    def _validate_finite_non_negative(value: Any, field_name: str) -> float:
        """
        Validate a precipitation or rain value from the upstream response.

        This enforces FlowSight's upstream-data contract at the service
        boundary, ahead of the CurrentWeather/HourlyForecast schemas. Doing
        the check here rather than relying solely on the schema's own
        Field(ge=0.0) constraint means every rejection is reported as
        WeatherUpstreamError: the schema constraint alone would still leave a
        pydantic.ValidationError escaping this service's exception contract,
        and would not catch +/-infinity at all, since inf >= 0.0 is True.

        Args:
            value: The raw value from the upstream JSON body.
            field_name: Identifies the field in the error message, e.g.
                "current.precipitation" or "hourly.rain[2]".

        Returns:
            The validated value as a float.

        Raises:
            WeatherUpstreamError: If the value is not numeric (bool is not
                accepted even though it is technically an int subtype), or is
                NaN, positive/negative infinity, or negative.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise WeatherUpstreamError(
                f"Weather provider '{field_name}' must be numeric, got "
                f"{type(value).__name__}: {value!r}."
            )

        numeric_value = float(value)

        if not math.isfinite(numeric_value):
            raise WeatherUpstreamError(
                f"Weather provider '{field_name}' must be a finite number, "
                f"got {value!r}."
            )

        if numeric_value < 0.0:
            raise WeatherUpstreamError(
                f"Weather provider '{field_name}' must not be negative, "
                f"got {value!r}."
            )

        return numeric_value

    @staticmethod
    def _parse_timestamp(value: Any, timezone_name: str) -> datetime:
        """
        Parse an Open-Meteo timestamp string into a timezone-aware datetime.

        Open-Meteo returns local wall-clock timestamps for the requested
        timezone without a UTC offset (e.g. "2026-09-08T18:00"), so the
        timezone is attached explicitly using the response's own declared
        zone rather than assumed to be UTC.

        Args:
            value: The raw timestamp value from the response.
            timezone_name: IANA timezone name to attach if the parsed value
                is naive.

        Returns:
            A timezone-aware datetime.

        Raises:
            WeatherUpstreamError: If the value is not a string or cannot be
                parsed as an ISO-8601 timestamp.
        """
        if not isinstance(value, str):
            raise WeatherUpstreamError(
                f"Weather provider returned a non-string timestamp: {value!r}."
            )

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise WeatherUpstreamError(
                f"Weather provider returned an unparseable timestamp: {value!r}."
            ) from exc

        if parsed.tzinfo is not None:
            return parsed

        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "Unknown timezone '%s' in weather response; falling back to UTC.",
                timezone_name,
            )
            zone = timezone.utc

        return parsed.replace(tzinfo=zone)