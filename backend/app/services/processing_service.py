"""
Terrain processing service for FlowSight (Phase 1)

Executes the real Phase 1 terrain-processing pipeline:

    raw DSM
      -> input validation
      -> reprojection to EPSG:32643 (WGS 84 / UTM zone 43N)
      -> depression filling
      -> Horn slope
      -> D8 flow direction
      -> D8 flow accumulation
      -> output validation and provenance

Boundaries:
- Path resolution belongs to app.data.terrain_provider.TerrainProvider.
- Raster validation belongs to app.data.validators.RasterValidator.
- CRS interpretation belongs to app.data.crs.CRSHandler, via the validator.
- Environment detection belongs to app.core.environment.
- Provenance representation belongs to app.data.metadata.DatasetMetadata.
- Pipeline state is represented with app.models.processing.
This service orchestrates those components and invokes GDAL and WhiteboxTools.
It reimplements none of them.

Nothing here is simulated. Every product is written by GDAL or WhiteboxTools
and validated before it is promoted. If the required tools are unavailable the
pipeline fails immediately rather than reporting work it did not do.

Rainfall, runoff, drainage, hydraulics, flood prediction, machine learning, and
routing are later phases and are absent by design.

Atomicity:
    Tools write into a per-pipeline directory under data/working/. A product is
    moved into data/processed/terrain/ only after its validation passes, so a
    half-written raster is never visible as a finished product. When replacing
    an existing product, the old file is displaced only after the replacement
    has validated, and is restored if the move fails. A failed step's working
    output is removed; the raw input is never touched.

Usage:
    from app.services.processing_service import TerrainProcessingService

    service = TerrainProcessingService()
    pipeline = service.process_terrain(
        filename="copernicus_dsm.tif",
        source="Copernicus GLO-30",
    )
    if pipeline.status is ProcessingStatus.COMPLETED:
        print(pipeline.output_dataset_id)
"""

import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

from app.core.environment import EnvironmentStatus, get_environment_report
from app.core.logging import get_logger
from app.data.loader import DataLoadError, RasterLoader
from app.data.metadata import DatasetMetadata, MetadataError
from app.data.terrain_provider import (
    TerrainNotFoundError,
    TerrainProvider,
    TerrainProviderError,
)
from app.data.validators import (
    RasterValidationReport,
    RasterValidator,
    ValidationError,
)
from app.models.processing import (
    ProcessingOperation,
    ProcessingPipeline,
    ProcessingStatus,
    ProcessingStep,
)
from app.models.terrain import TerrainStage

logger = get_logger(__name__)


# Phase 1 processing CRS for the Mumbai pilot: WGS 84 / UTM zone 43N.
# Metric coordinates are required for slope and flow analysis; the raw
# Copernicus DSM arrives in geographic WGS 84.
TARGET_CRS_EPSG: int = 32643
TARGET_CRS_SUFFIX: str = "utm43n"

# Exact set of values WhiteboxTools writes into a D8 pointer raster.
# 0 marks a pit, flat, or cell with no downslope neighbour and is valid.
# This is a categorical encoding, not a numeric range.
D8_POINTER_VALID_VALUES: frozenset[int] = frozenset(
    {0, 1, 2, 4, 8, 16, 32, 64, 128}
)

# gdaldem writes slope in degrees when -p is not supplied.
SLOPE_MIN_DEGREES: float = 0.0
SLOPE_MAX_DEGREES: float = 90.0

TOOL_GDAL: str = "GDAL"
TOOL_WHITEBOX: str = "WhiteboxTools"

DEFAULT_COMMAND_TIMEOUT_SECONDS: int = 3600

# Directories derived from the provider's data root. TerrainProvider owns the
# raw/validated/processed stages; these two sit outside that vocabulary.
WORKING_DIRECTORY_NAME: str = "working"
METADATA_DIRECTORY_NAME: str = "metadata"

# Suffix used to hold a displaced product while its replacement is moved in.
REPLACED_PRODUCT_SUFFIX: str = ".replaced"

# Deterministic product naming, derived from the input stem so every product
# is traceable to its source without consulting a registry.
PRODUCT_SUFFIXES: dict[ProcessingOperation, str] = {
    ProcessingOperation.REPROJECTION: f"_{TARGET_CRS_SUFFIX}",
    ProcessingOperation.DEPRESSION_FILLING: f"_filled_{TARGET_CRS_SUFFIX}",
    ProcessingOperation.SLOPE: f"_slope_deg_{TARGET_CRS_SUFFIX}",
    ProcessingOperation.D8_FLOW_DIRECTION: f"_d8_pointer_{TARGET_CRS_SUFFIX}",
    ProcessingOperation.D8_FLOW_ACCUMULATION: f"_d8_accumulation_{TARGET_CRS_SUFFIX}",
}

# Dataset categories recorded in provenance for each product.
PRODUCT_DATASET_TYPES: dict[ProcessingOperation, str] = {
    ProcessingOperation.REPROJECTION: "terrain_dsm",
    ProcessingOperation.DEPRESSION_FILLING: "terrain_dsm_filled",
    ProcessingOperation.SLOPE: "slope",
    ProcessingOperation.D8_FLOW_DIRECTION: "flow_direction_d8",
    ProcessingOperation.D8_FLOW_ACCUMULATION: "flow_accumulation_d8",
}

# Pixel-value units per product. None where values are categorical.
PRODUCT_UNITS: dict[ProcessingOperation, Optional[str]] = {
    ProcessingOperation.REPROJECTION: "meters",
    ProcessingOperation.DEPRESSION_FILLING: "meters",
    ProcessingOperation.SLOPE: "degrees",
    ProcessingOperation.D8_FLOW_DIRECTION: None,
    ProcessingOperation.D8_FLOW_ACCUMULATION: "cells",
}


