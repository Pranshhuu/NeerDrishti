"""
Raster dataset loader for FlowSight Phase 1

Provides safe, memory-conscious access to raster datasets stored on disk.

This module:
- Opens rasters using rasterio
- Reads individual bands on explicit request
- Reports lightweight, JSON-safe raster information
- Reuses app.data.crs.CRSHandler for CRS interpretation

This module does NOT:
- Process, reproject, or resample rasters
- Derive slope, flow direction, or flow accumulation
- Validate raster values, nodata regions, or CRS correctness (see validators.py)
- Create provenance metadata (see metadata.py)
- Download data, cache data, or access databases or APIs

Memory safety:
    open_raster() never reads pixel data. Pixel arrays are only produced when
    the caller explicitly invokes read_band(). This keeps the loader usable
    with large geospatial datasets.

Usage:
    from app.data.loader import RasterLoader, DataLoadError

    info = RasterLoader.get_raster_info("data/raw/terrain/dem.tif")

    with RasterLoader.open_raster("data/raw/terrain/dem.tif") as src:
        profile = src.profile

    array = RasterLoader.read_band("data/raw/terrain/dem.tif", band=1)
"""

import math
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

try:
    import rasterio
    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

from app.core.logging import get_logger
from app.data.crs import CRSHandler, CRSError

logger = get_logger(__name__)

# Accepted path types for all public methods.
PathLike = Union[str, Path]


class DataLoadError(Exception):
    """
    Raised when a raster dataset cannot be opened, inspected, or read.

    This includes missing dependencies, missing or non-regular files,
    invalid band indices, and errors reported by rasterio.
    """

    pass


