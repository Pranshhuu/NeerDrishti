"""
API schemas for FlowSight basin-level runoff scenarios.

These schemas describe provisional terrain/land-cover-derived runoff
scenarios. They do not represent observed drainage capacity, flood depth,
inundation extent, or calibrated municipal catchments.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


__all__ = [
    "RunoffSource",
    "RunoffStatistics",
    "BasinRunoffResponse",
    "WardRunoffAllocation",
    "WardRunoffResponse",
    "FlowConcentrationResponse",
    "ForecastRunoffResponse",
]


def _require_not_blank(value: str) -> str:
    """Reject empty and whitespace-only strings."""
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


class RunoffSource(BaseModel):
    """Provenance describing the inputs used for a runoff scenario."""

    model_config = ConfigDict(extra="forbid")

    rainfall_source: str = Field(
        min_length=1,
        description="Rainfall data source and its observational/model status.",
    )
    rainfall_scenario: str = Field(
        min_length=1,
        description="Description of the rainfall scenario used.",
    )
    terrain_source: str = Field(
        min_length=1,
        description="Terrain dataset used to derive modeling basins.",
    )
    landcover_source: str = Field(
        min_length=1,
        description="Land-cover dataset used for provisional runoff coefficients.",
    )
    coefficient_status: str = Field(
        min_length=1,
        description="Status of the runoff coefficients, e.g. 'provisional'.",
    )

    @field_validator(
        "rainfall_source",
        "rainfall_scenario",
        "terrain_source",
        "landcover_source",
        "coefficient_status",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_not_blank(value)


class RunoffStatistics(BaseModel):
    """Summary statistics for one runoff scenario across eligible basins."""

    model_config = ConfigDict(extra="forbid")

    minimum: float = Field(ge=0.0)
    median: float = Field(ge=0.0)
    mean: float = Field(ge=0.0)
    maximum: float = Field(ge=0.0)

    @field_validator("minimum", "median", "mean", "maximum")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not value == value or value in (float("inf"), float("-inf")):
            raise ValueError("statistic must be finite")
        return value


class BasinRunoffResponse(BaseModel):
    """
    Summary of a basin-level Rational Method runoff scenario.

    Values are terrain-derived modeling results using provisional
    land-cover coefficient bounds. They are not flood depths or
    municipal drainage-capacity predictions.
    """

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        min_length=1,
        description="Processing outcome, normally 'completed'.",
    )
    eligible_basin_count: int = Field(
        ge=0,
        description="Number of terrain-derived basins meeting the land-cover support threshold.",
    )
    worldcover_support_threshold: float = Field(
        gt=0.0,
        le=1.0,
        description="Minimum fraction of a basin covered by aligned WorldCover data.",
    )
    rainfall_intensity_mm_h: float = Field(
        ge=0.0,
        description="Rainfall intensity used for the Rational Method scenario.",
    )
    runoff_coefficient_low: RunoffStatistics
    runoff_coefficient_high: RunoffStatistics
    peak_discharge_low_m3s: RunoffStatistics
    peak_discharge_high_m3s: RunoffStatistics
    source: RunoffSource
    generated_at: datetime

    @field_validator("status")
    @classmethod
    def _status_not_blank(cls, value: str) -> str:
        return _require_not_blank(value)


class WardRunoffAllocation(BaseModel):
    """
    Runoff allocated to one BMC administrative ward.

    This is an area allocation from terrain-derived modeling basins.
    It is not measured municipal drainage discharge.
    """

    model_config = ConfigDict(extra="forbid")

    ward_id: int = Field(
        ge=1,
        description="BMC administrative ward identifier.",
    )
    ward_name: str = Field(
        min_length=1,
        description="BMC administrative ward name.",
    )
    basin_count: int = Field(
        ge=0,
        description="Number of eligible terrain-derived basins contributing to this ward.",
    )
    contributing_area_m2: float = Field(
        ge=0.0,
        description="Area allocated to this ward from eligible basin cells.",
    )
    runoff_low_m3s: float = Field(
        ge=0.0,
        description="Low provisional Rational Method runoff allocation in m³/s.",
    )
    runoff_high_m3s: float = Field(
        ge=0.0,
        description="High provisional Rational Method runoff allocation in m³/s.",
    )

    @field_validator(
        "ward_name",
        "contributing_area_m2",
        "runoff_low_m3s",
        "runoff_high_m3s",
    )
    @classmethod
    def _finite_or_not_blank(cls, value):
        if isinstance(value, str):
            return _require_not_blank(value)

        if not value == value or value in (float("inf"), float("-inf")):
            raise ValueError("value must be finite")

        return value


class WardRunoffResponse(BaseModel):
    """
    Summary of provisional runoff allocated across BMC wards.

    Ward values are area-allocated terrain-derived modeling results.
    They are not official municipal drainage-catchment discharges,
    flood depths, or inundation predictions.
    """

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        min_length=1,
        description="Processing outcome, normally 'completed'.",
    )
    eligible_basin_count: int = Field(
        ge=0,
        description="Number of eligible terrain-derived basins used.",
    )
    ward_count: int = Field(
        ge=0,
        description="Number of BMC wards represented in the result.",
    )
    rainfall_intensity_mm_h: float = Field(
        ge=0.0,
        description="Rainfall intensity used for the Rational Method scenario.",
    )
    allocations: list[WardRunoffAllocation] = Field(
        description="Runoff allocated to each BMC administrative ward.",
    )
    outside_bmc_runoff_low_m3s: float = Field(
        ge=0.0,
        description="Low provisional runoff remaining outside BMC ward boundaries.",
    )
    outside_bmc_runoff_high_m3s: float = Field(
        ge=0.0,
        description="High provisional runoff remaining outside BMC ward boundaries.",
    )
    source: RunoffSource
    generated_at: datetime

    @field_validator("status")
    @classmethod
    def _status_not_blank(cls, value: str) -> str:
        return _require_not_blank(value)


class FlowConcentrationResponse(BaseModel):
    """Response for the terrain-derived flow-concentration indicator."""

    status: str
    dataset_filename: str
    width: int
    height: int
    class_0_cells: int
    class_1_cells: int
    class_2_cells: int
    class_3_cells: int
    p95_accumulation_cells: float
    p99_accumulation_cells: float
    terrain_source: str
    boundary_source: str
    interpretation: str
    generated_at: datetime


class ForecastRunoffResponse(BaseModel):
    """
    Summary of a forecast-driven provisional basin runoff scenario.

    Rainfall intensity is taken directly from one hourly weather-forecast
    entry (e.g. Open-Meteo / ECMWF IFS HRES), not a rain-gauge observation.
    Values are terrain-derived modeling results using provisional
    land-cover coefficient bounds. They are not flood depths, flood
    extents, flood probabilities, drainage capacity, an official
    catchment, measured discharge, or an operational flood prediction.
    """

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        min_length=1,
        description="Processing outcome, normally 'completed'.",
    )
    selected_forecast_timestamp: datetime = Field(
        description="Timestamp of the hourly forecast entry used for this scenario.",
    )
    rainfall_intensity_mm_h: float = Field(
        ge=0.0,
        description=(
            "Forecast precipitation for the selected hour, used directly "
            "as mm/hour."
        ),
    )
    rainfall_data_type: str = Field(
        min_length=1,
        description="Nature of the rainfall input, e.g. 'forecast'.",
    )
    precipitation_basis: str = Field(
        min_length=1,
        description=(
            "Explanation of the one-hour precipitation-to-mm/hour "
            "equivalence used for this scenario."
        ),
    )
    eligible_basin_count: int = Field(
        ge=0,
        description="Number of eligible terrain-derived basins used.",
    )
    runoff_coefficient_low: RunoffStatistics
    runoff_coefficient_high: RunoffStatistics
    peak_discharge_low_m3s: RunoffStatistics
    peak_discharge_high_m3s: RunoffStatistics
    source: RunoffSource
    generated_at: datetime

    @field_validator("status", "rainfall_data_type", "precipitation_basis")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _require_not_blank(value)