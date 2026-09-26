"""
Weather service tests for FlowSight (Phase 2A)

Exercises app.services.weather_service.WeatherService directly, with the
upstream HTTP client replaced by a small in-file fake. No test in this file
reaches the network.

Async methods are invoked via asyncio.run() rather than a pytest-asyncio
plugin, since none is currently declared as a project dependency and adding
one is unnecessary for this scope.

Numeric regression coverage strategy:
    current.precipitation, current.rain, hourly.precipitation, and
    hourly.rain are all validated by the same shared
    WeatherService._validate_finite_non_negative helper. Rather than testing
    every invalid category (NaN, +inf, -inf, negative, non-numeric,
    boolean) against all four fields, this file tests the full set of
    categories exhaustively against one field (current.precipitation) to
    prove the helper itself is correct, then adds one or two tests per
    remaining field to confirm the helper is actually wired up at that call
    site. This avoids 24 near-identical combinatorial tests while still
    covering every failure mode and every call site at least once.
"""

import asyncio

import httpx
import pytest

from app.services.weather_service import (
    MAX_FORECAST_HOURS,
    MIN_FORECAST_HOURS,
    WEATHER_MODEL_NAME,
    WEATHER_PROVIDER_NAME,
    WeatherRequestError,
    WeatherService,
    WeatherServiceError,
    WeatherTimeoutError,
    WeatherUpstreamError,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class FakeResponse:
    """Stand-in for httpx.Response exposing only what this service uses."""

    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class FakeAsyncHTTPClient:
    """
    Stand-in for httpx.AsyncClient that records calls and returns a fixed
    response or raises a fixed exception, so no test reaches the network.
    """

    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self._exception is not None:
            raise self._exception
        return self._response


def _sample_payload(
    timezone_name="Asia/Kolkata",
    current_time="2026-09-08T18:00",
    current_precipitation=0.0,
    current_rain=0.0,
    current_weather_code=3,
    hourly_times=("2026-09-08T18:00", "2026-09-08T19:00", "2026-09-08T20:00"),
    precipitation=(0.0, 0.2, 0.0),
    rain=(0.0, 0.2, 0.0),
):
    """Build a realistic Open-Meteo response, matching the shape of the URL
    already confirmed working live against api.open-meteo.com."""
    return {
        "latitude": 19.05,
        "longitude": 72.86,
        "timezone": timezone_name,
        "current": {
            "time": current_time,
            "precipitation": current_precipitation,
            "rain": current_rain,
            "weather_code": current_weather_code,
        },
        "hourly": {
            "time": list(hourly_times),
            "precipitation": list(precipitation),
            "rain": list(rain),
        },
    }


def _run(coro):
    """Run a coroutine to completion without a pytest-asyncio dependency."""
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# 1. Successful responses
# ----------------------------------------------------------------------


def test_get_weather_returns_normalized_response():
    """A successful upstream call produces a fully populated WeatherResponse."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    result = _run(service.get_weather(latitude=19.05, longitude=72.86, forecast_hours=3))

    assert result.location.latitude == 19.05
    assert result.location.longitude == 72.86
    assert result.location.timezone == "Asia/Kolkata"
    assert len(result.hourly) == 3
    assert result.status == "ok"


def test_get_weather_defaults_to_configured_mumbai_coordinates():
    """Omitting coordinates falls back to Settings.WEATHER_DEFAULT_*."""
    from app.core.config import settings

    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    _run(service.get_weather())

    call = fake_client.calls[0]
    assert call["params"]["latitude"] == settings.WEATHER_DEFAULT_LATITUDE
    assert call["params"]["longitude"] == settings.WEATHER_DEFAULT_LONGITUDE


def test_get_weather_uses_supplied_custom_coordinates():
    """Explicit coordinates are passed through to the upstream request."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    _run(service.get_weather(latitude=28.6139, longitude=77.2090))

    call = fake_client.calls[0]
    assert call["params"]["latitude"] == 28.6139
    assert call["params"]["longitude"] == 77.2090


def test_current_weather_parses_expected_fields():
    """Current-block values are parsed into the CurrentWeather schema exactly."""
    payload = _sample_payload(
        current_precipitation=1.5, current_rain=1.2, current_weather_code=61
    )
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    result = _run(service.get_weather())

    assert result.current.precipitation_mm == 1.5
    assert result.current.rain_mm == 1.2
    assert result.current.weather_code == 61
    assert result.current.timestamp.tzinfo is not None


def test_hourly_forecast_parses_each_entry():
    """Every hourly entry is parsed with matching precipitation/rain values."""
    payload = _sample_payload(
        hourly_times=("2026-09-08T18:00", "2026-09-08T19:00"),
        precipitation=(0.0, 2.5),
        rain=(0.0, 2.5),
    )
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    result = _run(service.get_weather(forecast_hours=2))

    assert len(result.hourly) == 2
    assert result.hourly[1].precipitation_mm == 2.5
    assert result.hourly[1].rain_mm == 2.5
    assert all(entry.timestamp.tzinfo is not None for entry in result.hourly)


def test_source_identifies_open_meteo_ecmwf():
    """Every response names the actual provider and model, never a placeholder."""
    from app.core.config import settings

    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    result = _run(service.get_weather())

    assert result.source.provider == WEATHER_PROVIDER_NAME == "Open-Meteo"
    assert result.source.model == WEATHER_MODEL_NAME
    assert "ECMWF" in result.source.model
    assert result.source.source_url == settings.WEATHER_API_BASE_URL


def test_response_timezone_reflects_upstream_value():
    """The location's timezone is taken from the response, not hardcoded."""
    payload = _sample_payload(timezone_name="UTC")
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    result = _run(service.get_weather())

    assert result.location.timezone == "UTC"


def test_get_current_weather_requests_a_single_hour():
    """get_current_weather requests the minimal forecast window."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    _run(service.get_current_weather())

    assert fake_client.calls[0]["params"]["forecast_hours"] == MIN_FORECAST_HOURS


def test_get_forecast_uses_the_requested_hour_count():
    """get_forecast passes forecast_hours through unchanged."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    _run(service.get_forecast(forecast_hours=6))

    assert fake_client.calls[0]["params"]["forecast_hours"] == 6


# ----------------------------------------------------------------------
# 2. Malformed upstream responses
# ----------------------------------------------------------------------


def test_malformed_json_raises_upstream_error():
    """An unparseable body is reported as a distinct upstream failure."""
    fake_client = FakeAsyncHTTPClient(
        response=FakeResponse(200, payload=None, json_error=True)
    )
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="JSON"):
        _run(service.get_weather())


