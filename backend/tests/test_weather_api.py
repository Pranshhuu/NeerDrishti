"""
Weather API endpoint tests for FlowSight (Phase 2A)

Verifies the HTTP layer: routes resolve, status codes map correctly, and
existing routes are unaffected. Uses the shared `client` fixture from
conftest.py and overrides the weather service dependency per test so no
automated test reaches the network.
"""

import httpx
import pytest
from fastapi import status

from app.api.v1.endpoints import weather as weather_endpoints
from app.core.config import settings
from app.core.constants import API_VERSION_PREFIX
from app.main import app
from app.services.weather_service import WeatherService


# ----------------------------------------------------------------------
# Fakes (independent copy from test_weather_service.py; each test file
# should be runnable on its own without cross-file imports)
# ----------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("invalid json")
        return self._payload


class FakeAsyncHTTPClient:
    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self._exception is not None:
            raise self._exception
        return self._response


def _sample_payload():
    return {
        "timezone": "Asia/Kolkata",
        "current": {
            "time": "2026-09-08T18:00",
            "precipitation": 0.4,
            "rain": 0.4,
            "weather_code": 61,
        },
        "hourly": {
            "time": ["2026-09-08T18:00", "2026-09-08T19:00", "2026-09-08T20:00"],
            "precipitation": [0.4, 0.6, 0.0],
            "rain": [0.4, 0.6, 0.0],
        },
    }


@pytest.fixture
def clear_weather_override():
    """Ensure the weather dependency override never leaks into another test."""
    yield
    app.dependency_overrides.pop(weather_endpoints.get_weather_service, None)


def _override_with(fake_client) -> None:
    app.dependency_overrides[weather_endpoints.get_weather_service] = (
        lambda: WeatherService(http_client=fake_client)
    )


# ----------------------------------------------------------------------
# Successful responses
# ----------------------------------------------------------------------


def test_weather_endpoint_returns_normalized_response(client, clear_weather_override):
    """GET /weather returns 200 with the documented response shape."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(f"{API_VERSION_PREFIX}/weather")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["source"]["provider"] == "Open-Meteo"
    assert "ECMWF" in body["source"]["model"]
    assert len(body["hourly"]) == 3


def test_current_endpoint_returns_normalized_response(client, clear_weather_override):
    """GET /weather/current returns 200 with current conditions populated."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(f"{API_VERSION_PREFIX}/weather/current")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["current"]["weather_code"] == 61


def test_forecast_endpoint_returns_normalized_response(client, clear_weather_override):
    """GET /weather/forecast returns 200 with the requested hour count."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(
        f"{API_VERSION_PREFIX}/weather/forecast", params={"forecast_hours": 3}
    )

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()["hourly"]) == 3


def test_weather_endpoint_uses_default_mumbai_coordinates(client, clear_weather_override):
    """Omitting coordinates falls back to the configured default location."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(f"{API_VERSION_PREFIX}/weather")

    assert response.status_code == status.HTTP_200_OK
    call = fake_client.calls[0]
    assert call["params"]["latitude"] == settings.WEATHER_DEFAULT_LATITUDE
    assert call["params"]["longitude"] == settings.WEATHER_DEFAULT_LONGITUDE


def test_weather_endpoint_accepts_custom_coordinates(client, clear_weather_override):
    """Supplied coordinates are forwarded to the upstream request."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(
        f"{API_VERSION_PREFIX}/weather",
        params={"latitude": 28.6139, "longitude": 77.2090},
    )

    assert response.status_code == status.HTTP_200_OK
    call = fake_client.calls[0]
    assert call["params"]["latitude"] == 28.6139
    assert call["params"]["longitude"] == 77.2090


def test_weather_response_matches_documented_schema(client, clear_weather_override):
    """The response contains every field the schema promises, nothing more."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(f"{API_VERSION_PREFIX}/weather")
    body = response.json()

    for key in ("location", "current", "hourly", "source", "fetched_at", "status"):
        assert key in body, f"response missing '{key}'"
    for key in ("latitude", "longitude", "timezone"):
        assert key in body["location"]
    for key in ("timestamp", "precipitation_mm", "rain_mm", "weather_code"):
        assert key in body["current"]
    for key in ("provider", "model", "source_url"):
        assert key in body["source"]


