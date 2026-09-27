"""
Forecast rainfall to basin-runoff coupling service for FlowSight.

This service connects one hourly entry from an already-fetched
WeatherResponse (see app.schemas.weather) to the existing
BasinRunoffService, so a single forecast hour can drive a provisional
basin-level Rational Method runoff scenario.

IMPORTANT SCOPE:

- The supplied WeatherResponse is forecast model output (e.g. Open-Meteo /
  ECMWF IFS HRES), never a rain-gauge observation. This service never
  labels it "measured" or "observed" rainfall.
- HourlyForecast.precipitation_mm is precipitation forecast for a ONE-HOUR
  interval. Using that numeric value directly as a Rational Method
  rainfall_intensity_mm_h (mm/hour) is dimensionally valid ONLY because the
  selected interval is exactly one hour; it is not a general mm -> mm/h
  conversion. This assumption is documented in this module, at the point
  of use below, and in the precipitation_basis field of the returned
  result, so it is never silently implicit.
- This service does not fetch weather data itself and makes no network
  calls: it operates on a WeatherResponse the caller already obtained.
- This service does not modify, wrap, or duplicate WeatherService,
  the Rational Method engine, or BasinRunoffService's own validation.
  It selects one input value and delegates.
- Output is a forecast-driven provisional runoff scenario. It is not flood
  depth, flood extent, flood probability, drainage capacity, an official
  catchment, measured discharge, or an operational flood prediction.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from app.schemas.weather import HourlyForecast, WeatherResponse
from app.services.runoff.basin_service import BasinRunoffService

# Fixed explanatory text carried in every result, per the scope note above.
# Kept as a single module constant so the wording used in code comments,
# docstrings, and the API-visible field always match exactly.
PRECIPITATION_BASIS = (
    "precipitation_mm from a one-hour forecast interval is used directly "
    "as rainfall_intensity_mm_h (mm/hour); this is valid only because the "
    "selected interval is exactly one hour, and is not a general mm to "
    "mm/h conversion."
)


class ForecastCouplingServiceError(Exception):
    """Base error for forecast-to-runoff coupling."""


class ForecastCouplingInputError(ValueError, ForecastCouplingServiceError):
    """Raised when a supplied argument is structurally invalid."""


class ForecastTimestampNotFoundError(ForecastCouplingServiceError):
    """
    Raised when no hourly forecast entry matches the requested timestamp.

    Also raised when the supplied WeatherResponse has no hourly entries at
    all, since that trivially cannot contain any requested timestamp.
    """


@dataclass(frozen=True)
class ForecastRunoffCouplingSummary:
    """Result of coupling one forecast hour to a basin-level runoff scenario."""

    selected_forecast_timestamp: datetime
    rainfall_intensity_mm_h: float
    rainfall_source: str
    rainfall_scenario: str
    rainfall_data_type: str
    precipitation_basis: str
    eligible_basin_count: int
    runoff_coefficient_low: Dict[str, float]
    runoff_coefficient_high: Dict[str, float]
    peak_discharge_low_m3s: Dict[str, float]
    peak_discharge_high_m3s: Dict[str, float]
    terrain_source: str
    landcover_source: str
    coefficient_status: str


class ForecastRunoffCouplingService:
    """
    Couple one hourly forecast entry to a BasinRunoffService scenario.

    BasinRunoffService is injected (defaulting to a real instance) so tests
    can supply a fake exposing only .calculate(...), without touching the
    real terrain/WorldCover rasters BasinRunoffService reads from disk.
    """

    def __init__(
        self,
        basin_service: Optional[BasinRunoffService] = None,
    ) -> None:
        self.basin_service: BasinRunoffService = (
            basin_service if basin_service is not None else BasinRunoffService()
        )

    def calculate_for_timestamp(
        self,
        weather: WeatherResponse,
        timestamp: datetime,
    ) -> ForecastRunoffCouplingSummary:
        """
        Run a basin-level runoff scenario for one selected forecast hour.

        Args:
            weather: An already-fetched WeatherResponse. Not fetched here.
            timestamp: Timezone-aware timestamp identifying the hourly
                forecast entry to use. Must exactly match one entry's
                timestamp in weather.hourly.

        Returns:
            ForecastRunoffCouplingSummary combining the selected forecast
            hour's provenance with the resulting basin runoff scenario.

        Raises:
            ForecastCouplingInputError: If timestamp is not timezone-aware.
            ForecastTimestampNotFoundError: If weather.hourly is empty, or
                no entry matches the requested timestamp.
            BasinRunoffInputError: Propagated unchanged if BasinRunoffService
                rejects the selected precipitation value.
            BasinRunoffDataError: Propagated unchanged if BasinRunoffService
                cannot load its terrain/WorldCover inputs.
        """
        if timestamp.tzinfo is None:
            raise ForecastCouplingInputError(
                "timestamp must be timezone-aware."
            )

        matched = self._select_hourly_entry(weather, timestamp)

        rainfall_source = (
            f"{weather.source.provider} / {weather.source.model} forecast"
        )
        rainfall_scenario = (
            f"Open-Meteo hourly forecast valid {matched.timestamp.isoformat()}"
        )

        # See PRECIPITATION_BASIS / module docstring: precipitation_mm for
        # this ONE-HOUR interval is used directly as mm/hour here. This is
        # the single point where that equivalence is applied.
        rainfall_intensity_mm_h = matched.precipitation_mm

        basin_summary = self.basin_service.calculate(
            rainfall_intensity_mm_h=rainfall_intensity_mm_h,
            rainfall_source=rainfall_source,
            rainfall_scenario=rainfall_scenario,
        )

        return ForecastRunoffCouplingSummary(
            selected_forecast_timestamp=matched.timestamp,
            rainfall_intensity_mm_h=basin_summary.rainfall_intensity_mm_h,
            rainfall_source=basin_summary.rainfall_source,
            rainfall_scenario=basin_summary.rainfall_scenario,
            rainfall_data_type="forecast",
            precipitation_basis=PRECIPITATION_BASIS,
            eligible_basin_count=basin_summary.eligible_basin_count,
            runoff_coefficient_low=basin_summary.runoff_coefficient_low,
            runoff_coefficient_high=basin_summary.runoff_coefficient_high,
            peak_discharge_low_m3s=basin_summary.peak_discharge_low_m3s,
            peak_discharge_high_m3s=basin_summary.peak_discharge_high_m3s,
            terrain_source=basin_summary.terrain_source,
            landcover_source=basin_summary.landcover_source,
            coefficient_status=basin_summary.coefficient_status,
        )

    @staticmethod
    def _select_hourly_entry(
        weather: WeatherResponse,
        timestamp: datetime,
    ) -> HourlyForecast:
        """
        Find the hourly forecast entry matching timestamp exactly.

        Raises:
            ForecastTimestampNotFoundError: If weather.hourly is empty, or
                no entry's timestamp equals the requested timestamp exactly.
        """
        if not weather.hourly:
            raise ForecastTimestampNotFoundError(
                "Weather response contains no hourly forecast entries."
            )

        for entry in weather.hourly:
            if entry.timestamp == timestamp:
                return entry

        available = ", ".join(
            entry.timestamp.isoformat() for entry in weather.hourly
        )
        raise ForecastTimestampNotFoundError(
            f"No hourly forecast entry found for timestamp "
            f"{timestamp.isoformat()}. Available timestamps: {available}."
        )