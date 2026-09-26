"""
CRS utilities for FlowSight Phase 1

Provides raster coordinate reference system inspection, validation, and normalization.

This module:
- Reads CRS from raster files
- Validates raster CRS against expected values
- Normalizes CRS for comparison and metadata
- Does NOT perform reprojection (that is separate)
- Does NOT contain CRS definitions (those are in data/CRS_DEFINITIONS.yaml)
- Does NOT assume input CRS values

Dependencies:
- rasterio: for reading raster metadata
- pyproj: for CRS comparison and normalization

Both are optional at import time. The module remains importable without them so
that the FastAPI application starts in an incomplete geospatial environment; any
operation that actually needs a missing library raises CRSError with
installation guidance rather than failing obscurely.

Usage:
    from app.data.crs import CRSHandler, CRSError
    
    # Inspect raster CRS
    crs_info = CRSHandler.get_raster_crs("data/raw/terrain/dem.tif")
    if crs_info:
        print(f"EPSG code: {crs_info['epsg']}")
    
    # Validate against expected CRS
    is_valid, message = CRSHandler.validate_crs(
        "data/raw/terrain/dem.tif",
        expected_crs=4326  # WGS84 geographic
    )
    if not is_valid:
        print(f"Validation failed: {message}")
"""

# Annotations are stored as strings rather than evaluated at definition time.
# normalize_crs is annotated `-> pyproj.CRS`, which would otherwise be
# dereferenced while this module is imported and raise NameError whenever
# pyproj is unavailable, defeating the optional-dependency design above.
# This must remain the first statement after the docstring.
from __future__ import annotations

import logging
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

try:
    import rasterio
    RASTERIO_AVAILABLE = True
except ImportError:
    # Bound explicitly so an unguarded reference fails as a clear AttributeError
    # on None rather than an obscure NameError.
    rasterio = None
    RASTERIO_AVAILABLE = False

try:
    import pyproj
    PYPROJ_AVAILABLE = True
except ImportError:
    pyproj = None
    PYPROJ_AVAILABLE = False

from app.core.logging import get_logger

logger = get_logger(__name__)


class CRSError(Exception):
    """
    Raised when a CRS operation cannot be performed.
    
    This includes missing dependencies, invalid input,
    or file access failures.
    """
    pass


