"""
Ward-level runoff aggregation for Flowshift.

This service allocates terrain-derived basin runoff to BMC wards
according to raster-cell overlap.

IMPORTANT:
- BMC wards are administrative boundaries, not drainage catchments.
- Basin-to-ward runoff is an area allocation, not measured discharge.
- Basins extending outside BMC retain an explicit outside-BMC fraction.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom

from app.services.runoff.basin_service import (
    BasinRunoffDataError,
    BasinRunoffInputError,
    BasinRunoffService,
)


WARD_FILENAME = "BMC_admin_wards.geojson"
WARD_STAGE = "raw/boundaries/mumbai"


class WardRunoffServiceError(Exception):
    """Base error for ward-level runoff aggregation."""


@dataclass(frozen=True)
class WardRunoffAllocation:
    """Runoff allocated to one BMC ward."""

    ward_id: int
    ward_name: str
    basin_count: int
    contributing_area_m2: float
    runoff_low_m3s: float
    runoff_high_m3s: float


@dataclass(frozen=True)
class WardRunoffSummary:
    """Aggregate ward-level runoff result."""

    eligible_basin_count: int
    ward_count: int
    rainfall_intensity_mm_h: float
    rainfall_source: str
    rainfall_scenario: str
    allocations: Tuple[WardRunoffAllocation, ...]
    outside_bmc_runoff_low_m3s: float
    outside_bmc_runoff_high_m3s: float
    terrain_source: str
    landcover_source: str
    coefficient_status: str


class WardRunoffService:
    """
    Allocate basin-level Rational Method runoff across BMC wards.

    The underlying basin calculations remain owned by BasinRunoffService.
    """

    def __init__(
        self,
        data_root: Path | str | None = None,
        support_threshold: float = 0.90,
    ) -> None:
        self.basin_service = BasinRunoffService(
            data_root=data_root,
            support_threshold=support_threshold,
        )

        self.data_root = self.basin_service.data_root

    @property
    def ward_path(self) -> Path:
        """Path to the BMC administrative ward GeoJSON."""
        return self.data_root / WARD_STAGE / WARD_FILENAME

    def _load_ward_geojson_document(self) -> Dict[str, Any]:
        """
        Load and parse the BMC ward GeoJSON document.

        This is the single point of file I/O for BMC ward data.
        _load_wards() (feature list only) and get_ward_boundaries() (the
        full FeatureCollection, for direct API exposure) both delegate
        here, so the source file is read and parsed in exactly one place.

        Returns:
            The parsed GeoJSON document as a dictionary, unmodified.

        Raises:
            BasinRunoffDataError: If the file is missing, unreadable, not
                valid JSON, not a JSON object, or has no non-empty
                'features' list.
        """
        if not self.ward_path.is_file():
            raise BasinRunoffDataError(
                f"BMC ward file not found: {self.ward_path}"
            )

        try:
            with self.ward_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise BasinRunoffDataError(
                f"Cannot read BMC ward file: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise BasinRunoffDataError(
                "BMC ward GeoJSON is not a JSON object."
            )

        features = payload.get("features")

        if not isinstance(features, list) or not features:
            raise BasinRunoffDataError(
                "BMC ward GeoJSON contains no features."
            )

        return payload

    def _load_wards(self) -> List[dict]:
        """
        Load BMC ward geometries.

        Delegates to _load_ward_geojson_document() for the actual file
        I/O, preserving this method's existing return type and exception
        behavior exactly.
        """
        payload = self._load_ward_geojson_document()
        return payload["features"]

    def get_ward_boundaries(self) -> Dict[str, Any]:
        """
        Return the real BMC administrative ward boundary GeoJSON document.

        This exposes the same source file used internally for basin-to-ward
        allocation, unmodified: no geometry is simplified, generated, or
        synthesized, and no property is added, removed, or renamed.

        Returns:
            The parsed GeoJSON FeatureCollection, exactly as stored on disk.

        Raises:
            BasinRunoffDataError: If the source file is missing, unreadable,
                or not a valid GeoJSON FeatureCollection.
        """
        payload = self._load_ward_geojson_document()

        if payload.get("type") != "FeatureCollection":
            raise BasinRunoffDataError(
                "BMC ward GeoJSON is not a FeatureCollection."
            )

        return payload

    def _rasterize_wards(
        self,
        basin_shape: Tuple[int, int],
        basin_transform,
        basin_crs,
    ) -> Tuple[np.ndarray, Dict[int, str]]:
        """Rasterize BMC wards onto the exact basin grid."""
        features = self._load_wards()

        shapes = []
        names: Dict[int, str] = {}

        for feature in features:
            properties = feature.get("properties", {})
            geometry = feature.get("geometry")

            try:
                ward_id = int(properties["gid"])
                ward_name = str(properties["name"])
            except (KeyError, TypeError, ValueError) as exc:
                raise BasinRunoffDataError(
                    f"Invalid BMC ward properties: {properties!r}"
                ) from exc

            if not geometry:
                raise BasinRunoffDataError(
                    f"Ward {ward_id} has no geometry."
                )

            transformed = transform_geom(
                "OGC:CRS84",
                basin_crs,
                geometry,
                precision=6,
            )

            shapes.append((transformed, ward_id))
            names[ward_id] = ward_name

        raster = rasterize(
            shapes,
            out_shape=basin_shape,
            transform=basin_transform,
            fill=0,
            dtype="int16",
        )

        if not np.any(raster > 0):
            raise BasinRunoffDataError(
                "BMC ward rasterization produced no valid ward cells."
            )

        return raster, names

    @staticmethod
    def _statistics_check(value: float, field_name: str) -> None:
        if not math.isfinite(value):
            raise BasinRunoffDataError(
                f"{field_name} contains a non-finite value."
            )

    def calculate(
        self,
        rainfall_intensity_mm_h: float,
        rainfall_source: str,
        rainfall_scenario: str,
    ) -> WardRunoffSummary:
        """
        Calculate provisional runoff allocated to BMC wards.

        Ward runoff is derived from terrain-basin runoff and raster-cell
        overlap with administrative ward boundaries.
        """
        if not isinstance(rainfall_source, str) or not rainfall_source.strip():
            raise BasinRunoffInputError(
                "rainfall_source must be a non-empty string."
            )

        if not isinstance(rainfall_scenario, str) or not rainfall_scenario.strip():
            raise BasinRunoffInputError(
                "rainfall_scenario must be a non-empty string."
            )

        if (
            not math.isfinite(rainfall_intensity_mm_h)
            or rainfall_intensity_mm_h < 0.0
        ):
            raise BasinRunoffInputError(
                "rainfall_intensity_mm_h must be finite and >= 0."
            )

        basins, worldcover = self.basin_service._load_aligned_arrays()
        coefficient_ranges, mapping_status = (
            self.basin_service._load_mapping()
        )

        (
            eligible_ids,
            c_low,
            c_high,
            area_m2,
        ) = self.basin_service._derive_basin_coefficients(
            basins=basins,
            worldcover=worldcover,
            coefficient_ranges=coefficient_ranges,
        )

        if eligible_ids.size == 0:
            raise BasinRunoffDataError(
                "No eligible basins are available for ward aggregation."
            )

        q_low = np.empty(len(eligible_ids), dtype=np.float64)
        q_high = np.empty(len(eligible_ids), dtype=np.float64)

        from app.services.runoff.engine import calculate_peak_discharge

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

        with rasterio.open(self.basin_service.basin_path) as src:
            ward_raster, ward_names = self._rasterize_wards(
                basin_shape=basins.shape,
                basin_transform=src.transform,
                basin_crs=src.crs,
            )

        ward_low: Dict[int, float] = {
            ward_id: 0.0 for ward_id in ward_names
        }
        ward_high: Dict[int, float] = {
            ward_id: 0.0 for ward_id in ward_names
        }
        ward_area: Dict[int, float] = {
            ward_id: 0.0 for ward_id in ward_names
        }
        ward_basins: Dict[int, set] = {
            ward_id: set() for ward_id in ward_names
        }

        outside_low = 0.0
        outside_high = 0.0

        for index, basin_id in enumerate(eligible_ids):
            basin_mask = basins == basin_id
            total_cells = int(np.count_nonzero(basin_mask))

            if total_cells == 0:
                continue

            basin_wards = ward_raster[basin_mask]
            cell_area = float(area_m2[index]) / total_cells

            unique_wards, counts = np.unique(
                basin_wards[basin_wards > 0],
                return_counts=True,
            )

            allocated_fraction = 0.0

            for ward_id_raw, count in zip(unique_wards, counts):
                ward_id = int(ward_id_raw)
                fraction = float(count) / total_cells
                allocated_fraction += fraction

                ward_low[ward_id] += float(q_low[index]) * fraction
                ward_high[ward_id] += float(q_high[index]) * fraction
                ward_area[ward_id] += cell_area * int(count)
                ward_basins[ward_id].add(int(basin_id))

            outside_fraction = max(0.0, 1.0 - allocated_fraction)

            outside_low += float(q_low[index]) * outside_fraction
            outside_high += float(q_high[index]) * outside_fraction

        allocations = []

        for ward_id in sorted(ward_names):
            allocation = WardRunoffAllocation(
                ward_id=ward_id,
                ward_name=ward_names[ward_id],
                basin_count=len(ward_basins[ward_id]),
                contributing_area_m2=ward_area[ward_id],
                runoff_low_m3s=ward_low[ward_id],
                runoff_high_m3s=ward_high[ward_id],
            )

            self._statistics_check(
                allocation.runoff_low_m3s,
                f"ward {ward_id} low runoff",
            )
            self._statistics_check(
                allocation.runoff_high_m3s,
                f"ward {ward_id} high runoff",
            )

            allocations.append(allocation)

        self._statistics_check(
            outside_low,
            "outside-BMC low runoff",
        )
        self._statistics_check(
            outside_high,
            "outside-BMC high runoff",
        )

        return WardRunoffSummary(
            eligible_basin_count=len(eligible_ids),
            ward_count=len(allocations),
            rainfall_intensity_mm_h=float(rainfall_intensity_mm_h),
            rainfall_source=rainfall_source,
            rainfall_scenario=rainfall_scenario,
            allocations=tuple(allocations),
            outside_bmc_runoff_low_m3s=float(outside_low),
            outside_bmc_runoff_high_m3s=float(outside_high),
            terrain_source="Copernicus GLO-30 terrain-derived isobasins",
            landcover_source="ESA WorldCover 2021",
            coefficient_status=mapping_status,
        )