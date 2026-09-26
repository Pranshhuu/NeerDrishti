"""
Application-level terrain service for FlowSight (Phase 1)

TerrainService is the application-level facade for terrain operations. It
orchestrates the existing data-layer components and gives the API layer a
single, stable entry point.

Boundaries:
- Path resolution, existence checks, and raster access belong to
  app.data.terrain_provider.TerrainProvider. This service never builds
  filesystem paths itself.
- Raster validation belongs to app.data.validators.RasterValidator.
- Metadata representation belongs to app.data.metadata.DatasetMetadata.
- Terrain processing (reprojection, depression filling, slope, D8 flow
  direction and accumulation) belongs to app.services.processing_service.
  Nothing here invokes GDAL or WhiteboxTools.
- HTTP concerns belong to the API layer. This service returns domain objects,
  provider results, and plain dictionaries; it imports no API schemas and no
  web framework, so its return values are never HTTP responses.

Later phases add rainfall, runoff, flood depth and risk, drainage coupling,
and routing. None of that is represented here.

Usage:
    from app.services.terrain_service import TerrainService
    from app.models.terrain import TerrainStage

    service = TerrainService()
    names = service.list_terrain(TerrainStage.PROCESSED)
    info = service.get_terrain_info("copernicus_dsm_utm43n.tif",
                                    TerrainStage.PROCESSED)
"""

from typing import Any, Optional

from app.core.logging import get_logger
from app.data.metadata import DatasetMetadata
from app.data.terrain_provider import (
    InvalidTerrainFilenameError,
    TerrainNotFoundError,
    TerrainProvider,
    TerrainProviderError,
    UnsupportedTerrainStageError,
)
from app.data.validators import RasterValidationReport, ValidationError
from app.models.terrain import TerrainStage

logger = get_logger(__name__)


class TerrainServiceError(Exception):
    """
    Base exception for terrain service failures.

    Data-layer errors are translated into this type so callers depend on one
    service-level contract rather than on which component happened to fail.
    The original exception is always chained so the cause is not lost.
    """

    pass


class TerrainUnavailableError(TerrainServiceError):
    """Raised when a requested terrain dataset is not available."""

    pass


class InvalidTerrainRequestError(TerrainServiceError):
    """Raised when a request names an unknown stage or an unusable filename."""

    pass