class CRSHandler:
    """
    Inspect and validate coordinate reference systems in raster datasets.
    
    This class provides utilities for reading CRS metadata from raster files,
    validating CRS against expected values, and normalizing CRS representations
    for safe comparison.
    
    What this class does:
    - Read CRS from raster files using rasterio
    - Extract bounds, resolution, and dimensions
    - Validate raster CRS against caller-supplied expected CRS
    - Normalize CRS representations using pyproj
    - Compare CRS using proper pyproj equality (not string matching)
    
    What this class does NOT do:
    - Perform reprojection (transformation of pixel coordinates)
    - Contain or manage CRS definitions
    - Hard-code assumptions about input CRS
    - Return CRS configuration or descriptive metadata
    - Download or process raster data
    
    All operations that require rasterio or pyproj will raise CRSError
    if the dependency is not available, rather than silently failing.
    """
    
    @staticmethod
    def get_raster_crs(filepath: str) -> Optional[Dict[str, Any]]:
        """
        Read CRS from a raster file.
        
        Extracts coordinate reference system information from a raster dataset
        without making assumptions about whether the CRS is expected or valid.
        
        Args:
            filepath: Path to raster file (GeoTIFF or other rasterio-compatible format)
        
        Returns:
            Dictionary containing:
            - 'epsg': EPSG code as int, or None if not available
            - 'wkt': WKT representation of CRS
            - 'proj4': PROJ4 string representation
            - 'pyproj_crs': pyproj.CRS object for further processing
            
            Returns None if raster has no CRS defined.
        
        Raises:
            CRSError: If rasterio or pyproj is unavailable, the file is not
                found, or the CRS cannot be read
        
        Example:
            >>> crs_info = CRSHandler.get_raster_crs("dem.tif")
            >>> if crs_info:
            ...     print(f"EPSG:{crs_info['epsg']}")
        """
        if not RASTERIO_AVAILABLE:
            raise CRSError(
                "rasterio required for CRS inspection. "
                "Install: pip install rasterio"
            )
        
        # This method calls pyproj.CRS.from_wkt() below to normalize the CRS it
        # returns, so pyproj is required here as well. Without this check the
        # call would fail on an unbound name instead of reporting the missing
        # dependency the way every other method in this class does.
        if not PYPROJ_AVAILABLE:
            raise CRSError(
                "pyproj required for CRS inspection. "
                "Install: pip install pyproj"
            )
        
        if not Path(filepath).exists():
            raise CRSError(f"File not found: {filepath}")
        
        try:
            with rasterio.open(filepath) as src:
                rasterio_crs = src.crs
                
                if rasterio_crs is None:
                    return None
                
                # Convert rasterio CRS to pyproj.CRS for canonical representation
                try:
                    pyproj_crs_obj = pyproj.CRS.from_wkt(rasterio_crs.to_wkt())
                except Exception as e:
                    raise CRSError(
                        f"Cannot convert rasterio CRS to pyproj: {str(e)}"
                    )
                
                # Extract EPSG code if available
                epsg_code = None
                try:
                    epsg_code = pyproj_crs_obj.to_epsg()
                except Exception:
                    pass
                
                return {
                    'epsg': epsg_code,
                    'wkt': pyproj_crs_obj.to_wkt(),
                    'proj4': pyproj_crs_obj.to_proj4(),
                    'pyproj_crs': pyproj_crs_obj,
                }
        
        except CRSError:
            raise
        except Exception as e:
            raise CRSError(f"Cannot read CRS from {filepath}: {str(e)}")
    
    @staticmethod
    def get_raster_bounds(filepath: str) -> Optional[Dict[str, float]]:
        """
        Read geographic bounds from a raster file.
        
        Returns the bounding box of the full raster extent in its native
        coordinate reference system. Do not assume the coordinates are
        geographic (lat/lon) - they correspond to the raster's CRS.
        
        Args:
            filepath: Path to raster file
        
        Returns:
            Dictionary containing:
            - 'min_x': minimum x coordinate (western/left edge)
            - 'max_x': maximum x coordinate (eastern/right edge)
            - 'min_y': minimum y coordinate (southern/bottom edge)
            - 'max_y': maximum y coordinate (northern/top edge)
            
            Returns None if bounds cannot be determined.
        
        Raises:
            CRSError: If rasterio not available, file not found, or cannot read bounds
        
        Example:
            >>> bounds = CRSHandler.get_raster_bounds("dem.tif")
            >>> print(f"Extent: {bounds['min_x']} to {bounds['max_x']}")
        """
        if not RASTERIO_AVAILABLE:
            raise CRSError(
                "rasterio required for bounds inspection. "
                "Install: pip install rasterio"
            )
        
        if not Path(filepath).exists():
            raise CRSError(f"File not found: {filepath}")
        
        try:
            with rasterio.open(filepath) as src:
                bounds = src.bounds
                
                return {
                    'min_x': bounds.left,
                    'max_x': bounds.right,
                    'min_y': bounds.bottom,
                    'max_y': bounds.top,
                }
        
        except Exception as e:
            raise CRSError(f"Cannot read bounds from {filepath}: {str(e)}")
    
    @staticmethod
    def get_raster_resolution(filepath: str) -> Optional[Tuple[float, float]]:
        """
        Read raster resolution from the geotransform.
        
        Resolution is the pixel size in the raster's native CRS units.
        For geographic CRS (degrees), values are in degrees.
        For projected CRS (meters), values are in meters.
        
        IMPORTANT: Do not assume any specific resolution.
        Always inspect the actual file.
        
        Args:
            filepath: Path to raster file
        
        Returns:
            Tuple of (pixel_width, pixel_height) in absolute value.
            Returns None if resolution cannot be determined.
        
        Raises:
            CRSError: If rasterio not available, file not found, or cannot read resolution
        
        Example:
            >>> res = CRSHandler.get_raster_resolution("dem.tif")
            >>> print(f"Resolution: {res[0]} x {res[1]} units")
        """
        if not RASTERIO_AVAILABLE:
            raise CRSError(
                "rasterio required for resolution inspection. "
                "Install: pip install rasterio"
            )
        
        if not Path(filepath).exists():
            raise CRSError(f"File not found: {filepath}")
        
        try:
            with rasterio.open(filepath) as src:
                res = src.res
                
                if res and len(res) >= 2:
                    # Return absolute values (pixels are always positive size)
                    return (abs(res[0]), abs(res[1]))
                
                return None
        
        except Exception as e:
            raise CRSError(f"Cannot read resolution from {filepath}: {str(e)}")
    
    @staticmethod
    def get_raster_dimensions(filepath: str) -> Optional[Dict[str, int]]:
        """
        Read raster dimensions in pixels.
        
        Args:
            filepath: Path to raster file
        
        Returns:
            Dictionary containing:
            - 'width': width in pixels (number of columns)
            - 'height': height in pixels (number of rows)
            
            Returns None if dimensions cannot be determined.
        
        Raises:
            CRSError: If rasterio not available, file not found, or cannot read dimensions
        
        Example:
            >>> dims = CRSHandler.get_raster_dimensions("dem.tif")
            >>> print(f"Size: {dims['width']} x {dims['height']} pixels")
        """
        if not RASTERIO_AVAILABLE:
            raise CRSError(
                "rasterio required for dimension inspection. "
                "Install: pip install rasterio"
            )
        
        if not Path(filepath).exists():
            raise CRSError(f"File not found: {filepath}")
        
        try:
            with rasterio.open(filepath) as src:
                return {
                    'width': src.width,
                    'height': src.height,
                }
        
        except Exception as e:
            raise CRSError(f"Cannot read dimensions from {filepath}: {str(e)}")
    
    @staticmethod
    def validate_crs(
        filepath: str,
        expected_crs: Any,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that a raster's CRS matches an expected CRS.
        
        Uses pyproj CRS equality for robust comparison. Does NOT perform
        reprojection - only validation.
        
        IMPORTANT: This method validates structure, not values. It confirms
        that the raster's CRS matches what you expect, but does not transform
        or modify the raster data.
        
        Args:
            filepath: Path to raster file
            expected_crs: Expected CRS. Can be:
                - EPSG code as int (e.g., 4326)
                - EPSG string (e.g., "EPSG:4326")
                - pyproj.CRS object
                - WKT string
                - PROJ4 string
        
        Returns:
            Tuple of (matches: bool, message: str or None)
            - If raster has no CRS: (False, "No CRS found in raster")
            - If CRS matches expected: (True, None)
            - If CRS does not match: (False, descriptive message)
        
        Raises:
            CRSError: If dependencies not available, file not found,
                     or expected_crs is invalid
        
        Example:
            >>> is_valid, msg = CRSHandler.validate_crs(
            ...     "dem.tif",
            ...     expected_crs=4326
            ... )
            >>> if not is_valid:
            ...     print(f"CRS mismatch: {msg}")
        """
        if not RASTERIO_AVAILABLE or not PYPROJ_AVAILABLE:
            raise CRSError(
                "rasterio and pyproj required for CRS validation. "
                "Install: pip install rasterio pyproj"
            )
        
        # Normalize expected CRS to pyproj.CRS
        try:
            expected_normalized = CRSHandler.normalize_crs(expected_crs)
        except CRSError as e:
            raise CRSError(f"Invalid expected CRS: {str(e)}")
        
        # Get raster CRS
        raster_crs_info = CRSHandler.get_raster_crs(filepath)
        
        if raster_crs_info is None:
            return False, "No CRS found in raster"
        
        # Raster CRS is already a normalized pyproj.CRS from get_raster_crs()
        raster_crs_normalized = raster_crs_info['pyproj_crs']
        
        # Compare using pyproj CRS equality (proper comparison, not string matching)
        try:
            if raster_crs_normalized == expected_normalized:
                return True, None
            else:
                # Both are pyproj.CRS objects, get their string representations
                raster_str = raster_crs_normalized.to_string()
                expected_str = expected_normalized.to_string()
                return (
                    False,
                    f"CRS mismatch: raster has {raster_str}, expected {expected_str}"
                )
        
        except Exception as e:
            raise CRSError(f"CRS comparison failed: {str(e)}")
    
    @staticmethod
    def normalize_crs(crs_input: Any) -> pyproj.CRS:
        """
        Normalize a CRS representation to a pyproj.CRS object.
        
        Accepts multiple CRS input formats and returns a normalized
        CRS suitable for comparison and metadata generation.
        
        Args:
            crs_input: CRS in one of these formats:
                - pyproj.CRS object (returned after validation)
                - rasterio CRS object (converted to pyproj.CRS)
                - EPSG code as int (e.g., 4326)
                - EPSG string (e.g., "EPSG:4326")
                - WKT string
                - PROJ4 string
        
        Returns:
            Normalized pyproj.CRS object
        
        Raises:
            CRSError: If input format is invalid or CRS cannot be parsed
        
        Example:
            >>> crs1 = CRSHandler.normalize_crs(4326)
            >>> crs2 = CRSHandler.normalize_crs("EPSG:4326")
            >>> crs3 = CRSHandler.normalize_crs("WKT_STRING...")
            >>> crs1 == crs2 == crs3  # All equal after normalization
        """
        if not PYPROJ_AVAILABLE:
            raise CRSError(
                "pyproj required for CRS normalization. "
                "Install: pip install pyproj"
            )
        
        # If already a pyproj.CRS object, validate and return
        if isinstance(crs_input, pyproj.CRS):
            try:
                # Verify it's valid by accessing properties
                _ = crs_input.to_wkt()
                return crs_input
            except Exception as e:
                raise CRSError(f"Invalid pyproj.CRS object: {str(e)}")
        
        # If rasterio CRS object, convert to pyproj.CRS
        if hasattr(crs_input, 'to_wkt') and hasattr(crs_input, 'to_proj4'):
            # This looks like a rasterio CRS object
            try:
                wkt = crs_input.to_wkt()
                return pyproj.CRS.from_wkt(wkt)
            except Exception as e:
                raise CRSError(
                    f"Cannot convert rasterio CRS to pyproj: {str(e)}"
                )
        
        # If EPSG code as int
        if isinstance(crs_input, int):
            try:
                return pyproj.CRS.from_epsg(crs_input)
            except Exception as e:
                raise CRSError(f"Invalid EPSG code {crs_input}: {str(e)}")
        
        # If string (handles EPSG strings, WKT, PROJ4, etc.)
        if isinstance(crs_input, str):
            try:
                return pyproj.CRS.from_string(crs_input)
            except Exception as e:
                raise CRSError(f"Cannot parse CRS string: {str(e)}")
        
        raise CRSError(
            f"Unsupported CRS format: {type(crs_input).__name__}. "
            f"Provide int, string, pyproj.CRS, or rasterio CRS object."
        )
    
    @staticmethod
    def get_raster_metadata(filepath: str) -> Dict[str, Any]:
        """
        Get comprehensive raster metadata combining CRS, bounds, resolution, dimensions.
        
        Convenient method for gathering all metadata needed for provenance tracking
        and dataset inventory.
        
        Args:
            filepath: Path to raster file
        
        Returns:
            Dictionary containing:
            - 'crs': result from get_raster_crs()
            - 'bounds': result from get_raster_bounds()
            - 'resolution': result from get_raster_resolution()
            - 'dimensions': result from get_raster_dimensions()
        
        Raises:
            CRSError: If rasterio not available, file cannot be read, or metadata cannot be retrieved
        
        Example:
            >>> metadata = CRSHandler.get_raster_metadata("dem.tif")
            >>> print(f"EPSG: {metadata['crs']['epsg']}")
            >>> print(f"Size: {metadata['dimensions']['width']} pixels")
        """
        try:
            crs = CRSHandler.get_raster_crs(filepath)
            bounds = CRSHandler.get_raster_bounds(filepath)
            resolution = CRSHandler.get_raster_resolution(filepath)
            dimensions = CRSHandler.get_raster_dimensions(filepath)
            
            return {
                'crs': crs,
                'bounds': bounds,
                'resolution': resolution,
                'dimensions': dimensions,
            }
        
        except CRSError:
            raise
        except Exception as e:
            raise CRSError(f"Cannot retrieve raster metadata: {str(e)}")