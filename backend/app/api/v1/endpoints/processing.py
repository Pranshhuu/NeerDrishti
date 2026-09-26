"""
Terrain processing endpoints for FlowSight (Phase 1)

Exposes TerrainProcessingService through HTTP.

Synchronous execution:
    TerrainProcessingService.process_terrain runs GDAL and WhiteboxTools
    inline and returns only when the pipeline finishes. This endpoint holds
    the request open for that entire duration, which for a city-scale DSM is
    minutes, not seconds. No background execution is simulated here, because
    the service does not provide any: pretending otherwise would return a
    "started" response for work that had not begun.

    See the deployment note in start_terrain_processing for what this means
    in practice.

No status lookup:
    The service holds no pipeline registry. A ProcessingPipeline exists only
    as the return value of the call that produced it, so there is nothing for
    a status endpoint to look up. One is therefore not provided.

Individual operations (reproject, slope, D8) are not exposed: they take
filesystem paths as arguments, and accepting server paths from clients is not
something this API should do.
"""

from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status as http_status,
)

from app.core.logging import get_logger
from app.models.processing import ProcessingPipeline, ProcessingStatus
from app.models.terrain import TerrainStage
from app.schemas.processing import ProcessingPipelineSchema, ProcessingStepSchema
from app.services.processing_service import (
    ProcessingEnvironmentError,
    ProcessingInputError,
    ProcessingServiceError,
    TerrainProcessingService,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/processing", tags=["processing"])


def get_processing_service() -> TerrainProcessingService:
    """
    Provide a TerrainProcessingService instance.

    Construction performs no environment check and no I/O, so building one per
    request is cheap and keeps the dependency overridable in tests.

    Returns:
        A TerrainProcessingService using the default TerrainProvider.
    """
    return TerrainProcessingService()


def _redact(text: Optional[str], data_root: str) -> Optional[str]:
    """
    Remove the server data root from a diagnostic message.

    Failure messages name the file that could not be written, which is useful
    to keep. The absolute prefix is not, so it is replaced rather than the
    whole message discarded.

    Args:
        text: Message to redact, or None.
        data_root: Absolute data root to remove.

    Returns:
        The message with the data root replaced, or None.
    """
    if text is None:
        return None
    return text.replace(data_root, "<data>")


def _to_pipeline_schema(
    pipeline: ProcessingPipeline,
    data_root: str,
) -> ProcessingPipelineSchema:
    """
    Convert a domain pipeline into its API representation.

    Step parameters are provenance (tool name, algorithm, target CRS) and
    contain no paths, so they pass through unchanged. Error messages may name
    output files and are redacted.

    Args:
        pipeline: The pipeline returned by the service.
        data_root: Absolute data root, removed from messages.

    Returns:
        The API representation of the pipeline.
    """
    steps = [
        ProcessingStepSchema(
            step_id=step.step_id,
            operation=step.operation,
            status=step.status,
            input_dataset_id=step.input_dataset_id,
            output_dataset_id=step.output_dataset_id,
            started_at=step.started_at,
            completed_at=step.completed_at,
            duration_seconds=step.duration_seconds,
            tool_name=step.tool_name,
            tool_version=step.tool_version,
            parameters=step.parameters,
            warnings=[_redact(w, data_root) or "" for w in step.warnings],
            error_message=_redact(step.error_message, data_root),
        )
        for step in pipeline.steps
    ]

    # The domain model records when the pipeline record was created; the API
    # contract calls the same moment requested_at.
    return ProcessingPipelineSchema(
        pipeline_id=pipeline.pipeline_id,
        name=pipeline.name,
        status=pipeline.status,
        input_dataset_id=pipeline.input_dataset_id,
        output_dataset_id=pipeline.output_dataset_id,
        steps=steps,
        requested_at=pipeline.created_at,
        started_at=pipeline.started_at,
        completed_at=pipeline.completed_at,
        error_message=_redact(pipeline.error_message, data_root),
    )


@router.post(
    "/terrain",
    response_model=ProcessingPipelineSchema,
    summary="Run the Phase 1 terrain processing pipeline",
)
def start_terrain_processing(
    response: Response,
    filename: str = Query(
        ...,
        min_length=1,
        description="Input terrain filename, relative to its stage directory.",
    ),
    source: str = Query(
        ...,
        min_length=1,
        description=(
            "Originating dataset or provider, e.g. 'Copernicus GLO-30'. "
            "Required so provenance records what was supplied rather than an "
            "assumption."
        ),
    ),
    stage: TerrainStage = Query(
        default=TerrainStage.RAW,
        description="Lifecycle stage holding the input.",
    ),
    expected_input_crs: Optional[int] = Query(
        default=None,
        description=(
            "Optional EPSG code the input is expected to use. Only checked "
            "when supplied; the input CRS is inspected, never assumed."
        ),
    ),
    validate_pixel_values: bool = Query(
        default=False,
        description=(
            "Scan full pixel arrays during validation. Off by default because "
            "it is expensive at city scale. The D8 pointer output is always "
            "checked pixel-by-pixel regardless, since its correctness is "
            "defined by an exact value set."
        ),
    ),
    overwrite: bool = Query(
        default=False,
        description=(
            "Replace existing products of the same name. Replacement happens "
            "only after the new output has validated."
        ),
    ),
    service: TerrainProcessingService = Depends(get_processing_service),
) -> ProcessingPipelineSchema:
    """
    Run the terrain pipeline: validate, reproject, fill, slope, D8 flow.

    This call blocks until processing finishes. A city-scale DSM takes minutes,
    which exceeds most default proxy and client timeouts, so this endpoint is
    suited to operator-initiated runs rather than interactive use. Moving to
    background execution requires a job store, which Phase 1 does not have.

    A pipeline that fails returns 422 with the full record: every step, its
    status, timings, and the error that stopped it. The HTTP error reflects
    that processing did not succeed, while the body preserves the diagnostics
    needed to find out why.

    Args:
        filename: Input terrain filename.
        source: Originating dataset or provider.
        stage: Stage holding the input. Defaults to raw.
        expected_input_crs: Optional expected EPSG code of the input.
        validate_pixel_values: Whether validation scans pixel arrays.
        overwrite: Whether existing products may be replaced.

    Returns:
        ProcessingPipelineSchema recording every step attempted.

    Raises:
        HTTPException: 404 if the input cannot be found, 503 if a required
            tool is unavailable, 500 for any other processing failure.
    """
    logger.info(
        "Terrain processing requested: filename=%s stage=%s",
        filename,
        stage.value,
    )

    try:
        pipeline = service.process_terrain(
            filename=filename,
            source=source,
            stage=stage,
            expected_input_crs=expected_input_crs,
            validate_pixel_values=validate_pixel_values,
            overwrite=overwrite,
        )
    except ProcessingInputError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ProcessingEnvironmentError as exc:
        logger.error("Terrain processing blocked by environment: %s", exc)
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ProcessingServiceError as exc:
        logger.error("Terrain processing failed: %s", exc)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    result = _to_pipeline_schema(pipeline, str(service.provider.data_root))

    if pipeline.status is not ProcessingStatus.COMPLETED:
        response.status_code = http_status.HTTP_422_UNPROCESSABLE_ENTITY

    return result