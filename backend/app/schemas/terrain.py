"""
API schemas for FlowSight terrain endpoints (Phase 1)

This module defines the request and response contracts for terrain endpoints.
It describes what clients send and receive, independently of how terrain is
stored or processed internally.

Boundaries:
- Internal domain models live in app.models.terrain. Those describe FlowSight's
  own concepts and may change without changing this API contract. Only the
  TerrainStage enum is shared, since its three values are the public vocabulary
  of the terrain lifecycle and duplicating them would let the two drift apart.
- Raster access and validation belong to app.data (loader.py, validators.py,
  crs.py, terrain_provider.py).
- Terrain processing belongs to the processing layer; processing requests and
  results are contracted in app.schemas.processing, not here.

Nothing in this module performs raster I/O, filesystem access, network access,
or processing. Paths are validated as API values only and are never resolved.

Domain note:
    Copernicus GLO-30 is a Digital Surface Model (DSM), not a bare-earth DEM,
    and these schemas do not assume any particular source. Any terrain provider
    can be described through them.

Usage:
    from app.schemas.terrain import TerrainResponse, TerrainListResponse

    response = TerrainResponse(
        dataset_id="copernicus_glo30_utm43n",
        name="Copernicus GLO-30 DSM (UTM 43N)",
        source="Copernicus GLO-30",
        dataset_type="terrain_dsm",
        stage=TerrainStage.PROCESSED,
    )
"""

import math
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.terrain import TerrainStage

__all__ = [
    "TerrainStage",
    "CRSSchema",
    "BoundsSchema",
    "DimensionsSchema",
    "ResolutionSchema",
    "TerrainSummary",
    "TerrainResponse",
    "TerrainListResponse",
    "TerrainQuery",
    "TerrainCheckSchema",
    "TerrainVerificationResponse",
]


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


def _reject_blank_optional(value: Optional[str]) -> Optional[str]:
    """Apply blank rejection to an optional string, leaving None untouched."""
    if value is not None:
        _reject_blank(value)
    return value


def _check_json_safe(value: Any, path: str) -> None:
    """
    Verify recursively that a value can be serialized to strict JSON.

    Response payloads cross a network boundary, so non-serializable values must
    be rejected at construction rather than at response time. This also keeps
    internal objects — NumPy arrays, rasterio datasets, pyproj CRS objects,
    pathlib paths, exceptions, file handles — out of API output.

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


class CRSSchema(BaseModel):
    """
    Coordinate reference system as exposed to API clients.

    This is a plain description, never a live CRS object. WKT is optional
    because an EPSG code or authority string identifies the CRS for most
    clients; WKT is included only when a client genuinely needs the full
    definition.
    """

    model_config = ConfigDict(extra="forbid")

    epsg: Optional[int] = Field(
        default=None,
        description="EPSG code, when the CRS has one.",
    )
    authority: Optional[str] = Field(
        default=None,
        description="Authority identifier, e.g. 'EPSG:32643'.",
    )
    name: Optional[str] = Field(
        default=None,
        description="Human-readable CRS name.",
    )
    wkt: Optional[str] = Field(
        default=None,
        description="Full WKT definition. Omitted unless explicitly needed.",
    )

    @field_validator("authority", "name", "wkt")
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _reject_blank_optional(value)

    @model_validator(mode="after")
    def _require_identifier(self) -> "CRSSchema":
        """Ensure the CRS carries at least one identifying value."""
        if not any((self.epsg, self.authority, self.name, self.wkt)):
            raise ValueError(
                "CRS requires at least one of: epsg, authority, name, wkt"
            )
        return self


class BoundsSchema(BaseModel):
    """
    Spatial extent of a terrain dataset.

    Coordinates are in the dataset's own CRS and are NOT assumed to be
    latitude and longitude: for a projected CRS they are that projection's
    linear units.
    """

    model_config = ConfigDict(extra="forbid")

    min_x: float = Field(description="Minimum x coordinate (left edge), in CRS units.")
    min_y: float = Field(description="Minimum y coordinate (bottom edge), in CRS units.")
    max_x: float = Field(description="Maximum x coordinate (right edge), in CRS units.")
    max_y: float = Field(description="Maximum y coordinate (top edge), in CRS units.")

    @field_validator("min_x", "min_y", "max_x", "max_y")
    @classmethod
    def _must_be_finite(cls, value: float) -> float:
        """Reject NaN and infinity, which are not valid coordinates."""
        if not math.isfinite(value):
            raise ValueError("bounds coordinates must be finite numbers")
        return value

    @model_validator(mode="after")
    def _check_ordering(self) -> "BoundsSchema":
        """Ensure minimum coordinates do not exceed maximum coordinates."""
        if self.min_x > self.max_x:
            raise ValueError("min_x must be less than or equal to max_x")
        if self.min_y > self.max_y:
            raise ValueError("min_y must be less than or equal to max_y")
        return self


class DimensionsSchema(BaseModel):
    """Pixel dimensions and band count of a terrain dataset."""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0, description="Width in pixels (columns).")
    height: int = Field(gt=0, description="Height in pixels (rows).")
    band_count: int = Field(gt=0, description="Number of raster bands.")


class ResolutionSchema(BaseModel):
    """
    Pixel size of a terrain dataset.

    IMPORTANT: x and y are pixel sizes in the dataset's own CRS units, not
    necessarily metres or degrees. A smaller value means a finer resolution.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(gt=0, description="Pixel width in the dataset's CRS units.")
    y: float = Field(gt=0, description="Pixel height in the dataset's CRS units.")

    @field_validator("x", "y")
    @classmethod
    def _must_be_finite(cls, value: float) -> float:
        """Reject NaN and infinity, which are not valid pixel sizes."""
        if not math.isfinite(value):
            raise ValueError("resolution values must be finite numbers")
        return value