class TerrainService:
    """
    Application-level facade over the terrain data layer.

    The service adds orchestration and error translation. It deliberately adds
    no raster logic of its own: every raster operation is delegated to the
    provider, which owns path resolution and access.

    Attributes:
        provider: The TerrainProvider this service delegates to.
    """

    def __init__(self, provider: Optional[TerrainProvider] = None) -> None:
        """
        Create a terrain service.

        Args:
            provider: Optional TerrainProvider to delegate to. When omitted, a
                default provider is created. Injection exists so the service
                can be exercised against a temporary data root without
                touching the real one.
        """
        self.provider: TerrainProvider = provider or TerrainProvider()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_terrain(
        self,
        stage: Optional[TerrainStage] = None,
    ) -> dict[str, list[str]]:
        """
        List terrain products available at one stage or at every stage.

        Results are grouped by stage so a caller can see the whole terrain
        lifecycle in one call, which is the common case for a status view.
        A stage directory that does not exist yet yields an empty list rather
        than an error, since an unpopulated stage is a normal state.

        Args:
            stage: Restrict the listing to one stage. When None, every stage
                is listed.

        Returns:
            Mapping of stage value to the filenames available at that stage.

        Raises:
            InvalidTerrainRequestError: If the stage is not recognized.
            TerrainServiceError: If a stage directory cannot be read.
        """
        stages = [stage] if stage is not None else list(TerrainStage)
        listing: dict[str, list[str]] = {}

        for target in stages:
            try:
                listing[self._stage_value(target)] = self.provider.list_terrain(target)
            except UnsupportedTerrainStageError as exc:
                raise InvalidTerrainRequestError(str(exc)) from exc
            except TerrainProviderError as exc:
                raise TerrainServiceError(
                    f"Cannot list terrain products: {exc}"
                ) from exc

        logger.debug(
            "Listed terrain products for %d stage(s)",
            len(listing),
        )
        return listing

    def terrain_exists(
        self,
        filename: str,
        stage: TerrainStage = TerrainStage.RAW,
    ) -> bool:
        """
        Report whether a terrain product is present at a stage.

        Presence only: the file is not opened, so True does not guarantee the
        product is a readable raster. Use verify_terrain for that.

        Args:
            filename: Name of the terrain file, relative to the stage directory.
            stage: Terrain stage. Defaults to raw.

        Returns:
            True if the product exists.

        Raises:
            InvalidTerrainRequestError: If the stage or filename is unusable.
        """
        try:
            return self.provider.terrain_exists(filename, stage)
        except UnsupportedTerrainStageError as exc:
            raise InvalidTerrainRequestError(str(exc)) from exc
        except TerrainProviderError as exc:
            raise InvalidTerrainRequestError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_terrain_path(
        self,
        filename: str,
        stage: TerrainStage = TerrainStage.RAW,
    ) -> str:
        """
        Resolve the location of an existing terrain product.

        The path is returned as a string for the application layer; the
        provider remains responsible for how it was resolved. The raster is
        not opened.

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage. Defaults to raw.

        Returns:
            Path to the existing terrain product, as a string.

        Raises:
            TerrainUnavailableError: If the product does not exist.
            InvalidTerrainRequestError: If the stage or filename is unusable.
        """
        return str(self._require(filename, stage))

    def get_terrain_info(
        self,
        filename: str,
        stage: TerrainStage = TerrainStage.RAW,
    ) -> dict[str, Any]:
        """
        Return structural information about a terrain product.

        No pixel data is read. The provider returns JSON-safe values, so the
        result passes through to the API layer unchanged.

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage. Defaults to raw.

        Returns:
            Dictionary of raster information, including the resolved stage
            and filename.

        Raises:
            TerrainUnavailableError: If the product does not exist.
            InvalidTerrainRequestError: If the stage or filename is unusable.
            TerrainServiceError: If the product cannot be inspected.
        """
        logger.debug("Terrain info requested: %s (%s)", filename, self._stage_value(stage))

        try:
            return self.provider.get_terrain_info(filename, stage)
        except TerrainNotFoundError as exc:
            raise TerrainUnavailableError(str(exc)) from exc
        except UnsupportedTerrainStageError as exc:
            raise InvalidTerrainRequestError(str(exc)) from exc
        except InvalidTerrainFilenameError as exc:
            raise InvalidTerrainRequestError(str(exc)) from exc
        except TerrainProviderError as exc:
            raise TerrainServiceError(
                f"Cannot inspect terrain product '{filename}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_terrain(
        self,
        filename: str,
        stage: TerrainStage = TerrainStage.RAW,
        expected_crs: Optional[Any] = None,
        validate_values: bool = True,
    ) -> RasterValidationReport:
        """
        Verify that a terrain product is acceptable for FlowSight processing.

        Validation is delegated to the data layer and its report is returned
        unchanged, so a failing check reaches the caller intact rather than
        being reduced to a boolean.

        An expected CRS is only checked when the caller supplies one: this
        service assumes no projection, since the correct CRS depends on the
        pipeline stage and city being processed.

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage. Defaults to raw.
            expected_crs: Optional expected CRS. Never assumed by this service.
            validate_values: Whether to read and validate pixel data. Reading
                pixels is the expensive part of verification, so callers
                checking many products may switch it off.

        Returns:
            The validation report produced by the data layer.

        Raises:
            TerrainUnavailableError: If the product does not exist.
            InvalidTerrainRequestError: If the stage or filename is unusable.
            TerrainServiceError: If verification could not be performed.
        """
        logger.info(
            "Terrain verification requested: %s (%s), pixel checks=%s",
            filename,
            self._stage_value(stage),
            validate_values,
        )

        try:
            report = self.provider.verify_terrain(
                filename,
                stage=stage,
                expected_crs=expected_crs,
                check_pixel_values=validate_values,
            )
        except TerrainNotFoundError as exc:
            raise TerrainUnavailableError(str(exc)) from exc
        except UnsupportedTerrainStageError as exc:
            raise InvalidTerrainRequestError(str(exc)) from exc
        except TerrainProviderError as exc:
            raise TerrainServiceError(
                f"Cannot verify terrain product '{filename}': {exc}"
            ) from exc
        except ValidationError as exc:
            raise TerrainServiceError(
                f"Verification could not run for '{filename}': {exc}"
            ) from exc

        logger.info(
            "Terrain verification result for %s: valid=%s",
            filename,
            report.valid,
        )
        return report

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def build_terrain_metadata(
        self,
        filename: str,
        dataset_id: str,
        dataset_name: str,
        source: str,
        dataset_type: str,
        stage: TerrainStage = TerrainStage.RAW,
        **metadata_fields: Any,
    ) -> DatasetMetadata:
        """
        Build a provenance record for a terrain product.

        Extraction is delegated to the data layer, which reads the spatial
        fields from the file itself. Nothing is written to disk here.

        Args:
            filename: Name of the terrain file.
            dataset_id: Stable identifier for the dataset.
            dataset_name: Human-readable dataset name.
            source: Originating dataset or provider, e.g. "Copernicus GLO-30".
            dataset_type: Category of dataset, e.g. "terrain_dsm".
            stage: Terrain stage. Defaults to raw.
            **metadata_fields: Additional metadata fields passed through
                unchanged, such as acquisition_date or units.

        Returns:
            The provenance record for the product.

        Raises:
            TerrainUnavailableError: If the product does not exist.
            InvalidTerrainRequestError: If the stage or filename is unusable.
            TerrainServiceError: If metadata could not be produced.
        """
        logger.debug(
            "Terrain metadata requested: %s (%s)",
            filename,
            self._stage_value(stage),
        )

        try:
            return self.provider.build_terrain_metadata(
                filename,
                dataset_id=dataset_id,
                dataset_name=dataset_name,
                source=source,
                dataset_type=dataset_type,
                stage=stage,
                **metadata_fields,
            )
        except TerrainNotFoundError as exc:
            raise TerrainUnavailableError(str(exc)) from exc
        except UnsupportedTerrainStageError as exc:
            raise InvalidTerrainRequestError(str(exc)) from exc
        except TerrainProviderError as exc:
            raise TerrainServiceError(
                f"Cannot build metadata for terrain product '{filename}': {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require(self, filename: str, stage: TerrainStage) -> Any:
        """
        Resolve an existing terrain product, translating data-layer errors.

        Args:
            filename: Name of the terrain file.
            stage: Terrain stage.

        Returns:
            The provider's resolved path to the existing product.

        Raises:
            TerrainUnavailableError: If the product does not exist.
            InvalidTerrainRequestError: If the stage or filename is unusable.
        """
        try:
            return self.provider.require_terrain(filename, stage)
        except TerrainNotFoundError as exc:
            raise TerrainUnavailableError(str(exc)) from exc
        except UnsupportedTerrainStageError as exc:
            raise InvalidTerrainRequestError(str(exc)) from exc
        except TerrainProviderError as exc:
            raise InvalidTerrainRequestError(str(exc)) from exc

    @staticmethod
    def _stage_value(stage: TerrainStage) -> str:
        """
        Return the string value of a stage for logging and result keys.

        Args:
            stage: A TerrainStage member, or a plain string if a caller passed
                one through.

        Returns:
            The stage's string value.
        """
        return stage.value if isinstance(stage, TerrainStage) else str(stage)