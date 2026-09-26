"""
Raster validation for FlowSight Phase 1

Determines whether a raster dataset is structurally and numerically suitable
for downstream FlowSight terrain processing.

Division of responsibility:
    loader.py     answers "Can I safely open and read this raster?"
    validators.py answers "Is this raster acceptable for FlowSight processing?"

This module:
- Verifies file accessibility and raster structure
- Validates CRS presence and, when requested, CRS match
- Validates resolution, nodata configuration, and pixel values
- Produces structured, JSON-serializable validation reports

This module does NOT:
- Reproject, resample, or otherwise modify rasters
- Derive slope, flow direction, flow accumulation, or filled terrain
- Assume any particular CRS, resolution, or elevation range
- Access databases, APIs, caches, or external downloads

Usage:
    from app.data.validators import RasterValidator, ValidationError

    report = RasterValidator.validate_terrain_raster(
        "data/raw/terrain/dem.tif",
        expected_crs=4326,      # supplied by the caller, never assumed
    )
    if not report.valid:
        for error in report.errors:
            print(error)
"""

import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import rasterio
    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False

from app.core.logging import get_logger
from app.data.crs import CRSHandler, CRSError
from app.data.loader import RasterLoader, DataLoadError

logger = get_logger(__name__)

# Accepted path types for all public methods.
PathLike = Union[str, Path]

# Names of the individual checks, used as the 'check' field in results.
CHECK_FILE = "file"
CHECK_STRUCTURE = "structure"
CHECK_CRS = "crs"
CHECK_RESOLUTION = "resolution"
CHECK_NODATA = "nodata"
CHECK_PIXEL_VALUES = "pixel_values"

# Errors that can surface while reading raster metadata through an open
# dataset handle. rasterio.errors.RasterioError is included when rasterio is
# importable so that low-level read failures are reported as failed checks
# rather than escaping as unhandled exceptions.
_METADATA_ACCESS_ERRORS: Tuple[type, ...] = (
    DataLoadError,
    OSError,
    ValueError,
    TypeError,
    AttributeError,
    IndexError,
)
if RASTERIO_AVAILABLE:
    _METADATA_ACCESS_ERRORS = _METADATA_ACCESS_ERRORS + (rasterio.errors.RasterioError,)


class ValidationError(Exception):
    """
    Raised when validation cannot be performed at all.

    This is distinct from a raster failing validation: a failing raster
    produces a ValidationResult with valid=False. ValidationError signals
    that the check itself could not run, for example because a required
    dependency is missing or an unexpected error occurred.
    """

    pass


def _to_json_safe_number(value: Any) -> Optional[float]:
    """
    Convert a numeric value (including NumPy scalars) into a JSON-safe float.

    Non-finite values are converted to None because NaN and Infinity are not
    representable in strict JSON.

    Args:
        value: Numeric value, NumPy scalar, or None.

    Returns:
        Python float, or None if absent or non-finite.
    """
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _to_json_safe_int(value: Any) -> Optional[int]:
    """
    Convert an integer-like value (including NumPy integers) into a Python int.

    Args:
        value: Integer-like value or None.

    Returns:
        Python int, or None if the value cannot be converted.
    """
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ValidationResult:
    """
    Outcome of a single validation check.

    Attributes:
        valid: Whether this check passed.
        check: Name of the check (e.g. "structure", "crs").
        message: Human-readable summary of the outcome.
        details: JSON-safe supporting information. Never contains rasterio
            datasets, pyproj CRS objects, or NumPy arrays.
    """

    valid: bool
    check: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dictionary for this result."""
        return asdict(self)


@dataclass
class RasterValidationReport:
    """
    Combined outcome of several validation checks on one raster.

    Attributes:
        valid: True only if every executed check passed.
        filepath: Path of the validated raster, as a string.
        checks: Ordered list of individual check results.
        errors: Messages from checks that failed.
        warnings: Non-fatal observations worth surfacing to the caller.
        info: JSON-safe structural information about the raster, when readable.
    """

    valid: bool
    filepath: str
    checks: List[ValidationResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: Optional[Dict[str, Any]] = None

    def add(self, result: ValidationResult) -> None:
        """
        Append a check result and update the overall status.

        Args:
            result: The check result to record.
        """
        self.checks.append(result)
        if not result.valid:
            self.valid = False
            self.errors.append(f"[{result.check}] {result.message}")

    def add_warning(self, message: str) -> None:
        """Record a non-fatal observation."""
        self.warnings.append(message)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dictionary for this report."""
        return {
            "valid": self.valid,
            "filepath": self.filepath,
            "checks": [check.to_dict() for check in self.checks],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "info": self.info,
        }


