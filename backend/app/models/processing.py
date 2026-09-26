"""
Domain models for FlowSight terrain-processing operations (Phase 1)

This module defines the domain model layer describing processing requests,
individual steps, their results, and overall pipeline state. It records WHAT
was requested or performed, on which datasets, with which parameters and
tools, and with what outcome.

Boundaries:
- Actual execution belongs to processing_service.py, which drives GDAL and
  WhiteboxTools.
- Raster access, validation, and metadata belong to app.data.
- Nothing here performs file I/O, subprocess execution, network access, or
  any geospatial computation.

Phase 1 scope:
    The represented operations cover terrain preparation and its derivatives:
    validation, reprojection, depression filling, slope, D8 flow direction,
    and D8 flow accumulation. Rainfall ingestion and forecasting, runoff,
    flood depth and risk, drainage simulation, machine learning, and safe
    routing are later phases and are deliberately absent.

Provenance:
    Together these models answer what operation ran, on which input, producing
    which output, when, with which tool and version, under which parameters,
    whether it succeeded, and what warnings or errors it raised. That record is
    what makes a processing run reproducible.

Usage:
    from app.models.processing import (
        ProcessingOperation,
        ProcessingPipeline,
        ProcessingRequest,
        ProcessingStatus,
        ProcessingStep,
    )

    request = ProcessingRequest(
        operation=ProcessingOperation.REPROJECTION,
        input_dataset_id="copernicus_glo30_raw",
        parameters={"target_crs": "EPSG:32643"},
    )
"""

import math
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Values permitted inside a JSON-safe parameter dictionary.
_JSON_SCALAR_TYPES = (str, int, float, bool)


class ProcessingStatus(str, Enum):
    """Lifecycle state of a processing operation, step, or pipeline."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProcessingOperation(str, Enum):
    """
    Terrain-processing operations representable in Phase 1.

    These describe the operation only; the tool that carries it out and the
    parameters it uses are recorded separately, so a given operation is not
    tied to one implementation.
    """

    VALIDATION = "validation"
    REPROJECTION = "reprojection"
    DEPRESSION_FILLING = "depression_filling"
    SLOPE = "slope"
    D8_FLOW_DIRECTION = "d8_flow_direction"
    D8_FLOW_ACCUMULATION = "d8_flow_accumulation"


def _reject_blank(value: str) -> str:
    """
    Reject whitespace-only identifiers without altering valid ones.

    Args:
        value: Candidate identifier or name.

    Returns:
        The value unchanged.

    Raises:
        ValueError: If the value contains only whitespace.
    """
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _check_json_safe(value: Any, path: str = "parameters") -> None:
    """
    Verify recursively that a value can be serialized to strict JSON.

    Parameters are logged and persisted alongside results, so a value that
    cannot round-trip through JSON would silently break provenance later.
    NaN and infinity are rejected because they are not valid JSON.

    Args:
        value: Value to inspect.
        path: Dotted path used in error messages.

    Raises:
        ValueError: If the value or any nested value is not JSON-safe.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite; NaN and Infinity are not JSON")
        return

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings, got {type(key).__name__}")
            _check_json_safe(item, f"{path}.{key}")
        return

    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_json_safe(item, f"{path}[{index}]")
        return

    raise ValueError(
        f"{path} must be JSON-safe (str, int, float, bool, None, list, dict), "
        f"got {type(value).__name__}"
    )


def _validate_parameters(value: dict[str, Any]) -> dict[str, Any]:
    """Validate that a parameter dictionary is JSON-safe."""
    _check_json_safe(value)
    return value


def _validate_duration(value: Optional[float]) -> Optional[float]:
    """
    Validate an elapsed-time value.

    Args:
        value: Duration in seconds, or None when not yet known.

    Returns:
        The duration unchanged.

    Raises:
        ValueError: If the duration is non-finite or negative.
    """
    if value is None:
        return None

    if not math.isfinite(value):
        raise ValueError("duration_seconds must be a finite number")

    if value < 0:
        raise ValueError("duration_seconds must not be negative")

    return value


