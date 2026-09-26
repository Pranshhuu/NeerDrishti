"""
Environment verification for FlowSight Phase 2
Checks GDAL, WhiteboxTools, and other runtime dependencies
"""

import logging
import sys
import subprocess
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class EnvironmentStatus:
    """
    Structured report of runtime environment status.
    
    All fields are populated, but availability flags indicate which
    tools/libraries are actually functional.
    """
    python_version: str
    python_version_str: str  # e.g. "3.12.3"
    
    # GDAL
    gdal_python_available: bool
    gdal_python_version: Optional[str]
    
    gdal_cli_available: bool
    gdal_cli_version: Optional[str]
    
    # rasterio
    rasterio_available: bool
    rasterio_version: Optional[str]
    
    # pyproj
    pyproj_available: bool
    pyproj_version: Optional[str]
    
    # numpy
    numpy_available: bool
    numpy_version: Optional[str]
    
    # WhiteboxTools
    whitebox_available: bool
    whitebox_version: Optional[str]
    
    # Composite status
    all_available: bool
    missing_tools: List[str]  # List of unavailable critical tools
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


class EnvironmentChecker:
    """
    Check availability and versions of geospatial dependencies.
    
    All methods are static and handle failures gracefully.
    No method will raise an exception - failures are reported
    as availability=False with version=None.
    """
    
    @staticmethod
    def get_python_version() -> str:
        """
        Get Python version as string.
        
        Returns:
            Version string like "3.12.3"
        """
        return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    @staticmethod
    def check_gdal_python() -> Tuple[bool, Optional[str]]:
        """
        Check if GDAL Python bindings (osgeo) are available.
        
        Returns:
            Tuple of (available, version_string)
            version_string is None if not available
        """
        try:
            from osgeo import gdal
            
            # Try to get version
            version = None
            if hasattr(gdal, '__version__'):
                version = gdal.__version__
            else:
                # Fallback: try to extract from VersionInfo
                try:
                    version_info = gdal.VersionInfo()
                    # VersionInfo returns something like "GDAL 3.8.1, released 2024/02/01"
                    # Extract just the version number
                    import re
                    match = re.search(r'(\d+\.\d+\.\d+)', version_info)
                    if match:
                        version = match.group(1)
                except Exception:
                    version = "unknown"
            
            return True, version
        
        except ImportError:
            return False, None
        except Exception as e:
            logger.debug(f"Error checking GDAL Python: {e}")
            return False, None
    
    @staticmethod
    def check_gdal_cli() -> Tuple[bool, Optional[str]]:
        """
        Check if GDAL CLI tools are available (gdalwarp, gdaldem, gdalinfo).
        
        Uses subprocess to run gdalinfo --version to detect GDAL CLI.
        
        Returns:
            Tuple of (available, version_string)
            version_string is None if not available
        """
        try:
            result = subprocess.run(
                ["gdalinfo", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                version_output = result.stdout.strip()
                # Output format: "GDAL 3.8.1, released 2024/02/01"
                import re
                match = re.search(r'GDAL (\d+\.\d+\.\d+)', version_output)
                if match:
                    version = match.group(1)
                else:
                    version = version_output
                
                return True, version
            
            return False, None
        
        except FileNotFoundError:
            # gdalinfo not found in PATH
            return False, None
        
        except subprocess.TimeoutExpired:
            logger.debug("gdalinfo check timed out")
            return False, None
        
        except Exception as e:
            logger.debug(f"Error checking GDAL CLI: {e}")
            return False, None
    
    @staticmethod
    def check_rasterio() -> Tuple[bool, Optional[str]]:
        """
        Check if rasterio is available.
        
        Returns:
            Tuple of (available, version_string)
        """
        try:
            import rasterio
            return True, rasterio.__version__
        
        except ImportError:
            return False, None
        
        except Exception as e:
            logger.debug(f"Error checking rasterio: {e}")
            return False, None
    
    @staticmethod
    def check_pyproj() -> Tuple[bool, Optional[str]]:
        """
        Check if pyproj is available.
        
        Returns:
            Tuple of (available, version_string)
        """
        try:
            import pyproj
            return True, pyproj.__version__
        
        except ImportError:
            return False, None
        
        except Exception as e:
            logger.debug(f"Error checking pyproj: {e}")
            return False, None
    
    @staticmethod
    def check_numpy_available() -> Tuple[bool, Optional[str]]:
        """
        Check if numpy is available.
        
        numpy underpins every raster read and validation path, so it is
        treated as a required dependency rather than an optional one.
        
        Returns:
            Tuple of (available, version_string)
        """
        try:
            import numpy
            return True, numpy.__version__
        
        except ImportError:
            return False, None
        
        except Exception as e:
            logger.debug(f"Error checking numpy: {e}")
            return False, None
    
    @staticmethod
    def check_whitebox() -> Tuple[bool, Optional[str]]:
        """
        Check if WhiteboxTools Python package is available and get its version.
        
        WhiteboxTools version is obtained by instantiating the tool and calling
        the version() method.
        
        Returns:
            Tuple of (available, version_string)
            version_string is None if not available
        """
        try:
            import whitebox
            
            # Try to instantiate and get version
            try:
                wbt = whitebox.WhiteboxTools()
                version_output = wbt.version()
                
                # Output format: "WhiteboxTools v2.0.0 by Dr. John B. Lindsay..."
                import re
                match = re.search(r'v(\d+\.\d+\.\d+)', version_output)
                if match:
                    version = match.group(1)
                else:
                    version = "unknown"
            
            except Exception as e:
                logger.debug(f"Error getting WhiteboxTools version: {e}")
                version = "unknown"
            
            return True, version
        
        except ImportError:
            return False, None
        
        except Exception as e:
            logger.debug(f"Error checking WhiteboxTools: {e}")
            return False, None
    
    @staticmethod
    def check_all() -> EnvironmentStatus:
        """
        Check entire environment and return structured status.
        
        This method checks all dependencies and returns a comprehensive
        EnvironmentStatus object. It never raises an exception.
        
        Returns:
            EnvironmentStatus dataclass with all checks completed
        """
        python_ver = EnvironmentChecker.get_python_version()
        
        gdal_py_avail, gdal_py_ver = EnvironmentChecker.check_gdal_python()
        gdal_cli_avail, gdal_cli_ver = EnvironmentChecker.check_gdal_cli()
        rasterio_avail, rasterio_ver = EnvironmentChecker.check_rasterio()
        pyproj_avail, pyproj_ver = EnvironmentChecker.check_pyproj()
        numpy_avail, numpy_ver = EnvironmentChecker.check_numpy_available()
        whitebox_avail, whitebox_ver = EnvironmentChecker.check_whitebox()
        
        # Determine which critical tools are missing
        missing = []
        
        # GDAL is critical for terrain processing
        if not gdal_cli_avail:
            missing.append("GDAL CLI (gdalwarp, gdaldem, gdalinfo)")
        
        # WhiteboxTools is critical for depression filling and flow direction
        if not whitebox_avail:
            missing.append("WhiteboxTools")
        
        # numpy is critical for validation
        if not numpy_avail:
            missing.append("numpy")
        
        # rasterio is important for validation
        if not rasterio_avail:
            missing.append("rasterio")
        
        all_available = len(missing) == 0
        
        status = EnvironmentStatus(
            python_version=python_ver,
            python_version_str=python_ver,
            gdal_python_available=gdal_py_avail,
            gdal_python_version=gdal_py_ver,
            gdal_cli_available=gdal_cli_avail,
            gdal_cli_version=gdal_cli_ver,
            rasterio_available=rasterio_avail,
            rasterio_version=rasterio_ver,
            pyproj_available=pyproj_avail,
            pyproj_version=pyproj_ver,
            numpy_available=numpy_avail,
            numpy_version=numpy_ver,
            whitebox_available=whitebox_avail,
            whitebox_version=whitebox_ver,
            all_available=all_available,
            missing_tools=missing,
        )
        
        return status


def get_environment_report() -> EnvironmentStatus:
    """
    Get a complete environment status report.
    
    This is the primary public function for checking the environment.
    It performs all checks and returns a structured report that can be:
    - Used by CLI to determine if terrain processing is available
    - Serialized to JSON for API responses
    - Logged for debugging
    
    Returns:
        EnvironmentStatus dataclass with complete environment information
    
    Example:
        >>> status = get_environment_report()
        >>> if status.all_available:
        ...     print("Ready for terrain processing")
        ... else:
        ...     print(f"Missing: {status.missing_tools}")
    """
    return EnvironmentChecker.check_all()


def format_environment_report(status: EnvironmentStatus) -> str:
    """
    Format environment status as human-readable report.
    
    Useful for CLI output and logging.
    
    Args:
        status: EnvironmentStatus object from get_environment_report()
    
    Returns:
        Multi-line formatted string
    
    Example:
        >>> status = get_environment_report()
        >>> print(format_environment_report(status))
    """
    lines = []
    lines.append("=" * 70)
    lines.append("FLOWSIGHT ENVIRONMENT STATUS")
    lines.append("=" * 70)
    lines.append("")
    
    lines.append(f"Python:                           {status.python_version_str}")
    lines.append("")
    
    lines.append("GDAL (Geospatial Data Abstraction Library):")
    if status.gdal_python_available:
        lines.append(f"  ✓ Python bindings (osgeo):      {status.gdal_python_version}")
    else:
        lines.append(f"  ✗ Python bindings (osgeo):      NOT AVAILABLE")
    
    if status.gdal_cli_available:
        lines.append(f"  ✓ CLI tools (gdalwarp, gdaldem): {status.gdal_cli_version}")
    else:
        lines.append(f"  ✗ CLI tools (gdalwarp, gdaldem): NOT AVAILABLE")
        lines.append("    Install: GDAL system libraries (see docs/ENVIRONMENT_SETUP.md)")
    
    lines.append("")
    lines.append("WhiteboxTools (Terrain Analysis):")
    if status.whitebox_available:
        lines.append(f"  ✓ Python package:                {status.whitebox_version}")
    else:
        lines.append(f"  ✗ Python package:                NOT AVAILABLE")
        lines.append("    Install: pip install whitebox==2.3.6")
    
    lines.append("")
    lines.append("Validation & CRS Transformation:")
    if status.numpy_available:
        lines.append(f"  ✓ numpy:                         {status.numpy_version}")
    else:
        lines.append(f"  ✗ numpy:                         NOT AVAILABLE")
        lines.append("    Install: pip install numpy")
    
    if status.rasterio_available:
        lines.append(f"  ✓ rasterio:                      {status.rasterio_version}")
    else:
        lines.append(f"  ✗ rasterio:                      NOT AVAILABLE")
        lines.append("    Install: pip install rasterio")
    
    if status.pyproj_available:
        lines.append(f"  ✓ pyproj:                        {status.pyproj_version}")
    else:
        lines.append(f"  ✗ pyproj:                        NOT AVAILABLE")
        lines.append("    Install: pip install pyproj")
    
    lines.append("")
    
    if status.all_available:
        lines.append("✓ ALL DEPENDENCIES MET")
        lines.append("  Terrain processing is available")
    else:
        lines.append("✗ MISSING DEPENDENCIES")
        lines.append("  Terrain processing is NOT available")
        lines.append("")
        lines.append("Missing critical tools:")
        for tool in status.missing_tools:
            lines.append(f"  • {tool}")
    
    lines.append("=" * 70)
    
    return "\n".join(lines)