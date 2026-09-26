"""
Shared API schemas for FlowSight (Phase 1)

This module defines cross-cutting request and response contracts used across
FlowSight endpoints. It is deliberately generic: nothing here knows about
terrain, rasters, or processing.

Boundaries:
- Internal domain concepts live in app.models (terrain.py, processing.py).
  Those are not API contracts and are not re-exported here.
- Terrain-specific API schemas belong in app.schemas.terrain.
- Processing-specific API schemas belong in app.schemas.processing.
- Business logic belongs in app.services; HTTP routing belongs in app.api.

Nothing in this module performs I/O, network access, or health checking. The
service and API layers populate these schemas; the schemas only describe shape.

Usage:
    from app.schemas.common import ErrorResponse, HealthResponse, StatusResponse

    response = HealthResponse(
        status=ServiceStatus.OK,
        service="flowsight-backend",
        version="1.0.0",
    )
"""

import math
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServiceStatus(str, Enum):
    """
    Coarse operational state reported by health and status endpoints.

    DEGRADED covers the case where the service is answering requests but some
    dependency is unavailable, which for FlowSight is a real state: the API
    runs without GDAL or WhiteboxTools present, but terrain processing cannot.
    """

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


def _reject_blank(value: str) -> str:
    """
    Reject empty and whitespace-only strings without altering valid values.

    Args:
        value: Candidate string.

    Returns:
        The value unchanged.

    Raises:
        ValueError: If the value contains only whitespace.
    """
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _check_json_safe(value: Any, path: str) -> None:
    """
    Verify recursively that a value can be serialized to strict JSON.

    Response payloads cross a network boundary, so a value that cannot be
    serialized would fail at response time rather than at construction. NaN and
    infinity are rejected because they are not valid JSON, and objects such as
    NumPy arrays, rasterio datasets, pyproj CRS objects, pathlib paths, file
    handles, and exceptions are rejected because they must never reach a client.

    Args:
        value: Value to inspect.
        path: Dotted path used in error messages.

    Raises:
        ValueError: If the value or any nested value is not JSON-safe.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite; NaN and Infinity are not JSON")
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"{path} keys must be strings, got {type(key).__name__}"
                )
            _check_json_safe(item, f"{path}.{key}")
        return

    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_json_safe(item, f"{path}[{index}]")
        return

    raise ValueError(
        f"{path} must be JSON-safe (str, int, float, bool, None, list, dict), "
        f"got {type(value).__name__}"
    )


def _validate_json_mapping(
    value: Optional[dict[str, Any]],
    field_name: str,
) -> Optional[dict[str, Any]]:
    """
    Validate that an optional mapping contains only JSON-safe values.

    Args:
        value: Mapping to validate, or None.
        field_name: Field name used in error messages.

    Returns:
        The mapping unchanged.

    Raises:
        ValueError: If any key or value is not JSON-safe.
    """
    if value is None:
        return None

    _check_json_safe(value, field_name)
    return value


class ErrorResponse(BaseModel):
    """
    Structured API error.

    The 'error' field is a short machine-readable code that clients can branch
    on; 'message' is for humans. 'details' carries additional context and is
    validated to be JSON-safe, so exception objects, tracebacks, and internal
    handles cannot leak through it.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(
        default=False,
        description="Always false for error responses.",
    )
    error: str = Field(
        min_length=1,
        description="Short machine-readable error code, e.g. 'terrain_not_found'.",
    )
    message: str = Field(
        min_length=1,
        description="Human-readable description of what went wrong.",
    )
    details: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional JSON-safe context about the error.",
    )

    @field_validator("error", "message")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("details")
    @classmethod
    def _details_json_safe(
        cls,
        value: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        return _validate_json_mapping(value, "details")


class HealthResponse(BaseModel):
    """
    Basic liveness response for the FlowSight backend.

    This schema describes the shape of the answer only. Whether the service is
    healthy is determined by the service layer, which populates these fields.
    """

    model_config = ConfigDict(extra="forbid")

    status: ServiceStatus = Field(
        description="Overall operational state of the service.",
    )
    service: str = Field(
        min_length=1,
        description="Name of the service reporting health.",
    )
    version: str = Field(
        min_length=1,
        description="Version of the running service.",
    )
    timestamp: Optional[datetime] = Field(
        default=None,
        description="When the health state was determined, if recorded.",
    )

    @field_validator("service", "version")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)


class StatusResponse(BaseModel):
    """
    System status with optional per-component detail.

    This is the richer counterpart to HealthResponse: use HealthResponse for a
    plain liveness check, and this when the caller needs to see which
    components are available. For FlowSight that distinction matters, since the
    API can be healthy while a processing dependency is missing.
    """

    model_config = ConfigDict(extra="forbid")

    status: ServiceStatus = Field(
        description="Overall operational state.",
    )
    message: str = Field(
        min_length=1,
        description="Human-readable summary of the current state.",
    )
    timestamp: Optional[datetime] = Field(
        default=None,
        description="When the status was determined, if recorded.",
    )
    components: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional JSON-safe per-component status, keyed by component name."
        ),
    )

    @field_validator("message")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("components")
    @classmethod
    def _components_json_safe(
        cls,
        value: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        return _validate_json_mapping(value, "components")


class MessageResponse(BaseModel):
    """
    Acknowledgement for operations whose only useful answer is an outcome.

    Endpoints that return a resource should return that resource's own schema
    rather than wrapping it here; this exists for the cases where there is
    nothing to return but success and an explanation.
    """

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(
        default=True,
        description="Whether the operation succeeded.",
    )
    message: str = Field(
        min_length=1,
        description="Human-readable description of the outcome.",
    )

    @field_validator("message")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)