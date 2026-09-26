"""
API schemas for FlowSight processing endpoints (Phase 1)

This module defines the request and response contracts for terrain-processing
endpoints: what a client sends to request an operation, and what the API
returns describing steps, results, and pipeline state.

Boundaries:
- Internal domain models live in app.models.processing. Those describe
  FlowSight's own concepts; these schemas describe the API contract. The
  ProcessingStatus and ProcessingOperation enums are reused rather than
  duplicated, so the API vocabulary cannot drift from the internal one.
- Execution belongs to the service layer, which drives GDAL and WhiteboxTools
  and performs domain-model to schema conversion.

Nothing here executes processing. Parameters are configuration recorded for an
operation, not instructions this module interprets: no command is invoked, no
raster is opened, no filesystem or network access occurs.

Phase 1 scope:
    Operations cover terrain preparation and its derivatives only. Rainfall,
    runoff, flood depth and risk, drainage coupling, hydraulic simulation,
    machine learning, and routing belong to later phases.

Usage:
    from app.schemas.processing import ProcessingRequestSchema

    request = ProcessingRequestSchema(
        operation=ProcessingOperation.REPROJECTION,
        input_dataset_id="copernicus_glo30_raw",
        parameters={"target_crs": "EPSG:32643"},
    )
"""

import math
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.processing import ProcessingOperation, ProcessingStatus

__all__ = [
    "ProcessingOperation",
    "ProcessingStatus",
    "ProcessingRequestSchema",
    "ProcessingResultSchema",
    "ProcessingStepSchema",
    "ProcessingPipelineSchema",
    "ProcessingStatusResponse",
]


def _reject_blank(value: str) -> str:
    """
    Reject empty and whitespace-only strings without altering valid values.

    Args:
        value: Candidate string.

    Returns:
        The value unchanged.

    Raises:
        ValueError: If the value contains only whitespace.
    """
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _reject_blank_optional(value: Optional[str]) -> Optional[str]:
    """Apply blank rejection to an optional string, leaving None untouched."""
    if value is not None:
        _reject_blank(value)
    return value


def _check_json_safe(value: Any, path: str) -> None:
    """
    Verify recursively that a value can be serialized to strict JSON.

    Parameters are logged and persisted alongside results, so a value that
    cannot round-trip through JSON would break provenance later. Rejecting the
    value here is deliberate: coercing it with a string fallback would hide an
    internal object rather than refuse it.

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
                raise ValueError(
                    f"{path} keys must be strings, got {type(key).__name__}"
                )
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
    _check_json_safe(value, "parameters")
    return value


def _validate_duration(value: Optional[float]) -> Optional[float]:
    """
    Validate an elapsed-time value.

    Args:
        value: Duration in seconds, or None when execution has not finished.

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


def _check_execution_consistency(
    status: ProcessingStatus,
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
    error_message: Optional[str],
    subject: str,
) -> None:
    """
    Validate that a recorded execution state is internally coherent.

    The rules are deliberately narrow so that legitimate intermediate records
    remain constructible: a pending record may carry no timestamps at all, and
    a running record may have a start but no completion or duration.

    Args:
        status: Reported state.
        started_at: When execution began, if recorded.
        completed_at: When execution finished, if recorded.
        error_message: Failure description, if recorded.
        subject: Noun used in error messages, e.g. "result", "step".

    Raises:
        ValueError: If the combination of fields contradicts itself.
    """
    if status is ProcessingStatus.COMPLETED and error_message is not None:
        raise ValueError(
            f"a completed {subject} must not carry an error_message; "
            f"use status='failed' instead"
        )

    if status is ProcessingStatus.RUNNING and completed_at is not None:
        raise ValueError(
            f"a running {subject} must not have completed_at set"
        )

    if started_at is not None and completed_at is not None:
        if completed_at < started_at:
            raise ValueError("completed_at must not precede started_at")


