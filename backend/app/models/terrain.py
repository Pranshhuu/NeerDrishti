"""
Domain models for FlowSight terrain products (Phase 1)

This module defines the domain model layer describing WHAT a terrain product
is. Models carry metadata and references, never raster contents.

Boundaries:
- File I/O, raster reading, and validation belong to app.data
  (loader.py, validators.py, crs.py, terrain_provider.py).
- Terrain processing (reprojection, depression filling, slope, D8 flow
  direction and accumulation) belongs to the processing layer.
- These models perform no I/O, no network access, and hold no rasterio
  datasets or NumPy arrays.

Domain note:
    Copernicus GLO-30 is a Digital Surface Model (DSM), not a bare-earth DEM.
    It includes buildings, vegetation, and infrastructure, and at 30 m it is a
    regional geospatial foundation rather than street-level terrain. These
    models deliberately do not assume Copernicus: any terrain source (SRTM,
    municipal LiDAR, a future provider) can be described here.

Lifecycle:
    raw -> validated -> processed

Usage:
    from app.models.terrain import (
        TerrainProduct,
        TerrainStage,
        SpatialBounds,
        RasterDimensions,
        RasterResolution,
    )

    product = TerrainProduct(
        dataset_id="copernicus_glo30_utm43n",
        name="Copernicus GLO-30 DSM (UTM 43N)",
        source="Copernicus GLO-30",
        stage=TerrainStage.PROCESSED,
        dataset_type="terrain_dsm",
    )
    payload = product.model_dump_json()
"""

import math
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TerrainStage(str, Enum):
    """
    Lifecycle stage of a terrain product.

    A product only reaches a later stage because a validation or processing
    step placed it there; the stage is a record of what has happened, not a
    request for it to happen.
    """

    RAW = "raw"
    VALIDATED = "validated"
    PROCESSED = "processed"


class SpatialBounds(BaseModel):
    """
    Spatial extent of a terrain raster.

    Coordinates are expressed in the raster's own CRS and are NOT assumed to
    be latitude and longitude. For a geographic CRS they are degrees; for a
    projected CRS they are that projection's linear units.
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
    def _check_ordering(self) -> "SpatialBounds":
        """Ensure the minimum coordinates do not exceed the maximum coordinates."""
        if self.min_x > self.max_x:
            raise ValueError("min_x must be less than or equal to max_x")
        if self.min_y > self.max_y:
            raise ValueError("min_y must be less than or equal to max_y")
        return self

    @property
    def width(self) -> float:
        """Extent along the x axis, in CRS units."""
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        """Extent along the y axis, in CRS units."""
        return self.max_y - self.min_y


class RasterDimensions(BaseModel):
    """Pixel dimensions and band count of a terrain raster."""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0, description="Raster width in pixels (columns).")
    height: int = Field(gt=0, description="Raster height in pixels (rows).")
    band_count: int = Field(gt=0, description="Number of raster bands.")

    @property
    def pixel_count(self) -> int:
        """Total number of pixels in a single band."""
        return self.width * self.height


class RasterResolution(BaseModel):
    """
    Pixel size of a terrain raster.

    IMPORTANT: x and y are pixel sizes expressed in the raster's own CRS
    units. They are not assumed to be metres or degrees; the CRS determines
    the unit. A smaller pixel size means a finer spatial resolution.
    """

    model_config = ConfigDict(extra="forbid")

    x: float = Field(gt=0, description="Pixel width in the raster's CRS units.")
    y: float = Field(gt=0, description="Pixel height in the raster's CRS units.")

    @field_validator("x", "y")
    @classmethod
    def _must_be_finite(cls, value: float) -> float:
        """Reject NaN and infinity, which are not valid pixel sizes."""
        if not math.isfinite(value):
            raise ValueError("resolution values must be finite numbers")
        return value

    @property
    def is_square(self) -> bool:
        """Whether pixels are square within floating-point tolerance."""
        return math.isclose(self.x, self.y, rel_tol=1e-9)


class CRSReference(BaseModel):
    """
    Reference to a coordinate reference system.

    This is a description, not a live CRS object. Semantic CRS comparison and
    normalization belong to app.data.crs. At least one identifying field must
    be present so the reference is meaningful.
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
        description="WKT representation, when available.",
    )

    @model_validator(mode="after")
    def _require_identifier(self) -> "CRSReference":
        """Ensure the reference carries at least one identifying value."""
        if not any((self.epsg, self.authority, self.name, self.wkt)):
            raise ValueError(
                "CRSReference requires at least one of: epsg, authority, name, wkt"
            )
        return self


