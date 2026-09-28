"""
Endpoint tests for GET /api/v1/flood-risk.

Both get_weather_service and get_flood_risk_service are overridden with
fakes, so these tests make no real Open-Meteo call and touch no real
terrain/WorldCover/flow-concentration raster. FloodRiskService's own
methodology/scoring behavior is already covered by
test_flood_risk_service.py and is not repeated here.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints.weather import get_weather_service
from app.api.v1.endpoints.flood_risk import get_flood_risk_service
from app.main import app
from app.schemas.weather import (
    CurrentWeather,
    HourlyForecast,
    WeatherLocation,
    WeatherResponse,
    WeatherSource,
)
from app.services.flood_risk_service import (
    FloodRiskDataError,
    FloodRiskSummary,
    FloodRiskTimestampNotFoundError,
    WardFloodRiskResult,
    RISK_METHODOLOGY,
    FLOW_CONCENTRATION_METHODOLOGY,
)
from app.services.runoff.basin_service import (
    BasinRunoffDataError,
    BasinRunoffInputError,
)
from app.services.weather_service import (
    WeatherRequestError,
    WeatherTimeoutError,
    WeatherUpstreamError,
)

client = TestClient(app)

TS = datetime(2026, 6, 27, 7, 0, tzinfo=timezone.utc)


def _make_weather_response() -> WeatherResponse:
    fetched_at = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
    return WeatherResponse(
        location=WeatherLocation(
            latitude=19.0760, longitude=72.8777, timezone="Asia/Kolkata"
        ),
        current=CurrentWeather(
            timestamp=fetched_at, precipitation_mm=0.0, rain_mm=0.0, weather_code=1
        ),
        hourly=[
            HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)
        ],
        source=WeatherSource(
            provider="Open-Meteo",
            model="ECMWF IFS HRES 9km",
            source_url="https://api.open-meteo.com/v1/forecast",
        ),
        fetched_at=fetched_at,
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


class _FakeFloodRiskService:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def calculate_for_forecast(self, weather):
        if self._error is not None:
            raise self._error
        return self._result


def _fixed_summary() -> FloodRiskSummary:
    return FloodRiskSummary(
        selected_forecast_timestamp=TS,
        rainfall_intensity_mm_h=25.3,
        rainfall_source="Open-Meteo / ECMWF IFS HRES 9km forecast",
        rainfall_scenario=f"Open-Meteo hourly forecast valid {TS.isoformat()}",
        rainfall_data_type="forecast",
        ward_count=2,
        wards=(
            WardFloodRiskResult(
                ward_id=1,
                ward_name="Ward 1",
                runoff_low_m3s=0.5,
                runoff_high_m3s=1.0,
                flow_concentration_high_fraction=0.1,
                flow_concentration_very_high_fraction=0.0,
                risk_score=12.5,
                risk_level="LOW",
            ),
            WardFloodRiskResult(
                ward_id=24,
                ward_name="Ward 24",
                runoff_low_m3s=2.0,
                runoff_high_m3s=4.0,
                flow_concentration_high_fraction=0.2,
                flow_concentration_very_high_fraction=0.95,
                risk_score=82.3,
                risk_level="VERY_HIGH",
            ),
        ),
        terrain_source="Copernicus GLO-30 terrain-derived isobasins",
        landcover_source="ESA WorldCover 2021",
        coefficient_status="provisional",
        flow_concentration_methodology=FLOW_CONCENTRATION_METHODOLOGY,
        risk_methodology=RISK_METHODOLOGY,
    )


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_weather_service, None)
    app.dependency_overrides.pop(get_flood_risk_service, None)


def test_flood_risk_returns_200_with_expected_structure():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response())
    )
    app.dependency_overrides[get_flood_risk_service] = (
        lambda: _FakeFloodRiskService(result=_fixed_summary())
    )

    response = client.get("/api/v1/flood-risk")

    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "completed"
    assert body["rainfall_intensity_mm_h"] == 25.3
    assert body["rainfall_data_type"] == "forecast"
    assert body["ward_count"] == 2
    assert len(body["wards"]) == 2

    ward_by_id = {w["ward_id"]: w for w in body["wards"]}
    assert ward_by_id[1]["risk_level"] == "LOW"
    assert ward_by_id[24]["risk_level"] == "VERY_HIGH"
    assert 0.0 <= ward_by_id[24]["flow_concentration_very_high_fraction"] <= 1.0

    assert body["terrain_source"] == "Copernicus GLO-30 terrain-derived isobasins"
    assert body["landcover_source"] == "ESA WorldCover 2021"
    assert body["coefficient_status"] == "provisional"
    assert "risk_methodology" in body
    assert "flow_concentration_methodology" in body
    assert "generated_at" in body

    # No affirmative claim of flood depth/probability/prediction anywhere
    # in the API-visible methodology text.
    combined = (body["risk_methodology"] + " " + body["flow_concentration_methodology"]).lower()
    assert "this is flood depth" not in combined
    assert "this is a flood probability" not in combined
    assert "operational flood prediction" not in combined or "not" in combined


def test_flood_risk_maps_weather_request_error_to_400():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(error=WeatherRequestError("bad coordinates"))
    )
    app.dependency_overrides[get_flood_risk_service] = (
        lambda: _FakeFloodRiskService(result=_fixed_summary())
    )

    response = client.get("/api/v1/flood-risk")

    assert response.status_code == 400


def test_flood_risk_maps_weather_timeout_to_504():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(error=WeatherTimeoutError("timed out"))
    )
    app.dependency_overrides[get_flood_risk_service] = (
        lambda: _FakeFloodRiskService(result=_fixed_summary())
    )

    response = client.get("/api/v1/flood-risk")

    assert response.status_code == 504


def test_flood_risk_maps_weather_upstream_error_to_502():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(error=WeatherUpstreamError("upstream failed"))
    )
    app.dependency_overrides[get_flood_risk_service] = (
        lambda: _FakeFloodRiskService(result=_fixed_summary())
    )

    response = client.get("/api/v1/flood-risk")

    assert response.status_code == 502


def test_flood_risk_maps_timestamp_not_found_to_503():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response())
    )
    app.dependency_overrides[get_flood_risk_service] = (
        lambda: _FakeFloodRiskService(
            error=FloodRiskTimestampNotFoundError("no future hourly entry")
        )
    )

    response = client.get("/api/v1/flood-risk")

    assert response.status_code == 503


def test_flood_risk_maps_basin_input_error_to_422():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response())
    )
    app.dependency_overrides[get_flood_risk_service] = (
        lambda: _FakeFloodRiskService(
            error=BasinRunoffInputError("invalid rainfall intensity")
        )
    )

    response = client.get("/api/v1/flood-risk")

    assert response.status_code == 422


def test_flood_risk_maps_basin_data_error_to_503():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response())
    )
    app.dependency_overrides[get_flood_risk_service] = (
        lambda: _FakeFloodRiskService(
            error=BasinRunoffDataError("terrain raster unavailable")
        )
    )

    response = client.get("/api/v1/flood-risk")

    assert response.status_code == 503


def test_flood_risk_maps_flood_risk_data_error_to_503():
    app.dependency_overrides[get_weather_service] = (
        lambda: _FakeWeatherService(response=_make_weather_response())
    )
    app.dependency_overrides[get_flood_risk_service] = (
        lambda: _FakeFloodRiskService(
            error=FloodRiskDataError("flow-concentration overlay failed")
        )
    )

    response = client.get("/api/v1/flood-risk")

    assert response.status_code == 503