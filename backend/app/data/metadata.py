"""
Dataset metadata and provenance utilities for FlowSight Phase 1

Provides a structured, JSON-serializable representation of geospatial dataset
metadata used for inventory, provenance tracking, and reproducible processing.

This module:
- Describes datasets (raw, validated, processed, derived)
- Records an ordered list of processing steps
- Serializes to/from JSON-compatible dictionaries
- Reuses app.data.crs.CRSHandler to inspect rasters

This module does NOT:
- Read, write, or process raster pixel data
- Perform reprojection or terrain derivation
- Validate raster values (see validators.py)
- Contain CRS definitions (see data/CRS_DEFINITIONS.yaml)
- Access external APIs, databases, or caches

Usage:
    from app.data.metadata import DatasetMetadata, MetadataError

    meta = DatasetMetadata.from_raster(
        filepath="data/raw/terrain/dem.tif",
        dataset_id="copernicus_glo30_raw",
        dataset_name="Copernicus GLO-30 DSM (raw)",
        source="Copernicus GLO-30",
        dataset_type="terrain_dsm",
        processing_status="raw",
    )
    print(meta.to_json())
"""

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.data.crs import CRSHandler, CRSError

logger = get_logger(__name__)


# Allowed processing statuses.
# These describe the position of a dataset in the FlowSight data lifecycle.
# They are deliberately few and are not a state machine.
VALID_PROCESSING_STATUSES: frozenset[str] = frozenset(
    {
        "raw",
        "validated",
        "processed",
        "derived",
        "failed",
    }
)

# Current metadata schema version. Schema migration is not implemented.
METADATA_VERSION: str = "1.0"

# Keys required in a bounds dictionary.
REQUIRED_BOUNDS_KEYS: tuple[str, ...] = ("min_x", "max_x", "min_y", "max_y")


class MetadataError(Exception):
    """
    Raised when dataset metadata cannot be created, validated, or serialized.

    This includes malformed input dictionaries, invalid field values,
    non-serializable content, and failures while inspecting a raster.
    """

    pass


def _utc_now_iso() -> str:
    """
    Return the current UTC time as a timezone-aware ISO-8601 string.

    Returns:
        ISO-8601 timestamp, e.g. "2026-09-05T23:34:07.123456+00:00"
    """
    return datetime.now(timezone.utc).isoformat()


def _crs_to_json_safe(crs_value: Any) -> Optional[Dict[str, Any]]:
    """
    Convert a CRS representation into a JSON-safe dictionary.

    Accepts either a dictionary produced by CRSHandler.get_raster_crs()
    (which may contain a live pyproj.CRS object) or a CRS object directly.

    Only fields that are actually available are included. No CRS information
    is invented.

    Args:
        crs_value: A dictionary, a pyproj.CRS-like object, or None.

    Returns:
        JSON-safe dictionary with any of 'epsg', 'authority', 'name', 'wkt',
        'proj4', or None if no CRS information is available.

    Raises:
        MetadataError: If the supplied value cannot be interpreted as a CRS.
    """
    if crs_value is None:
        return None

    # Case 1: already a dictionary (possibly from CRSHandler.get_raster_crs()).
    if isinstance(crs_value, dict):
        safe: Dict[str, Any] = {}

        # Copy through the plain scalar fields when present.
        for key in ("epsg", "authority", "name", "wkt", "proj4"):
            value = crs_value.get(key)
            if value is not None:
                safe[key] = value

        # If a live CRS object is embedded, extract additional safe details
        # from it rather than serializing the object itself.
        crs_object = crs_value.get("pyproj_crs")
        if crs_object is not None:
            derived = _crs_object_to_json_safe(crs_object)
            for key, value in derived.items():
                safe.setdefault(key, value)

        return safe if safe else None

    # Case 2: a CRS object (pyproj.CRS or rasterio CRS).
    if hasattr(crs_value, "to_wkt"):
        derived = _crs_object_to_json_safe(crs_value)
        return derived if derived else None

    raise MetadataError(
        f"Unsupported CRS representation: {type(crs_value).__name__}. "
        f"Provide a dictionary or a CRS object."
    )


