"""
Endpoint tests for GET /api/v1/runoff/forecast.

Both get_weather_service and get_forecast_coupling_service are overridden
with fakes, so these tests make no real Open-Meteo call and touch no real
terrain/WorldCover raster. Coupling-service-level behavior is already
covered by test_forecast_coupling_service.py and is not repeated here.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.weather import get_weather_service
from app.api.v1.endpoints.runoff import get_forecast_coupling_service
from app.main import app
from app.schemas.weather import (
    CurrentWeather,
    HourlyForecast,
    WeatherLocation,
    WeatherResponse,
    WeatherSource,
)
from app.services.runoff.basin_service import (
    BasinRunoffDataError,
    BasinRunoffInputError,
)
from app.services.runoff.forecast_coupling_service import (
    ForecastRunoffCouplingSummary,
    ForecastTimestampNotFoundError,
)
from app.services.weather_service import (
    WeatherRequestError,
    WeatherTimeoutError,
    WeatherUpstreamError,
)

client = TestClient(app)

TS = datetime(2026, 6, 27, 7, 0, tzinfo=timezone.utc)


def _make_weather_response(hourly: list[HourlyForecast]) -> WeatherResponse:
    now = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
    return WeatherResponse(
        location=WeatherLocation(
            latitude=19.0760, longitude=72.8777, timezone="Asia/Kolkata"
        ),
        current=CurrentWeather(
            timestamp=now, precipitation_mm=0.0, rain_mm=0.0, weather_code=1
        ),
        hourly=hourly,
        source=WeatherSource(
            provider="Open-Meteo",
            model="ECMWF IFS HRES 9km",
            source_url="https://api.open-meteo.com/v1/forecast",
        ),
        fetched_at=now,
        status="ok",
    )


class _FakeWeatherService:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    async def get_weather(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._response


class _FakeCouplingService:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def calculate_for_timestamp(self, weather, timestamp):
        if self._error is not None:
            raise self._error
        return self._result


def _fixed_summary() -> ForecastRunoffCouplingSummary:
    return ForecastRunoffCouplingSummary(
        selected_forecast_timestamp=TS,
        rainfall_intensity_mm_h=25.3,
        rainfall_source="Open-Meteo / ECMWF IFS HRES 9km forecast",
        rainfall_scenario=f"Open-Meteo hourly forecast valid {TS.isoformat()}",
        rainfall_data_type="forecast",
        precipitation_basis="one-hour precipitation used directly as mm/hour",
        eligible_basin_count=438,
        runoff_coefficient_low={
            "minimum": 0.2, "median": 0.35, "mean": 0.36, "maximum": 0.55,
        },
        runoff_coefficient_high={
            "minimum": 0.4, "median": 0.6, "mean": 0.61, "maximum": 0.85,
        },
        peak_discharge_low_m3s={
            "minimum": 0.01, "median": 0.42, "mean": 0.50, "maximum": 3.2,
        },
        peak_discharge_high_m3s={
            "minimum": 0.02, "median": 0.88, "mean": 1.05, "maximum": 6.4,
        },
        terrain_source="Copernicus GLO-30 terrain-derived isobasins",
        landcover_source="ESA WorldCover 2021",
        coefficient_status="provisional",
    )


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_weather_service, None)
    app.dependency_overrides.pop(get_forecast_coupling_service, None)


def test_forecast_runoff_returns_200_on_success():
    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response(hourly))
    )
    app.dependency_overrides[get_forecast_coupling_service] = (
        lambda: _FakeCouplingService(result=_fixed_summary())
    )

    response = client.get("/api/v1/runoff/forecast")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["rainfall_intensity_mm_h"] == 25.3
    assert body["rainfall_data_type"] == "forecast"
    assert body["eligible_basin_count"] == 438
    assert body["source"]["coefficient_status"] == "provisional"
    assert body["peak_discharge_low_m3s"]["median"] == 0.42


def test_forecast_runoff_returns_503_when_hourly_empty():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response([]))
    )
    app.dependency_overrides[get_forecast_coupling_service] = (
        lambda: _FakeCouplingService(result=_fixed_summary())
    )

    response = client.get("/api/v1/runoff/forecast")

    assert response.status_code == 503


def test_forecast_runoff_maps_weather_request_error_to_400():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(error=WeatherRequestError("bad coordinates"))
    )
    app.dependency_overrides[get_forecast_coupling_service] = (
        lambda: _FakeCouplingService(result=_fixed_summary())
    )

    response = client.get("/api/v1/runoff/forecast")

    assert response.status_code == 400


def test_forecast_runoff_maps_weather_timeout_to_504():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(error=WeatherTimeoutError("timed out"))
    )
    app.dependency_overrides[get_forecast_coupling_service] = (
        lambda: _FakeCouplingService(result=_fixed_summary())
    )

    response = client.get("/api/v1/runoff/forecast")

    assert response.status_code == 504


def test_forecast_runoff_maps_weather_upstream_error_to_502():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(error=WeatherUpstreamError("upstream failed"))
    )
    app.dependency_overrides[get_forecast_coupling_service] = (
        lambda: _FakeCouplingService(result=_fixed_summary())
    )

    response = client.get("/api/v1/runoff/forecast")

    assert response.status_code == 502


def test_forecast_runoff_maps_basin_input_error_to_422():
    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response(hourly))
    )
    app.dependency_overrides[get_forecast_coupling_service] = (
        lambda: _FakeCouplingService(
            error=BasinRunoffInputError("invalid rainfall intensity")
        )
    )

    response = client.get("/api/v1/runoff/forecast")

    assert response.status_code == 422


def test_forecast_runoff_maps_basin_data_error_to_503():
    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response(hourly))
    )
    app.dependency_overrides[get_forecast_coupling_service] = (
        lambda: _FakeCouplingService(
            error=BasinRunoffDataError("terrain raster unavailable")
        )
    )

    response = client.get("/api/v1/runoff/forecast")

    assert response.status_code == 503


def test_forecast_runoff_maps_coupling_service_error_to_503():
    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response(hourly))
    )
    app.dependency_overrides[get_forecast_coupling_service] = (
        lambda: _FakeCouplingService(
            error=ForecastTimestampNotFoundError("no hourly entries")
        )
    )

    response = client.get("/api/v1/runoff/forecast")

    assert response.status_code == 503
    