"""
Weather endpoints for FlowSight (Phase 2A)

Exposes WeatherService over HTTP: current conditions, a short-range
precipitation forecast, and a combined view, for any coordinates or the
configured default (Mumbai).

Status code mapping is deliberate:
    WeatherRequestError  -> 400  (bad coordinates / forecast_hours)
    WeatherTimeoutError   -> 504  (upstream did not respond in time)
    WeatherUpstreamError  -> 502  (upstream reachable but failed, or its
                                    response could not be parsed)
    anything else         -> 500

Coordinate and forecast_hours bounds are deliberately NOT enforced via
FastAPI's Query(ge=..., le=...), because that produces HTTP 422, and this API
is required to return 400 for those cases. Range checking is delegated to
WeatherService, whose WeatherRequestError is translated to 400 below.

Data semantics:
    Every response embeds a WeatherSource block naming Open-Meteo and ECMWF
    IFS HRES 9km explicitly. The precipitation and rain figures throughout
    this API are model forecast values, not rain-gauge observations; nothing
    in this module or its schemas describes them as "observed" data.

Phase scope: ingestion and normalization only. No flood-risk computation,
drainage coupling, ML nowcasting, or routing is implemented here.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status

from app.core.logging import get_logger
from app.schemas.weather import WeatherResponse
from app.services.weather_service import (
    WeatherRequestError,
    WeatherService,
    WeatherServiceError,
    WeatherTimeoutError,
    WeatherUpstreamError,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/weather", tags=["weather"])


def get_weather_service() -> WeatherService:
    """
    Provide a WeatherService instance.

    Construction performs no network I/O, so building one per request is
    cheap and keeps the dependency overridable in tests.

    Returns:
        A WeatherService using the shared application settings.
    """
    return WeatherService()


def _handle_service_error(exc: WeatherServiceError) -> HTTPException:
    """
    Map a weather service error onto an HTTP response.

    Args:
        exc: The service-level exception.

    Returns:
        The HTTPException to raise.
    """
    if isinstance(exc, WeatherRequestError):
        return HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if isinstance(exc, WeatherTimeoutError):
        return HTTPException(
            status_code=http_status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)
        )

    if isinstance(exc, WeatherUpstreamError):
        return HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    logger.error("Weather service error: %s", exc)
    return HTTPException(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
    )


_LATITUDE_QUERY = Query(
    default=None,
    description=(
        "Latitude in decimal degrees. Must be supplied together with "
        "longitude, or both omitted to use the default location (Mumbai)."
    ),
)
_LONGITUDE_QUERY = Query(
    default=None,
    description="Longitude in decimal degrees.",
)
_FORECAST_HOURS_QUERY = Query(
    default=3,
    description="Number of hourly forecast entries to request, 1-24.",
)


@router.get(
    "",
    response_model=WeatherResponse,
    summary="Current weather and short-range forecast, combined",
)
async def get_weather(
    latitude: Optional[float] = _LATITUDE_QUERY,
    longitude: Optional[float] = _LONGITUDE_QUERY,
    forecast_hours: int = _FORECAST_HOURS_QUERY,
    service: WeatherService = Depends(get_weather_service),
) -> WeatherResponse:
    """
    Return current conditions and an hourly forecast for one location.

    Args:
        latitude: Optional latitude. Defaults to the configured location.
        longitude: Optional longitude. Defaults to the configured location.
        forecast_hours: Number of hourly entries, 1-24. Defaults to 3.

    Returns:
        A normalized WeatherResponse.

    Raises:
        HTTPException: 400 for invalid coordinates/forecast_hours, 502 for an
            upstream failure, 504 for an upstream timeout.
    """
    try:
        return await service.get_weather(
            latitude=latitude, longitude=longitude, forecast_hours=forecast_hours
        )
    except WeatherServiceError as exc:
        raise _handle_service_error(exc) from exc


@router.get(
    "/current",
    response_model=WeatherResponse,
    summary="Current meteorological conditions",
)
async def get_current_weather(
    latitude: Optional[float] = _LATITUDE_QUERY,
    longitude: Optional[float] = _LONGITUDE_QUERY,
    service: WeatherService = Depends(get_weather_service),
) -> WeatherResponse:
    """
    Return current conditions for one location.

    The response includes the current conditions and the minimum hourly
    forecast window returned by the weather service. Callers that only need
    current conditions should use the `current` field.

    Args:
        latitude: Optional latitude. Defaults to the configured location.
        longitude: Optional longitude. Defaults to the configured location.

    Returns:
        A normalized WeatherResponse.

    Raises:
        HTTPException: 400 for invalid coordinates, 502 for an upstream
            failure, 504 for an upstream timeout.
    """
    try:
        return await service.get_current_weather(latitude=latitude, longitude=longitude)
    except WeatherServiceError as exc:
        raise _handle_service_error(exc) from exc


@router.get(
    "/forecast",
    response_model=WeatherResponse,
    summary="Short-range precipitation forecast",
)
async def get_forecast(
    latitude: Optional[float] = _LATITUDE_QUERY,
    longitude: Optional[float] = _LONGITUDE_QUERY,
    forecast_hours: int = _FORECAST_HOURS_QUERY,
    service: WeatherService = Depends(get_weather_service),
) -> WeatherResponse:
    """
    Return a short-range hourly precipitation forecast for one location.

    The response's `current` field is still populated, since Open-Meteo
    returns it in the same call.

    Args:
        latitude: Optional latitude. Defaults to the configured location.
        longitude: Optional longitude. Defaults to the configured location.
        forecast_hours: Number of hourly entries, 1-24. Defaults to 3.

    Returns:
        A normalized WeatherResponse.

    Raises:
        HTTPException: 400 for invalid coordinates/forecast_hours, 502 for an
            upstream failure, 504 for an upstream timeout.
    """
    try:
        return await service.get_forecast(
            latitude=latitude, longitude=longitude, forecast_hours=forecast_hours
        )
    except WeatherServiceError as exc:
        raise _handle_service_error(exc) from exc