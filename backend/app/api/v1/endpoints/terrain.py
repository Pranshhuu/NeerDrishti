"""
Terrain dataset endpoints for FlowSight (Phase 1)

Exposes TerrainService through HTTP: listing available terrain products,
inspecting one, and verifying that one is acceptable for processing.

Path safety:
    TerrainService returns absolute server paths in several places
    (get_terrain_info's 'path', a validation report's 'filepath', and a 'path'
    key inside each check's details). Those are stripped here. Clients address
    terrain by filename and stage, never by path, and TerrainService.
    get_terrain_path is deliberately not exposed.

Phase 1 scope: terrain only. Rainfall, drainage, flood prediction, and routing
are later phases and have no representation here.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status

from app.core.logging import get_logger
from app.models.terrain import TerrainStage
from app.schemas.terrain import TerrainCheckSchema, TerrainVerificationResponse
from app.services.terrain_service import (
    InvalidTerrainRequestError,
    TerrainService,
    TerrainServiceError,
    TerrainUnavailableError,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/terrain", tags=["terrain"])

# Keys holding absolute server paths, removed before anything is returned.
_PATH_KEYS: frozenset[str] = frozenset({"path", "filepath"})


def get_terrain_service() -> TerrainService:
    """
    Provide a TerrainService instance.

    Returns:
        A TerrainService using the default TerrainProvider.
    """
    return TerrainService()


def _strip_paths(data: dict[str, Any]) -> dict[str, Any]:
    """
    Remove absolute path entries from a service result.

    Dropping the keys is preferred over rewriting them: a partially redacted
    path still describes server layout, and nothing downstream needs it.

    Args:
        data: Dictionary returned by the service layer.

    Returns:
        A copy without path keys.
    """
    return {key: value for key, value in data.items() if key not in _PATH_KEYS}


def _handle_service_error(exc: TerrainServiceError) -> HTTPException:
    """
    Map a terrain service error onto an HTTP response.

    Args:
        exc: The service-level exception.

    Returns:
        The HTTPException to raise.
    """
    if isinstance(exc, TerrainUnavailableError):
        return HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(exc, InvalidTerrainRequestError):
        return HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    logger.error("Terrain service error: %s", exc)
    return HTTPException(
        status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(exc),
    )


@router.get(
    "",
    response_model=dict[str, list[str]],
    summary="List available terrain products",
)
def list_terrain(
    stage: Optional[TerrainStage] = Query(
        default=None,
        description="Restrict the listing to one lifecycle stage.",
    ),
    service: TerrainService = Depends(get_terrain_service),
) -> dict[str, list[str]]:
    """
    List terrain product filenames, grouped by lifecycle stage.

    A stage with no products yields an empty list rather than being omitted, so
    a client can distinguish an empty stage from an unknown one.

    Args:
        stage: Optional stage filter. All stages are listed when omitted.

    Returns:
        Mapping of stage value to the filenames available at that stage.

    Raises:
        HTTPException: 400 for an unrecognised stage, 500 if a stage directory
            cannot be read.
    """
    try:
        return service.list_terrain(stage)
    except TerrainServiceError as exc:
        raise _handle_service_error(exc) from exc


@router.get(
    "/info",
    response_model=dict[str, Any],
    summary="Inspect a terrain product",
)
def get_terrain_info(
    filename: str = Query(
        ...,
        min_length=1,
        description="Terrain filename, relative to its stage directory.",
    ),
    stage: TerrainStage = Query(
        default=TerrainStage.RAW,
        description="Lifecycle stage holding the product.",
    ),
    service: TerrainService = Depends(get_terrain_service),
) -> dict[str, Any]:
    """
    Return structural information about a terrain product.

    No pixel data is read. The server path is removed from the result; the
    product remains addressable by its filename and stage.

    Args:
        filename: Terrain filename.
        stage: Lifecycle stage. Defaults to raw.

    Returns:
        Raster information: driver, dimensions, band count, dtype, transform,
        bounds, resolution, nodata, CRS, plus the resolved stage and filename.

    Raises:
        HTTPException: 404 if the product does not exist, 400 for an invalid
            request, 500 if it cannot be inspected.
    """
    try:
        info = service.get_terrain_info(filename, stage)
    except TerrainServiceError as exc:
        raise _handle_service_error(exc) from exc

    return _strip_paths(info)


@router.post(
    "/verify",
    response_model=TerrainVerificationResponse,
    summary="Verify a terrain product",
)
def verify_terrain(
    filename: str = Query(
        ...,
        min_length=1,
        description="Terrain filename, relative to its stage directory.",
    ),
    stage: TerrainStage = Query(
        default=TerrainStage.RAW,
        description="Lifecycle stage holding the product.",
    ),
    expected_crs: Optional[int] = Query(
        default=None,
        description=(
            "Optional EPSG code the product is expected to use. Only checked "
            "when supplied; no CRS is assumed."
        ),
    ),
    validate_values: bool = Query(
        default=False,
        description=(
            "Read and scan pixel values. Off by default because scanning a "
            "city-scale raster is expensive; structural, CRS, resolution, and "
            "nodata checks run regardless."
        ),
    ),
    service: TerrainService = Depends(get_terrain_service),
) -> TerrainVerificationResponse:
    """
    Verify that a terrain product is acceptable for FlowSight processing.

    Every executed check is returned, including failures, so a client sees why
    a product was rejected rather than only that it was.

    Args:
        filename: Terrain filename.
        stage: Lifecycle stage. Defaults to raw.
        expected_crs: Optional expected EPSG code.
        validate_values: Whether to read pixel data.

    Returns:
        TerrainVerificationResponse describing the verdict and every check.

    Raises:
        HTTPException: 404 if the product does not exist, 400 for an invalid
            request, 500 if verification could not run.
    """
    try:
        report = service.verify_terrain(
            filename,
            stage=stage,
            expected_crs=expected_crs,
            validate_values=validate_values,
        )
    except TerrainServiceError as exc:
        raise _handle_service_error(exc) from exc

    checks = [
        TerrainCheckSchema(
            check=result.check,
            valid=result.valid,
            message=result.message,
            details=_strip_paths(result.details or {}) or None,
        )
        for result in report.checks
    ]

    # dataset_id carries the filename the caller supplied. The verification
    # report has no other identifier, and inventing one would misrepresent it.
    return TerrainVerificationResponse(
        dataset_id=filename,
        stage=stage,
        valid=report.valid,
        checks=checks,
        errors=list(report.errors),
        warnings=list(report.warnings),
        verified_at=datetime.now(timezone.utc),
    )