class ProcessingServiceError(Exception):
    """Base exception for terrain-processing failures."""


class ProcessingEnvironmentError(ProcessingServiceError):
    """Raised when a required geospatial tool is unavailable."""


class ProcessingInputError(ProcessingServiceError):
    """Raised when the input dataset is missing or unusable."""


class ProcessingExecutionError(ProcessingServiceError):
    """Raised when GDAL or WhiteboxTools failed to produce an output."""


class ProcessingValidationError(ProcessingServiceError):
    """Raised when a produced output failed validation."""


class TerrainProcessingService:
    """
    Orchestrates the Phase 1 terrain-processing pipeline.

    The service owns sequencing, tool invocation, promotion of validated
    outputs, and provenance. It delegates every other concern to the data layer.

    Command execution and the WhiteboxTools instance are both injectable so the
    orchestration can be unit-tested without a real DSM. Injection replaces how
    a tool is invoked, never whether it ran: a test that stubs the runner still
    exercises the same validation and status logic.

    Attributes:
        provider: TerrainProvider used for all path resolution.
        target_epsg: Processing CRS for this pipeline.
    """

    def __init__(
        self,
        provider: Optional[TerrainProvider] = None,
        target_epsg: int = TARGET_CRS_EPSG,
        command_runner: Optional[
            Callable[[Sequence[str], int], "subprocess.CompletedProcess[str]"]
        ] = None,
        whitebox_factory: Optional[Callable[[], Any]] = None,
        command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        """
        Create a processing service.

        Args:
            provider: TerrainProvider to resolve dataset locations. A default
                provider is created when omitted.
            target_epsg: EPSG code of the processing CRS. Defaults to 32643.
            command_runner: Optional replacement for subprocess execution.
            whitebox_factory: Optional factory returning a WhiteboxTools
                instance.
            command_timeout_seconds: Per-command timeout for GDAL invocations.
        """
        self.provider: TerrainProvider = provider or TerrainProvider()
        self.target_epsg: int = target_epsg
        self._command_runner = command_runner
        self._whitebox_factory = whitebox_factory
        self._command_timeout = command_timeout_seconds
        self._whitebox: Any = None

    # ------------------------------------------------------------------
    # Pipeline orchestration
    # ------------------------------------------------------------------

    def process_terrain(
        self,
        filename: str,
        source: str,
        stage: TerrainStage = TerrainStage.RAW,
        pipeline_id: Optional[str] = None,
        pipeline_name: Optional[str] = None,
        expected_input_crs: Optional[Any] = None,
        validate_pixel_values: bool = False,
        overwrite: bool = False,
    ) -> ProcessingPipeline:
        """
        Run the full Phase 1 terrain pipeline against a raw terrain dataset.

        Each stage is validated before the next begins, so a defect is caught
        where it originates rather than propagating into downstream products.
        The pipeline stops at the first failure: continuing would build
        derivatives on top of a raster already known to be wrong.

        Args:
            filename: Name of the input terrain file, relative to its stage
                directory.
            source: Originating dataset or provider, e.g. "Copernicus GLO-30".
                Required rather than defaulted, so provenance records what was
                actually supplied instead of an assumption.
            stage: Stage holding the input. Defaults to raw.
            pipeline_id: Optional identifier. Derived from the input stem and a
                UTC timestamp when omitted, so it stays traceable.
            pipeline_name: Optional human-readable name.
            expected_input_crs: Optional expected CRS of the input. Only checked
                when supplied; the raw DSM's CRS is inspected, never assumed.
            validate_pixel_values: Whether validation reads and scans full pixel
                arrays. Defaults to False: scanning a city-scale raster with
                NumPy at every stage is expensive, and the checks that catch
                structural defects — readability, dimensions, CRS, resolution,
                nodata metadata — do not require it. Enable it for a thorough
                pass over a new or suspect dataset. Regardless of this setting,
                the D8 pointer output is always inspected pixel-by-pixel,
                because its correctness is defined by membership in an exact
                value set that no metadata check can confirm.
            overwrite: Whether existing products of the same name may be
                replaced. Replacement happens only after the new output has
                validated.

        Returns:
            A ProcessingPipeline recording every step attempted, its status,
            timings, tool versions, parameters, warnings, and errors.

        Raises:
            ProcessingInputError: If the input cannot be located.
            ProcessingEnvironmentError: If a required tool is unavailable.
        """
        started_at = self._utc_now()
        source_path = self._resolve_input(filename, stage)
        stem = source_path.stem

        resolved_pipeline_id = pipeline_id or self._default_pipeline_id(stem, started_at)
        input_dataset_id = stem

        logger.info(
            "Terrain pipeline %s starting: input=%s target=EPSG:%d",
            resolved_pipeline_id,
            source_path.name,
            self.target_epsg,
        )

        environment = self._require_environment()
        gdal_version = environment.gdal_cli_version
        whitebox_version = environment.whitebox_version

        processed_dir = self._processed_directory()
        working_dir = self._working_directory(resolved_pipeline_id)
        processed_dir.mkdir(parents=True, exist_ok=True)
        working_dir.mkdir(parents=True, exist_ok=True)

        steps: list[ProcessingStep] = []
        failure: Optional[str] = None
        final_output_id: Optional[str] = None

        try:
            plan = self._build_plan(
                stem=stem,
                source_path=source_path,
                processed_dir=processed_dir,
                working_dir=working_dir,
                input_dataset_id=input_dataset_id,
                expected_input_crs=expected_input_crs,
                validate_pixel_values=validate_pixel_values,
                gdal_version=gdal_version,
                whitebox_version=whitebox_version,
                overwrite=overwrite,
            )

            for descriptor in plan:
                step = self._execute_step(descriptor)
                steps.append(step)

                if step.status is ProcessingStatus.FAILED:
                    failure = step.error_message
                    logger.error(
                        "Terrain pipeline %s failed at step '%s': %s",
                        resolved_pipeline_id,
                        step.step_id,
                        failure,
                    )
                    break

                if step.output_dataset_id is not None:
                    final_output_id = step.output_dataset_id
                    self._write_product_metadata(
                        descriptor=descriptor,
                        source=source,
                        parent_dataset_id=descriptor["input_dataset_id"],
                        step=step,
                    )
        finally:
            self._cleanup_working_directory(working_dir)

        completed_at = self._utc_now()
        succeeded = failure is None and bool(steps)

        pipeline = ProcessingPipeline(
            pipeline_id=resolved_pipeline_id,
            name=pipeline_name or f"Phase 1 terrain pipeline: {stem}",
            status=(
                ProcessingStatus.COMPLETED if succeeded else ProcessingStatus.FAILED
            ),
            input_dataset_id=input_dataset_id,
            output_dataset_id=final_output_id if succeeded else None,
            steps=steps,
            created_at=started_at,
            started_at=started_at,
            completed_at=completed_at,
            error_message=None if succeeded else failure,
        )

        logger.info(
            "Terrain pipeline %s finished: status=%s steps=%d",
            resolved_pipeline_id,
            pipeline.status.value,
            len(steps),
        )
        return pipeline

    def _build_plan(
        self,
        stem: str,
        source_path: Path,
        processed_dir: Path,
        working_dir: Path,
        input_dataset_id: str,
        expected_input_crs: Optional[Any],
        validate_pixel_values: bool,
        gdal_version: Optional[str],
        whitebox_version: Optional[str],
        overwrite: bool,
    ) -> list[dict[str, Any]]:
        """
        Describe the ordered steps of the pipeline.

        Building the plan up front keeps sequencing, naming, and validation
        policy in one readable place rather than scattered through execution.

        Note on ordering: slope is derived from the depression-filled surface,
        following the Phase 1 specification. Filling alters the surface
        specifically to make flow routing continuous, so a workflow that wants
        slope of the unmodified terrain should derive it from the reprojected
        product instead.

        Args:
            stem: Input filename stem, used for deterministic product names.
            source_path: Resolved path of the raw input.
            processed_dir: Destination directory for validated products.
            working_dir: Scratch directory for this pipeline run.
            input_dataset_id: Identifier of the input dataset.
            expected_input_crs: Optional expected CRS of the input.
            validate_pixel_values: Whether validation reads pixel data.
            gdal_version: GDAL version reported by the environment checker.
            whitebox_version: WhiteboxTools version reported by the checker.
            overwrite: Whether existing products may be replaced.

        Returns:
            Ordered list of step descriptors.
        """
        reprojected = self._product_path(processed_dir, stem, ProcessingOperation.REPROJECTION)
        filled = self._product_path(processed_dir, stem, ProcessingOperation.DEPRESSION_FILLING)
        slope = self._product_path(processed_dir, stem, ProcessingOperation.SLOPE)
        pointer = self._product_path(processed_dir, stem, ProcessingOperation.D8_FLOW_DIRECTION)
        accumulation = self._product_path(
            processed_dir, stem, ProcessingOperation.D8_FLOW_ACCUMULATION
        )

        def working_for(destination: Path) -> Path:
            return working_dir / destination.name

        return [
            {
                "step_id": "01_validate_input",
                "operation": ProcessingOperation.VALIDATION,
                "input_dataset_id": input_dataset_id,
                "output_dataset_id": None,
                "source_path": source_path,
                "destination": None,
                "working": None,
                "tool_name": None,
                "tool_version": None,
                "parameters": {
                    "expected_crs": self._describe_crs(expected_input_crs),
                    "check_pixel_values": validate_pixel_values,
                },
                "action": None,
                "validate": lambda path: self._validate_raster(
                    path,
                    expected_crs=expected_input_crs,
                    check_pixel_values=validate_pixel_values,
                ),
                "validate_target": source_path,
                "overwrite": overwrite,
            },
            {
                "step_id": "02_reproject",
                "operation": ProcessingOperation.REPROJECTION,
                "input_dataset_id": input_dataset_id,
                "output_dataset_id": reprojected.stem,
                "source_path": source_path,
                "destination": reprojected,
                "working": working_for(reprojected),
                "tool_name": TOOL_GDAL,
                "tool_version": gdal_version,
                "parameters": {
                    "command": "gdalwarp",
                    "target_crs": f"EPSG:{self.target_epsg}",
                    "resampling": "bilinear",
                    "compression": "NONE",
                },
                "action": lambda src, dst: self.reproject_terrain(src, dst),
                "validate": lambda path: self._validate_raster(
                    path,
                    expected_crs=self.target_epsg,
                    check_pixel_values=validate_pixel_values,
                ),
                "validate_target": None,
                "overwrite": overwrite,
            },
            {
                "step_id": "03_fill_depressions",
                "operation": ProcessingOperation.DEPRESSION_FILLING,
                "input_dataset_id": reprojected.stem,
                "output_dataset_id": filled.stem,
                "source_path": reprojected,
                "destination": filled,
                "working": working_for(filled),
                "tool_name": TOOL_WHITEBOX,
                "tool_version": whitebox_version,
                "parameters": {"tool": "fill_depressions"},
                "action": lambda src, dst: self.fill_depressions(src, dst),
                "validate": lambda path: self._validate_raster(
                    path,
                    expected_crs=self.target_epsg,
                    check_pixel_values=validate_pixel_values,
                ),
                "validate_target": None,
                "overwrite": overwrite,
            },
            {
                "step_id": "04_slope",
                "operation": ProcessingOperation.SLOPE,
                "input_dataset_id": filled.stem,
                "output_dataset_id": slope.stem,
                "source_path": filled,
                "destination": slope,
                "working": working_for(slope),
                "tool_name": TOOL_GDAL,
                "tool_version": gdal_version,
                "parameters": {
                    "command": "gdaldem slope",
                    "algorithm": "Horn",
                    "compute_edges": True,
                    "units": "degrees",
                },
                "action": lambda src, dst: self.calculate_slope(src, dst),
                "validate": lambda path: self._validate_raster(
                    path,
                    expected_crs=self.target_epsg,
                    check_pixel_values=validate_pixel_values,
                    min_value=SLOPE_MIN_DEGREES,
                    max_value=SLOPE_MAX_DEGREES,
                ),
                "validate_target": None,
                "overwrite": overwrite,
            },
            {
                "step_id": "05_d8_pointer",
                "operation": ProcessingOperation.D8_FLOW_DIRECTION,
                "input_dataset_id": filled.stem,
                "output_dataset_id": pointer.stem,
                "source_path": filled,
                "destination": pointer,
                "working": working_for(pointer),
                "tool_name": TOOL_WHITEBOX,
                "tool_version": whitebox_version,
                "parameters": {
                    "tool": "d8_pointer",
                    "valid_values": sorted(D8_POINTER_VALID_VALUES),
                },
                "action": lambda src, dst: self.calculate_d8_pointer(src, dst),
                # Pixel-range checks are meaningless for a categorical raster,
                # so the range validator is skipped and the allowed-value
                # check below is applied instead. That check always reads
                # pixels, independent of validate_pixel_values, because
                # membership in the D8 value set cannot be inferred from
                # metadata.
                "validate": lambda path: self._validate_d8_pointer(path),
                "validate_target": None,
                "overwrite": overwrite,
            },
            {
                "step_id": "06_d8_accumulation",
                "operation": ProcessingOperation.D8_FLOW_ACCUMULATION,
                "input_dataset_id": pointer.stem,
                "output_dataset_id": accumulation.stem,
                "source_path": pointer,
                "destination": accumulation,
                "working": working_for(accumulation),
                "tool_name": TOOL_WHITEBOX,
                "tool_version": whitebox_version,
                "parameters": {
                    "tool": "d8_flow_accumulation",
                    "out_type": "cells",
                    "input_is_pointer": True,
                },
                "action": lambda src, dst: self.calculate_d8_accumulation(src, dst),
                "validate": lambda path: self._validate_raster(
                    path,
                    expected_crs=self.target_epsg,
                    check_pixel_values=validate_pixel_values,
                    min_value=0.0,
                ),
                "validate_target": None,
                "overwrite": overwrite,
            },
        ]

    def _execute_step(self, descriptor: dict[str, Any]) -> ProcessingStep:
        """
        Run one pipeline step and record its outcome.

        Execution, validation, and promotion happen together so a product
        reaches its final location only after it has been checked. A failure at
        any of the three yields a failed step carrying the reason.

        An existing destination is detected up front so a run with
        overwrite=False fails before spending time on the tool. The existing
        file itself is not touched here: replacement is deferred to promotion,
        after the new output has validated.

        Args:
            descriptor: Step descriptor produced by _build_plan.

        Returns:
            A ProcessingStep recording status, timings, and any error.
        """
        step_id: str = descriptor["step_id"]
        operation: ProcessingOperation = descriptor["operation"]
        destination: Optional[Path] = descriptor["destination"]
        working: Optional[Path] = descriptor["working"]
        overwrite: bool = descriptor["overwrite"]
        action = descriptor["action"]
        validate = descriptor["validate"]

        started_at = self._utc_now()
        monotonic_start = time.monotonic()
        warnings: list[str] = []
        error_message: Optional[str] = None
        status = ProcessingStatus.RUNNING

        logger.info("Step %s starting: %s", step_id, operation.value)

        try:
            if destination is not None and destination.exists():
                if not overwrite:
                    raise ProcessingExecutionError(
                        f"Product already exists: {destination}. "
                        f"Pass overwrite=True to replace it."
                    )
                logger.warning(
                    "Existing product %s will be replaced if the new output "
                    "validates.",
                    destination.name,
                )

            if action is None:
                target = descriptor["validate_target"]
            else:
                action(descriptor["source_path"], working)
                target = working

            warnings = validate(target)

            if working is not None and destination is not None:
                self._promote(working, destination, overwrite=overwrite)

            status = ProcessingStatus.COMPLETED

        except ProcessingServiceError as exc:
            status = ProcessingStatus.FAILED
            error_message = str(exc)
            self._discard_incomplete_output(working)
        except (DataLoadError, ValidationError, OSError) as exc:
            status = ProcessingStatus.FAILED
            error_message = f"{operation.value} failed: {exc}"
            logger.exception("Step %s raised an unexpected error", step_id)
            self._discard_incomplete_output(working)

        completed_at = self._utc_now()
        duration = max(0.0, time.monotonic() - monotonic_start)

        if status is ProcessingStatus.COMPLETED:
            logger.info(
                "Step %s completed in %.2fs (%d warning(s))",
                step_id,
                duration,
                len(warnings),
            )

        return ProcessingStep(
            step_id=step_id,
            operation=operation,
            input_dataset_id=descriptor["input_dataset_id"],
            output_dataset_id=(
                descriptor["output_dataset_id"]
                if status is ProcessingStatus.COMPLETED
                else None
            ),
            status=status,
            parameters=descriptor["parameters"],
            tool_name=descriptor["tool_name"],
            tool_version=descriptor["tool_version"],
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            warnings=warnings,
            error_message=error_message,
        )

    # ------------------------------------------------------------------
    # Tool invocation
    # ------------------------------------------------------------------

    def reproject_terrain(self, source: Path, destination: Path) -> Path:
        """
        Reproject a raster to the processing CRS using gdalwarp.

        GDAL performs the coordinate transformation and resampling; pixel
        coordinates are never transformed by hand.

        Written as an uncompressed, tiled GeoTIFF deliberately: the reprojected
        raster is the direct input to WhiteboxTools' fill_depressions, and
        WhiteboxTools' GeoTIFF reader does not support the floating-point
        predictor (PREDICTOR=3) that a DEFLATE-compressed float raster would
        otherwise use, causing fill_depressions to report success while
        writing no output. COMPRESS=NONE avoids that predictor entirely.

        Args:
            source: Input raster.
            destination: Output raster. The source is never modified.

        Returns:
            The destination path.

        Raises:
            ProcessingExecutionError: If gdalwarp failed or wrote no output.
        """
        args = [
            "gdalwarp",
            "-t_srs",
            f"EPSG:{self.target_epsg}",
            "-r",
            "bilinear",
            "-of",
            "GTiff",
            "-co",
            "TILED=YES",
            "-co",
            "BLOCKXSIZE=256",
            "-co",
            "BLOCKYSIZE=256",
            "-co",
            "COMPRESS=NONE",
            "-overwrite",
            str(source.resolve()),
            str(destination.resolve()),
        ]
        self._run_gdal(args, operation="reprojection")
        return self._require_output(destination, "gdalwarp")

    def calculate_slope(self, source: Path, destination: Path) -> Path:
        """
        Derive slope in degrees using gdaldem with the Horn algorithm.

        -compute_edges is passed deliberately: without it gdaldem leaves a
        nodata border one pixel wide, which would appear downstream as missing
        terrain at the study-area boundary. No -s scale factor is used because
        the input is already projected in metres.

        Args:
            source: Input elevation raster in the processing CRS.
            destination: Output slope raster.

        Returns:
            The destination path.

        Raises:
            ProcessingExecutionError: If gdaldem failed or wrote no output.
        """
        args = [
            "gdaldem",
            "slope",
            str(source.resolve()),
            str(destination.resolve()),
            "-of",
            "GTiff",
            "-alg",
            "Horn",
            "-compute_edges",
            "-co",
            "COMPRESS=DEFLATE",
        ]
        self._run_gdal(args, operation="slope")
        return self._require_output(destination, "gdaldem slope")

    def fill_depressions(self, source: Path, destination: Path) -> Path:
        """
        Fill surface depressions using WhiteboxTools.

        Args:
            source: Input elevation raster.
            destination: Output filled raster.

        Returns:
            The destination path.

        Raises:
            ProcessingExecutionError: If the tool failed or wrote no output.
            ProcessingEnvironmentError: If WhiteboxTools is unavailable.
        """
        wbt = self._get_whitebox()
        self._run_whitebox(
            lambda: wbt.fill_depressions(
                str(source.resolve()), str(destination.resolve())
            ),
            tool="fill_depressions",
        )
        return self._require_output(destination, "fill_depressions")

    def calculate_d8_pointer(self, source: Path, destination: Path) -> Path:
        """
        Derive a D8 flow-direction pointer using WhiteboxTools.

        Output values use Whitebox's own D8 encoding; esri_pntr is left at its
        default so the values match D8_POINTER_VALID_VALUES.

        Args:
            source: Input depression-filled elevation raster.
            destination: Output pointer raster.

        Returns:
            The destination path.

        Raises:
            ProcessingExecutionError: If the tool failed or wrote no output.
            ProcessingEnvironmentError: If WhiteboxTools is unavailable.
        """
        wbt = self._get_whitebox()
        self._run_whitebox(
            lambda: wbt.d8_pointer(
                str(source.resolve()), str(destination.resolve())
            ),
            tool="d8_pointer",
        )
        return self._require_output(destination, "d8_pointer")

    def calculate_d8_accumulation(self, pointer: Path, destination: Path) -> Path:
        """
        Derive D8 flow accumulation from an existing pointer raster.

        pntr=True tells WhiteboxTools the input is already a D8 pointer rather
        than a DEM. Omitting it would make the tool interpret pointer codes as
        elevations and silently produce a meaningless result, so the flag is
        not optional here.

        Args:
            pointer: Input D8 pointer raster.
            destination: Output accumulation raster, in cell counts.

        Returns:
            The destination path.

        Raises:
            ProcessingExecutionError: If the tool failed or wrote no output.
            ProcessingEnvironmentError: If WhiteboxTools is unavailable.
        """
        wbt = self._get_whitebox()
        self._run_whitebox(
            lambda: wbt.d8_flow_accumulation(
                str(pointer.resolve()),
                str(destination.resolve()),
                out_type="cells",
                pntr=True,
            ),
            tool="d8_flow_accumulation",
        )
        return self._require_output(destination, "d8_flow_accumulation")

    def _run_gdal(self, args: Sequence[str], operation: str) -> None:
        """
        Execute a GDAL command and fail loudly on a non-zero return code.

        The argument list is passed directly to the process: no shell is
        involved, so filenames cannot be reinterpreted as shell syntax.

        Args:
            args: Full argument list, beginning with the executable name.
            operation: Operation name used in error messages.

        Raises:
            ProcessingExecutionError: If the command failed, timed out, or the
                executable was not found.
        """
        logger.debug("Invoking: %s", " ".join(args))

        try:
            if self._command_runner is not None:
                result = self._command_runner(args, self._command_timeout)
            else:
                result = subprocess.run(
                    list(args),
                    capture_output=True,
                    text=True,
                    check=False,
                    shell=False,
                    timeout=self._command_timeout,
                )
        except FileNotFoundError as exc:
            raise ProcessingExecutionError(
                f"{operation} failed: '{args[0]}' not found. "
                f"Install the GDAL command-line tools."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProcessingExecutionError(
                f"{operation} timed out after {self._command_timeout}s"
            ) from exc
        except OSError as exc:
            raise ProcessingExecutionError(f"{operation} could not start: {exc}") from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.error("%s failed (exit %d): %s", args[0], result.returncode, stderr)
            raise ProcessingExecutionError(
                f"{operation} failed: {args[0]} exited with "
                f"{result.returncode}: {stderr or 'no stderr output'}"
            )

    def _run_whitebox(self, invocation: Callable[[], Any], tool: str) -> None:
        """
        Execute a WhiteboxTools call and fail loudly on a non-zero return code.

        The wrapper returns an integer return code; some versions return None.
        Both are handled, and any exception from the wrapper is treated as a
        failure rather than being absorbed.

        Args:
            invocation: Zero-argument callable performing the tool call.
            tool: Tool name used in error messages.

        Raises:
            ProcessingExecutionError: If the tool reported failure or raised.
        """
        logger.debug("Invoking WhiteboxTools: %s", tool)

        try:
            returncode = invocation()
        except TypeError as exc:
            raise ProcessingExecutionError(
                f"WhiteboxTools {tool} rejected its arguments: {exc}. "
                f"Verify the installed whitebox package signature."
            ) from exc
        except Exception as exc:
            raise ProcessingExecutionError(
                f"WhiteboxTools {tool} raised: {exc}"
            ) from exc

        if returncode is not None and returncode != 0:
            raise ProcessingExecutionError(
                f"WhiteboxTools {tool} failed with return code {returncode}"
            )

    def _get_whitebox(self) -> Any:
        """
        Return a WhiteboxTools instance, creating one on first use.

        Verbose output is disabled so tool progress does not flood the logs.

        Returns:
            A WhiteboxTools instance.

        Raises:
            ProcessingEnvironmentError: If the package cannot be imported.
        """
        if self._whitebox is not None:
            return self._whitebox

        if self._whitebox_factory is not None:
            self._whitebox = self._whitebox_factory()
            return self._whitebox

        try:
            import whitebox
        except ImportError as exc:
            raise ProcessingEnvironmentError(
                "WhiteboxTools is not available. Install: pip install whitebox==2.3.6"
            ) from exc

        instance = whitebox.WhiteboxTools()
        instance.verbose = False
        self._whitebox = instance
        return instance

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_raster(
        self,
        path: Path,
        expected_crs: Optional[Any] = None,
        check_pixel_values: bool = False,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
    ) -> list[str]:
        """
        Validate a raster using the data layer's validator.

        No elevation range is imposed. Ranges are supplied only where the
        producing tool defines one, such as slope in degrees or a non-negative
        accumulation count, and they apply only when pixel checks are enabled.

        Args:
            path: Raster to validate.
            expected_crs: Optional expected CRS.
            check_pixel_values: Whether to read pixel data. Defaults to False
                so structural checks stay cheap on large rasters.
            min_value: Optional lower bound on valid pixel values.
            max_value: Optional upper bound on valid pixel values.

        Returns:
            Warnings raised during validation.

        Raises:
            ProcessingValidationError: If any check failed.
        """
        try:
            report: RasterValidationReport = RasterValidator.validate_terrain_raster(
                path,
                expected_crs=expected_crs,
                min_value=min_value,
                max_value=max_value,
                check_pixel_values=check_pixel_values,
            )
        except ValidationError as exc:
            raise ProcessingValidationError(
                f"Validation could not run for {path.name}: {exc}"
            ) from exc

        if not report.valid:
            raise ProcessingValidationError(
                f"{path.name} failed validation: {'; '.join(report.errors)}"
            )

        return list(report.warnings)

    def _validate_d8_pointer(self, path: Path) -> list[str]:
        """
        Validate a D8 pointer raster's structure and categorical values.

        Structural, CRS, and resolution checks are delegated to the validator;
        pixel-range checking is skipped because the values are a code, not a
        measurement. The allowed-value check below always reads pixels, since
        a pointer raster is only correct if every value belongs to the D8 set,
        and no amount of metadata inspection can establish that.

        Args:
            path: D8 pointer raster.

        Returns:
            Warnings raised during validation.

        Raises:
            ProcessingValidationError: If any check failed.
        """
        warnings = self._validate_raster(
            path,
            expected_crs=self.target_epsg,
            check_pixel_values=False,
        )
        warnings.extend(
            self._validate_categorical_values(path, D8_POINTER_VALID_VALUES)
        )
        return warnings

    def _validate_categorical_values(
        self,
        path: Path,
        allowed: frozenset[int],
        band: int = 1,
    ) -> list[str]:
        """
        Check that a raster band contains only an allowed set of values.

        This is a general categorical check, added because the data-layer
        validator supports range and finiteness checks but not membership.
        Pixels equal to the band's declared nodata value are excluded: nodata
        is an absence of data, not an invalid code.

        Args:
            path: Raster to check.
            allowed: The complete set of permitted values.
            band: 1-based band index.

        Returns:
            Warnings raised during the check.

        Raises:
            ProcessingValidationError: If numpy is unavailable, the raster
                cannot be read, or unexpected values are present.
        """
        if not NUMPY_AVAILABLE:
            raise ProcessingValidationError(
                "numpy is required to validate categorical raster values."
            )

        try:
            info = RasterLoader.get_raster_info(path)
            array = RasterLoader.read_band(path, band=band)
        except DataLoadError as exc:
            raise ProcessingValidationError(
                f"Cannot read {path.name} for value validation: {exc}"
            ) from exc

        nodata = info.get("nodata")
        mask = np.isfinite(array) if np.issubdtype(array.dtype, np.floating) else None
        data = array[mask] if mask is not None else array

        if nodata is not None:
            data = data[data != nodata]

        if data.size == 0:
            raise ProcessingValidationError(
                f"{path.name} contains no valid pixels to validate."
            )

        present = {int(value) for value in np.unique(data)}
        unexpected = sorted(present - allowed)

        if unexpected:
            raise ProcessingValidationError(
                f"{path.name} contains values outside the allowed set "
                f"{sorted(allowed)}: {unexpected}"
            )

        warnings: list[str] = []
        if nodata is None:
            warnings.append(
                f"{path.name} declares no nodata value; every pixel was treated "
                f"as data during value validation."
            )

        logger.debug(
            "%s categorical check passed: %d distinct value(s)",
            path.name,
            len(present),
        )
        return warnings

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def _write_product_metadata(
        self,
        descriptor: dict[str, Any],
        source: str,
        parent_dataset_id: str,
        step: ProcessingStep,
    ) -> None:
        """
        Record provenance for a completed product.

        Spatial fields are read from the product itself by the metadata layer.
        Nothing is invented: no acquisition date is assigned, and the source is
        the value the caller supplied. A metadata failure is logged rather than
        failing the pipeline, since the product itself is already valid.

        Args:
            descriptor: Step descriptor for the completed step.
            source: Originating dataset or provider.
            parent_dataset_id: Identifier of the dataset this product came from.
            step: The completed processing step.
        """
        destination: Optional[Path] = descriptor["destination"]
        dataset_id: Optional[str] = descriptor["output_dataset_id"]

        if destination is None or dataset_id is None:
            return

        operation: ProcessingOperation = descriptor["operation"]

        try:
            nodata = RasterLoader.get_raster_info(destination).get("nodata")

            record = DatasetMetadata.from_raster(
                filepath=str(destination),
                dataset_id=dataset_id,
                dataset_name=destination.stem,
                source=source,
                dataset_type=PRODUCT_DATASET_TYPES[operation],
                processing_status="processed",
                parent_dataset_id=parent_dataset_id,
                file_format="GTiff",
                nodata_value=nodata,
                units=PRODUCT_UNITS[operation],
            )
            record.add_processing_step(
                step=operation.value,
                description=f"FlowSight Phase 1 {operation.value}",
                tool=step.tool_name,
                version=step.tool_version,
                parameters=descriptor["parameters"],
            )

            metadata_dir = self._metadata_directory()
            metadata_dir.mkdir(parents=True, exist_ok=True)
            (metadata_dir / f"{dataset_id}.json").write_text(record.to_json())

            logger.debug("Wrote provenance for %s", dataset_id)

        except (MetadataError, DataLoadError, OSError) as exc:
            logger.error("Could not record provenance for %s: %s", dataset_id, exc)

    # ------------------------------------------------------------------
    # Environment and paths
    # ------------------------------------------------------------------

    def _require_environment(self) -> EnvironmentStatus:
        """
        Confirm the tools this pipeline needs are present.

        Args:
            None.

        Returns:
            The environment report, used for tool versions in provenance.

        Raises:
            ProcessingEnvironmentError: If any required tool is missing.
        """
        status = get_environment_report()

        missing: list[str] = []
        if not status.gdal_cli_available:
            missing.append("GDAL command-line tools (gdalwarp, gdaldem)")
        if not status.whitebox_available:
            missing.append("WhiteboxTools")
        if not status.rasterio_available:
            missing.append("rasterio")
        if not status.numpy_available:
            missing.append("numpy")

        if missing:
            raise ProcessingEnvironmentError(
                "Terrain processing cannot run. Missing: " + ", ".join(missing)
            )

        logger.debug(
            "Environment verified: GDAL=%s WhiteboxTools=%s",
            status.gdal_cli_version,
            status.whitebox_version,
        )
        return status

    def _resolve_input(self, filename: str, stage: TerrainStage) -> Path:
        """
        Resolve the input dataset through the provider.

        Args:
            filename: Name of the terrain file.
            stage: Stage holding the input.

        Returns:
            Path to the existing input raster.

        Raises:
            ProcessingInputError: If the input cannot be located.
        """
        try:
            return Path(self.provider.require_terrain(filename, self._stage_value(stage)))
        except TerrainNotFoundError as exc:
            raise ProcessingInputError(str(exc)) from exc
        except TerrainProviderError as exc:
            raise ProcessingInputError(
                f"Cannot resolve input '{filename}': {exc}"
            ) from exc

    def _processed_directory(self) -> Path:
        """Return the directory holding validated terrain products."""
        return Path(
            self.provider.get_stage_directory(self._stage_value(TerrainStage.PROCESSED))
        )

    def _working_directory(self, pipeline_id: str) -> Path:
        """
        Return the scratch directory for one pipeline run.

        Derived from the provider's data root because working/ is not a terrain
        stage and has no provider accessor.

        Args:
            pipeline_id: Identifier of the pipeline run.

        Returns:
            Path to this run's working directory.
        """
        return self.provider.data_root / WORKING_DIRECTORY_NAME / pipeline_id

    def _metadata_directory(self) -> Path:
        """Return the directory holding provenance records."""
        return self.provider.data_root / METADATA_DIRECTORY_NAME

    # ------------------------------------------------------------------
    # Filesystem helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _promote(working: Path, destination: Path, overwrite: bool = False) -> None:
        """
        Move a validated output into its final location.

        Promotion happens only after validation, so a partially written raster
        never appears in the processed directory.

        When a product already occupies the destination, it is displaced to a
        sidecar rather than deleted, the new output is moved in, and only then
        is the sidecar removed. If the move fails, the sidecar is restored, so
        a failed replacement leaves the previous valid product in place instead
        of destroying it. The sidecar sits in the destination directory so the
        rename stays on one filesystem.

        Args:
            working: Validated output in the working directory.
            destination: Final product location.
            overwrite: Whether an existing product may be replaced.

        Raises:
            ProcessingExecutionError: If the destination is occupied and
                overwrite is False, or if the move failed.
        """
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProcessingExecutionError(
                f"Cannot create destination directory {destination.parent}: {exc}"
            ) from exc

        if not destination.exists():
            try:
                shutil.move(str(working), str(destination))
            except OSError as exc:
                raise ProcessingExecutionError(
                    f"Cannot place product at {destination}: {exc}"
                ) from exc
            return

        if not overwrite:
            raise ProcessingExecutionError(
                f"Product already exists: {destination}. "
                f"Pass overwrite=True to replace it."
            )

        sidecar = destination.with_name(destination.name + REPLACED_PRODUCT_SUFFIX)

        try:
            if sidecar.exists():
                sidecar.unlink()
            destination.rename(sidecar)
        except OSError as exc:
            raise ProcessingExecutionError(
                f"Cannot set aside existing product {destination}: {exc}. "
                f"The existing product was left unchanged."
            ) from exc

        try:
            shutil.move(str(working), str(destination))
        except OSError as exc:
            try:
                sidecar.rename(destination)
                restored = "The previous product was restored."
            except OSError as restore_exc:
                restored = (
                    f"The previous product could not be restored and remains "
                    f"at {sidecar}: {restore_exc}"
                )
            raise ProcessingExecutionError(
                f"Cannot place product at {destination}: {exc}. {restored}"
            ) from exc

        try:
            sidecar.unlink()
        except OSError as exc:
            logger.warning("Could not remove replaced product %s: %s", sidecar, exc)

        logger.info("Replaced existing product: %s", destination.name)

    @staticmethod
    def _require_output(destination: Path, tool: str) -> Path:
        """
        Confirm a tool actually wrote its output.

        A zero return code is not sufficient evidence that a file exists, so
        the file is checked directly.

        Args:
            destination: Expected output path.
            tool: Tool name used in the error message.

        Returns:
            The destination path.

        Raises:
            ProcessingExecutionError: If the output is missing or empty.
        """
        if not destination.is_file():
            raise ProcessingExecutionError(
                f"{tool} reported success but wrote no output at {destination}"
            )

        if destination.stat().st_size == 0:
            raise ProcessingExecutionError(
                f"{tool} wrote an empty file at {destination}"
            )

        return destination

    @staticmethod
    def _discard_incomplete_output(working: Optional[Path]) -> None:
        """
        Remove the incomplete output of a failed step.

        Only the failed step's working file is removed. Products already
        validated and promoted are left in place, and the raw input is never
        touched.

        Args:
            working: Working output of the failed step, if any.
        """
        if working is None or not working.exists():
            return

        try:
            working.unlink()
            logger.debug("Removed incomplete output: %s", working.name)
        except OSError as exc:
            logger.warning("Could not remove incomplete output %s: %s", working, exc)

    @staticmethod
    def _cleanup_working_directory(working_dir: Path) -> None:
        """
        Remove a pipeline's working directory.

        Args:
            working_dir: Directory to remove.
        """
        if not working_dir.exists():
            return

        try:
            shutil.rmtree(working_dir)
        except OSError as exc:
            logger.warning("Could not remove working directory %s: %s", working_dir, exc)

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _product_path(
        directory: Path,
        stem: str,
        operation: ProcessingOperation,
    ) -> Path:
        """
        Build the deterministic path of a product.

        Args:
            directory: Destination directory.
            stem: Input filename stem.
            operation: Operation producing the file.

        Returns:
            Path to the product.
        """
        return directory / f"{stem}{PRODUCT_SUFFIXES[operation]}.tif"

    @staticmethod
    def _default_pipeline_id(stem: str, moment: datetime) -> str:
        """
        Build a deterministic, traceable pipeline identifier.

        A timestamp is used rather than a random UUID so the identifier ties
        back to both the input and the run.

        Args:
            stem: Input filename stem.
            moment: Pipeline start time.

        Returns:
            Pipeline identifier.
        """
        return f"{stem}_{moment.strftime('%Y%m%dT%H%M%SZ')}"

    @staticmethod
    def _stage_value(stage: TerrainStage) -> str:
        """
        Return a stage as a plain string for the provider.

        The provider defines its own TerrainStage enum. Passing the string
        value keeps this service independent of which enum object the provider
        expects.

        Args:
            stage: A TerrainStage member, or a string.

        Returns:
            The stage's string value.
        """
        return stage.value if isinstance(stage, TerrainStage) else str(stage)

    @staticmethod
    def _describe_crs(expected_crs: Optional[Any]) -> Optional[str]:
        """
        Render an expected-CRS argument as a JSON-safe string for provenance.

        Args:
            expected_crs: Expected CRS as supplied by the caller, or None.

        Returns:
            String description, or None when no CRS was supplied.
        """
        if expected_crs is None:
            return None
        if isinstance(expected_crs, int):
            return f"EPSG:{expected_crs}"
        return str(expected_crs)

    @staticmethod
    def _utc_now() -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        return datetime.now(timezone.utc)