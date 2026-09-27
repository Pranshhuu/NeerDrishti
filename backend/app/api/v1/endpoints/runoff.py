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
from fastapi.responses import FileResponse

from app.api.v1.endpoints.weather import get_weather_service
from app.schemas.runoff import (
    BasinRunoffResponse,
    FlowConcentrationResponse,
    ForecastRunoffResponse,
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
from app.services.runoff.forecast_coupling_service import (
    ForecastCouplingServiceError,
    ForecastRunoffCouplingService,
)
from app.services.runoff.ward_service import WardRunoffService
from app.services.weather_service import (
    WeatherRequestError,
    WeatherService,
    WeatherServiceError,
    WeatherTimeoutError,
    WeatherUpstreamError,
)

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


@router.get(
    "/wards/boundaries",
    summary="Return the existing BMC administrative ward boundaries as GeoJSON",
)
def get_ward_boundaries(
    service: WardRunoffService = Depends(get_ward_runoff_service),
) -> dict:
    """
    Return the real BMC administrative ward boundary GeoJSON.

    This exposes the same BMC ward GeoJSON document already used
    internally by WardRunoffService for basin-to-ward allocation.
    Geometry and properties are returned exactly as stored in the
    source file; nothing is simplified, generated, or synthesized.

    No response_model is declared deliberately: this endpoint returns the
    source GeoJSON document as-is, and pinning it to a rigid schema would
    require modeling every possible GeoJSON geometry/property shape for no
    benefit, since the frontend only needs the document itself.

    Returns:
        The BMC ward FeatureCollection, unmodified.

    Raises:
        HTTPException: 503 if the source GeoJSON is missing, unreadable,
            or not a valid FeatureCollection.
    """
    try:
        return service.get_ward_boundaries()
    except BasinRunoffDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


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


@router.get(
    "/flow-concentration/image",
    summary="Return the terrain-derived flow-concentration visualization PNG",
)
def get_flow_concentration_image(
    service: FlowConcentrationService = Depends(
        get_flow_concentration_service
    ),
) -> FileResponse:
    """
    Return the precomputed flow-concentration visualization as a PNG image.

    This is a terrain-derived flow-concentration visualization only. It is
    not flood depth, inundation extent, a drainage network, hydraulic
    capacity, or an operational flood prediction. It renders the same
    underlying D8 flow-accumulation analysis reported by the JSON
    /flow-concentration endpoint, for direct visual inspection.

    Returns:
        The visualization PNG, served with media_type "image/png".

    Raises:
        HTTPException: 503 if the visualization PNG has not been generated
            or cannot be found at its expected location.
    """
    if not service.visualization_path.is_file():
        error = FlowConcentrationDataError(
            f"Flow-concentration visualization not found: "
            f"{service.visualization_path}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error

    return FileResponse(
        path=service.visualization_path,
        media_type="image/png",
    )


def get_forecast_coupling_service() -> ForecastRunoffCouplingService:
    """Provide the forecast-to-runoff coupling service."""
    return ForecastRunoffCouplingService()


@router.get(
    "/forecast",
    response_model=ForecastRunoffResponse,
    summary="Calculate a forecast-driven provisional basin runoff scenario",
)
async def calculate_forecast_runoff(
    weather_service: WeatherService = Depends(get_weather_service),
    coupling_service: ForecastRunoffCouplingService = Depends(
        get_forecast_coupling_service
    ),
) -> ForecastRunoffResponse:
    """
    Calculate a provisional basin runoff scenario from the live forecast.

    Fetches the current default-location (Mumbai) forecast through the
    existing WeatherService, selects the first available hourly forecast
    entry, and couples it to BasinRunoffService via
    ForecastRunoffCouplingService. No second weather client is used and no
    direct upstream call is made from this endpoint.

    This is a forecast-driven provisional Rational Method runoff scenario.
    It is not flood depth, flood extent, flood probability, drainage
    capacity, an official catchment, measured discharge, or an operational
    flood prediction.

    Raises:
        HTTPException: 400/504/502 for the underlying weather-fetch
            failure modes (bad coordinates, upstream timeout, upstream
            failure), mapped exactly as in the /weather endpoints. 503 if
            the forecast has no hourly entries, or if BasinRunoffService's
            terrain/WorldCover inputs are unavailable. 422 if the selected
            precipitation value is rejected as a rainfall input.
    """
    try:
        weather = await weather_service.get_weather()
    except WeatherRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except WeatherTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)
        ) from exc
    except WeatherUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    except WeatherServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    if not weather.hourly:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Weather forecast contains no hourly entries.",
        )

    selected_timestamp = weather.hourly[0].timestamp

    try:
        result = coupling_service.calculate_for_timestamp(
            weather, selected_timestamp
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
    except ForecastCouplingServiceError as exc:
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

    return ForecastRunoffResponse(
        status="completed",
        selected_forecast_timestamp=result.selected_forecast_timestamp,
        rainfall_intensity_mm_h=result.rainfall_intensity_mm_h,
        rainfall_data_type=result.rainfall_data_type,
        precipitation_basis=result.precipitation_basis,
        eligible_basin_count=result.eligible_basin_count,
        runoff_coefficient_low=result.runoff_coefficient_low,
        runoff_coefficient_high=result.runoff_coefficient_high,
        peak_discharge_low_m3s=result.peak_discharge_low_m3s,
        peak_discharge_high_m3s=result.peak_discharge_high_m3s,
        source=source,
        generated_at=datetime.now(timezone.utc),
    )