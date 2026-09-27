"""
Tests for ForecastRunoffCouplingService.

BasinRunoffService is replaced with a fake exposing only .calculate(...),
so these tests never read the real terrain/WorldCover rasters and make no
network calls. WeatherResponse/HourlyForecast objects are constructed
directly from the real schemas - no WeatherService/Open-Meteo call is
made anywhere in this file.
"""

from datetime import datetime, timezone

import pytest

from app.schemas.weather import (
    CurrentWeather,
    HourlyForecast,
    WeatherLocation,
    WeatherResponse,
    WeatherSource,
)
from app.services.runoff.basin_service import BasinRunoffSummary
from app.services.runoff.forecast_coupling_service import (
    ForecastCouplingInputError,
    ForecastRunoffCouplingService,
    ForecastTimestampNotFoundError,
    PRECIPITATION_BASIS,
)


class _FakeBasinRunoffService:
    """Records the arguments it was called with and returns a fixed summary."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def calculate(
        self,
        rainfall_intensity_mm_h: float,
        rainfall_source: str,
        rainfall_scenario: str,
    ) -> BasinRunoffSummary:
        self.calls.append(
            {
                "rainfall_intensity_mm_h": rainfall_intensity_mm_h,
                "rainfall_source": rainfall_source,
                "rainfall_scenario": rainfall_scenario,
            }
        )
        return BasinRunoffSummary(
            eligible_basin_count=438,
            worldcover_support_threshold=0.90,
            rainfall_intensity_mm_h=rainfall_intensity_mm_h,
            runoff_coefficient_low={
                "minimum": 0.2,
                "median": 0.35,
                "mean": 0.36,
                "maximum": 0.55,
            },
            runoff_coefficient_high={
                "minimum": 0.4,
                "median": 0.6,
                "mean": 0.61,
                "maximum": 0.85,
            },
            peak_discharge_low_m3s={
                "minimum": 0.01,
                "median": 0.42,
                "mean": 0.50,
                "maximum": 3.2,
            },
            peak_discharge_high_m3s={
                "minimum": 0.02,
                "median": 0.88,
                "mean": 1.05,
                "maximum": 6.4,
            },
            rainfall_source=rainfall_source,
            rainfall_scenario=rainfall_scenario,
            terrain_source="Copernicus GLO-30 terrain-derived isobasins",
            landcover_source="ESA WorldCover 2021",
            coefficient_status="provisional",
        )


def _make_weather_response(
    hourly_specs: list[tuple[datetime, float]],
) -> WeatherResponse:
    """Build a real WeatherResponse with the given (timestamp, precip) hours."""
    now = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
    return WeatherResponse(
        location=WeatherLocation(
            latitude=19.0760, longitude=72.8777, timezone="Asia/Kolkata"
        ),
        current=CurrentWeather(
            timestamp=now,
            precipitation_mm=0.0,
            rain_mm=0.0,
            weather_code=1,
        ),
        hourly=[
            HourlyForecast(
                timestamp=ts,
                precipitation_mm=precip,
                rain_mm=precip,
            )
            for ts, precip in hourly_specs
        ],
        source=WeatherSource(
            provider="Open-Meteo",
            model="ECMWF IFS HRES 9km",
            source_url="https://api.open-meteo.com/v1/forecast",
        ),
        fetched_at=now,
        status="ok",
    )


TS_1 = datetime(2026, 6, 27, 7, 0, tzinfo=timezone.utc)
TS_2 = datetime(2026, 6, 27, 8, 0, tzinfo=timezone.utc)


def test_valid_forecast_timestamp_produces_runoff_result():
    weather = _make_weather_response([(TS_1, 25.3), (TS_2, 40.0)])
    fake_basins = _FakeBasinRunoffService()
    service = ForecastRunoffCouplingService(basin_service=fake_basins)

    result = service.calculate_for_timestamp(weather, TS_1)

    assert result.selected_forecast_timestamp == TS_1
    assert result.rainfall_intensity_mm_h == 25.3
    assert result.rainfall_data_type == "forecast"
    assert result.eligible_basin_count == 438
    assert result.terrain_source == "Copernicus GLO-30 terrain-derived isobasins"
    assert result.landcover_source == "ESA WorldCover 2021"
    assert result.coefficient_status == "provisional"
    assert result.peak_discharge_low_m3s["median"] == 0.42
    assert result.peak_discharge_high_m3s["median"] == 0.88


def test_missing_timestamp_raises_clear_error():
    weather = _make_weather_response([(TS_1, 25.3)])
    service = ForecastRunoffCouplingService(basin_service=_FakeBasinRunoffService())

    missing = datetime(2026, 6, 27, 9, 0, tzinfo=timezone.utc)

    with pytest.raises(ForecastTimestampNotFoundError, match="9:00|09:00"):
        service.calculate_for_timestamp(weather, missing)


def test_empty_hourly_forecast_is_handled():
    weather = _make_weather_response([])
    service = ForecastRunoffCouplingService(basin_service=_FakeBasinRunoffService())

    with pytest.raises(ForecastTimestampNotFoundError, match="no hourly forecast entries"):
        service.calculate_for_timestamp(weather, TS_1)


def test_naive_timestamp_is_rejected():
    weather = _make_weather_response([(TS_1, 25.3)])
    service = ForecastRunoffCouplingService(basin_service=_FakeBasinRunoffService())

    naive = datetime(2026, 6, 27, 7, 0)  # no tzinfo

    with pytest.raises(ForecastCouplingInputError, match="timezone-aware"):
        service.calculate_for_timestamp(weather, naive)


def test_rainfall_provenance_is_preserved():
    weather = _make_weather_response([(TS_1, 25.3)])
    fake_basins = _FakeBasinRunoffService()
    service = ForecastRunoffCouplingService(basin_service=fake_basins)

    result = service.calculate_for_timestamp(weather, TS_1)

    assert result.rainfall_source == "Open-Meteo / ECMWF IFS HRES 9km forecast"
    assert TS_1.isoformat() in result.rainfall_scenario
    assert result.rainfall_data_type == "forecast"
    # Never described as measured/observed anywhere in provenance text.
    assert "measured" not in result.rainfall_source.lower()
    assert "observed" not in result.rainfall_source.lower()


def test_precipitation_value_passed_correctly_to_basin_service():
    weather = _make_weather_response([(TS_1, 25.3), (TS_2, 40.0)])
    fake_basins = _FakeBasinRunoffService()
    service = ForecastRunoffCouplingService(basin_service=fake_basins)

    service.calculate_for_timestamp(weather, TS_2)

    assert len(fake_basins.calls) == 1
    assert fake_basins.calls[0]["rainfall_intensity_mm_h"] == 40.0


def test_one_hour_precipitation_to_mm_h_assumption_is_documented():
    weather = _make_weather_response([(TS_1, 25.3)])
    service = ForecastRunoffCouplingService(basin_service=_FakeBasinRunoffService())

    result = service.calculate_for_timestamp(weather, TS_1)

    assert result.precipitation_basis == PRECIPITATION_BASIS
    assert "one hour" in result.precipitation_basis
    assert "mm/hour" in result.precipitation_basis or "mm/h" in result.precipitation_basis