class ProcessingRequest(BaseModel):
    """
    A requested processing operation.

    This records an intention. Creating one runs nothing; processing_service.py
    consumes the request and performs the work.
    """

    model_config = ConfigDict(extra="forbid")

    operation: ProcessingOperation = Field(
        description="The terrain-processing operation being requested.",
    )
    input_dataset_id: str = Field(
        min_length=1,
        description="Identifier of the dataset the operation reads.",
    )
    output_dataset_id: Optional[str] = Field(
        default=None,
        description=(
            "Identifier of the dataset the operation should produce. Omitted "
            "for operations such as validation that produce no new dataset."
        ),
    )
    requested_at: Optional[datetime] = Field(
        default=None,
        description="When the request was made, if known at construction time.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON-safe operation parameters, e.g. {'target_crs': 'EPSG:32643'} "
            "or {'algorithm': 'Horn'}. Keys are not constrained by this model."
        ),
    )
    requested_by: Optional[str] = Field(
        default=None,
        description="Optional identifier of the requester, for audit purposes.",
    )

    @field_validator("input_dataset_id")
    @classmethod
    def _input_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("output_dataset_id", "requested_by")
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _reject_blank(value)
        return value

    @field_validator("parameters")
    @classmethod
    def _parameters_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters(value)


class ProcessingResult(BaseModel):
    """
    Outcome of a processing operation.

    Timestamps are intentionally loose: execution details may arrive
    asynchronously, so a result can be constructed before every field is known.
    A failed result carries its error message rather than discarding it.
    """

    model_config = ConfigDict(extra="forbid")

    operation: ProcessingOperation = Field(
        description="The operation this result describes.",
    )
    status: ProcessingStatus = Field(
        description="Outcome state of the operation.",
    )
    input_dataset_id: str = Field(
        min_length=1,
        description="Identifier of the dataset the operation read.",
    )
    output_dataset_id: Optional[str] = Field(
        default=None,
        description=(
            "Identifier of the dataset produced, when the operation produces one."
        ),
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="When execution began, if recorded.",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="When execution finished, if recorded.",
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        description="Elapsed execution time in seconds, if measured.",
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="Tool that performed the work, e.g. 'GDAL', 'WhiteboxTools'.",
    )
    tool_version: Optional[str] = Field(
        default=None,
        description="Version reported by the tool. Never assumed by this model.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-safe parameters the operation actually ran with.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal messages raised during execution.",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Failure description, when the operation did not succeed.",
    )

    @field_validator("input_dataset_id")
    @classmethod
    def _input_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("output_dataset_id", "tool_name", "tool_version")
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _reject_blank(value)
        return value

    @field_validator("duration_seconds")
    @classmethod
    def _duration_valid(cls, value: Optional[float]) -> Optional[float]:
        return _validate_duration(value)

    @field_validator("parameters")
    @classmethod
    def _parameters_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters(value)

    @model_validator(mode="after")
    def _check_outcome_consistency(self) -> "ProcessingResult":
        """
        Ensure the recorded outcome does not contradict itself.

        A completed operation must not also carry an error message, since that
        would leave the record ambiguous about whether the work succeeded.
        """
        if self.status is ProcessingStatus.COMPLETED and self.error_message is not None:
            raise ValueError(
                "a completed result must not carry an error_message; "
                "use status='failed' instead"
            )

        if self.started_at is not None and self.completed_at is not None:
            if self.completed_at < self.started_at:
                raise ValueError("completed_at must not precede started_at")

        return self

    @property
    def succeeded(self) -> bool:
        """Whether the operation finished successfully."""
        return self.status is ProcessingStatus.COMPLETED


