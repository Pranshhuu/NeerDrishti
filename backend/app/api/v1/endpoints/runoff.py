"""
Basin-level runoff endpoint for FlowSight.

This endpoint exposes the provisional terrain/WorldCover runoff scenario
calculation through HTTP.

The returned discharge values are Rational Method peak-discharge scenarios.
They are not flood depths, inundation extents, municipal drainage capacity,
or calibrated operational predictions.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.schemas.runoff import (
    BasinRunoffResponse,
    FlowConcentrationResponse,
    RunoffSource,
    WardRunoffResponse,
)
from app.services.flow_concentration_service import (
    FlowConcentrationDataError,
    FlowConcentrationService,
)
from app.services.runoff.basin_service import (
    BasinRunoffDataError,
    BasinRunoffInputError,
    BasinRunoffService,
)
from app.services.runoff.ward_service import WardRunoffService

router = APIRouter(prefix="/runoff", tags=["runoff"])


def get_basin_runoff_service() -> BasinRunoffService:
    """Provide the basin runoff service."""
    return BasinRunoffService()


@router.get(
    "/basins",
    response_model=BasinRunoffResponse,
    summary="Calculate a provisional basin-level runoff scenario",
)
def calculate_basin_runoff(
    rainfall_intensity_mm_h: float = Query(
        ...,
        ge=0.0,
        description="Rainfall intensity scenario in mm/hour.",
    ),
    rainfall_source: str = Query(
        ...,
        min_length=1,
        description="Source and status of the rainfall data.",
    ),
    rainfall_scenario: str = Query(
        ...,
        min_length=1,
        description="Description of the rainfall scenario.",
    ),
    service: BasinRunoffService = Depends(get_basin_runoff_service),
) -> BasinRunoffResponse:
    """
    Calculate basin-level low/high Rational Method runoff scenarios.

    The service uses terrain-derived modeling basins and provisional
    WorldCover-based runoff coefficient ranges.
    """
    try:
        result = service.calculate(
            rainfall_intensity_mm_h=rainfall_intensity_mm_h,
            rainfall_source=rainfall_source,
            rainfall_scenario=rainfall_scenario,
        )
    except BasinRunoffInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except BasinRunoffDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    source = RunoffSource(
        rainfall_source=result.rainfall_source,
        rainfall_scenario=result.rainfall_scenario,
        terrain_source=result.terrain_source,
        landcover_source=result.landcover_source,
        coefficient_status=result.coefficient_status,
    )

    return BasinRunoffResponse(
        status="completed",
        eligible_basin_count=result.eligible_basin_count,
        worldcover_support_threshold=result.worldcover_support_threshold,
        rainfall_intensity_mm_h=result.rainfall_intensity_mm_h,
        runoff_coefficient_low=result.runoff_coefficient_low,
        runoff_coefficient_high=result.runoff_coefficient_high,
        peak_discharge_low_m3s=result.peak_discharge_low_m3s,
        peak_discharge_high_m3s=result.peak_discharge_high_m3s,
        source=source,
        generated_at=datetime.now(timezone.utc),
    )


def get_ward_runoff_service() -> WardRunoffService:
    """Provide the ward runoff service."""
    return WardRunoffService()


@router.get(
    "/wards",
    response_model=WardRunoffResponse,
    summary="Calculate provisional runoff allocated across BMC wards",
)
def calculate_ward_runoff(
    rainfall_intensity_mm_h: float = Query(
        ...,
        ge=0.0,
        description="Rainfall intensity scenario in mm/hour.",
    ),
    rainfall_source: str = Query(
        ...,
        min_length=1,
        description="Source and status of the rainfall data.",
    ),
    rainfall_scenario: str = Query(
        ...,
        min_length=1,
        description="Description of the rainfall scenario.",
    ),
    service: WardRunoffService = Depends(get_ward_runoff_service),
) -> WardRunoffResponse:
    """
    Calculate provisional runoff allocated across BMC administrative wards.

    Ward values are area allocations from terrain-derived modeling basins.
    They are not official municipal drainage-catchment discharges.
    """
    try:
        result = service.calculate(
            rainfall_intensity_mm_h=rainfall_intensity_mm_h,
            rainfall_source=rainfall_source,
            rainfall_scenario=rainfall_scenario,
        )
    except BasinRunoffInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except BasinRunoffDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    source = RunoffSource(
        rainfall_source=result.rainfall_source,
        rainfall_scenario=result.rainfall_scenario,
        terrain_source=result.terrain_source,
        landcover_source=result.landcover_source,
        coefficient_status=result.coefficient_status,
    )

    return WardRunoffResponse(
        status="completed",
        eligible_basin_count=result.eligible_basin_count,
        ward_count=result.ward_count,
        rainfall_intensity_mm_h=result.rainfall_intensity_mm_h,
        allocations=[
            {
                "ward_id": allocation.ward_id,
                "ward_name": allocation.ward_name,
                "basin_count": allocation.basin_count,
                "contributing_area_m2": allocation.contributing_area_m2,
                "runoff_low_m3s": allocation.runoff_low_m3s,
                "runoff_high_m3s": allocation.runoff_high_m3s,
            }
            for allocation in result.allocations
        ],
        outside_bmc_runoff_low_m3s=result.outside_bmc_runoff_low_m3s,
        outside_bmc_runoff_high_m3s=result.outside_bmc_runoff_high_m3s,
        source=source,
        generated_at=datetime.now(timezone.utc),
    )


def get_flow_concentration_service() -> FlowConcentrationService:
    """Provide the flow-concentration service."""
    return FlowConcentrationService()


@router.get(
    "/flow-concentration",
    response_model=FlowConcentrationResponse,
    summary="Inspect the terrain-derived flow-concentration indicator",
)
def get_flow_concentration(
    service: FlowConcentrationService = Depends(
        get_flow_concentration_service
    ),
) -> FlowConcentrationResponse:
    """
    Return the precomputed BMC terrain-derived flow-concentration indicator.

    This indicator is derived from D8 flow accumulation and BMC boundaries.
    It is not a drainage network, flood depth, or inundation prediction.
    """
    try:
        result = service.calculate()
    except FlowConcentrationDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return FlowConcentrationResponse(
        status="completed",
        dataset_filename=result.dataset_filename,
        width=result.width,
        height=result.height,
        class_0_cells=result.class_0_cells,
        class_1_cells=result.class_1_cells,
        class_2_cells=result.class_2_cells,
        class_3_cells=result.class_3_cells,
        p95_accumulation_cells=result.p95_accumulation_cells,
        p99_accumulation_cells=result.p99_accumulation_cells,
        terrain_source=result.terrain_source,
        boundary_source=result.boundary_source,
        interpretation=result.interpretation,
        generated_at=datetime.now(timezone.utc),
    )
