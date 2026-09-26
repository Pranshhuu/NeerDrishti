"""
Terrain product access for FlowSight Phase 1

TerrainProvider gives higher-level services a single, stable way to reach
FlowSight terrain products. It abstracts the on-disk layout of the data
directory and delegates all low-level raster access to RasterLoader.

Supported stages:
    raw        - terrain as obtained from the provider, unmodified
    validated  - terrain that has passed FlowSight validation
    processed  - terrain products produced by the processing pipeline

This module:
- Resolves terrain product paths deterministically
- Reports whether a product exists
- Returns JSON-safe raster information
- Opens raster handles through RasterLoader
- Optionally builds provenance metadata or runs verification on request

This module does NOT:
- Process, reproject, resample, or fill terrain
- Derive slope, D8 flow direction, or D8 flow accumulation
- Download Copernicus or any other external dataset
- Call external APIs or create placeholder terrain data
- Model rainfall, runoff, drainage, flood depth, or routing

Domain note:
    Copernicus GLO-30 is a Digital Surface Model (DSM), not a bare-earth DEM.
    It includes buildings, vegetation, and infrastructure, and at 30 m it is a
    regional geospatial foundation rather than street-level terrain. Higher
    resolution terrain, drainage networks, and rainfall/nowcast layers are
    integrated in later phases.

Usage:
    from app.data.terrain_provider import TerrainProvider, TerrainStage

    provider = TerrainProvider()

    if provider.terrain_exists("copernicus_dsm_utm43n.tif", TerrainStage.PROCESSED):
        info = provider.get_terrain_info(
            "copernicus_dsm_utm43n.tif", TerrainStage.PROCESSED
        )

    with provider.open_terrain("copernicus_dsm_utm43n.tif") as src:
        profile = src.profile
"""

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.core.logging import get_logger
from app.data.loader import RasterLoader, DataLoadError
from app.data.metadata import DatasetMetadata, MetadataError
from app.data.validators import RasterValidator, RasterValidationReport

logger = get_logger(__name__)

# Accepted path types for public methods.
PathLike = Union[str, Path]

# Directory name for the terrain layer inside each stage directory.
TERRAIN_SUBDIRECTORY = "terrain"

# Directory name of the data root relative to the project root.
DATA_DIRECTORY_NAME = "data"


class TerrainProviderError(Exception):
    """
    Raised when a terrain product cannot be located, opened, or described.

    Covers unsupported stages, unsafe or malformed filenames, missing
    products, and raster access failures surfaced by RasterLoader.
    """

    pass


class UnsupportedTerrainStageError(TerrainProviderError):
    """Raised when a requested terrain stage is not one of the known stages."""

    pass


class TerrainNotFoundError(TerrainProviderError):
    """Raised when a terrain product does not exist at its resolved path."""

    pass


class InvalidTerrainFilenameError(TerrainProviderError):
    """
    Raised when a supplied filename cannot be resolved safely.

    Covers absolute paths, parent-directory traversal, empty or
    whitespace-only names, and values of an unsupported type. These are
    client input errors, not path-resolution failures caused by the server
    environment, so callers translate this distinctly from a generic
    TerrainProviderError.
    """

    pass


class TerrainStage(str, Enum):
    """
    Lifecycle stage of a terrain product.

    Each stage maps to its own directory. Products are never mixed between
    stages: a file only reaches 'validated' or 'processed' because a
    validation or processing step placed it there.
    """

    RAW = "raw"
    VALIDATED = "validated"
    PROCESSED = "processed"


# Stage to directory mapping, relative to the data root.
STAGE_DIRECTORIES: Dict[TerrainStage, str] = {
    TerrainStage.RAW: "raw",
    TerrainStage.VALIDATED: "validated",
    TerrainStage.PROCESSED: "processed",
}


def _default_data_root() -> Path:
    """
    Resolve the default data root for the project.

    The FlowSight application settings are consulted first, so a deployment
    that already configures a data directory continues to control it. If no
    such setting is available, the root is derived from this module's location
    (backend/app/data/ -> project root -> data/), which keeps the default
    portable rather than machine-specific.

    Returns:
        Path to the data root directory. The directory is not created here and
        is not required to exist.
    """
    try:
        from app.core import config as app_config
    except ImportError:
        app_config = None

    if app_config is not None:
        settings = getattr(app_config, "settings", None)
        for attribute in ("DATA_ROOT", "DATA_DIR", "data_root", "data_dir"):
            for holder in (settings, app_config):
                if holder is None:
                    continue
                configured = getattr(holder, attribute, None)
                if configured:
                    return Path(configured)

    # backend/app/data/terrain_provider.py -> backend/app/data -> backend/app
    # -> backend -> project root
    module_path = Path(__file__).resolve()
    project_root = module_path.parent.parent.parent.parent
    return project_root / DATA_DIRECTORY_NAME