def test_missing_current_raises_upstream_error():
    """A response with no 'current' block is rejected, not silently defaulted."""
    payload = _sample_payload()
    del payload["current"]
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="current"):
        _run(service.get_weather())


def test_missing_hourly_raises_upstream_error():
    """A response with no 'hourly' block is rejected."""
    payload = _sample_payload()
    del payload["hourly"]
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="hourly"):
        _run(service.get_weather())


def test_hourly_missing_precipitation_array_raises_upstream_error():
    """A missing required array inside 'hourly' is rejected by name."""
    payload = _sample_payload()
    del payload["hourly"]["precipitation"]
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="precipitation"):
        _run(service.get_weather())


def test_hourly_mismatched_array_lengths_raises_upstream_error():
    """time/precipitation/rain arrays of different lengths are rejected."""
    payload = _sample_payload()
    payload["hourly"]["rain"] = [0.0]
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="mismatched"):
        _run(service.get_weather())


def test_empty_hourly_arrays_raise_upstream_error():
    """An hourly block present but empty is rejected, not treated as zero forecast."""
    payload = _sample_payload(hourly_times=(), precipitation=(), rain=())
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="empty"):
        _run(service.get_weather())


def test_invalid_timestamp_raises_upstream_error():
    """An unparseable timestamp string is rejected, not silently skipped."""
    payload = _sample_payload(current_time="not-a-timestamp")
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="timestamp"):
        _run(service.get_weather())