def _crs_object_to_json_safe(crs_object: Any) -> Dict[str, Any]:
    """
    Extract JSON-safe fields from a CRS object.

    Args:
        crs_object: A pyproj.CRS or rasterio CRS object.

    Returns:
        Dictionary containing only the fields that could be extracted.

    Raises:
        MetadataError: If the object exposes no usable CRS information.
    """
    safe: Dict[str, Any] = {}

    to_epsg = getattr(crs_object, "to_epsg", None)
    if callable(to_epsg):
        try:
            epsg = to_epsg()
        except (ValueError, TypeError, AttributeError):
            epsg = None
        if epsg is not None:
            safe["epsg"] = int(epsg)

    name = getattr(crs_object, "name", None)
    if isinstance(name, str) and name:
        safe["name"] = name

    to_authority = getattr(crs_object, "to_authority", None)
    if callable(to_authority):
        try:
            authority = to_authority()
        except (ValueError, TypeError, AttributeError):
            authority = None
        if authority:
            # pyproj returns a (authority_name, code) tuple.
            safe["authority"] = ":".join(str(part) for part in authority)

    to_wkt = getattr(crs_object, "to_wkt", None)
    if callable(to_wkt):
        try:
            wkt = to_wkt()
        except (ValueError, TypeError, AttributeError) as exc:
            raise MetadataError(f"Cannot extract WKT from CRS object: {exc}")
        if isinstance(wkt, str) and wkt:
            safe["wkt"] = wkt

    if not safe:
        raise MetadataError(
            "CRS object exposed no usable coordinate reference information."
        )

    return safe