class TerrainProvider:
    """
    Higher-level accessor for FlowSight terrain products.

    The provider owns path resolution and delegates raster work: RasterLoader
    handles all rasterio access, RasterValidator handles verification, and
    DatasetMetadata handles provenance. Nothing here reads, writes, or
    transforms pixel data.

    Attributes:
        data_root: Root of the FlowSight data directory. Stage directories are
            resolved beneath it.
    """

    def __init__(self, data_root: Optional[PathLike] = None) -> None:
        """
        Create a provider bound to a data root.

        Args:
            data_root: Optional explicit data root. When omitted, the root is
                taken from application settings if configured, otherwise
                derived from the project layout. The directory does not need
                to exist yet.

        Raises:
            TerrainProviderError: If the supplied data root is not a usable path.
        """
        if data_root is None:
            resolved_root = _default_data_root()
        elif isinstance(data_root, (str, Path)):
            resolved_root = Path(data_root)
        else:
            raise TerrainProviderError(
                f"data_root must be a str or Path, got {type(data_root).__name__}."
            )

        self.data_root: Path = resolved_root
        logger.debug("TerrainProvider data root: %s", self.data_root)

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_stage(stage: Union[TerrainStage, str]) -> TerrainStage:
        """
        Normalize a stage argument into a TerrainStage member.

        Args:
            stage: A TerrainStage member or its string value.

        Returns:
            The corresponding TerrainStage.

        Raises:
            UnsupportedTerrainStageError: If the stage is not recognized.
        """
        if isinstance(stage, TerrainStage):
            return stage

        if isinstance(stage, str):
            try:
                return TerrainStage(stage.lower())
            except ValueError:
                pass

        supported = ", ".join(member.value for member in TerrainStage)
        raise UnsupportedTerrainStageError(
            f"Unsupported terrain stage: {stage!r}. Supported stages: {supported}."
        )

    @staticmethod
    def _validate_filename(filename: PathLike) -> str:
        """
        Validate that a filename is safe to resolve beneath a stage directory.

        Absolute paths and parent-directory traversal are rejected so that
        products can only be addressed inside the data root.

        Args:
            filename: Name of the terrain file, optionally with a relative
                subdirectory.

        Returns:
            The validated filename as a string.

        Raises:
            InvalidTerrainFilenameError: If the filename is empty, of an
                unsupported type, absolute, or attempts to escape the stage
                directory.
        """
        if not isinstance(filename, (str, Path)):
            raise InvalidTerrainFilenameError(
                f"Terrain filename must be a str or Path, "
                f"got {type(filename).__name__}."
            )

        text = str(filename).strip()
        if not text:
            raise InvalidTerrainFilenameError("Terrain filename must not be empty.")

        candidate = Path(text)

        if candidate.is_absolute():
            raise InvalidTerrainFilenameError(
                f"Terrain filename must be relative to the stage directory, "
                f"got absolute path: {text}"
            )

        if ".." in candidate.parts:
            raise InvalidTerrainFilenameError(
                f"Terrain filename must not traverse parent directories: {text}"
            )

        return text

    def get_stage_directory(self, stage: Union[TerrainStage, str]) -> Path:
        """
        Return the terrain directory for a stage.

        The directory is not created and does not need to exist.

        Args:
            stage: Terrain stage.

        Returns:
            Path such as <data_root>/processed/terrain.

        Raises:
            UnsupportedTerrainStageError: If the stage is not recognized.
        """
        resolved_stage = self._resolve_stage(stage)
        return self.data_root / STAGE_DIRECTORIES[resolved_stage] / TERRAIN_SUBDIRECTORY

    def get_terrain_path(
        self,
        filename: PathLike,
        stage: Union[TerrainStage, str] = TerrainStage.PROCESSED,
    ) -> Path:
        """
        Resolve the path of a terrain product without requiring it to exist.

        Args:
            filename: Name of the terrain file, relative to the stage directory.
            stage: Terrain stage. Defaults to processed.

        Returns:
            Resolved path to the terrain product.

        Raises:
            UnsupportedTerrainStageError: If the stage is not recognized.
            InvalidTerrainFilenameError: If the filename is unsafe or malformed.
        """
        safe_filename = self._validate_filename(filename)
        return self.get_stage_directory(stage) / safe_filename

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def terrain_exists(
        self,
        filename: PathLike,
        stage: Union[TerrainStage, str] = TerrainStage.PROCESSED,
    ) -> bool:
        """
        Report whether a terrain product exists as a regular file.

        This checks presence only; it does not open the file, so True does not
        guarantee the file is a readable raster.

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage. Defaults to processed.

        Returns:
            True if the product exists and is a regular file.

        Raises:
            UnsupportedTerrainStageError: If the stage is not recognized.
            InvalidTerrainFilenameError: If the filename is unsafe or malformed.
        """
        path = self.get_terrain_path(filename, stage)
        return RasterLoader.raster_exists(path)

    def require_terrain(
        self,
        filename: PathLike,
        stage: Union[TerrainStage, str] = TerrainStage.PROCESSED,
    ) -> Path:
        """
        Resolve a terrain product path and confirm the product is present.

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage. Defaults to processed.

        Returns:
            Path to the existing terrain product.

        Raises:
            UnsupportedTerrainStageError: If the stage is not recognized.
            InvalidTerrainFilenameError: If the filename is unsafe or malformed.
            TerrainNotFoundError: If the product does not exist.
        """
        resolved_stage = self._resolve_stage(stage)
        path = self.get_terrain_path(filename, resolved_stage)

        if not RasterLoader.raster_exists(path):
            raise TerrainNotFoundError(
                f"Terrain product not found at {resolved_stage.value} stage: {path}. "
                f"Place the file in {self.get_stage_directory(resolved_stage)} or run "
                f"the step that produces it. No terrain data is generated here."
            )

        return path

    def list_terrain(
        self,
        stage: Union[TerrainStage, str] = TerrainStage.PROCESSED,
        pattern: str = "*",
    ) -> List[str]:
        """
        List terrain product filenames available at a stage.

        Returns an empty list when the stage directory does not exist yet.
        Files are not opened, so entries are not guaranteed to be readable
        rasters.

        Args:
            stage: Terrain stage. Defaults to processed.
            pattern: Optional glob pattern applied to filenames.

        Returns:
            Sorted list of filenames relative to the stage directory.

        Raises:
            UnsupportedTerrainStageError: If the stage is not recognized.
            TerrainProviderError: If the directory cannot be listed.
        """
        directory = self.get_stage_directory(stage)

        if not directory.is_dir():
            return []

        try:
            entries = [
                str(item.relative_to(directory))
                for item in sorted(directory.glob(pattern))
                if item.is_file()
            ]
        except OSError as exc:
            raise TerrainProviderError(f"Cannot list terrain directory {directory}: {exc}")

        return entries

    # ------------------------------------------------------------------
    # Stage convenience accessors
    # ------------------------------------------------------------------

    def get_raw_terrain(self, filename: PathLike) -> Path:
        """
        Resolve an existing raw terrain product.

        Args:
            filename: Name of the terrain file.

        Returns:
            Path to the existing raw product.

        Raises:
            TerrainNotFoundError: If the product does not exist.
            InvalidTerrainFilenameError: If the filename is unsafe or malformed.
        """
        return self.require_terrain(filename, TerrainStage.RAW)

    def get_validated_terrain(self, filename: PathLike) -> Path:
        """
        Resolve an existing validated terrain product.

        Args:
            filename: Name of the terrain file.

        Returns:
            Path to the existing validated product.

        Raises:
            TerrainNotFoundError: If the product does not exist.
            InvalidTerrainFilenameError: If the filename is unsafe or malformed.
        """
        return self.require_terrain(filename, TerrainStage.VALIDATED)

    def get_processed_terrain(self, filename: PathLike) -> Path:
        """
        Resolve an existing processed terrain product.

        Args:
            filename: Name of the terrain file.

        Returns:
            Path to the existing processed product.

        Raises:
            TerrainNotFoundError: If the product does not exist.
            InvalidTerrainFilenameError: If the filename is unsafe or malformed.
        """
        return self.require_terrain(filename, TerrainStage.PROCESSED)

    # ------------------------------------------------------------------
    # Raster access
    # ------------------------------------------------------------------

    def open_terrain(
        self,
        filename: PathLike,
        stage: Union[TerrainStage, str] = TerrainStage.PROCESSED,
    ) -> Any:
        """
        Open a terrain product for reading.

        No pixel data is read. Access is delegated to RasterLoader.

        IMPORTANT: The caller owns the returned handle and is responsible for
        closing it, normally by using it as a context manager:

            with provider.open_terrain(name) as src:
                ...

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage. Defaults to processed.

        Returns:
            An open rasterio dataset handle.

        Raises:
            TerrainNotFoundError: If the product does not exist.
            TerrainProviderError: If the raster cannot be opened.
        """
        path = self.require_terrain(filename, stage)

        try:
            return RasterLoader.open_raster(path)
        except DataLoadError as exc:
            raise TerrainProviderError(f"Cannot open terrain product {path}: {exc}")

    def get_terrain_info(
        self,
        filename: PathLike,
        stage: Union[TerrainStage, str] = TerrainStage.PROCESSED,
    ) -> Dict[str, Any]:
        """
        Return JSON-safe structural information about a terrain product.

        No pixel data is read. The returned dictionary contains only strings,
        numbers, lists, dictionaries, and None: no rasterio datasets, CRS
        objects, Affine objects, or NumPy arrays.

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage. Defaults to processed.

        Returns:
            Dictionary of raster information from RasterLoader, with the
            resolved 'stage' and 'filename' added for caller convenience.

        Raises:
            TerrainNotFoundError: If the product does not exist.
            TerrainProviderError: If the raster cannot be inspected.
        """
        resolved_stage = self._resolve_stage(stage)
        path = self.require_terrain(filename, resolved_stage)

        try:
            info = RasterLoader.get_raster_info(path)
        except DataLoadError as exc:
            raise TerrainProviderError(f"Cannot inspect terrain product {path}: {exc}")

        info["stage"] = resolved_stage.value
        info["filename"] = str(filename)
        return info

    # ------------------------------------------------------------------
    # Metadata and optional verification
    # ------------------------------------------------------------------

    def build_terrain_metadata(
        self,
        filename: PathLike,
        dataset_id: str,
        dataset_name: str,
        source: str,
        dataset_type: str,
        stage: Union[TerrainStage, str] = TerrainStage.PROCESSED,
        **metadata_fields: Any,
    ) -> DatasetMetadata:
        """
        Build a provenance record for a terrain product.

        Spatial fields are read from the file by DatasetMetadata.from_raster.
        The processing status is taken from the stage, so a product described
        from the raw directory is never labelled as processed. No metadata is
        written to disk here.

        Args:
            filename: Name of the terrain file.
            dataset_id: Stable identifier for the dataset.
            dataset_name: Human-readable dataset name.
            source: Originating dataset or provider, e.g. "Copernicus GLO-30".
            dataset_type: Category of dataset, e.g. "terrain_dsm".
            stage: Terrain stage. Defaults to processed.
            **metadata_fields: Additional DatasetMetadata fields passed through
                unchanged, such as acquisition_date or units.

        Returns:
            Validated DatasetMetadata instance.

        Raises:
            TerrainNotFoundError: If the product does not exist.
            TerrainProviderError: If metadata cannot be produced.
        """
        resolved_stage = self._resolve_stage(stage)
        path = self.require_terrain(filename, resolved_stage)

        try:
            return DatasetMetadata.from_raster(
                filepath=str(path),
                dataset_id=dataset_id,
                dataset_name=dataset_name,
                source=source,
                dataset_type=dataset_type,
                processing_status=resolved_stage.value,
                **metadata_fields,
            )
        except MetadataError as exc:
            raise TerrainProviderError(
                f"Cannot build metadata for terrain product {path}: {exc}"
            )

    def verify_terrain(
        self,
        filename: PathLike,
        stage: Union[TerrainStage, str] = TerrainStage.PROCESSED,
        expected_crs: Optional[Any] = None,
        check_pixel_values: bool = False,
    ) -> RasterValidationReport:
        """
        Run FlowSight validation against a terrain product on request.

        Validation is not part of the provider's core responsibility: this is a
        convenience wrapper so a caller can confirm a product is still usable
        before relying on it. Pixel reads are off by default to keep the check
        cheap for large rasters.

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage. Defaults to processed.
            expected_crs: Optional expected CRS supplied by the caller. Never
                assumed by this provider.
            check_pixel_values: Whether to read and validate pixel data.

        Returns:
            RasterValidationReport describing every executed check.

        Raises:
            TerrainNotFoundError: If the product does not exist.
            InvalidTerrainFilenameError: If the filename is unsafe or malformed.
            UnsupportedTerrainStageError: If the stage is not recognized.
        """
        path = self.require_terrain(filename, stage)

        return RasterValidator.validate_terrain_raster(
            path,
            expected_crs=expected_crs,
            check_pixel_values=check_pixel_values,
        )