class TerrainSummary(BaseModel):
    """
    Compact terrain dataset description for listings.

    Carries only what a client needs to identify a dataset and decide whether
    to request its full description.
    """

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(
        min_length=1,
        description="Stable identifier for this terrain dataset.",
    )
    name: str = Field(
        min_length=1,
        description="Human-readable dataset name.",
    )
    source: str = Field(
        min_length=1,
        description="Terrain source or provider, e.g. 'Copernicus GLO-30'.",
    )
    dataset_type: str = Field(
        min_length=1,
        description=(
            "Category of dataset, e.g. 'terrain_dsm', 'slope', "
            "'flow_direction_d8', 'flow_accumulation_d8'."
        ),
    )
    stage: TerrainStage = Field(
        description="Lifecycle stage: raw, validated, or processed.",
    )

    @field_validator("dataset_id", "name", "source", "dataset_type")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)


class TerrainResponse(TerrainSummary):
    """
    Full terrain dataset description returned by the terrain API.

    Optional fields stay absent when genuinely unknown rather than being filled
    with assumed values, so a client can distinguish "not recorded" from a real
    measurement.
    """

    model_config = ConfigDict(extra="forbid")

    relative_path: Optional[str] = Field(
        default=None,
        description=(
            "Location of the dataset relative to the managed data root, e.g. "
            "'copernicus_dsm_utm43n.tif'. Always relative: absolute server "
            "paths are never exposed through the API."
        ),
    )
    file_format: Optional[str] = Field(
        default=None,
        description="File format label, e.g. 'GTiff'.",
    )

    crs: Optional[CRSSchema] = Field(
        default=None,
        description="Coordinate reference system of the dataset.",
    )
    dimensions: Optional[DimensionsSchema] = Field(
        default=None,
        description="Pixel dimensions and band count.",
    )
    resolution: Optional[ResolutionSchema] = Field(
        default=None,
        description="Pixel size in the dataset's own CRS units.",
    )
    bounds: Optional[BoundsSchema] = Field(
        default=None,
        description="Spatial extent in the dataset's own CRS.",
    )

    vertical_reference: Optional[str] = Field(
        default=None,
        description="Vertical reference, e.g. 'EGM2008 ellipsoidal height'.",
    )
    horizontal_reference: Optional[str] = Field(
        default=None,
        description="Horizontal reference, e.g. 'WGS84-G1150'.",
    )
    nodata_value: Optional[float] = Field(
        default=None,
        description="Declared nodata value, when the dataset defines one.",
    )
    units: Optional[str] = Field(
        default=None,
        description="Units of the pixel values, e.g. 'meters', 'degrees'.",
    )

    description: Optional[str] = Field(
        default=None,
        description="Free-text description of the dataset.",
    )
    source_url: Optional[str] = Field(
        default=None,
        description=(
            "URL identifying the originating dataset. Recorded as given and "
            "never fetched or verified by the API."
        ),
    )
    acquisition_date: Optional[date] = Field(
        default=None,
        description="Date the source data was acquired, when known.",
    )
    processing_date: Optional[datetime] = Field(
        default=None,
        description="When this dataset was produced, when known.",
    )
    parent_dataset_id: Optional[str] = Field(
        default=None,
        description="Identifier of the dataset this one was derived from.",
    )
    metadata_reference: Optional[str] = Field(
        default=None,
        description=(
            "Reference to the provenance record, such as the relative path of "
            "the dataset's metadata document."
        ),
    )

    @field_validator(
        "file_format",
        "vertical_reference",
        "horizontal_reference",
        "units",
        "description",
        "source_url",
        "parent_dataset_id",
        "metadata_reference",
    )
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _reject_blank_optional(value)

    @field_validator("nodata_value")
    @classmethod
    def _nodata_must_be_finite(cls, value: Optional[float]) -> Optional[float]:
        """Reject NaN and infinity as declared nodata values."""
        if value is not None and not math.isfinite(value):
            raise ValueError("nodata_value must be a finite number")
        return value

    @field_validator("relative_path")
    @classmethod
    def _path_must_be_relative(cls, value: Optional[str]) -> Optional[str]:
        """
        Ensure the exposed path stays inside the managed data root.

        Only the API value is checked: no filesystem access or path resolution
        happens here. Absolute paths and parent traversal are rejected so that
        server layout is never leaked and no client can address arbitrary
        locations.
        """
        if value is None:
            return None

        text = value.strip()
        if not text:
            raise ValueError("relative_path must not be blank")

        if text.startswith("/") or text.startswith("\\"):
            raise ValueError("relative_path must be relative, not absolute")

        # Windows drive-letter form, e.g. "C:\\data\\dem.tif".
        if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
            raise ValueError("relative_path must be relative, not absolute")

        if ".." in text.replace("\\", "/").split("/"):
            raise ValueError("relative_path must not traverse parent directories")

        return value