def test_non_object_json_response_raises_upstream_error():
    """A top-level JSON array (or any non-object) is rejected."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, [1, 2, 3]))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="JSON object"):
        _run(service.get_weather())


# ----------------------------------------------------------------------
# 3. Transport failures
# ----------------------------------------------------------------------


def test_upstream_http_error_raises_upstream_error():
    """A non-200 upstream status is reported with the actual status code."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(503, payload={}))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="503"):
        _run(service.get_weather())


def test_timeout_raises_weather_timeout_error_specifically():
    """
    A timeout is WeatherTimeoutError, not the generic WeatherUpstreamError.

    httpx.TimeoutException is a subclass of httpx.RequestError, so the except
    clauses in WeatherService must be ordered timeout-first; this test would
    fail if that ordering were ever reversed.
    """
    fake_client = FakeAsyncHTTPClient(exception=httpx.TimeoutException("timed out"))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherTimeoutError):
        _run(service.get_weather())


def test_connection_error_raises_upstream_error_not_timeout():
    """A connection failure is WeatherUpstreamError, distinct from a timeout."""
    fake_client = FakeAsyncHTTPClient(exception=httpx.ConnectError("connection refused"))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError):
        _run(service.get_weather())


# ----------------------------------------------------------------------
# 4. Input validation
# ----------------------------------------------------------------------


def test_invalid_latitude_raises_request_error():
    """A latitude outside [-90, 90] is rejected before any upstream call."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherRequestError, match="latitude"):
        _run(service.get_weather(latitude=120.0, longitude=72.86))

    assert fake_client.calls == []


def test_invalid_longitude_raises_request_error():
    """A longitude outside [-180, 180] is rejected before any upstream call."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherRequestError, match="longitude"):
        _run(service.get_weather(latitude=19.05, longitude=200.0))

    assert fake_client.calls == []


def test_only_one_coordinate_supplied_raises_request_error():
    """Supplying latitude without longitude (or vice versa) is rejected."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherRequestError, match="both"):
        _run(service.get_weather(latitude=19.05, longitude=None))

    with pytest.raises(WeatherRequestError, match="both"):
        _run(service.get_weather(latitude=None, longitude=72.86))


def test_invalid_forecast_hours_below_minimum_raises_request_error():
    """forecast_hours below 1 is rejected before any upstream call."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherRequestError, match="forecast_hours"):
        _run(service.get_weather(forecast_hours=0))

    assert fake_client.calls == []


def test_invalid_forecast_hours_above_maximum_raises_request_error():
    """forecast_hours above 24 is rejected before any upstream call."""
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, _sample_payload()))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherRequestError, match="forecast_hours"):
        _run(service.get_weather(forecast_hours=MAX_FORECAST_HOURS + 1))

    assert fake_client.calls == []


# ----------------------------------------------------------------------
# 5. Regression tests for the original production validation fixes
# ----------------------------------------------------------------------


def test_current_precipitation_rejects_nan():
    """
    A NaN precipitation value from the upstream response is rejected.

    NaN would otherwise silently corrupt every downstream comparison or sum
    that touches this value.
    """
    payload = _sample_payload(current_precipitation=float("nan"))
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="current.precipitation"):
        _run(service.get_weather())


def test_current_rain_rejects_negative_value():
    """A negative rain value from the upstream response is rejected."""
    payload = _sample_payload(current_rain=-5.0)
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="current.rain"):
        _run(service.get_weather())


def test_hourly_precipitation_rejects_positive_infinity():
    """A positive-infinity precipitation value in the hourly forecast is rejected."""
    payload = _sample_payload(
        hourly_times=("2026-09-08T18:00",),
        precipitation=(float("inf"),),
        rain=(0.0,),
    )
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="hourly.precipitation"):
        _run(service.get_weather())


def test_unsupported_weather_provider_is_rejected_at_initialization():
    """
    Constructing WeatherService with an unsupported WEATHER_PROVIDER raises.

    An unrecognised provider value means the configuration cannot be trusted
    to describe the service this code actually talks to (Open-Meteo).
    """
    from app.core.config import Settings

    bad_settings = Settings(WEATHER_PROVIDER="unsupported_provider")

    with pytest.raises(WeatherServiceError, match="Unsupported WEATHER_PROVIDER"):
        WeatherService(settings=bad_settings)