class ProcessingStep(BaseModel):
    """
    A single step within a terrain-processing pipeline.

    Some operations, notably validation, inspect a dataset without producing a
    new one, so output_dataset_id is optional throughout.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(
        min_length=1,
        description="Identifier of this step, unique within its pipeline.",
    )
    operation: ProcessingOperation = Field(
        description="The operation this step performs.",
    )
    input_dataset_id: str = Field(
        min_length=1,
        description="Identifier of the dataset this step reads.",
    )
    output_dataset_id: Optional[str] = Field(
        default=None,
        description="Identifier of the dataset this step produces, when it produces one.",
    )
    status: ProcessingStatus = Field(
        default=ProcessingStatus.PENDING,
        description="Current state of this step.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-safe parameters for this step.",
    )
    tool_name: Optional[str] = Field(
        default=None,
        description="Tool used for this step, e.g. 'GDAL', 'WhiteboxTools'.",
    )
    tool_version: Optional[str] = Field(
        default=None,
        description="Version reported by the tool. Never assumed by this model.",
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="When this step began, if recorded.",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="When this step finished, if recorded.",
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        description="Elapsed time for this step in seconds, if measured.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal messages raised during this step.",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Failure description, when this step did not succeed.",
    )

    @field_validator("step_id", "input_dataset_id")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("output_dataset_id", "tool_name", "tool_version")
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _reject_blank(value)
        return value

    @field_validator("duration_seconds")
    @classmethod
    def _duration_valid(cls, value: Optional[float]) -> Optional[float]:
        return _validate_duration(value)

    @field_validator("parameters")
    @classmethod
    def _parameters_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters(value)

    @model_validator(mode="after")
    def _check_outcome_consistency(self) -> "ProcessingStep":
        """Ensure a completed step does not also record a failure."""
        if self.status is ProcessingStatus.COMPLETED and self.error_message is not None:
            raise ValueError(
                "a completed step must not carry an error_message; "
                "use status='failed' instead"
            )

        if self.started_at is not None and self.completed_at is not None:
            if self.completed_at < self.started_at:
                raise ValueError("completed_at must not precede started_at")

        return self

    def to_result(self) -> ProcessingResult:
        """
        Express this step's recorded outcome as a ProcessingResult.

        Returns:
            ProcessingResult carrying the same operation, datasets, timings,
            tool details, parameters, warnings, and error state.
        """
        return ProcessingResult(
            operation=self.operation,
            status=self.status,
            input_dataset_id=self.input_dataset_id,
            output_dataset_id=self.output_dataset_id,
            started_at=self.started_at,
            completed_at=self.completed_at,
            duration_seconds=self.duration_seconds,
            tool_name=self.tool_name,
            tool_version=self.tool_version,
            parameters=dict(self.parameters),
            warnings=list(self.warnings),
            error_message=self.error_message,
        )


class ProcessingPipeline(BaseModel):
    """
    An ordered terrain-processing workflow and its state.

    The pipeline records the sequence of steps that were requested or carried
    out. It holds no execution logic: processing_service.py advances the state
    as it works through the steps.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(
        min_length=1,
        description="Identifier of this pipeline run.",
    )
    name: str = Field(
        min_length=1,
        description="Human-readable name for this pipeline.",
    )
    status: ProcessingStatus = Field(
        default=ProcessingStatus.PENDING,
        description="Overall state of the pipeline.",
    )
    input_dataset_id: str = Field(
        min_length=1,
        description="Identifier of the dataset the pipeline starts from.",
    )
    output_dataset_id: Optional[str] = Field(
        default=None,
        description="Identifier of the pipeline's final product, when produced.",
    )
    steps: list[ProcessingStep] = Field(
        default_factory=list,
        description="Ordered steps making up this pipeline.",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="When the pipeline record was created, if known.",
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="When execution began, if recorded.",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="When execution finished, if recorded.",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Failure description, when the pipeline did not succeed.",
    )

    @field_validator("pipeline_id", "name", "input_dataset_id")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("output_dataset_id")
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _reject_blank(value)
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> "ProcessingPipeline":
        """
        Ensure the pipeline's recorded state is internally coherent.

        A completed pipeline must not carry an error message or contain a
        failed step, since either would contradict the reported success.
        Step identifiers must be unique so each step remains addressable.
        """
        if self.status is ProcessingStatus.COMPLETED:
            if self.error_message is not None:
                raise ValueError(
                    "a completed pipeline must not carry an error_message; "
                    "use status='failed' instead"
                )

            failed = [
                step.step_id
                for step in self.steps
                if step.status is ProcessingStatus.FAILED
            ]
            if failed:
                raise ValueError(
                    f"a completed pipeline must not contain failed steps: "
                    f"{', '.join(failed)}"
                )

        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id values must be unique within a pipeline")

        if self.started_at is not None and self.completed_at is not None:
            if self.completed_at < self.started_at:
                raise ValueError("completed_at must not precede started_at")

        return self

    @property
    def step_count(self) -> int:
        """Number of steps recorded in this pipeline."""
        return len(self.steps)

    def get_step(self, step_id: str) -> Optional[ProcessingStep]:
        """
        Return a step by identifier.

        Args:
            step_id: Identifier of the step to find.

        Returns:
            The matching ProcessingStep, or None if no step has that identifier.
        """
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def steps_with_status(self, status: ProcessingStatus) -> list[ProcessingStep]:
        """
        Return every step currently in a given state.

        Args:
            status: State to filter by.

        Returns:
            List of matching steps, in pipeline order.
        """
        return [step for step in self.steps if step.status is status]