class RasterValidator:
    """
    Validate rasters against FlowSight's structural and numerical requirements.

    All methods are static. Individual checks return a ValidationResult;
    validate_terrain_raster() combines them into a RasterValidationReport.

    Expected CRS, resolution limits, and value ranges are always supplied by
    the caller. This validator hard-codes no location, projection, or
    elevation assumption.
    """

    # ------------------------------------------------------------------
    # File and structure
    # ------------------------------------------------------------------

    @staticmethod
    def validate_file(filepath: PathLike) -> ValidationResult:
        """
        Verify that a path exists, is a regular file, and can be opened.

        No pixel data is read.

        Args:
            filepath: Path to the raster file.

        Returns:
            ValidationResult for the "file" check.
        """
        path = Path(filepath)

        if not RasterLoader.raster_exists(path):
            return ValidationResult(
                valid=False,
                check=CHECK_FILE,
                message=f"File does not exist or is not a regular file: {path}",
                details={"path": str(path), "exists": False},
            )

        try:
            with RasterLoader.open_raster(path):
                pass
        except DataLoadError as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_FILE,
                message=f"File exists but cannot be opened as a raster: {exc}",
                details={"path": str(path), "exists": True, "openable": False},
            )

        return ValidationResult(
            valid=True,
            check=CHECK_FILE,
            message="File exists and can be opened as a raster.",
            details={"path": str(path), "exists": True, "openable": True},
        )

    @staticmethod
    def validate_raster_structure(filepath: PathLike) -> ValidationResult:
        """
        Validate raster dimensions, band count, transform, bounds, and dtype.

        Structural checks never read pixel data.

        Args:
            filepath: Path to the raster file.

        Returns:
            ValidationResult for the "structure" check.
        """
        path = Path(filepath)

        try:
            info = RasterLoader.get_raster_info(path)
        except DataLoadError as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_STRUCTURE,
                message=f"Cannot inspect raster structure: {exc}",
                details={"path": str(path)},
            )

        problems: List[str] = []

        width = info.get("width")
        height = info.get("height")
        count = info.get("count")

        if not isinstance(width, int) or width <= 0:
            problems.append(f"width must be positive, got {width!r}")
        if not isinstance(height, int) or height <= 0:
            problems.append(f"height must be positive, got {height!r}")
        if not isinstance(count, int) or count < 1:
            problems.append(f"band count must be at least 1, got {count!r}")

        transform = info.get("transform")
        if not transform or len(transform) < 6:
            problems.append("raster has no usable affine transform")
        elif any(coefficient is None for coefficient in transform):
            problems.append("affine transform contains non-finite coefficients")

        bounds = info.get("bounds")
        if not bounds:
            problems.append("raster has no bounds")
        else:
            missing = [
                key for key in ("min_x", "max_x", "min_y", "max_y")
                if bounds.get(key) is None
            ]
            if missing:
                problems.append(
                    f"bounds are missing or non-finite for: {', '.join(missing)}"
                )
            else:
                if bounds["min_x"] > bounds["max_x"]:
                    problems.append("bounds min_x exceeds max_x")
                if bounds["min_y"] > bounds["max_y"]:
                    problems.append("bounds min_y exceeds max_y")

        resolution = info.get("resolution")
        if not resolution or len(resolution) < 2:
            problems.append("raster has no resolution")
        elif any(value is None for value in resolution):
            problems.append("resolution contains non-finite values")

        if not info.get("dtype"):
            problems.append("raster dtype is unavailable")

        details = {
            "path": str(path),
            "width": width,
            "height": height,
            "count": count,
            "dtype": info.get("dtype"),
            "transform": transform,
            "bounds": bounds,
            "resolution": resolution,
        }

        if problems:
            return ValidationResult(
                valid=False,
                check=CHECK_STRUCTURE,
                message="Raster structure is invalid: " + "; ".join(problems),
                details=details,
            )

        return ValidationResult(
            valid=True,
            check=CHECK_STRUCTURE,
            message="Raster structure is valid.",
            details=details,
        )

    # ------------------------------------------------------------------
    # CRS
    # ------------------------------------------------------------------

    @staticmethod
    def validate_crs(
        filepath: PathLike,
        expected_crs: Optional[Any] = None,
    ) -> ValidationResult:
        """
        Validate the raster's coordinate reference system.

        CRS interpretation and comparison are delegated to CRSHandler, which
        compares CRS objects semantically rather than by string. No CRS is
        assumed, and no reprojection is performed.

        Args:
            filepath: Path to the raster file.
            expected_crs: Optional expected CRS supplied by the caller. May be
                an EPSG int, an EPSG string, WKT, PROJ, or a CRS object. When
                None, only CRS presence is checked.

        Returns:
            ValidationResult for the "crs" check. Distinguishes CRS present,
            CRS missing, and CRS mismatch.
        """
        path = Path(filepath)

        try:
            crs_info = CRSHandler.get_raster_crs(str(path))
        except CRSError as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_CRS,
                message=f"Cannot read CRS: {exc}",
                details={"path": str(path), "crs_present": False},
            )

        if crs_info is None:
            return ValidationResult(
                valid=False,
                check=CHECK_CRS,
                message="Raster has no CRS defined.",
                details={"path": str(path), "crs_present": False},
            )

        # Drop the live pyproj object; results must stay JSON-safe.
        details: Dict[str, Any] = {
            "path": str(path),
            "crs_present": True,
            "epsg": crs_info.get("epsg"),
        }

        if expected_crs is None:
            return ValidationResult(
                valid=True,
                check=CHECK_CRS,
                message="Raster has a CRS. No expected CRS was supplied for comparison.",
                details=details,
            )

        try:
            matches, message = CRSHandler.validate_crs(str(path), expected_crs)
        except CRSError as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_CRS,
                message=f"CRS comparison failed: {exc}",
                details=details,
            )

        details["expected_crs_supplied"] = True

        if matches:
            return ValidationResult(
                valid=True,
                check=CHECK_CRS,
                message="Raster CRS matches the expected CRS.",
                details=details,
            )

        return ValidationResult(
            valid=False,
            check=CHECK_CRS,
            message=message or "Raster CRS does not match the expected CRS.",
            details=details,
        )

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @staticmethod
    def validate_resolution(
        filepath: PathLike,
        min_resolution: Optional[float] = None,
        max_resolution: Optional[float] = None,
    ) -> ValidationResult:
        """
        Validate the raster's actual pixel resolution.

        Resolution is read from the file and never assumed. Optional bounds
        are applied only when the caller supplies them; the raster is never
        modified.

        Units and direction:
            Both bounds are expressed as PIXEL SIZE in the raster's own CRS
            units (degrees for a geographic CRS, metres for a projected one).
            A smaller pixel size means a finer spatial resolution; a larger
            pixel size means a coarser one.

            min_resolution is the MINIMUM ALLOWED PIXEL SIZE. A pixel size
            below it is finer than permitted and fails.

            max_resolution is the MAXIMUM ALLOWED PIXEL SIZE. A pixel size
            above it is coarser than permitted and fails.

        Args:
            filepath: Path to the raster file.
            min_resolution: Optional inclusive minimum allowed pixel size, in
                the raster's own CRS units. Pixel sizes below this fail.
            max_resolution: Optional inclusive maximum allowed pixel size, in
                the raster's own CRS units. Pixel sizes above this fail.

        Returns:
            ValidationResult for the "resolution" check.
        """
        path = Path(filepath)

        try:
            resolution = CRSHandler.get_raster_resolution(str(path))
        except CRSError as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_RESOLUTION,
                message=f"Cannot read resolution: {exc}",
                details={"path": str(path)},
            )

        if resolution is None:
            return ValidationResult(
                valid=False,
                check=CHECK_RESOLUTION,
                message="Raster has no resolution information.",
                details={"path": str(path)},
            )

        pixel_width = _to_json_safe_number(resolution[0])
        pixel_height = _to_json_safe_number(resolution[1])

        details: Dict[str, Any] = {
            "path": str(path),
            "pixel_width": pixel_width,
            "pixel_height": pixel_height,
            "min_resolution": _to_json_safe_number(min_resolution),
            "max_resolution": _to_json_safe_number(max_resolution),
            "constraint_units": "pixel size in the raster's own CRS units",
        }

        problems: List[str] = []

        if pixel_width is None or pixel_height is None:
            problems.append("resolution values are not finite")
        else:
            if pixel_width <= 0 or pixel_height <= 0:
                problems.append(
                    f"pixel size must be positive, got "
                    f"{pixel_width} x {pixel_height}"
                )

            if min_resolution is not None:
                if pixel_width < min_resolution or pixel_height < min_resolution:
                    problems.append(
                        f"pixel size {pixel_width} x {pixel_height} is smaller than "
                        f"the minimum allowed pixel size {min_resolution} "
                        f"(finer than permitted)"
                    )

            if max_resolution is not None:
                if pixel_width > max_resolution or pixel_height > max_resolution:
                    problems.append(
                        f"pixel size {pixel_width} x {pixel_height} is larger than "
                        f"the maximum allowed pixel size {max_resolution} "
                        f"(coarser than permitted)"
                    )

        if problems:
            return ValidationResult(
                valid=False,
                check=CHECK_RESOLUTION,
                message="Resolution is invalid: " + "; ".join(problems),
                details=details,
            )

        return ValidationResult(
            valid=True,
            check=CHECK_RESOLUTION,
            message=(
                f"Resolution is valid: pixel size {pixel_width} x {pixel_height} "
                f"in the raster's CRS units."
            ),
            details=details,
        )

    # ------------------------------------------------------------------
    # Nodata
    # ------------------------------------------------------------------

    @staticmethod
    def validate_nodata(filepath: PathLike, band: int = 1) -> ValidationResult:
        """
        Inspect the nodata configuration of a band.

        This checks metadata only. A passing result means the nodata
        declaration is readable and well-formed; it says nothing about whether
        the band's pixel values are valid. Use validate_pixel_values() for that.

        A raster with no declared nodata value is not an error.

        Args:
            filepath: Path to the raster file.
            band: 1-based band index. Defaults to 1.

        Returns:
            ValidationResult for the "nodata" check.
        """
        path = Path(filepath)

        try:
            RasterLoader.validate_band(path, band=band)
        except DataLoadError as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_NODATA,
                message=f"Cannot inspect band {band}: {exc}",
                details={"path": str(path), "band": band},
            )

        # Metadata access can fail below the loader boundary (for example a
        # truncated or corrupt header), so rasterio-level errors are reported
        # as a failed check rather than escaping unhandled.
        try:
            with RasterLoader.open_raster(path) as src:
                nodata_values = src.nodatavals
                raw_nodata = (
                    nodata_values[band - 1]
                    if nodata_values is not None and len(nodata_values) >= band
                    else None
                )
        except _METADATA_ACCESS_ERRORS as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_NODATA,
                message=f"Cannot read nodata metadata for band {band}: {exc}",
                details={"path": str(path), "band": band},
            )

        details: Dict[str, Any] = {
            "path": str(path),
            "band": band,
            "nodata_defined": raw_nodata is not None,
            "nodata_value": None,
        }

        if raw_nodata is None:
            return ValidationResult(
                valid=True,
                check=CHECK_NODATA,
                message=(
                    f"Band {band} declares no nodata value. "
                    f"This is permitted; pixel validity is checked separately."
                ),
                details=details,
            )

        safe_nodata = _to_json_safe_number(raw_nodata)
        if safe_nodata is None:
            return ValidationResult(
                valid=False,
                check=CHECK_NODATA,
                message=(
                    f"Band {band} declares a nodata value that is not a finite "
                    f"number: {raw_nodata!r}"
                ),
                details=details,
            )

        details["nodata_value"] = safe_nodata

        return ValidationResult(
            valid=True,
            check=CHECK_NODATA,
            message=(
                f"Band {band} declares a finite nodata value ({safe_nodata}). "
                f"Pixel validity is checked separately."
            ),
            details=details,
        )

    # ------------------------------------------------------------------
    # Pixel values
    # ------------------------------------------------------------------

    @staticmethod
    def validate_pixel_values(
        filepath: PathLike,
        band: int = 1,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> ValidationResult:
        """
        Validate the numerical content of a single band.

        This is the only check that reads pixel data, and it reads exactly one
        band. Pixels equal to the band's declared nodata value are excluded
        from the valid-data statistics.

        No value range is assumed. min_value and max_value are applied only
        when the caller supplies them; a high or low elevation is never
        rejected on its own.

        Args:
            filepath: Path to the raster file.
            band: 1-based band index. Defaults to 1.
            min_value: Optional inclusive lower bound for valid pixels.
            max_value: Optional inclusive upper bound for valid pixels.

        Returns:
            ValidationResult for the "pixel_values" check.

        Raises:
            ValidationError: If numpy is unavailable, so the check cannot run.
        """
        if not NUMPY_AVAILABLE:
            raise ValidationError(
                "numpy required to validate pixel values. "
                "Install: pip install numpy"
            )

        path = Path(filepath)

        try:
            RasterLoader.validate_band(path, band=band)
        except DataLoadError as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_PIXEL_VALUES,
                message=f"Cannot read band {band}: {exc}",
                details={"path": str(path), "band": band},
            )

        # The band's declared nodata value is metadata, so it is inspected
        # here through a metadata-only open that reads no pixels. The pixel
        # array itself is loaded separately and explicitly further below via
        # RasterLoader.read_band(), which is the only pixel read in this module.
        try:
            with RasterLoader.open_raster(path) as src:
                nodata_values = src.nodatavals
                raw_nodata = (
                    nodata_values[band - 1]
                    if nodata_values is not None and len(nodata_values) >= band
                    else None
                )
        except _METADATA_ACCESS_ERRORS as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_PIXEL_VALUES,
                message=f"Cannot read nodata metadata for band {band}: {exc}",
                details={"path": str(path), "band": band},
            )

        # Explicit pixel read: this is the point at which data enters memory.
        try:
            array = RasterLoader.read_band(path, band=band)
        except DataLoadError as exc:
            return ValidationResult(
                valid=False,
                check=CHECK_PIXEL_VALUES,
                message=f"Cannot read pixel values for band {band}: {exc}",
                details={"path": str(path), "band": band},
            )

        details: Dict[str, Any] = {
            "path": str(path),
            "band": band,
            "dtype": str(array.dtype),
        }

        total_pixels = int(array.size)
        details["total_pixels"] = total_pixels

        if total_pixels == 0:
            return ValidationResult(
                valid=False,
                check=CHECK_PIXEL_VALUES,
                message=f"Band {band} contains no pixels.",
                details=details,
            )

        if not np.issubdtype(array.dtype, np.number):
            return ValidationResult(
                valid=False,
                check=CHECK_PIXEL_VALUES,
                message=(
                    f"Band {band} has non-numeric dtype '{array.dtype}'; "
                    f"terrain processing requires numeric data."
                ),
                details=details,
            )

        # Non-finite counts only apply to floating-point data; integer arrays
        # cannot hold NaN or Infinity.
        if np.issubdtype(array.dtype, np.floating):
            finite_mask = np.isfinite(array)
            nan_count = int(np.count_nonzero(np.isnan(array)))
            posinf_count = int(np.count_nonzero(np.isposinf(array)))
            neginf_count = int(np.count_nonzero(np.isneginf(array)))
        else:
            finite_mask = np.ones(array.shape, dtype=bool)
            nan_count = 0
            posinf_count = 0
            neginf_count = 0

        non_finite_count = total_pixels - int(np.count_nonzero(finite_mask))

        details["nan_pixels"] = nan_count
        details["positive_infinity_pixels"] = posinf_count
        details["negative_infinity_pixels"] = neginf_count
        details["non_finite_pixels"] = non_finite_count
        details["non_finite_proportion"] = _to_json_safe_number(
            non_finite_count / total_pixels
        )

        # Exclude declared nodata pixels from the valid-data statistics.
        safe_nodata = _to_json_safe_number(raw_nodata)
        details["nodata_value"] = safe_nodata

        valid_mask = finite_mask
        if safe_nodata is not None:
            valid_mask = valid_mask & (array != safe_nodata)

        nodata_pixel_count = int(
            np.count_nonzero(finite_mask) - np.count_nonzero(valid_mask)
        )
        details["nodata_pixels"] = nodata_pixel_count

        valid_count = int(np.count_nonzero(valid_mask))
        details["valid_pixels"] = valid_count
        details["valid_proportion"] = _to_json_safe_number(valid_count / total_pixels)

        if valid_count == 0:
            return ValidationResult(
                valid=False,
                check=CHECK_PIXEL_VALUES,
                message=(
                    f"Band {band} contains no valid data: all {total_pixels} pixels "
                    f"are non-finite or nodata."
                ),
                details=details,
            )

        valid_data = array[valid_mask]
        details["min"] = _to_json_safe_number(np.min(valid_data))
        details["max"] = _to_json_safe_number(np.max(valid_data))
        details["mean"] = _to_json_safe_number(np.mean(valid_data))

        details["min_value_constraint"] = _to_json_safe_number(min_value)
        details["max_value_constraint"] = _to_json_safe_number(max_value)

        problems: List[str] = []

        if non_finite_count > 0:
            problems.append(
                f"{non_finite_count} of {total_pixels} pixels are non-finite "
                f"(NaN={nan_count}, +Inf={posinf_count}, -Inf={neginf_count})"
            )

        # Range constraints apply only when explicitly supplied by the caller.
        if min_value is not None and details["min"] is not None:
            if details["min"] < min_value:
                problems.append(
                    f"minimum value {details['min']} is below the supplied "
                    f"minimum {min_value}"
                )

        if max_value is not None and details["max"] is not None:
            if details["max"] > max_value:
                problems.append(
                    f"maximum value {details['max']} exceeds the supplied "
                    f"maximum {max_value}"
                )

        if problems:
            return ValidationResult(
                valid=False,
                check=CHECK_PIXEL_VALUES,
                message="Pixel values are invalid: " + "; ".join(problems),
                details=details,
            )

        return ValidationResult(
            valid=True,
            check=CHECK_PIXEL_VALUES,
            message=(
                f"Band {band} has {valid_count} valid pixels of {total_pixels} "
                f"and no non-finite values."
            ),
            details=details,
        )

    # ------------------------------------------------------------------
    # Combined validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_terrain_raster(
        filepath: PathLike,
        expected_crs: Optional[Any] = None,
        band: int = 1,
        min_resolution: Optional[float] = None,
        max_resolution: Optional[float] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        check_pixel_values: bool = False,
    ) -> RasterValidationReport:
        """
        Run the full validation sequence and return a structured report.

        Checks run in order: file, structure, CRS, resolution, nodata, and
        (optionally) pixel values. If the file or structure check fails, the
        remaining checks are skipped because their results would be meaningless.

        Every constraint is supplied by the caller. This method assumes no CRS,
        no resolution, and no value range.

        Resolution constraints:
            min_resolution and max_resolution are PIXEL SIZES in the raster's
            own CRS units. A smaller pixel size is a finer spatial resolution;
            a larger pixel size is a coarser one. A pixel size below
            min_resolution fails as finer than permitted, and a pixel size
            above max_resolution fails as coarser than permitted.

        Args:
            filepath: Path to the raster file.
            expected_crs: Optional expected CRS for comparison.
            band: 1-based band index to validate. Defaults to 1.
            min_resolution: Optional inclusive minimum allowed pixel size, in
                the raster's own CRS units.
            max_resolution: Optional inclusive maximum allowed pixel size, in
                the raster's own CRS units.
            min_value: Optional lower bound on valid pixel values.
            max_value: Optional upper bound on valid pixel values.
            check_pixel_values: Whether to read and validate pixel data.
                Set False to keep validation free of pixel reads.

        Returns:
            RasterValidationReport summarizing all executed checks.

        Raises:
            ValidationError: If pixel validation was requested but numpy is
                unavailable.
        """
        path = Path(filepath)
        report = RasterValidationReport(valid=True, filepath=str(path))

        file_result = RasterValidator.validate_file(path)
        report.add(file_result)
        if not file_result.valid:
            logger.debug("Validation stopped at file check for %s", path)
            return report

        structure_result = RasterValidator.validate_raster_structure(path)
        report.add(structure_result)

        # Structural details double as the report's raster information.
        report.info = structure_result.details

        if not structure_result.valid:
            logger.debug("Validation stopped at structure check for %s", path)
            return report

        report.add(RasterValidator.validate_crs(path, expected_crs=expected_crs))

        if expected_crs is None:
            report.add_warning(
                "No expected CRS was supplied; only CRS presence was checked."
            )

        report.add(
            RasterValidator.validate_resolution(
                path,
                min_resolution=min_resolution,
                max_resolution=max_resolution,
            )
        )

        nodata_result = RasterValidator.validate_nodata(path, band=band)
        report.add(nodata_result)

        if nodata_result.valid and not nodata_result.details.get("nodata_defined"):
            report.add_warning(
                f"Band {band} declares no nodata value; nodata pixels cannot be "
                f"distinguished from valid data."
            )

        if check_pixel_values:
            report.add(
                RasterValidator.validate_pixel_values(
                    path,
                    band=band,
                    min_value=min_value,
                    max_value=max_value,
                )
            )
        else:
            report.add_warning(
                "Pixel values were not validated (check_pixel_values=False)."
            )

        logger.debug(
            "Validation completed for %s: valid=%s, checks=%d",
            path,
            report.valid,
            len(report.checks),
        )

        return report