# ----------------------------------------------------------------------
# 6. Expanded numeric regression coverage
#
# current.precipitation is swept through every invalid category to prove
# WeatherService._validate_finite_non_negative itself is correct. The
# remaining three call sites (current.rain, hourly.precipitation,
# hourly.rain) each get one additional test, using a category not already
# covered for that site by section 5, to confirm the shared helper is
# actually invoked there rather than bypassed.
# ----------------------------------------------------------------------


def test_current_precipitation_rejects_positive_infinity():
    """A positive-infinity current.precipitation value is rejected."""
    payload = _sample_payload(current_precipitation=float("inf"))
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="finite"):
        _run(service.get_weather())


def test_current_precipitation_rejects_negative_infinity():
    """A negative-infinity current.precipitation value is rejected."""
    payload = _sample_payload(current_precipitation=float("-inf"))
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="finite"):
        _run(service.get_weather())


def test_current_precipitation_rejects_negative_value():
    """A negative current.precipitation value is rejected."""
    payload = _sample_payload(current_precipitation=-3.0)
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="negative"):
        _run(service.get_weather())


def test_current_precipitation_rejects_non_numeric_string():
    """A non-numeric string current.precipitation value is rejected."""
    payload = _sample_payload(current_precipitation="heavy")
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="numeric"):
        _run(service.get_weather())


def test_current_precipitation_rejects_boolean():
    """
    A boolean current.precipitation value is rejected.

    Python's bool is technically an int subclass, so True/False could
    otherwise silently pass through as 1.0/0.0 rather than being recognised
    as the type error they represent.
    """
    payload = _sample_payload(current_precipitation=True)
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="numeric"):
        _run(service.get_weather())


def test_current_rain_rejects_nan():
    """A NaN current.rain value is rejected (section 5 already covers negative)."""
    payload = _sample_payload(current_rain=float("nan"))
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="current.rain"):
        _run(service.get_weather())


def test_hourly_precipitation_rejects_negative_value():
    """
    A negative hourly.precipitation value is rejected (section 5 already
    covers positive infinity for this field).
    """
    payload = _sample_payload(
        hourly_times=("2026-09-08T18:00",),
        precipitation=(-1.0,),
        rain=(0.0,),
    )
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="negative"):
        _run(service.get_weather())


def test_hourly_rain_rejects_non_numeric_string():
    """A non-numeric string hourly.rain value is rejected."""
    payload = _sample_payload(
        hourly_times=("2026-09-08T18:00",),
        precipitation=(0.0,),
        rain=("heavy",),
    )
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="numeric"):
        _run(service.get_weather())


def test_hourly_rain_rejects_negative_infinity():
    """A negative-infinity hourly.rain value is rejected."""
    payload = _sample_payload(
        hourly_times=("2026-09-08T18:00",),
        precipitation=(0.0,),
        rain=(float("-inf"),),
    )
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="finite"):
        _run(service.get_weather())


# ----------------------------------------------------------------------
# 7. weather_code regression coverage
#
# Only categories the current implementation actually enforces are tested
# here. Boolean weather_code (e.g. True) is currently accepted by
# WeatherService, since int(True) == 1 passes both the int() conversion and
# the >= 0 check; no test asserts rejection of it, since that behavior is
# not implemented and asserting it would misrepresent the production code.
# ----------------------------------------------------------------------


def test_current_weather_code_rejects_negative_value():
    """A negative weather_code is rejected."""
    payload = _sample_payload(current_weather_code=-1)
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="weather_code"):
        _run(service.get_weather())


def test_current_weather_code_rejects_non_numeric_string():
    """A non-numeric string weather_code is rejected."""
    payload = _sample_payload(current_weather_code="heavy")
    fake_client = FakeAsyncHTTPClient(response=FakeResponse(200, payload))
    service = WeatherService(http_client=fake_client)

    with pytest.raises(WeatherUpstreamError, match="weather_code"):
        _run(service.get_weather())