def _to_json_safe_number(value: Any) -> Optional[float]:
    """
    Convert a numeric value into a JSON-safe Python float.

    Non-finite values (NaN, +/-Infinity) are converted to None because they
    cannot be represented in strict JSON.

    Args:
        value: A numeric value, possibly a NumPy scalar, or None.

    Returns:
        A Python float, or None if the value is absent or non-finite.
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


class RasterLoader:
    """
    Safe accessor for raster datasets on disk.

    All methods are static and take a filesystem path. The loader holds no
    global state and keeps no open dataset handles; every method that opens a
    dataset also closes it, except open_raster(), whose handle is owned by the
    caller.

    Responsibilities:
    - Verify that a path refers to an existing regular file
    - Open rasters through rasterio
    - Read a single band on explicit request
    - Report structural information (size, band count, dtype, bounds, CRS)

    Not responsible for:
    - Raster processing or transformation of any kind
    - Data-quality validation (see validators.py)
    - Provenance metadata (see metadata.py)
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_rasterio() -> None:
        """
        Ensure rasterio is importable.

        Raises:
            DataLoadError: If rasterio is not installed.
        """
        if not RASTERIO_AVAILABLE:
            raise DataLoadError(
                "rasterio required for raster access. "
                "Install: pip install rasterio"
            )

    @staticmethod
    def _resolve_path(filepath: PathLike) -> Path:
        """
        Normalize a path argument and verify it refers to a regular file.

        Args:
            filepath: Path to a raster file, as str or Path.

        Returns:
            Normalized Path object.

        Raises:
            DataLoadError: If the argument is not a path, the path does not
                exist, or the path is not a regular file.
        """
        if not isinstance(filepath, (str, Path)):
            raise DataLoadError(
                f"Raster path must be a str or Path, "
                f"got {type(filepath).__name__}."
            )

        path = Path(filepath)

        if not path.exists():
            raise DataLoadError(f"Raster file not found: {path}")

        if not path.is_file():
            raise DataLoadError(f"Raster path is not a regular file: {path}")

        return path

    @staticmethod
    def _validate_band_index(band: int) -> int:
        """
        Validate that a band index is a positive integer.

        rasterio uses 1-based band indexing.

        Args:
            band: Requested band index.

        Returns:
            The validated band index.

        Raises:
            DataLoadError: If the band index is not a positive integer.
        """
        if isinstance(band, bool) or not isinstance(band, int):
            raise DataLoadError(
                f"Band index must be an integer, got {type(band).__name__}."
            )

        if band < 1:
            raise DataLoadError(
                f"Band index must be >= 1 (rasterio uses 1-based indexing), got {band}."
            )

        return band

    # ------------------------------------------------------------------
    # Opening
    # ------------------------------------------------------------------

    @staticmethod
    def open_raster(filepath: PathLike) -> Any:
        """
        Open a raster dataset for reading.

        No pixel data is read. The returned handle is a rasterio dataset that
        supports the context-manager protocol.

        IMPORTANT: The caller owns the returned handle and must close it,
        either explicitly or by using it as a context manager:

            with RasterLoader.open_raster(path) as src:
                ...

        Args:
            filepath: Path to the raster file.

        Returns:
            An open rasterio dataset handle.

        Raises:
            DataLoadError: If rasterio is unavailable, the file is missing or
                not a regular file, or rasterio cannot open the dataset.
        """
        RasterLoader._require_rasterio()
        path = RasterLoader._resolve_path(filepath)

        try:
            return rasterio.open(path)
        except rasterio.errors.RasterioError as exc:
            raise DataLoadError(f"Cannot open raster '{path}': {exc}")
        except OSError as exc:
            raise DataLoadError(f"Cannot open raster '{path}': {exc}")

    # ------------------------------------------------------------------
    # Existence and structure
    # ------------------------------------------------------------------

    @staticmethod
    def raster_exists(filepath: PathLike) -> bool:
        """
        Report whether a path refers to an existing regular file.

        This helper never raises for an ordinary missing path; it returns
        False instead. It does not attempt to open the file, so a True result
        does not guarantee the file is a readable raster.

        Args:
            filepath: Path to check.

        Returns:
            True if the path exists and is a regular file, False otherwise.
        """
        if not isinstance(filepath, (str, Path)):
            return False

        try:
            path = Path(filepath)
            return path.is_file()
        except OSError:
            return False

    @staticmethod
    def get_dataset_shape(filepath: PathLike) -> Tuple[int, int]:
        """
        Return raster dimensions without reading pixel data.

        Args:
            filepath: Path to the raster file.

        Returns:
            Tuple of (width, height) in pixels.

        Raises:
            DataLoadError: If the raster cannot be opened or inspected.
        """
        path = RasterLoader._resolve_path(filepath)

        with RasterLoader.open_raster(path) as src:
            try:
                return (int(src.width), int(src.height))
            except (AttributeError, TypeError, ValueError) as exc:
                raise DataLoadError(f"Cannot read dimensions from '{path}': {exc}")

    @staticmethod
    def get_dataset_count(filepath: PathLike) -> int:
        """
        Return the number of raster bands without reading pixel data.

        Args:
            filepath: Path to the raster file.

        Returns:
            Number of bands.

        Raises:
            DataLoadError: If the raster cannot be opened or inspected.
        """
        path = RasterLoader._resolve_path(filepath)

        with RasterLoader.open_raster(path) as src:
            try:
                return int(src.count)
            except (AttributeError, TypeError, ValueError) as exc:
                raise DataLoadError(f"Cannot read band count from '{path}': {exc}")

    @staticmethod
    def get_dataset_dtype(filepath: PathLike, band: int = 1) -> str:
        """
        Return the data type of a band without reading pixel data.

        Args:
            filepath: Path to the raster file.
            band: 1-based band index. Defaults to 1.

        Returns:
            Data type name as reported by rasterio, e.g. "float32".

        Raises:
            DataLoadError: If the raster cannot be opened, the band index is
                invalid, or the band is outside the available range.
        """
        path = RasterLoader._resolve_path(filepath)
        band_index = RasterLoader._validate_band_index(band)

        with RasterLoader.open_raster(path) as src:
            if band_index > src.count:
                raise DataLoadError(
                    f"Band {band_index} does not exist in '{path}': "
                    f"raster has {src.count} band(s)."
                )

            try:
                return str(src.dtypes[band_index - 1])
            except (AttributeError, IndexError, TypeError) as exc:
                raise DataLoadError(
                    f"Cannot read dtype for band {band_index} of '{path}': {exc}"
                )

    @staticmethod
    def validate_band(filepath: PathLike, band: int = 1) -> bool:
        """
        Check that a band exists in a raster.

        This is a structural check only. It does not inspect pixel values,
        nodata coverage, or data quality; those belong in validators.py.

        Args:
            filepath: Path to the raster file.
            band: 1-based band index. Defaults to 1.

        Returns:
            True if the band exists.

        Raises:
            DataLoadError: If the file is missing, the raster is unreadable,
                the band index is invalid, or the band is outside range.
        """
        path = RasterLoader._resolve_path(filepath)
        band_index = RasterLoader._validate_band_index(band)

        with RasterLoader.open_raster(path) as src:
            if band_index > src.count:
                raise DataLoadError(
                    f"Band {band_index} does not exist in '{path}': "
                    f"raster has {src.count} band(s)."
                )

        return True

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    @staticmethod
    def read_band(filepath: PathLike, band: int = 1) -> "np.ndarray":
        """
        Read a single raster band into a NumPy array.

        This is the only method that loads pixel data into memory, and it
        reads exactly one band. Callers working with large rasters should
        consider windowed reads via open_raster() instead.

        Args:
            filepath: Path to the raster file.
            band: 1-based band index. Defaults to 1.

        Returns:
            NumPy array containing the band's pixel values.

        Raises:
            DataLoadError: If numpy or rasterio are unavailable, the file is
                missing, the band index is invalid or out of range, or the
                read fails.
        """
        if not NUMPY_AVAILABLE:
            raise DataLoadError(
                "numpy required to read raster bands. Install: pip install numpy"
            )

        path = RasterLoader._resolve_path(filepath)
        band_index = RasterLoader._validate_band_index(band)

        with RasterLoader.open_raster(path) as src:
            if band_index > src.count:
                raise DataLoadError(
                    f"Band {band_index} does not exist in '{path}': "
                    f"raster has {src.count} band(s)."
                )

            try:
                array = src.read(band_index)
            except rasterio.errors.RasterioError as exc:
                raise DataLoadError(
                    f"Cannot read band {band_index} from '{path}': {exc}"
                )
            except (MemoryError, OSError, ValueError) as exc:
                raise DataLoadError(
                    f"Cannot read band {band_index} from '{path}': {exc}"
                )

        logger.debug("Read band %d from %s", band_index, path)
        return array

    # ------------------------------------------------------------------
    # Information
    # ------------------------------------------------------------------

    @staticmethod
    def get_raster_info(filepath: PathLike) -> Dict[str, Any]:
        """
        Return structural raster information without reading pixel data.

        The returned dictionary is JSON-safe: it contains only strings,
        numbers, lists, dictionaries, and None. Live rasterio dataset, CRS,
        and Affine objects are converted to safe representations, and NumPy
        scalar types are converted to Python numbers. Non-finite values are
        reported as None.

        CRS interpretation is delegated to CRSHandler; if the CRS cannot be
        interpreted, the 'crs' field is None rather than a partial guess.

        Args:
            filepath: Path to the raster file.

        Returns:
            Dictionary with keys:
            - 'path': file path as a string
            - 'driver': rasterio driver name
            - 'width', 'height': dimensions in pixels
            - 'count': number of bands
            - 'dtype': dtype of the first band, or None
            - 'dtypes': list of dtypes for all bands
            - 'crs': JSON-safe CRS dictionary, or None if absent/uninterpretable
            - 'transform': six affine coefficients as a list, or None
            - 'bounds': dictionary with min_x, max_x, min_y, max_y, or None
            - 'resolution': list of [pixel_width, pixel_height], or None
            - 'nodata': nodata value as a float, or None

        Raises:
            DataLoadError: If the raster cannot be opened or inspected.
        """
        path = RasterLoader._resolve_path(filepath)

        with RasterLoader.open_raster(path) as src:
            try:
                dtypes = [str(dtype) for dtype in src.dtypes]

                transform = None
                if src.transform is not None:
                    coefficients = [
                        _to_json_safe_number(value)
                        for value in tuple(src.transform)[:6]
                    ]
                    transform = coefficients

                bounds = None
                if src.bounds is not None:
                    bounds = {
                        "min_x": _to_json_safe_number(src.bounds.left),
                        "max_x": _to_json_safe_number(src.bounds.right),
                        "min_y": _to_json_safe_number(src.bounds.bottom),
                        "max_y": _to_json_safe_number(src.bounds.top),
                    }

                resolution = None
                if src.res is not None and len(src.res) >= 2:
                    resolution = [
                        _to_json_safe_number(abs(src.res[0])),
                        _to_json_safe_number(abs(src.res[1])),
                    ]

                info: Dict[str, Any] = {
                    "path": str(path),
                    "driver": str(src.driver) if src.driver else None,
                    "width": int(src.width),
                    "height": int(src.height),
                    "count": int(src.count),
                    "dtype": dtypes[0] if dtypes else None,
                    "dtypes": dtypes,
                    "transform": transform,
                    "bounds": bounds,
                    "resolution": resolution,
                    "nodata": _to_json_safe_number(src.nodata),
                }
            except (AttributeError, IndexError, TypeError, ValueError) as exc:
                raise DataLoadError(f"Cannot inspect raster '{path}': {exc}")

        # CRS interpretation is owned by CRSHandler, not duplicated here.
        info["crs"] = RasterLoader._get_json_safe_crs(path)

        return info

    @staticmethod
    def _get_json_safe_crs(path: Path) -> Optional[Dict[str, Any]]:
        """
        Obtain a JSON-safe CRS description via CRSHandler.

        The live pyproj.CRS object returned by CRSHandler is deliberately
        dropped so that the result can be serialized directly.

        Args:
            path: Path to the raster file.

        Returns:
            Dictionary with any of 'epsg', 'wkt', 'proj4', or None if the
            raster has no CRS or the CRS could not be read.
        """
        try:
            crs_info = CRSHandler.get_raster_crs(str(path))
        except CRSError as exc:
            logger.debug("CRS unavailable for %s: %s", path, exc)
            return None

        if crs_info is None:
            return None

        safe: Dict[str, Any] = {}
        for key in ("epsg", "wkt", "proj4"):
            value = crs_info.get(key)
            if value is not None:
                safe[key] = value

        return safe if safe else None