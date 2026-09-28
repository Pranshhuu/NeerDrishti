"""
Provisional ward-level flood-risk indicator for FlowSight.

This service combines two ALREADY-EXISTING signals - forecast-driven ward
runoff (via WardRunoffService) and terrain-derived flow-concentration class
coverage (via FlowConcentrationService) - into one transparent, deterministic
per-ward indicator.

IMPORTANT SCOPE - READ BEFORE USING THIS OUTPUT:

This indicator is NOT:
- flood depth
- inundation extent
- flood probability
- drainage capacity
- an official municipal catchment analysis
- measured discharge
- an operational flood prediction

It is a terrain + forecast-runoff-derived heuristic indicator, built from a
fixed, documented weighted combination of three measurable signals (see
RISK_METHODOLOGY below). It is NOT a calibrated probability and involves no
machine learning.

This service reuses, rather than duplicates:
- WardRunoffService for all basin/WorldCover/runoff computation.
- FlowConcentrationService for terrain flow-concentration raster validation
  and provenance (its raster is re-read once more here only to obtain the
  per-cell class array needed for the ward overlay, since .calculate()
  itself returns whole-domain aggregate counts only, not the array).
- The same "first hourly forecast entry >= fetched_at" selection rule
  already implemented for GET /api/v1/runoff/forecast in
  app/api/v1/endpoints/runoff.py. That rule is restated here (not imported)
  deliberately: importing from the API layer into a service would invert
  the correct dependency direction. If the selection rule ever changes,
  both copies must be updated together.

WardRunoffService's own ward-boundary rasterization targets the basin
raster's grid. The flow-concentration raster is a separate grid, so this
service performs its own vector-to-raster overlay of the same ward
geometries (obtained from WardRunoffService.get_ward_boundaries(), not
duplicated) onto the flow-concentration raster's own transform/CRS, using
the same technique WardRunoffService already uses internally.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom

from app.data.loader import DataLoadError, RasterLoader
from app.schemas.weather import HourlyForecast, WeatherResponse
from app.services.flow_concentration_service import (
    FlowConcentrationDataError,
    FlowConcentrationService,
)
from app.services.runoff.basin_service import (
    BasinRunoffDataError,
    BasinRunoffInputError,
)
from app.services.runoff.ward_service import WardRunoffService

# Flow-concentration class codes, matching FlowConcentrationService exactly.
CLASS_NO_DATA = 0
CLASS_BELOW_P95 = 1
CLASS_P95_TO_P99 = 2
CLASS_P99_AND_ABOVE = 3

# ---------------------------------------------------------------------------
# Risk-score methodology. All weights/thresholds are named constants,
# documented here, and echoed back verbatim in RISK_METHODOLOGY so the
# response is self-describing.
#
# score = 100 * (
#     RUNOFF_WEIGHT           * runoff_signal
#     + HIGH_CONC_WEIGHT      * flow_concentration_high_fraction
#     + VERY_HIGH_CONC_WEIGHT * flow_concentration_very_high_fraction
# )
#
# runoff_signal: each ward's runoff_high_m3s divided by the maximum
#   runoff_high_m3s across all wards IN THIS SAME RESPONSE. This is a
#   self-relative signal for the current forecast run, not a value compared
#   against an external/calibrated discharge threshold.
#
# flow_concentration_*_fraction: each ward's fraction of TERRAIN-CLASSIFIED
#   cells (class 1, 2, or 3 - excluding class 0 "no data") falling into the
#   P95-P99 ("high") or P99+ ("very high") empirical accumulation classes.
#
# VERY_HIGH_CONC_WEIGHT > HIGH_CONC_WEIGHT deliberately: class 3 (top 1% by
# empirical accumulation) is a rarer, more extreme terrain signal than
# class 2 (95th-99th percentile), so it is weighted more heavily.
# ---------------------------------------------------------------------------
RUNOFF_WEIGHT = 0.40
HIGH_CONC_WEIGHT = 0.25
VERY_HIGH_CONC_WEIGHT = 0.35

RISK_LEVEL_THRESHOLDS = (
    (25.0, "LOW"),
    (50.0, "MODERATE"),
    (75.0, "HIGH"),
    (math.inf, "VERY_HIGH"),
)

RISK_METHODOLOGY = (
    "risk_score (0-100) = 100 * ("
    f"{RUNOFF_WEIGHT:.2f} * runoff_signal + "
    f"{HIGH_CONC_WEIGHT:.2f} * flow_concentration_high_fraction + "
    f"{VERY_HIGH_CONC_WEIGHT:.2f} * flow_concentration_very_high_fraction"
    "). runoff_signal = this ward's runoff_high_m3s / the maximum "
    "runoff_high_m3s across all wards in this same response (self-relative "
    "to the current forecast run, not an externally calibrated threshold). "
    "flow_concentration_*_fraction = this ward's fraction of terrain-"
    "classified cells (excluding no-data) in the P95-P99 or P99-and-above "
    "empirical D8 flow-accumulation classes. risk_level thresholds on "
    "risk_score: <25 LOW, <50 MODERATE, <75 HIGH, >=75 VERY_HIGH. This is a "
    "deterministic heuristic combination of measurable signals, not a "
    "calibrated flood-risk probability, and involves no machine learning."
)

FLOW_CONCENTRATION_METHODOLOGY = (
    "Per-ward flow_concentration_high_fraction and "
    "flow_concentration_very_high_fraction are computed by overlaying BMC "
    "ward boundaries onto the terrain-derived D8 flow-accumulation class "
    "raster (classes: 1 = below P95, 2 = P95 to below P99, 3 = P99 and "
    "above; 0 = no data / not classified). Each fraction is relative to "
    "that ward's classified (non-zero-class) cell count only, so wards "
    "with large no-data areas are not misrepresented as low-concentration."
)


class FloodRiskServiceError(Exception):
    """Base error for flood-risk indicator processing."""


class FloodRiskInputError(ValueError, FloodRiskServiceError):
    """Raised when a supplied argument is structurally invalid."""


class FloodRiskTimestampNotFoundError(FloodRiskServiceError):
    """
    Raised when no hourly forecast entry at or after weather.fetched_at
    exists, including when weather.hourly is empty.
    """


class FloodRiskDataError(FloodRiskServiceError):
    """Raised when required ward/terrain/flow-concentration data cannot be loaded."""


@dataclass(frozen=True)
class WardFloodRiskResult:
    """Provisional flood-risk indicator for one BMC ward."""

    ward_id: int
    ward_name: str
    runoff_low_m3s: float
    runoff_high_m3s: float
    flow_concentration_high_fraction: float
    flow_concentration_very_high_fraction: float
    risk_score: float
    risk_level: str


@dataclass(frozen=True)
class FloodRiskSummary:
    """Provisional ward-level flood-risk indicator for one forecast hour."""

    selected_forecast_timestamp: datetime
    rainfall_intensity_mm_h: float
    rainfall_source: str
    rainfall_scenario: str
    rainfall_data_type: str
    ward_count: int
    wards: Tuple[WardFloodRiskResult, ...]
    terrain_source: str
    landcover_source: str
    coefficient_status: str
    flow_concentration_methodology: str
    risk_methodology: str


class FloodRiskService:
    """
    Combine ward-level forecast runoff and terrain flow-concentration
    class coverage into a provisional per-ward risk indicator.

    ward_service and flow_concentration_service are injected (each
    defaulting to a real instance) so tests can supply fakes without
    touching real terrain/WorldCover/flow-concentration rasters.
    """

    def __init__(
        self,
        ward_service: Optional[WardRunoffService] = None,
        flow_concentration_service: Optional[FlowConcentrationService] = None,
    ) -> None:
        self.ward_service: WardRunoffService = (
            ward_service if ward_service is not None else WardRunoffService()
        )
        self.flow_concentration_service: FlowConcentrationService = (
            flow_concentration_service
            if flow_concentration_service is not None
            else FlowConcentrationService()
        )

    def calculate_for_forecast(
        self, weather: WeatherResponse
    ) -> FloodRiskSummary:
        """
        Calculate the provisional ward-level flood-risk indicator for the
        next available (non-past) hourly forecast entry.

        Args:
            weather: An already-fetched WeatherResponse. Not fetched here.

        Returns:
            FloodRiskSummary with one WardFloodRiskResult per BMC ward.

        Raises:
            FloodRiskTimestampNotFoundError: If weather.hourly is empty, or
                every entry is earlier than weather.fetched_at.
            BasinRunoffInputError: Propagated unchanged from WardRunoffService
                if the selected precipitation value is rejected.
            BasinRunoffDataError: Propagated unchanged from WardRunoffService
                if its terrain/WorldCover inputs are unavailable.
            FloodRiskDataError: If the flow-concentration raster or ward
                boundary geometry cannot be loaded or overlaid.
        """
        selected = self._select_next_hourly_entry(weather)

        rainfall_source = (
            f"{weather.source.provider} / {weather.source.model} forecast"
        )
        rainfall_scenario = (
            f"Open-Meteo hourly forecast valid {selected.timestamp.isoformat()}"
        )

        ward_runoff = self.ward_service.calculate(
            rainfall_intensity_mm_h=selected.precipitation_mm,
            rainfall_source=rainfall_source,
            rainfall_scenario=rainfall_scenario,
        )

        # Reuses FlowConcentrationService's own full validation (single 2D
        # band, nodata == 0, classes subset of {0,1,2,3}) rather than
        # re-implementing it. Its terrain_source text is reused for
        # provenance below.
        try:
            flow_summary = self.flow_concentration_service.calculate()
        except FlowConcentrationDataError as exc:
            raise FloodRiskDataError(str(exc)) from exc

        high_fractions, very_high_fractions = self._ward_flow_concentration_fractions()

        max_runoff_high = max(
            (allocation.runoff_high_m3s for allocation in ward_runoff.allocations),
            default=0.0,
        )

        ward_results = []
        for allocation in ward_runoff.allocations:
            high_fraction = high_fractions.get(allocation.ward_id, 0.0)
            very_high_fraction = very_high_fractions.get(allocation.ward_id, 0.0)

            runoff_signal = (
                allocation.runoff_high_m3s / max_runoff_high
                if max_runoff_high > 0.0
                else 0.0
            )

            score = 100.0 * (
                RUNOFF_WEIGHT * runoff_signal
                + HIGH_CONC_WEIGHT * high_fraction
                + VERY_HIGH_CONC_WEIGHT * very_high_fraction
            )
            score = min(100.0, max(0.0, score))

            ward_results.append(
                WardFloodRiskResult(
                    ward_id=allocation.ward_id,
                    ward_name=allocation.ward_name,
                    runoff_low_m3s=allocation.runoff_low_m3s,
                    runoff_high_m3s=allocation.runoff_high_m3s,
                    flow_concentration_high_fraction=high_fraction,
                    flow_concentration_very_high_fraction=very_high_fraction,
                    risk_score=score,
                    risk_level=self._risk_level(score),
                )
            )

        return FloodRiskSummary(
            selected_forecast_timestamp=selected.timestamp,
            rainfall_intensity_mm_h=ward_runoff.rainfall_intensity_mm_h,
            rainfall_source=ward_runoff.rainfall_source,
            rainfall_scenario=ward_runoff.rainfall_scenario,
            rainfall_data_type="forecast",
            ward_count=len(ward_results),
            wards=tuple(ward_results),
            terrain_source=ward_runoff.terrain_source,
            landcover_source=ward_runoff.landcover_source,
            coefficient_status=ward_runoff.coefficient_status,
            flow_concentration_methodology=(
                f"{flow_summary.interpretation}. {FLOW_CONCENTRATION_METHODOLOGY}"
            ),
            risk_methodology=RISK_METHODOLOGY,
        )

    # ------------------------------------------------------------------
    # Forecast-hour selection
    # ------------------------------------------------------------------

    @staticmethod
    def _select_next_hourly_entry(weather: WeatherResponse) -> HourlyForecast:
        """
        Select the first hourly forecast entry not already in the past.

        Restates the identical rule used by GET /api/v1/runoff/forecast
        (app/api/v1/endpoints/runoff.py::_select_next_hourly_entry) rather
        than importing it, since a service must not depend on the API
        layer. Keep both in sync if this rule ever changes.
        """
        for entry in weather.hourly:
            if entry.timestamp >= weather.fetched_at:
                return entry

        raise FloodRiskTimestampNotFoundError(
            "No hourly forecast entry at or after the forecast fetch time "
            f"({weather.fetched_at.isoformat()}) was found."
        )

    # ------------------------------------------------------------------
    # Flow-concentration overlay
    # ------------------------------------------------------------------

    def _ward_flow_concentration_fractions(
        self,
    ) -> Tuple[Dict[int, float], Dict[int, float]]:
        """
        Overlay BMC ward boundaries onto the flow-concentration raster's
        own grid and compute, per ward, the fraction of its CLASSIFIED
        cells (class in {1,2,3}, excluding class 0 "no data") falling into
        class 2 ("high") and class 3 ("very high").

        Returns:
            (high_fractions, very_high_fractions), each a dict keyed by
            ward_id, values bounded [0.0, 1.0]. A ward with zero
            classified cells maps to 0.0 in both.

        Raises:
            FloodRiskDataError: If the raster or ward boundaries cannot be
                read, or the overlay produces no valid ward cells.
        """
        try:
            classes = RasterLoader.read_band(
                self.flow_concentration_service.raster_path
            )
        except DataLoadError as exc:
            raise FloodRiskDataError(str(exc)) from exc

        try:
            with rasterio.open(self.flow_concentration_service.raster_path) as src:
                raster_shape = (src.height, src.width)
                raster_transform = src.transform
                raster_crs = src.crs
        except rasterio.errors.RasterioError as exc:
            raise FloodRiskDataError(
                f"Cannot open flow-concentration raster for ward overlay: {exc}"
            ) from exc

        try:
            boundaries = self.ward_service.get_ward_boundaries()
        except BasinRunoffDataError as exc:
            raise FloodRiskDataError(str(exc)) from exc

        features = boundaries.get("features", [])
        shapes = []
        ward_ids = []

        for feature in features:
            properties = feature.get("properties", {})
            geometry = feature.get("geometry")

            try:
                ward_id = int(properties["gid"])
            except (KeyError, TypeError, ValueError) as exc:
                raise FloodRiskDataError(
                    f"Invalid BMC ward properties: {properties!r}"
                ) from exc

            if not geometry:
                raise FloodRiskDataError(f"Ward {ward_id} has no geometry.")

            transformed = transform_geom(
                "OGC:CRS84", raster_crs, geometry, precision=6
            )
            shapes.append((transformed, ward_id))
            ward_ids.append(ward_id)

        ward_raster = rasterize(
            shapes,
            out_shape=raster_shape,
            transform=raster_transform,
            fill=0,
            dtype="int32",
        )

        if not np.any(ward_raster > 0):
            raise FloodRiskDataError(
                "Ward overlay onto the flow-concentration raster produced "
                "no valid ward cells."
            )

        high_fractions: Dict[int, float] = {}
        very_high_fractions: Dict[int, float] = {}

        for ward_id in ward_ids:
            ward_mask = ward_raster == ward_id
            ward_classes = classes[ward_mask]

            classified_mask = ward_classes != CLASS_NO_DATA
            classified_count = int(np.count_nonzero(classified_mask))

            if classified_count == 0:
                high_fractions[ward_id] = 0.0
                very_high_fractions[ward_id] = 0.0
                continue

            classified = ward_classes[classified_mask]
            high_count = int(np.count_nonzero(classified == CLASS_P95_TO_P99))
            very_high_count = int(
                np.count_nonzero(classified == CLASS_P99_AND_ABOVE)
            )

            high_fractions[ward_id] = high_count / classified_count
            very_high_fractions[ward_id] = very_high_count / classified_count

        return high_fractions, very_high_fractions

    # ------------------------------------------------------------------
    # Risk categorization
    # ------------------------------------------------------------------

    @staticmethod
    def _risk_level(score: float) -> str:
        """Map a 0-100 risk_score to a fixed, deterministic category."""
        for threshold, level in RISK_LEVEL_THRESHOLDS:
            if score < threshold:
                return level
        return "VERY_HIGH"  # unreachable given math.inf sentinel, kept defensive