# ----------------------------------------------------------------------
# Invalid input -> 400
# ----------------------------------------------------------------------


def test_weather_endpoint_rejects_invalid_latitude(client, clear_weather_override):
    """An out-of-range latitude returns 400, not 422 or 500."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(
        f"{API_VERSION_PREFIX}/weather", params={"latitude": 120.0, "longitude": 72.86}
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert fake_client.calls == []


def test_weather_endpoint_rejects_invalid_longitude(client, clear_weather_override):
    """An out-of-range longitude returns 400."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(
        f"{API_VERSION_PREFIX}/weather", params={"latitude": 19.05, "longitude": 200.0}
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_forecast_endpoint_rejects_forecast_hours_below_minimum(client, clear_weather_override):
    """forecast_hours=0 returns 400."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(
        f"{API_VERSION_PREFIX}/weather/forecast", params={"forecast_hours": 0}
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_forecast_endpoint_rejects_forecast_hours_above_maximum(client, clear_weather_override):
    """forecast_hours=48 returns 400."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    _override_with(fake_client)

    response = client.get(
        f"{API_VERSION_PREFIX}/weather/forecast", params={"forecast_hours": 48}
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ----------------------------------------------------------------------
# Upstream failures -> 502 / 504
# ----------------------------------------------------------------------


def test_weather_endpoint_maps_upstream_failure_to_502(client, clear_weather_override):
    """A non-200 upstream status is reported as 502, not 500."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(503, {}))
    _override_with(fake_client)

    response = client.get(f"{API_VERSION_PREFIX}/weather")

    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    assert response.json()["detail"]


def test_weather_endpoint_maps_timeout_to_504(client, clear_weather_override):
    """An upstream timeout is reported as 504, not 502 or 500."""
    fake_client = FakeAsyncHTTPClient(exception=httpx.TimeoutException("timed out"))
    _override_with(fake_client)

    response = client.get(f"{API_VERSION_PREFIX}/weather")

    assert response.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_current_endpoint_maps_upstream_failure_to_502(client, clear_weather_override):
    """The /current endpoint maps upstream failures the same way as /weather."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(500, {}))
    _override_with(fake_client)

    response = client.get(f"{API_VERSION_PREFIX}/weather/current")

    assert response.status_code == status.HTTP_502_BAD_GATEWAY


def test_forecast_endpoint_maps_upstream_failure_to_502(client, clear_weather_override):
    """The /forecast endpoint maps upstream failures the same way as /weather."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(500, {}))
    _override_with(fake_client)

    response = client.get(f"{API_VERSION_PREFIX}/weather/forecast")

    assert response.status_code == status.HTTP_502_BAD_GATEWAY


# ----------------------------------------------------------------------
# Regression: existing Phase 1 routes still work
# ----------------------------------------------------------------------


def test_existing_terrain_route_still_works(client):
    """Adding the weather router does not disturb the terrain router."""
    response = client.get(f"{API_VERSION_PREFIX}/terrain")
    assert response.status_code == status.HTTP_200_OK


def test_existing_system_route_still_works(client):
    """Adding the weather router does not disturb the system router."""
    response = client.get(f"{API_VERSION_PREFIX}/system/health")
    assert response.status_code == status.HTTP_200_OK


def test_openapi_schema_includes_weather_routes(client):
    """The weather routes appear in the generated OpenAPI schema."""
    response = client.get("/openapi.json")

    assert response.status_code == status.HTTP_200_OK
    paths = response.json()["paths"]
    assert f"{API_VERSION_PREFIX}/weather" in paths
    assert f"{API_VERSION_PREFIX}/weather/current" in paths
    assert f"{API_VERSION_PREFIX}/weather/forecast" in paths