class ProcessingRequestSchema(BaseModel):
    """
    Client request to perform a terrain-processing operation.

    Submitting a request records an intention; the service layer decides when
    and how to execute it. output_dataset_id is optional because operations
    such as validation inspect a dataset without producing a new one, and
    because the service may assign the output identifier itself.
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
            "Identifier for the dataset to produce. Omit for operations that "
            "produce no new dataset, or to let the service assign one."
        ),
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON-safe operation parameters, e.g. {'target_crs': 'EPSG:32643'} "
            "or {'algorithm': 'Horn'}. Recorded, never interpreted here."
        ),
    )
    requested_at: Optional[datetime] = Field(
        default=None,
        description="When the request was made, if the client records it.",
    )
    requested_by: Optional[str] = Field(
        default=None,
        description="Optional identifier of the requester, for audit purposes.",
    )

    @field_validator("input_dataset_id")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("output_dataset_id", "requested_by")
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _reject_blank_optional(value)

    @field_validator("parameters")
    @classmethod
    def _parameters_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters(value)


class ProcessingResultSchema(BaseModel):
    """
    Outcome of a terrain-processing operation, as returned to clients.

    Timestamps and duration are optional so that a record can be returned
    mid-execution: a running operation legitimately has a start time and
    nothing else. Failures always carry their message rather than being
    reduced to a status alone.
    """

    model_config = ConfigDict(extra="forbid")

    operation: ProcessingOperation = Field(
        description="The operation this result describes.",
    )
    status: ProcessingStatus = Field(
        description="Current state of the operation.",
    )
    input_dataset_id: str = Field(
        min_length=1,
        description="Identifier of the dataset the operation read.",
    )
    output_dataset_id: Optional[str] = Field(
        default=None,
        description="Identifier of the dataset produced, when one was produced.",
    )
    requested_at: Optional[datetime] = Field(
        default=None,
        description="When the operation was requested, if recorded.",
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
        description=(
            "Tool that performed the work, e.g. 'GDAL', 'WhiteboxTools'. "
            "Informational only; never queried by this schema."
        ),
    )
    tool_version: Optional[str] = Field(
        default=None,
        description="Version the tool reported. Never verified here.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-safe parameters the operation ran with.",
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
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @field_validator("output_dataset_id", "tool_name", "tool_version")
    @classmethod
    def _optional_not_blank(cls, value: Optional[str]) -> Optional[str]:
        return _reject_blank_optional(value)

    @field_validator("duration_seconds")
    @classmethod
    def _duration_valid(cls, value: Optional[float]) -> Optional[float]:
        return _validate_duration(value)

    @field_validator("parameters")
    @classmethod
    def _parameters_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> "ProcessingResultSchema":
        """Ensure the reported state does not contradict the recorded fields."""
        _check_execution_consistency(
            status=self.status,
            started_at=self.started_at,
            completed_at=self.completed_at,
            error_message=self.error_message,
            subject="result",
        )
        return self

    @property
    def succeeded(self) -> bool:
        """Whether the operation finished successfully."""
        return self.status is ProcessingStatus.COMPLETED


class ProcessingStepSchema(BaseModel):
    """
    One step within a processing pipeline, as returned to clients.

    Steps follow the same rules as results: intermediate states are
    representable, and a step that produces no dataset simply omits its
    output identifier.
    """

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(
        min_length=1,
        description="Identifier of this step, unique within its pipeline.",
    )
    operation: ProcessingOperation = Field(
        description="The operation this step performs.",
    )
    status: ProcessingStatus = Field(
        description="Current state of this step.",
    )
    input_dataset_id: str = Field(
        min_length=1,
        description="Identifier of the dataset this step reads.",
    )
    output_dataset_id: Optional[str] = Field(
        default=None,
        description="Identifier of the dataset this step produces, if any.",
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
    tool_name: Optional[str] = Field(
        default=None,
        description="Tool used for this step. Informational only.",
    )
    tool_version: Optional[str] = Field(
        default=None,
        description="Version the tool reported. Never verified here.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-safe parameters for this step.",
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
        return _reject_blank_optional(value)

    @field_validator("duration_seconds")
    @classmethod
    def _duration_valid(cls, value: Optional[float]) -> Optional[float]:
        return _validate_duration(value)

    @field_validator("parameters")
    @classmethod
    def _parameters_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_parameters(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> "ProcessingStepSchema":
        """Ensure the reported state does not contradict the recorded fields."""
        _check_execution_consistency(
            status=self.status,
            started_at=self.started_at,
            completed_at=self.completed_at,
            error_message=self.error_message,
            subject="step",
        )
        return self


class ProcessingPipelineSchema(BaseModel):
    """
    A terrain-processing pipeline and its current state.

    A pipeline may legitimately have no steps yet, since the record can be
    created before execution begins. Step identifiers must be unique so each
    step stays addressable by clients tracking progress.
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
    steps: list[ProcessingStepSchema] = Field(
        default_factory=list,
        description="Steps making up this pipeline, in execution order.",
    )
    requested_at: Optional[datetime] = Field(
        default=None,
        description="When the pipeline was requested, if recorded.",
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
        return _reject_blank_optional(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> "ProcessingPipelineSchema":
        """
        Ensure the pipeline's reported state is coherent.

        A completed pipeline cannot also report an error or contain a failed
        step: either would leave the client unable to trust the status. A
        failed pipeline is not forced to carry a message inline, since the
        service may record the failure detail on the step that caused it.
        """
        _check_execution_consistency(
            status=self.status,
            started_at=self.started_at,
            completed_at=self.completed_at,
            error_message=self.error_message,
            subject="pipeline",
        )

        if self.status is ProcessingStatus.COMPLETED:
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

        return self

    @property
    def step_count(self) -> int:
        """Number of steps recorded in this pipeline."""
        return len(self.steps)


class ProcessingStatusResponse(BaseModel):
    """
    Lightweight progress answer for polling a pipeline.

    This exists so a client can track a long-running pipeline without
    retrieving every step's parameters, warnings, and tool details on each
    poll. Clients needing the full record request ProcessingPipelineSchema.
    """

    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(
        min_length=1,
        description="Identifier of the pipeline being reported.",
    )
    status: ProcessingStatus = Field(
        description="Overall state of the pipeline.",
    )
    completed_steps: int = Field(
        ge=0,
        description="Number of steps that have finished successfully.",
    )
    total_steps: int = Field(
        ge=0,
        description="Total number of steps recorded for this pipeline.",
    )
    current_operation: Optional[ProcessingOperation] = Field(
        default=None,
        description="Operation currently running, when one is in progress.",
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

    @field_validator("pipeline_id")
    @classmethod
    def _required_not_blank(cls, value: str) -> str:
        return _reject_blank(value)

    @model_validator(mode="after")
    def _check_consistency(self) -> "ProcessingStatusResponse":
        """Ensure progress counts and state are coherent."""
        _check_execution_consistency(
            status=self.status,
            started_at=self.started_at,
            completed_at=self.completed_at,
            error_message=self.error_message,
            subject="pipeline",
        )

        if self.completed_steps > self.total_steps:
            raise ValueError(
                f"completed_steps ({self.completed_steps}) must not exceed "
                f"total_steps ({self.total_steps})"
            )

        return self