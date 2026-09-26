"""
System health and status endpoints for FlowSight (Phase 1)

Exposes SystemService over HTTP. Environment detection lives in
app.core.environment and is reached only through SystemService; nothing is
re-checked here, and no geospatial library is touched.

Endpoints:
    GET /system/health  - compact liveness answer, suitable for polling
    GET /system/status  - detailed state including per-component availability

The two differ in cost and detail, not in what they measure. Health answers
"is the service up"; status answers "which dependencies are present". Both
matter for FlowSight, because the API serves requests whether or not GDAL and
WhiteboxTools are installed, while terrain processing does not.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status

from app.core.logging import get_logger
from app.schemas.common import HealthResponse, ServiceStatus, StatusResponse
from app.services.system_service import SystemService, SystemServiceError

logger = get_logger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


def get_system_service() -> SystemService:
    """
    Provide a SystemService instance.

    Construction performs no environment inspection and no I/O, so building one
    per request is cheap and keeps the dependency overridable in tests.

    Returns:
        A SystemService instance.
    """
    return SystemService()


def _as_service_status(value: Any) -> ServiceStatus:
    """
    Convert a status string from the service into the API's status enum.

    An unrecognised value is reported as unavailable rather than passed
    through: a status the API cannot interpret is not evidence that the system
    is healthy, and StatusResponse would reject the raw value anyway.

    Args:
        value: Status value reported by SystemService.

    Returns:
        The matching ServiceStatus member.
    """
    try:
        return ServiceStatus(value)
    except (ValueError, TypeError):
        logger.error("SystemService reported an unrecognised status: %r", value)
        return ServiceStatus.UNAVAILABLE


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service liveness",
)
def get_health(
    service: SystemService = Depends(get_system_service),
) -> HealthResponse:
    """
    Report whether the backend is running and whether its toolchain is present.

    Returns:
        HealthResponse describing the service and its overall state.

    Raises:
        HTTPException: 503 if the environment check could not be performed.
    """
    try:
        health = service.get_health()
    except SystemServiceError as exc:
        logger.error("Health check failed: %s", exc)
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return HealthResponse(
        status=_as_service_status(health["status"]),
        service=health["service"],
        version=health["version"],
        timestamp=datetime.now(timezone.utc),
    )


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Detailed system status",
)
def get_status(
    service: SystemService = Depends(get_system_service),
) -> StatusResponse:
    """
    Report system status with per-component availability.

    Unlike /health, this shows which geospatial tools are present, so an
    operator can see what is blocking terrain processing before attempting a
    run.

    Returns:
        StatusResponse describing the service state and its components.

    Raises:
        HTTPException: 503 if the environment check could not be performed.
    """
    try:
        report = service.get_status()
    except SystemServiceError as exc:
        logger.error("Status check failed: %s", exc)
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    environment = report.get("environment") or {}

    # StatusResponse carries free-text state in 'message'. The service supplies
    # one only when the environment is incomplete, so that explanation is
    # preferred over a generic sentence whenever it exists.
    message = environment.get("message") or (
        f"{report.get('service_full_name') or report.get('service')} is operational."
    )

    # StatusResponse has no dedicated environment field, so the environment
    # summary is carried alongside the per-tool components rather than dropped.
    components = dict(report.get("components") or {})
    components["environment"] = environment

    return StatusResponse(
        status=_as_service_status(report["status"]),
        message=message,
        timestamp=datetime.now(timezone.utc),
        components=components,
    )