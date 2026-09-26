"""
Basin-level runoff service for FlowSight.

This service couples terrain-derived modeling basins, aligned WorldCover
land-cover classes, provisional runoff-coefficient ranges, and a supplied
rainfall intensity.

IMPORTANT SCOPE:

- Basins are terrain-derived modeling basins, not official municipal
  drainage catchments.
- WorldCover-derived runoff coefficients are provisional sensitivity
  assumptions and are not calibrated operational coefficients.
- Rainfall intensity is supplied by the caller and retains its source/status.
- The Rational Method produces peak discharge only.
- This service does not model flood depth, inundation, drainage capacity,
  routing, storage, infiltration dynamics, or hydraulic network behavior.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

from app.data.loader import DataLoadError, RasterLoader
from app.data.terrain_provider import TerrainProvider
from app.services.runoff.engine import calculate_peak_discharge


# Terrain-derived modeling basin raster produced by WhiteboxTools isobasins.
BASIN_FILENAME = "Copernicus_Mumbai_GLO30_mosaic_isobasins_1000.tif"

# WorldCover raster aligned exactly to the terrain modeling grid.
WORLDCOVER_FILENAME = "mumbai_worldcover_2021_aligned_full.tif"

# Provisional WorldCover -> runoff coefficient ranges.
RUNOFF_MAPPING_FILENAME = "worldcover_runoff_mapping.json"

# Water is excluded from contributing land area.
PERMANENT_WATER_CLASS = 80

# Current provisional support requirement established during data preparation.
WORLDCOVER_SUPPORT_THRESHOLD = 0.90


class BasinRunoffServiceError(Exception):
    """Base error for basin-level runoff processing."""


class BasinRunoffInputError(ValueError, BasinRunoffServiceError):
    """Raised when required runoff inputs are invalid or incompatible."""


class BasinRunoffDataError(BasinRunoffServiceError):
    """Raised when required basin/runoff data cannot be loaded."""


@dataclass(frozen=True)
class BasinRunoffSummary:
    """Internal result of basin-level runoff processing."""

    eligible_basin_count: int
    worldcover_support_threshold: float
    rainfall_intensity_mm_h: float
    runoff_coefficient_low: Dict[str, float]
    runoff_coefficient_high: Dict[str, float]
    peak_discharge_low_m3s: Dict[str, float]
    peak_discharge_high_m3s: Dict[str, float]
    rainfall_source: str
    rainfall_scenario: str
    terrain_source: str
    landcover_source: str
    coefficient_status: str


class BasinRunoffService:
    """
    Calculate provisional basin-level Rational Method runoff scenarios.

    The service resolves data beneath the project's managed data root and
    never accepts arbitrary client filesystem paths.
    """

    def __init__(
        self,
        data_root: Path | str | None = None,
        support_threshold: float = WORLDCOVER_SUPPORT_THRESHOLD,
    ) -> None:
        if not 0.0 < support_threshold <= 1.0:
            raise BasinRunoffInputError(
                "support_threshold must be > 0 and <= 1."
            )

        if data_root is None:
            data_root = TerrainProvider().data_root

        self.data_root = Path(data_root)
        self.support_threshold = float(support_threshold)

    @property
    def basin_path(self) -> Path:
        """Path to the terrain-derived modeling basin raster."""
        return (
            self.data_root
            / "working"
            / BASIN_FILENAME
        )

    @property
    def worldcover_path(self) -> Path:
        """Path to the WorldCover raster aligned to the terrain grid."""
        return (
            self.data_root
            / "processed"
            / "landcover"
            / WORLDCOVER_FILENAME
        )

    @property
    def mapping_path(self) -> Path:
        """Path to the provisional WorldCover runoff mapping."""
        return (
            self.data_root
            / "processed"
            / "landcover"
            / RUNOFF_MAPPING_FILENAME
        )

    def calculate(
        self,
        rainfall_intensity_mm_h: float,
        rainfall_source: str,
        rainfall_scenario: str,
    ) -> BasinRunoffSummary:
        """
        Calculate basin-level low/high Rational Method runoff scenarios.

        Args:
            rainfall_intensity_mm_h:
                Rainfall intensity in mm/hour.
            rainfall_source:
                Source and status of the rainfall data.
            rainfall_scenario:
                Human-readable description of the rainfall scenario.

        Returns:
            BasinRunoffSummary containing aggregate statistics.

        Raises:
            BasinRunoffInputError:
                If rainfall inputs are invalid.
            BasinRunoffDataError:
                If required raster/mapping inputs are unavailable or
                incompatible.
        """
        self._validate_text(rainfall_source, "rainfall_source")
        self._validate_text(rainfall_scenario, "rainfall_scenario")

        if not math.isfinite(rainfall_intensity_mm_h):
            raise BasinRunoffInputError(
                "rainfall_intensity_mm_h must be finite."
            )

        if rainfall_intensity_mm_h < 0.0:
            raise BasinRunoffInputError(
                "rainfall_intensity_mm_h must be >= 0."
            )

        basins, worldcover = self._load_aligned_arrays()
        coefficient_ranges, mapping_status = self._load_mapping()

        (
            eligible_ids,
            c_low,
            c_high,
            area_m2,
        ) = self._derive_basin_coefficients(
            basins=basins,
            worldcover=worldcover,
            coefficient_ranges=coefficient_ranges,
        )

        if eligible_ids.size == 0:
            raise BasinRunoffDataError(
                "No terrain-derived basins meet the WorldCover support "
                f"threshold of {self.support_threshold:.0%}."
            )

        q_low = np.empty(len(eligible_ids), dtype=np.float64)
        q_high = np.empty(len(eligible_ids), dtype=np.float64)

        for index in range(len(eligible_ids)):
            q_low[index] = calculate_peak_discharge(
                rainfall_intensity_mm_h=rainfall_intensity_mm_h,
                runoff_coefficient=float(c_low[index]),
                drainage_area_m2=float(area_m2[index]),
            ).peak_discharge_m3s

            q_high[index] = calculate_peak_discharge(
                rainfall_intensity_mm_h=rainfall_intensity_mm_h,
                runoff_coefficient=float(c_high[index]),
                drainage_area_m2=float(area_m2[index]),
            ).peak_discharge_m3s

        return BasinRunoffSummary(
            eligible_basin_count=len(eligible_ids),
            worldcover_support_threshold=self.support_threshold,
            rainfall_intensity_mm_h=float(rainfall_intensity_mm_h),
            runoff_coefficient_low=self._statistics(c_low),
            runoff_coefficient_high=self._statistics(c_high),
            peak_discharge_low_m3s=self._statistics(q_low),
            peak_discharge_high_m3s=self._statistics(q_high),
            rainfall_source=rainfall_source,
            rainfall_scenario=rainfall_scenario,
            terrain_source="Copernicus GLO-30 terrain-derived isobasins",
            landcover_source="ESA WorldCover 2021",
            coefficient_status=mapping_status,
        )

    def _load_aligned_arrays(self) -> Tuple[np.ndarray, np.ndarray]:
        """Load and verify the basin and aligned WorldCover rasters."""
        try:
            basins = RasterLoader.read_band(self.basin_path)
            worldcover = RasterLoader.read_band(self.worldcover_path)
        except DataLoadError as exc:
            raise BasinRunoffDataError(str(exc)) from exc

        if basins.shape != worldcover.shape:
            raise BasinRunoffDataError(
                "Basin and WorldCover rasters are not aligned: "
                f"basins={basins.shape}, worldcover={worldcover.shape}."
            )

        return basins, worldcover

    def _load_mapping(
        self,
    ) -> Tuple[Dict[int, Tuple[float, float]], str]:
        """Load provisional WorldCover runoff coefficient ranges."""
        if not self.mapping_path.is_file():
            raise BasinRunoffDataError(
                f"Runoff mapping file not found: {self.mapping_path}"
            )

        try:
            with self.mapping_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise BasinRunoffDataError(
                f"Cannot read runoff mapping: {exc}"
            ) from exc

        raw_classes = payload.get("classes")
        if not isinstance(raw_classes, dict):
            raise BasinRunoffDataError(
                "Runoff mapping must contain a 'classes' object."
            )

        coefficient_ranges: Dict[int, Tuple[float, float]] = {}

        for class_key, class_data in raw_classes.items():
            try:
                class_id = int(class_key)
                c_range = class_data["c_range"]
                if not isinstance(c_range, (list, tuple)) or len(c_range) != 2:
                    raise ValueError("c_range must contain exactly two values")
                c_min = float(c_range[0])
                c_max = float(c_range[1])
            except (KeyError, TypeError, ValueError) as exc:
                raise BasinRunoffDataError(
                    f"Invalid runoff mapping for WorldCover class "
                    f"{class_key!r}: {exc}"
                ) from exc

            if (
                not math.isfinite(c_min)
                or not math.isfinite(c_max)
                or not 0.0 <= c_min <= c_max <= 1.0
            ):
                raise BasinRunoffDataError(
                    f"Invalid coefficient range for WorldCover class "
                    f"{class_id}: [{c_min}, {c_max}]"
                )

            coefficient_ranges[class_id] = (c_min, c_max)

        status = str(payload.get("status", "provisional"))

        return coefficient_ranges, status

    def _derive_basin_coefficients(
        self,
        basins: np.ndarray,
        worldcover: np.ndarray,
        coefficient_ranges: Dict[int, Tuple[float, float]],
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Derive supported basin-level coefficient ranges.

        WorldCover support is measured as the fraction of basin cells with a
        non-zero WorldCover class. Permanent water is included when measuring
        support but excluded from contributing land area.

        Coefficients are cell-weighted means over contributing WorldCover
        classes.
        """
        valid_basin = basins > 0
        if not np.any(valid_basin):
            raise BasinRunoffDataError(
                "The basin raster contains no valid basin cells."
            )

        basin_ids = basins[valid_basin].astype(np.int64, copy=False)
        wc_values = worldcover[valid_basin].astype(np.int64, copy=False)

        max_basin_id = int(basin_ids.max())

        basin_cell_counts = np.bincount(
            basin_ids,
            minlength=max_basin_id + 1,
        )

        supported_mask = wc_values > 0

        supported_counts = np.bincount(
            basin_ids[supported_mask],
            minlength=max_basin_id + 1,
        )

        support_fraction = np.zeros_like(
            basin_cell_counts,
            dtype=np.float64,
        )

        nonempty = basin_cell_counts > 0
        support_fraction[nonempty] = (
            supported_counts[nonempty]
            / basin_cell_counts[nonempty]
        )

        eligible = np.flatnonzero(
            nonempty
            & (support_fraction >= self.support_threshold)
        )

        if eligible.size == 0:
            return (
                np.array([], dtype=np.int64),
                np.array([], dtype=np.float64),
                np.array([], dtype=np.float64),
                np.array([], dtype=np.float64),
            )

        cell_area_m2 = self._cell_area_m2()

        c_low_sum = np.zeros(max_basin_id + 1, dtype=np.float64)
        c_high_sum = np.zeros(max_basin_id + 1, dtype=np.float64)
        contributing_cells = np.zeros(
            max_basin_id + 1,
            dtype=np.int64,
        )

        for class_id, (c_low, c_high) in coefficient_ranges.items():
            class_mask = (
                supported_mask
                & (wc_values == class_id)
                & (basin_ids > 0)
            )

            if not np.any(class_mask):
                continue

            class_basins = basin_ids[class_mask]

            if class_id != PERMANENT_WATER_CLASS:
                c_low_sum += np.bincount(
                    class_basins,
                    weights=np.full(
                        class_basins.size,
                        c_low,
                        dtype=np.float64,
                    ),
                    minlength=max_basin_id + 1,
                )
                c_high_sum += np.bincount(
                    class_basins,
                    weights=np.full(
                        class_basins.size,
                        c_high,
                        dtype=np.float64,
                    ),
                    minlength=max_basin_id + 1,
                )
                contributing_cells += np.bincount(
                    class_basins,
                    minlength=max_basin_id + 1,
                )

        eligible_contributing = contributing_cells[eligible] > 0

        if not np.all(eligible_contributing):
            eligible = eligible[eligible_contributing]

        if eligible.size == 0:
            raise BasinRunoffDataError(
                "No eligible basins contain contributing land-cover cells "
                "after excluding permanent water."
            )

        c_low = (
            c_low_sum[eligible]
            / contributing_cells[eligible]
        )
        c_high = (
            c_high_sum[eligible]
            / contributing_cells[eligible]
        )
        area_m2 = (
            contributing_cells[eligible].astype(np.float64)
            * cell_area_m2
        )

        return eligible, c_low, c_high, area_m2

    def _cell_area_m2(self) -> float:
        """Return the aligned WorldCover pixel area in square metres."""
        try:
            with RasterLoader.open_raster(self.worldcover_path) as src:
                x_resolution, y_resolution = src.res
        except DataLoadError as exc:
            raise BasinRunoffDataError(str(exc)) from exc

        area = abs(float(x_resolution) * float(y_resolution))

        if not math.isfinite(area) or area <= 0.0:
            raise BasinRunoffDataError(
                f"Invalid WorldCover pixel area: {area!r}"
            )

        return area

    @staticmethod
    def _statistics(values: np.ndarray) -> Dict[str, float]:
        """Return finite min/median/mean/max statistics."""
        if values.size == 0:
            raise BasinRunoffDataError(
                "Cannot calculate statistics for an empty result."
            )

        statistics = {
            "minimum": float(np.min(values)),
            "median": float(np.median(values)),
            "mean": float(np.mean(values)),
            "maximum": float(np.max(values)),
        }

        if not all(math.isfinite(value) for value in statistics.values()):
            raise BasinRunoffDataError(
                "Runoff statistics contain a non-finite value."
            )

        return statistics

    @staticmethod
    def _validate_text(value: str, field_name: str) -> None:
        """Validate a required textual input."""
        if not isinstance(value, str) or not value.strip():
            raise BasinRunoffInputError(
                f"{field_name} must be a non-empty string."
            )