class TerrainListResponse(BaseModel):
    """
    Collection of terrain datasets.

    No pagination: Phase 1 exposes a small, bounded set of terrain products,
    and pagination would add a contract clients must handle for no benefit.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[TerrainSummary] = Field(
        default_factory=list,
        description="Terrain datasets matching the request.",
    )
    count: int = Field(
        ge=0,
        description="Number of datasets in 'items'.",
    )

    @model_validator(mode="after")
    def _count_matches_items(self) -> "TerrainListResponse":
        """Ensure the reported count matches the payload actually returned."""
        if self.count != len(self.items):
            raise ValueError(
                f"count ({self.count}) must equal the number of items "
                f"({len(self.items)})"
            )
        return self


class TerrainQuery(BaseModel):
    """
    Filters for listing terrain datasets.

    Every field is optional; omitting all of them requests everything
    available. Filtering is limited to the attributes clients actually select
    on in Phase 1.
    """

    model_config = ConfigDict(extra="forbid")

    dataset_id: Optional[str] = Field(
        default=None,
        description="Restrict results to a specific dataset identifier.",
    )
    stage: Optional[TerrainStage] = Field(
        default=None,
        description="Restrict results to one lifecycle stage.",
    )
    source: Optional[str] = Field(
        default=None,
        description="Restrict results to one terrain source or provider.",
    )
    dataset_type: Optional[str] = Field(
        default=None,
        description="Restrict results to one dataset category.",
    )

    @field_validator("dataset_id", "source", "dataset_type")
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _reject_blank_optional(value)


class TerrainCheckSchema(BaseModel):
    """
    Outcome of one verification check, as exposed to API clients.

    This mirrors the shape of an internal validation result without importing
    it, so the internal validator can change its structure without breaking
    this contract.
    """

    model_config = ConfigDict(extra="forbid")

    check: str = Field(
        min_length=1,
        description="Name of the check, e.g. 'structure', 'crs', 'resolution'.",
    )
    valid: bool = Field(
        description="Whether this check passed.",
    )
    message: str = Field(
        min_length=1,
        description="Human-readable summary of the outcome.",
    )
    details: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional JSON-safe supporting information.",
    )

    @field_validator("check", "message")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("details")
    @classmethod
    def _details_json_safe(
        cls,
        value: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if value is None:
            return None
        _check_json_safe(value, "details")
        return value


class TerrainVerificationResponse(BaseModel):
    """
    Result of verifying a terrain dataset.

    This reports an outcome that has already been determined; requesting it
    does not itself run validation. Failures are always surfaced rather than
    summarized away.
    """

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(
        min_length=1,
        description="Identifier of the verified dataset.",
    )
    stage: Optional[TerrainStage] = Field(
        default=None,
        description="Lifecycle stage the dataset was verified at.",
    )
    valid: bool = Field(
        description="True only if every executed check passed.",
    )
    checks: list[TerrainCheckSchema] = Field(
        default_factory=list,
        description="Individual check outcomes, in execution order.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Messages from checks that failed.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal observations raised during verification.",
    )
    verified_at: Optional[datetime] = Field(
        default=None,
        description="When verification was performed, if recorded.",
    )

    @field_validator("dataset_id")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> "TerrainVerificationResponse":
        """
        Ensure the reported verdict matches the recorded checks.

        A response cannot claim validity while also listing errors or failed
        checks, since that would leave the client unable to trust either field.
        """
        if self.valid:
            if self.errors:
                raise ValueError(
                    "a valid verification result must not list errors"
                )

            failed = [check.check for check in self.checks if not check.valid]
            if failed:
                raise ValueError(
                    f"a valid verification result must not contain failed "
                    f"checks: {', '.join(failed)}"
                )

        return self