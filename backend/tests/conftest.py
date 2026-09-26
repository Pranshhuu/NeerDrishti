"""
Shared pytest fixtures for the FlowSight Phase 1 backend

Provides test infrastructure only: an isolated data root, a TestClient, services
wired against that root, and small synthetic rasters. No assertions live here.

Isolation:
    Every filesystem fixture is built on tmp_path, so tests never read from or
    write to data/raw, data/processed, or any other real project directory.
    pytest removes these automatically after each test.

Synthetic rasters:
    make_raster writes tiny deterministic GeoTIFFs for unit tests that need a
    readable file. These are test fixtures, never production data: a handful of
    pixels of a fixed pattern, unrelated to Mumbai or to Copernicus GLO-30, and
    confined to tmp_path. Nothing here downloads data or touches the network.
    Tests needing real terrain belong in a separate integration suite.

No tool initialization:
    TerrainProcessingService creates its WhiteboxTools instance lazily, on first
    use, so the processing_service fixture starts no external tool. A test that
    must not invoke GDAL or WhiteboxTools should construct the service itself
    with command_runner and whitebox_factory stubs, so the stubbing is visible
    in the test rather than hidden here.
"""

from pathlib import Path
from typing import Any, Callable, Optional

import pytest

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

try:
    import rasterio
    from rasterio.transform import from_origin
    RASTERIO_AVAILABLE = True
except ImportError:
    rasterio = None
    from_origin = None
    RASTERIO_AVAILABLE = False


# ----------------------------------------------------------------------
# Application client
# ----------------------------------------------------------------------


@pytest.fixture
def client():
    """
    Return a TestClient bound to the FastAPI application.

    The app is imported inside the fixture rather than at module scope so that
    collecting this file cannot fail on a missing optional dependency; only
    tests that actually request a client are affected.

    Returns:
        A TestClient for the FlowSight app.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


# ----------------------------------------------------------------------
# Isolated data root
# ----------------------------------------------------------------------


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """
    Create an isolated data root with the Phase 1 directory layout.

    Mirrors the real layout so path resolution behaves identically, without
    touching the project's own data directories.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        Path to the temporary data root.
    """
    root = tmp_path / "data"

    for relative in (
        "raw/terrain",
        "validated/terrain",
        "processed/terrain",
        "metadata",
        "working",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)

    return root


@pytest.fixture
def terrain_provider(data_root: Path):
    """
    Return a TerrainProvider rooted at the temporary data root.

    TerrainProvider accepts an explicit data_root, so isolation needs no
    patching of application code.

    Args:
        data_root: Temporary data root.

    Returns:
        A TerrainProvider for the temporary directory.
    """
    from app.data.terrain_provider import TerrainProvider

    return TerrainProvider(data_root=data_root)


@pytest.fixture
def terrain_service(terrain_provider):
    """
    Return a TerrainService using the isolated provider.

    Args:
        terrain_provider: Provider rooted at the temporary data root.

    Returns:
        A TerrainService for the temporary data root.
    """
    from app.services.terrain_service import TerrainService

    return TerrainService(provider=terrain_provider)


@pytest.fixture
def processing_service(terrain_provider):
    """
    Return a TerrainProcessingService using the isolated provider.

    Construction alone starts no external tool: GDAL is invoked per command and
    the WhiteboxTools instance is created on first use. Any test that would
    reach real tool execution should inject its own command_runner and
    whitebox_factory rather than rely on this fixture.

    Args:
        terrain_provider: Provider rooted at the temporary data root.

    Returns:
        A TerrainProcessingService for the temporary data root.
    """
    from app.services.processing_service import TerrainProcessingService

    return TerrainProcessingService(provider=terrain_provider)


# ----------------------------------------------------------------------
# Synthetic rasters
# ----------------------------------------------------------------------


@pytest.fixture
def make_raster(tmp_path: Path) -> Callable[..., Path]:
    """
    Return a factory that writes a small deterministic GeoTIFF.

    The default is a 4x4 float32 grid holding values 0..15 at a 30-unit pixel
    size — small enough that reads are instant and fixed so assertions are
    reproducible.

    A factory rather than a fixed file, because each test needs a different
    case: a missing CRS, a specific EPSG, a declared nodata value, a non-finite
    pixel, or a categorical band such as a D8 pointer. Every parameter is
    explicit so the test states exactly what it is exercising.

    Args:
        tmp_path: pytest's per-test temporary directory.

    Returns:
        Callable returning the path of the written raster.

    Raises:
        pytest.skip: If rasterio or numpy is unavailable.
    """
    if not RASTERIO_AVAILABLE or not NUMPY_AVAILABLE:
        pytest.skip("rasterio and numpy are required to build synthetic rasters")

    def _make(
        name: str = "synthetic.tif",
        crs: Optional[Any] = "EPSG:32643",
        width: int = 4,
        height: int = 4,
        values: Optional["np.ndarray"] = None,
        nodata: Optional[float] = None,
        dtype: str = "float32",
        origin: tuple[float, float] = (0.0, 0.0),
        pixel_size: float = 30.0,
    ) -> Path:
        """
        Write a synthetic GeoTIFF and return its path.

        Args:
            name: Filename inside the temporary directory.
            crs: CRS to write, or None to omit it entirely.
            width: Raster width in pixels.
            height: Raster height in pixels.
            values: Explicit pixel array. A 0..n gradient is used when omitted.
            nodata: Optional declared nodata value.
            dtype: Band data type.
            origin: (x, y) of the upper-left corner.
            pixel_size: Pixel size in CRS units.

        Returns:
            Path to the written raster.
        """
        if values is None:
            array = np.arange(width * height, dtype=dtype).reshape(height, width)
        else:
            array = np.asarray(values, dtype=dtype).reshape(height, width)

        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)

        profile: dict[str, Any] = {
            "driver": "GTiff",
            "width": width,
            "height": height,
            "count": 1,
            "dtype": dtype,
            "transform": from_origin(origin[0], origin[1], pixel_size, pixel_size),
        }

        if crs is not None:
            profile["crs"] = crs
        if nodata is not None:
            profile["nodata"] = nodata

        with rasterio.open(path, "w", **profile) as dst:
            dst.write(array, 1)

        return path

    return _make


@pytest.fixture
def raw_terrain_file(data_root: Path, make_raster) -> Path:
    """
    Write a synthetic raster into the raw terrain stage.

    Gives provider and service tests an addressable product without requiring a
    real DSM.

    Args:
        data_root: Temporary data root.
        make_raster: Synthetic raster factory.

    Returns:
        Path to the raster inside data_root/raw/terrain.
    """
    source = make_raster(name="test_terrain.tif")
    destination = data_root / "raw" / "terrain" / source.name
    destination.write_bytes(source.read_bytes())
    return destination