@dataclass
class DatasetMetadata:
    """
    Structured metadata and provenance record for a FlowSight dataset.

    Instances are validated on construction. All optional fields default to
    None (or an empty list for processing_steps) and are never populated with
    invented values.

    Required fields:
        dataset_id: Stable identifier for the dataset.
        dataset_name: Human-readable dataset name.
        source: Originating dataset or provider (e.g. "Copernicus GLO-30").
        dataset_type: Category of dataset (e.g. "terrain_dsm", "slope").

    Notes:
        - processing_date defaults to the current UTC time when omitted.
        - acquisition_date is never inferred; supply it only if known.
        - crs is stored as a JSON-safe dictionary, never a live CRS object.
        - Serialization is strict: NaN and Infinity are rejected, not emitted.
    """

    # Required identity fields
    dataset_id: str
    dataset_name: str
    source: str
    dataset_type: str

    # Provenance
    source_url: Optional[str] = None
    description: Optional[str] = None
    acquisition_date: Optional[str] = None
    processing_date: Optional[str] = None
    processing_status: str = "raw"
    parent_dataset_id: Optional[str] = None

    # Spatial reference and geometry
    crs: Optional[Dict[str, Any]] = None
    resolution: Optional[Tuple[float, float]] = None
    dimensions: Optional[Dict[str, int]] = None
    bounds: Optional[Dict[str, float]] = None
    vertical_reference: Optional[str] = None
    horizontal_reference: Optional[str] = None

    # File description
    file_path: Optional[str] = None
    file_format: Optional[str] = None
    nodata_value: Optional[float] = None
    units: Optional[str] = None

    # Processing history
    processing_steps: List[Dict[str, Any]] = field(default_factory=list)

    # Schema
    metadata_version: str = METADATA_VERSION

    def __post_init__(self) -> None:
        """
        Normalize and validate the metadata record after construction.

        Raises:
            MetadataError: If any field fails validation.
        """
        if self.processing_date is None:
            self.processing_date = _utc_now_iso()

        # Normalize CRS into a JSON-safe dictionary.
        self.crs = _crs_to_json_safe(self.crs)

        # Normalize resolution into a tuple of floats.
        if self.resolution is not None:
            self.resolution = self._normalize_resolution(self.resolution)

        self._validate()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        """
        Run lightweight structural validation on this metadata record.

        This does not inspect raster pixel values; that responsibility
        belongs to validators.py.

        Raises:
            MetadataError: If any field fails validation.
        """
        self._validate_required_strings()
        self._validate_status()
        self._validate_dimensions()
        self._validate_resolution()
        self._validate_bounds()
        self._validate_nodata_value()
        self._validate_processing_steps()

    def _validate_required_strings(self) -> None:
        """Ensure required identity fields are non-empty strings."""
        required = {
            "dataset_id": self.dataset_id,
            "dataset_name": self.dataset_name,
            "source": self.source,
            "dataset_type": self.dataset_type,
            "metadata_version": self.metadata_version,
        }
        for name, value in required.items():
            if not isinstance(value, str) or not value.strip():
                raise MetadataError(f"'{name}' must be a non-empty string.")

    def _validate_status(self) -> None:
        """Ensure processing_status is one of the allowed values."""
        if self.processing_status not in VALID_PROCESSING_STATUSES:
            allowed = ", ".join(sorted(VALID_PROCESSING_STATUSES))
            raise MetadataError(
                f"Invalid processing_status '{self.processing_status}'. "
                f"Allowed values: {allowed}"
            )

    def _validate_dimensions(self) -> None:
        """Ensure dimensions, if present, contain positive width and height."""
        if self.dimensions is None:
            return

        if not isinstance(self.dimensions, dict):
            raise MetadataError("'dimensions' must be a dictionary or None.")

        for key in ("width", "height"):
            if key not in self.dimensions:
                raise MetadataError(f"'dimensions' is missing required key '{key}'.")

            value = self.dimensions[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise MetadataError(f"dimensions['{key}'] must be an integer.")
            if value <= 0:
                raise MetadataError(f"dimensions['{key}'] must be positive, got {value}.")

    def _validate_resolution(self) -> None:
        """
        Ensure resolution, if present, contains finite positive numeric values.

        Rejects NaN and positive/negative infinity, which are not meaningful
        pixel sizes and are not representable in strict JSON.
        """
        if self.resolution is None:
            return

        for index, value in enumerate(self.resolution):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MetadataError(
                    f"resolution[{index}] must be numeric, got {type(value).__name__}."
                )
            if not math.isfinite(value):
                raise MetadataError(
                    f"resolution[{index}] must be finite, got {value!r}."
                )
            if value <= 0:
                raise MetadataError(
                    f"'resolution' values must be positive, got {self.resolution}."
                )

    def _validate_bounds(self) -> None:
        """
        Ensure bounds, if present, are structurally valid.

        All four coordinates must be finite numbers (no NaN, no infinity),
        and the minimum values must not exceed the maximum values.
        """
        if self.bounds is None:
            return

        if not isinstance(self.bounds, dict):
            raise MetadataError("'bounds' must be a dictionary or None.")

        for key in REQUIRED_BOUNDS_KEYS:
            if key not in self.bounds:
                raise MetadataError(f"'bounds' is missing required key '{key}'.")

            value = self.bounds[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MetadataError(f"bounds['{key}'] must be numeric.")
            if not math.isfinite(value):
                raise MetadataError(
                    f"bounds['{key}'] must be finite, got {value!r}."
                )

        if self.bounds["min_x"] > self.bounds["max_x"]:
            raise MetadataError("bounds['min_x'] must be <= bounds['max_x'].")
        if self.bounds["min_y"] > self.bounds["max_y"]:
            raise MetadataError("bounds['min_y'] must be <= bounds['max_y'].")

    def _validate_nodata_value(self) -> None:
        """
        Ensure nodata_value, if present, is a finite number.

        NaN and infinity are rejected: they are not representable in strict
        JSON and cannot be compared reliably during downstream validation.
        """
        if self.nodata_value is None:
            return

        if isinstance(self.nodata_value, bool) or not isinstance(
            self.nodata_value, (int, float)
        ):
            raise MetadataError(
                f"'nodata_value' must be numeric, "
                f"got {type(self.nodata_value).__name__}."
            )

        if not math.isfinite(self.nodata_value):
            raise MetadataError(
                f"'nodata_value' must be finite, got {self.nodata_value!r}."
            )

    def _validate_processing_steps(self) -> None:
        """
        Ensure processing_steps is an ordered list of JSON-serializable dictionaries.

        Steps supplied directly at construction time are checked here so that
        non-serializable values are rejected immediately rather than at write time.
        Serialization is strict: NaN and Infinity are rejected.
        Valid steps are left unmodified.
        """
        if not isinstance(self.processing_steps, list):
            raise MetadataError("'processing_steps' must be a list.")

        for index, step in enumerate(self.processing_steps):
            if not isinstance(step, dict):
                raise MetadataError(
                    f"processing_steps[{index}] must be a dictionary, "
                    f"got {type(step).__name__}."
                )

            try:
                json.dumps(step, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise MetadataError(
                    f"processing_steps[{index}] is not JSON-serializable: {exc}"
                )

    @staticmethod
    def _normalize_resolution(value: Any) -> Tuple[float, float]:
        """
        Normalize a resolution value into a tuple of two floats.

        Args:
            value: A sequence of two numeric values.

        Returns:
            Tuple of (pixel_width, pixel_height).

        Raises:
            MetadataError: If the value cannot be normalized.
        """
        if not isinstance(value, (list, tuple)):
            raise MetadataError(
                f"'resolution' must be a sequence of two numbers, "
                f"got {type(value).__name__}."
            )
        if len(value) != 2:
            raise MetadataError(
                f"'resolution' must contain exactly two values, got {len(value)}."
            )

        normalized: List[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise MetadataError("'resolution' values must be numeric.")
            normalized.append(float(item))

        return (normalized[0], normalized[1])

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert this metadata record into a JSON-serializable dictionary.

        The returned dictionary never contains live CRS objects or any other
        non-serializable Python object. Tuples are converted to lists.
        Serialization is strict: NaN and Infinity are rejected rather than
        emitted as non-standard JSON literals.

        Returns:
            JSON-compatible dictionary.

        Raises:
            MetadataError: If the record cannot be serialized.
        """
        data = asdict(self)

        # asdict() preserves the tuple type; JSON requires a list.
        if self.resolution is not None:
            data["resolution"] = [self.resolution[0], self.resolution[1]]

        # Defensive: guarantee the CRS block is JSON-safe.
        data["crs"] = _crs_to_json_safe(self.crs)

        # Fail loudly rather than emit metadata that cannot be written to disk.
        try:
            json.dumps(data, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise MetadataError(f"Metadata is not JSON-serializable: {exc}")

        return data

    def to_json(self, indent: int = 2) -> str:
        """
        Serialize this metadata record to a JSON string.

        Serialization is strict: NaN and Infinity are rejected rather than
        emitted as non-standard JSON literals.

        Args:
            indent: Indentation level for readability. Use None for compact output.

        Returns:
            JSON string.

        Raises:
            MetadataError: If the record cannot be serialized.
        """
        try:
            return json.dumps(
                self.to_dict(),
                indent=indent,
                sort_keys=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise MetadataError(f"Cannot serialize metadata to JSON: {exc}")

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatasetMetadata":
        """
        Reconstruct a DatasetMetadata record from a dictionary.

        The file referenced by 'file_path' does not need to exist.

        Args:
            data: Dictionary previously produced by to_dict(), or an
                equivalent structure.

        Returns:
            Validated DatasetMetadata instance.

        Raises:
            MetadataError: If the dictionary is malformed or fails validation.
        """
        if not isinstance(data, dict):
            raise MetadataError(
                f"Metadata input must be a dictionary, got {type(data).__name__}."
            )

        required_fields = ("dataset_id", "dataset_name", "source", "dataset_type")
        missing = [name for name in required_fields if name not in data]
        if missing:
            raise MetadataError(
                f"Metadata is missing required field(s): {', '.join(missing)}"
            )

        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(data) - known_fields
        if unknown:
            raise MetadataError(
                f"Metadata contains unknown field(s): {', '.join(sorted(unknown))}"
            )

        kwargs = {name: data[name] for name in known_fields if name in data}

        try:
            return cls(**kwargs)
        except MetadataError:
            raise
        except TypeError as exc:
            raise MetadataError(f"Cannot construct metadata from dictionary: {exc}")

    @classmethod
    def from_raster(
        cls,
        filepath: str,
        dataset_id: str,
        dataset_name: str,
        source: str,
        dataset_type: str,
        processing_status: str = "raw",
        source_url: Optional[str] = None,
        description: Optional[str] = None,
        acquisition_date: Optional[str] = None,
        processing_date: Optional[str] = None,
        file_format: Optional[str] = None,
        vertical_reference: Optional[str] = None,
        horizontal_reference: Optional[str] = None,
        nodata_value: Optional[float] = None,
        units: Optional[str] = None,
        processing_steps: Optional[List[Dict[str, Any]]] = None,
        parent_dataset_id: Optional[str] = None,
    ) -> "DatasetMetadata":
        """
        Build a metadata record by inspecting an existing raster file.

        Spatial fields (crs, resolution, dimensions, bounds) are read from the
        file using CRSHandler. No assumptions are made about resolution, CRS,
        or extent, and no reprojection is performed.

        Args:
            filepath: Path to the raster file to inspect.
            dataset_id: Stable identifier for the dataset.
            dataset_name: Human-readable dataset name.
            source: Originating dataset or provider.
            dataset_type: Category of dataset.
            processing_status: Lifecycle status; defaults to "raw".
            source_url: Optional source URL.
            description: Optional description.
            acquisition_date: Optional ISO-8601 acquisition date. Never inferred.
            processing_date: Optional ISO-8601 timestamp; defaults to UTC now.
            file_format: Optional format label (e.g. "GTiff").
            vertical_reference: Optional vertical reference description.
            horizontal_reference: Optional horizontal reference description.
            nodata_value: Optional nodata value. Must be finite if supplied.
            units: Optional value units.
            processing_steps: Optional ordered list of processing step records.
            parent_dataset_id: Optional identifier of the input dataset.

        Returns:
            Validated DatasetMetadata instance.

        Raises:
            MetadataError: If the raster cannot be inspected or the resulting
                metadata fails validation.
        """
        try:
            raster_metadata = CRSHandler.get_raster_metadata(filepath)
        except CRSError as exc:
            raise MetadataError(f"Cannot inspect raster '{filepath}': {exc}")

        logger.debug("Built metadata from raster: %s", filepath)

        return cls(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            source=source,
            dataset_type=dataset_type,
            source_url=source_url,
            description=description,
            acquisition_date=acquisition_date,
            processing_date=processing_date,
            processing_status=processing_status,
            parent_dataset_id=parent_dataset_id,
            crs=raster_metadata.get("crs"),
            resolution=raster_metadata.get("resolution"),
            dimensions=raster_metadata.get("dimensions"),
            bounds=raster_metadata.get("bounds"),
            vertical_reference=vertical_reference,
            horizontal_reference=horizontal_reference,
            file_path=str(filepath),
            file_format=file_format,
            nodata_value=nodata_value,
            units=units,
            processing_steps=list(processing_steps) if processing_steps else [],
        )

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def add_processing_step(
        self,
        step: str,
        description: Optional[str] = None,
        tool: Optional[str] = None,
        version: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        """
        Append a structured processing step to the provenance record.

        This records that a step occurred; it does not execute or validate
        the processing itself.

        Args:
            step: Short step identifier (e.g. "reproject").
            description: Optional human-readable description.
            tool: Optional tool name (e.g. "GDAL", "WhiteboxTools").
            version: Optional tool version as reported by the tool.
            parameters: Optional JSON-compatible parameter dictionary.
            timestamp: Optional ISO-8601 timestamp; defaults to UTC now.

        Raises:
            MetadataError: If the step is invalid or not JSON-serializable.
        """
        if not isinstance(step, str) or not step.strip():
            raise MetadataError("'step' must be a non-empty string.")

        if parameters is not None and not isinstance(parameters, dict):
            raise MetadataError("'parameters' must be a dictionary or None.")

        record: Dict[str, Any] = {
            "step": step,
            "timestamp": timestamp if timestamp is not None else _utc_now_iso(),
        }

        if description is not None:
            record["description"] = description
        if tool is not None:
            record["tool"] = tool
        if version is not None:
            record["version"] = version
        if parameters is not None:
            record["parameters"] = parameters

        try:
            json.dumps(record, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise MetadataError(f"Processing step is not JSON-serializable: {exc}")

        self.processing_steps.append(record)