class TerrainProduct(BaseModel):
    """
    Description of a terrain product held by FlowSight.

    The model records what the product is, where it sits in the lifecycle, and
    what is known about its spatial characteristics. It never holds pixel data
    and performs no I/O: existence, readability, and data quality are the
    responsibility of app.data.

    Optional fields stay None when genuinely unknown rather than being filled
    with assumed values.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    # Identity
    dataset_id: str = Field(
        min_length=1,
        description="Stable identifier for this terrain product.",
    )
    name: str = Field(
        min_length=1,
        description="Human-readable product name.",
    )
    source: str = Field(
        min_length=1,
        description=(
            "Terrain source or provider, e.g. 'Copernicus GLO-30', 'SRTM', "
            "'Municipal LiDAR'. Free text so new sources need no code change."
        ),
    )
    dataset_type: str = Field(
        min_length=1,
        description=(
            "Category of product, e.g. 'terrain_dsm', 'slope', "
            "'flow_direction_d8', 'flow_accumulation_d8'."
        ),
    )
    stage: TerrainStage = Field(
        default=TerrainStage.RAW,
        description="Lifecycle stage this product currently occupies.",
    )

    # Location within the FlowSight data layout
    relative_path: Optional[str] = Field(
        default=None,
        description=(
            "Location of the product relative to its stage directory, e.g. "
            "'copernicus_dsm_utm43n.tif'. Deliberately relative: absolute "
            "paths are environment-specific and are resolved by the data layer."
        ),
    )
    file_format: Optional[str] = Field(
        default=None,
        description="File format label, e.g. 'GTiff'.",
    )

    # Spatial characteristics
    crs: Optional[CRSReference] = Field(
        default=None,
        description="Coordinate reference system of this product.",
    )
    dimensions: Optional[RasterDimensions] = Field(
        default=None,
        description="Pixel dimensions and band count.",
    )
    resolution: Optional[RasterResolution] = Field(
        default=None,
        description="Pixel size in the product's own CRS units.",
    )
    bounds: Optional[SpatialBounds] = Field(
        default=None,
        description="Spatial extent in the product's own CRS.",
    )
    vertical_reference: Optional[str] = Field(
        default=None,
        description=(
            "Vertical reference description when known, e.g. "
            "'EGM2008 ellipsoidal height'."
        ),
    )
    horizontal_reference: Optional[str] = Field(
        default=None,
        description="Horizontal reference description when known, e.g. 'WGS84-G1150'.",
    )
    nodata_value: Optional[float] = Field(
        default=None,
        description="Declared nodata value, when the product defines one.",
    )
    units: Optional[str] = Field(
        default=None,
        description="Units of the pixel values, e.g. 'meters', 'degrees'.",
    )

    # Provenance
    description: Optional[str] = Field(
        default=None,
        description="Optional free-text description of the product.",
    )
    source_url: Optional[str] = Field(
        default=None,
        description="Optional URL identifying the originating dataset.",
    )
    acquisition_date: Optional[date] = Field(
        default=None,
        description="Date the source data was acquired, when known. Never inferred.",
    )
    processing_date: Optional[datetime] = Field(
        default=None,
        description="Timestamp at which this product was produced, when known.",
    )
    parent_dataset_id: Optional[str] = Field(
        default=None,
        description="Identifier of the product this one was derived from.",
    )
    metadata_reference: Optional[str] = Field(
        default=None,
        description=(
            "Reference to the provenance record for this product, such as the "
            "relative path of its metadata JSON."
        ),
    )

    @field_validator("dataset_id", "name", "source", "dataset_type")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """Reject whitespace-only identifiers and names."""
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

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
        Ensure the reference is a relative location inside the data layout.

        Absolute paths are environment-specific and would tie a product's
        identity to one machine, so they are rejected here.
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

        return text

    @model_validator(mode="after")
    def _check_lineage(self) -> "TerrainProduct":
        """Ensure a product does not claim to be derived from itself."""
        if self.parent_dataset_id is not None:
            if self.parent_dataset_id == self.dataset_id:
                raise ValueError("parent_dataset_id must differ from dataset_id")
        return self

    @property
    def is_derived(self) -> bool:
        """Whether this product records a parent it was produced from."""
        return self.parent_dataset_id is not None

    def summary(self) -> dict[str, Any]:
        """
        Return a compact description suitable for listings and log messages.

        Returns:
            Dictionary of the fields most useful for identifying the product.
        """
        return {
            "dataset_id": self.dataset_id,
            "name": self.name,
            "source": self.source,
            "dataset_type": self.dataset_type,
            "stage": self.stage.value,
            "relative_path": self.relative_path,
            "epsg": self.crs.epsg if self.crs else None,
        }