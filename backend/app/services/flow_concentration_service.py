"""Terrain-derived flow-concentration service for FlowSight.

This service exposes the precomputed BMC flow-concentration indicator
derived from Copernicus GLO-30 D8 flow accumulation.

IMPORTANT SCOPE:
- This is a terrain-derived flow-concentration indicator.
- It is not a municipal drainage network.
- It is not flood depth, inundation extent, or hydraulic capacity.
- Classes are based on empirical BMC accumulation percentiles.
"""

from dataclasses import dataclass
from pathlib import Path

from app.data.loader import DataLoadError, RasterLoader
from app.data.terrain_provider import TerrainProvider


FLOW_CONCENTRATION_FILENAME = (
    "Copernicus_Mumbai_GLO30_mosaic_flow_concentration_bmc.tif"
)

FLOW_CONCENTRATION_VISUALIZATION_FILENAME = (
    "Copernicus_Mumbai_GLO30_mosaic_flow_concentration_bmc.png"
)


class FlowConcentrationServiceError(Exception):
    """Base error for flow-concentration processing."""


class FlowConcentrationDataError(FlowConcentrationServiceError):
    """Raised when the flow-concentration dataset cannot be loaded."""


@dataclass(frozen=True)
class FlowConcentrationSummary:
    """Summary of the terrain-derived flow-concentration indicator."""

    dataset_filename: str
    width: int
    height: int
    class_0_cells: int
    class_1_cells: int
    class_2_cells: int
    class_3_cells: int
    p95_accumulation_cells: float
    p99_accumulation_cells: float
    terrain_source: str
    boundary_source: str
    interpretation: str


class FlowConcentrationService:
    """Expose the precomputed BMC flow-concentration indicator."""

    def __init__(self, data_root: Path | str | None = None) -> None:
        if data_root is None:
            data_root = TerrainProvider().data_root

        self.data_root = Path(data_root)

    @property
    def raster_path(self) -> Path:
        """Path to the processed flow-concentration raster."""
        return (
            self.data_root
            / "processed"
            / "terrain"
            / FLOW_CONCENTRATION_FILENAME
        )

    @property
    def visualization_path(self) -> Path:
        """Path to the processed flow-concentration visualization PNG."""
        return (
            self.data_root
            / "processed"
            / "terrain"
            / FLOW_CONCENTRATION_VISUALIZATION_FILENAME
        )

    def calculate(self) -> FlowConcentrationSummary:
        """Return summary information for the flow-concentration raster."""
        if not self.raster_path.is_file():
            raise FlowConcentrationDataError(
                f"Flow-concentration raster not found: {self.raster_path}"
            )

        try:
            array = RasterLoader.read_band(self.raster_path)
            info = RasterLoader.get_raster_info(self.raster_path)
        except DataLoadError as exc:
            raise FlowConcentrationDataError(str(exc)) from exc

        if array.ndim != 2:
            raise FlowConcentrationDataError(
                "Flow-concentration raster must contain a single 2D band."
            )

        if info["count"] != 1:
            raise FlowConcentrationDataError(
                "Flow-concentration raster must contain exactly one band."
            )

        if info["nodata"] != 0:
            raise FlowConcentrationDataError(
                "Flow-concentration raster must declare NoData as 0."
            )

        invalid_classes = set(array.astype("int64").ravel()) - {0, 1, 2, 3}
        if invalid_classes:
            raise FlowConcentrationDataError(
                "Flow-concentration raster contains unsupported classes: "
                f"{sorted(invalid_classes)}"
            )

        metadata = self._read_metadata()

        return FlowConcentrationSummary(
            dataset_filename=self.raster_path.name,
            width=int(info["width"]),
            height=int(info["height"]),
            class_0_cells=int((array == 0).sum()),
            class_1_cells=int((array == 1).sum()),
            class_2_cells=int((array == 2).sum()),
            class_3_cells=int((array == 3).sum()),
            p95_accumulation_cells=float(
                metadata["p95_accumulation_cells"]
            ),
            p99_accumulation_cells=float(
                metadata["p99_accumulation_cells"]
            ),
            terrain_source="Copernicus GLO-30 D8 flow accumulation",
            boundary_source="BMC_admin_wards.geojson",
            interpretation=(
                "terrain-derived flow-concentration indicator; "
                "not flood depth or drainage network"
            ),
        )

    def _read_metadata(self) -> dict[str, str]:
        """Read and validate the required GeoTIFF metadata tags."""
        try:
            import rasterio

            with rasterio.open(self.raster_path) as src:
                tags = src.tags()
        except (ImportError, rasterio.errors.RasterioError) as exc:
            raise FlowConcentrationDataError(
                f"Cannot read flow-concentration metadata: {exc}"
            ) from exc

        required = (
            "p95_accumulation_cells",
            "p99_accumulation_cells",
        )

        missing = [key for key in required if key not in tags]
        if missing:
            raise FlowConcentrationDataError(
                "Flow-concentration raster is missing required metadata: "
                f"{missing}"
            )

        try:
            float(tags["p95_accumulation_cells"])
            float(tags["p99_accumulation_cells"])
        except ValueError as exc:
            raise FlowConcentrationDataError(
                "Flow-concentration percentile metadata must be numeric."
            ) from exc

        return tags