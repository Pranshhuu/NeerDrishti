"""
Provisional ward-level flood-risk indicator endpoint for FlowSight.

This endpoint exposes FloodRiskService: a deterministic, transparent
combination of forecast-driven ward runoff and terrain flow-concentration
signals. It is NOT flood depth, inundation extent, flood probability,
drainage capacity, an official catchment analysis, measured discharge, or
an operational flood prediction, and involves no machine learning.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.endpoints.weather import get_weather_service
from app.schemas.runoff import FloodRiskResponse, WardFloodRiskResult
from app.services.flood_risk_service import (
    FloodRiskDataError,
    FloodRiskService,
    FloodRiskServiceError,
    FloodRiskTimestampNotFoundError,
)
from app.services.runoff.basin_service import (
    BasinRunoffDataError,
    BasinRunoffInputError,
)
from app.services.weather_service import (
    WeatherRequestError,
    WeatherService,
    WeatherServiceError,
    WeatherTimeoutError,
    WeatherUpstreamError,
)

router = APIRouter(prefix="/flood-risk", tags=["flood-risk"])


def get_flood_risk_service() -> FloodRiskService:
    """Provide the flood-risk service."""
    return FloodRiskService()


@router.get(
    "",
    response_model=FloodRiskResponse,
    summary="Calculate a provisional ward-level flood-risk indicator",
)
async def calculate_flood_risk(
    weather_service: WeatherService = Depends(get_weather_service),
    flood_risk_service: FloodRiskService = Depends(get_flood_risk_service),
) -> FloodRiskResponse:
    """
    Calculate the provisional flood-risk indicator for the next available
    forecast hour, per BMC ward.

    Fetches the current default-location (Mumbai) forecast through the
    existing WeatherService, then delegates to FloodRiskService, which
    combines forecast-driven ward runoff (WardRunoffService) and terrain
    flow-concentration class coverage (FlowConcentrationService) into a
    deterministic heuristic indicator.

    Raises:
        HTTPException: 400/504/502 for the underlying weather-fetch
            failure modes, mapped exactly as in the other /runoff and
            /weather endpoints. 503 if no hourly forecast entry at or
            after the fetch time exists, or if ward/terrain/flow-
            concentration data is unavailable. 422 if the selected
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

    try:
        result = flood_risk_service.calculate_for_forecast(weather)
    except FloodRiskTimestampNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except BasinRunoffInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except BasinRunoffDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except FloodRiskDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except FloodRiskServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    return FloodRiskResponse(
        status="completed",
        selected_forecast_timestamp=result.selected_forecast_timestamp,
        rainfall_intensity_mm_h=result.rainfall_intensity_mm_h,
        rainfall_source=result.rainfall_source,
        rainfall_data_type=result.rainfall_data_type,
        ward_count=result.ward_count,
        wards=[
            WardFloodRiskResult(
                ward_id=w.ward_id,
                ward_name=w.ward_name,
                runoff_low_m3s=w.runoff_low_m3s,
                runoff_high_m3s=w.runoff_high_m3s,
                flow_concentration_high_fraction=w.flow_concentration_high_fraction,
                flow_concentration_very_high_fraction=w.flow_concentration_very_high_fraction,
                risk_score=w.risk_score,
                risk_level=w.risk_level,
            )
            for w in result.wards
        ],
        terrain_source=result.terrain_source,
        landcover_source=result.landcover_source,
        coefficient_status=result.coefficient_status,
        flow_concentration_methodology=result.flow_concentration_methodology,
        risk_methodology=result.risk_methodology,
        generated_at=datetime.now(timezone.utc),
    )