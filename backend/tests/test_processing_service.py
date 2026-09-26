"""
Processing service tests for FlowSight Phase 1

Exercises app.services.processing_service: whether the service correctly
orchestrates the Phase 1 terrain pipeline, invokes each tool with the right
arguments, validates between stages, and handles failure without leaving a
half-built product behind.

Testing strategy:
    No GDAL command runs and no WhiteboxTools instance is created. The service
    already accepts command_runner and whitebox_factory, so those boundaries
    are replaced while the service's own sequencing, validation, promotion, and
    status logic execute unmodified. The service itself is never mocked.

    Stubs write real GeoTIFFs at the paths the service asks for, so the
    validation the service performs after each stage is genuine validation of a
    genuine file. A stub that only touched an empty file would make every
    validation trivially fail and prove nothing about orchestration.

Scope:
    Orchestration only. CRS behaviour belongs to test_crs.py, raster validation
    to test_validators.py, metadata utilities to test_metadata.py, and path
    resolution to test_terrain_provider.py.

Phase 1 only:
    validate, reproject to EPSG:32643, fill depressions, Horn slope, D8 flow
    direction, D8 flow accumulation. Nothing beyond that is represented.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.models.processing import (
    ProcessingOperation,
    ProcessingPipeline,
    ProcessingStatus,
)
from app.models.terrain import TerrainStage
from app.services import processing_service as processing_module
from app.services.processing_service import (
    D8_POINTER_VALID_VALUES,
    TARGET_CRS_EPSG,
    ProcessingEnvironmentError,
    ProcessingExecutionError,
    ProcessingInputError,
    ProcessingServiceError,
    TerrainProcessingService,
)


# ----------------------------------------------------------------------
# Recording stubs
# ----------------------------------------------------------------------


def _write_raster(
    path: Path,
    values: np.ndarray,
    dtype: str = "float32",
    crs: str = f"EPSG:{TARGET_CRS_EPSG}",
    nodata: float | None = None,
) -> None:
    """
    Write a small GeoTIFF at a path the service requested.

    Args:
        path: Destination, created by the stub standing in for a tool.
        values: Pixel values to write.
        dtype: Band data type.
        crs: CRS to write. Outputs carry the processing CRS.
        nodata: Optional declared nodata value.
    """
    array = np.asarray(values, dtype=dtype)
    height, width = array.shape

    path.parent.mkdir(parents=True, exist_ok=True)

    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": dtype,
        "crs": crs,
        "transform": from_origin(0.0, height * 30.0, 30.0, 30.0),
    }
    if nodata is not None:
        profile["nodata"] = nodata

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)


def _gdal_destination(arguments: list[str]) -> Path:
    """
    Determine the output path a GDAL invocation will write to.

    The two commands this service issues place their destination differently:

        gdalwarp [options] <source> <destination>
        gdaldem slope <source> <destination> [options]

    gdaldem takes its paths as positional arguments before the flags, so its
    destination is not the last element. Assuming otherwise makes a stub write
    to whatever the final flag value happens to be, leave the real destination
    absent, and report success anyway.

    Args:
        arguments: The full argument list, beginning with the executable name.

    Returns:
        The path the command is expected to produce.
    """
    if arguments[0] == "gdaldem":
        return Path(arguments[3])
    return Path(arguments[-1])


class RecordingGdal:
    """
    Stand-in for GDAL command execution that records and writes output.

    Records every argument list the service passes, then writes a real raster
    at the output path so the service's post-stage validation runs against an
    actual file.
    """

    def __init__(self, fail_on: str | None = None, timeout_on: str | None = None):
        """
        Args:
            fail_on: Substring of a command to fail with a non-zero exit code.
            timeout_on: Substring of a command to fail with a timeout.
        """
        self.calls: list[list[str]] = []
        self.timeouts: list[int] = []
        self._fail_on = fail_on
        self._timeout_on = timeout_on

    def __call__(self, args, timeout):
        """Record the invocation and produce the requested output."""
        arguments = list(args)
        self.calls.append(arguments)
        self.timeouts.append(timeout)

        command = " ".join(arguments)

        if self._timeout_on and self._timeout_on in command:
            raise subprocess.TimeoutExpired(cmd=arguments, timeout=timeout)

        if self._fail_on and self._fail_on in command:
            return subprocess.CompletedProcess(
                args=arguments,
                returncode=1,
                stdout="",
                stderr=f"simulated failure in {self._fail_on}",
            )

        destination = _gdal_destination(arguments)

        if arguments[0] == "gdaldem":
            # Slope in degrees, within the range the service validates against.
            values = np.full((4, 4), 5.0, dtype="float32")
        else:
            values = np.arange(16, dtype="float32").reshape(4, 4)

        _write_raster(destination, values)

        return subprocess.CompletedProcess(
            args=arguments, returncode=0, stdout="", stderr=""
        )

    def command_names(self) -> list[str]:
        """Return the executable of each recorded call, in order."""
        return [call[0] for call in self.calls]

    def call_containing(self, token: str) -> list[str]:
        """
        Return the single recorded call containing a token.

        Fails the test when absent or ambiguous, so a missing invocation cannot
        be mistaken for a passing assertion.
        """
        matches = [call for call in self.calls if token in call]
        assert matches, f"no GDAL call contained {token!r}: {self.calls}"
        assert len(matches) == 1, f"{len(matches)} calls contained {token!r}"
        return matches[0]


class RecordingWhitebox:
    """
    Stand-in for a WhiteboxTools instance that records and writes output.

    Records the arguments of each tool call, then writes a real raster at the
    requested destination. The D8 pointer output carries only valid pointer
    codes so the service's categorical check passes on the success path.
    """

    def __init__(self, fail_on: str | None = None, raise_on: str | None = None):
        """
        Args:
            fail_on: Tool name to fail by returning a non-zero code.
            raise_on: Tool name to fail by raising.
        """
        self.verbose = True
        self.calls: list[tuple[str, tuple, dict]] = []
        self._fail_on = fail_on
        self._raise_on = raise_on

    def _record(self, tool: str, args: tuple, kwargs: dict) -> int | None:
        """Record a call and apply any configured failure."""
        self.calls.append((tool, args, kwargs))

        if self._raise_on == tool:
            raise RuntimeError(f"simulated {tool} crash")

        if self._fail_on == tool:
            return 1

        return None

    def fill_depressions(self, *args, **kwargs):
        """Stand in for WhiteboxTools.fill_depressions."""
        outcome = self._record("fill_depressions", args, kwargs)
        if outcome is None:
            values = np.arange(16, dtype="float32").reshape(4, 4)
            _write_raster(Path(args[1]), values)
        return outcome

    def d8_pointer(self, *args, **kwargs):
        """Stand in for WhiteboxTools.d8_pointer."""
        outcome = self._record("d8_pointer", args, kwargs)
        if outcome is None:
            values = np.array(
                [[1, 2, 4, 8], [16, 32, 64, 128], [0, 1, 2, 4], [8, 16, 32, 64]],
                dtype="float32",
            )
            _write_raster(Path(args[1]), values)
        return outcome

    def d8_flow_accumulation(self, *args, **kwargs):
        """Stand in for WhiteboxTools.d8_flow_accumulation."""
        outcome = self._record("d8_flow_accumulation", args, kwargs)
        if outcome is None:
            values = np.arange(16, dtype="float32").reshape(4, 4)
            _write_raster(Path(args[1]), values)
        return outcome

    def tool_names(self) -> list[str]:
        """Return the name of each recorded tool call, in order."""
        return [name for name, _, _ in self.calls]

    def call_for(self, tool: str) -> tuple[tuple, dict]:
        """
        Return the args and kwargs of a single tool call.

        Fails the test when the tool was not invoked exactly once.
        """
        matches = [(a, k) for name, a, k in self.calls if name == tool]
        assert matches, f"{tool} was not invoked: {self.tool_names()}"
        assert len(matches) == 1, f"{tool} was invoked {len(matches)} times"
        return matches[0]


def _available_environment():
    """Return an environment report with every required tool present."""
    return SimpleNamespace(
        gdal_python_available=True,
        gdal_python_version="3.8.1",
        gdal_cli_available=True,
        gdal_cli_version="3.8.1",
        whitebox_available=True,
        whitebox_version="2.0.0",
        rasterio_available=True,
        rasterio_version="1.3.8",
        pyproj_available=True,
        pyproj_version="3.6.1",
        numpy_available=True,
        numpy_version="1.26.2",
        all_available=True,
        missing_tools=[],
    )


def _unavailable_environment():
    """Return an environment report with nothing installed."""
    return SimpleNamespace(
        gdal_python_available=False,
        gdal_python_version=None,
        gdal_cli_available=False,
        gdal_cli_version=None,
        whitebox_available=False,
        whitebox_version=None,
        rasterio_available=False,
        rasterio_version=None,
        pyproj_available=False,
        pyproj_version=None,
        numpy_available=False,
        numpy_version=None,
        all_available=False,
        missing_tools=[
            "GDAL command-line tools (gdalwarp, gdaldem)",
            "WhiteboxTools",
            "rasterio",
            "numpy",
        ],
    )


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def gdal_stub():
    """Return a recording GDAL stub that succeeds."""
    return RecordingGdal()


@pytest.fixture
def whitebox_stub():
    """Return a recording WhiteboxTools stub that succeeds."""
    return RecordingWhitebox()


@pytest.fixture
def available_environment(monkeypatch):
    """
    Report a complete toolchain regardless of the host machine.

    Patches the name inside processing_service, which binds
    get_environment_report with a from-import; patching app.core.environment
    would leave that reference untouched.
    """
    monkeypatch.setattr(
        processing_module, "get_environment_report", _available_environment
    )


@pytest.fixture
def raw_input(data_root: Path) -> Path:
    """
    Write a valid raw DSM into the raw terrain stage.

    Geographic WGS84, as a raw Copernicus product would be, so the pipeline's
    reprojection step has something to convert.
    """
    path = data_root / "raw" / "terrain" / "test_dsm.tif"
    values = np.arange(16, dtype="float32").reshape(4, 4)

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(72.8, 19.2, 0.00027, 0.00027),
    ) as dst:
        dst.write(values, 1)

    return path


@pytest.fixture
def service(terrain_provider, gdal_stub, whitebox_stub):
    """Return a service with both tool boundaries replaced by recorders."""
    return TerrainProcessingService(
        provider=terrain_provider,
        command_runner=gdal_stub,
        whitebox_factory=lambda: whitebox_stub,
    )


def _run(service, raw_input, **overrides) -> ProcessingPipeline:
    """
    Run the pipeline against the fixture input with sensible defaults.

    Args:
        service: The service under test.
        raw_input: Input raster written by the raw_input fixture.
        **overrides: Arguments to add or replace.

    Returns:
        The resulting pipeline.
    """
    kwargs = {
        "filename": raw_input.name,
        "source": "Test Fixture",
        "stage": TerrainStage.RAW,
    }
    kwargs.update(overrides)
    return service.process_terrain(**kwargs)


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_uses_injected_provider(terrain_provider):
    """The supplied provider is used rather than a default one."""
    service = TerrainProcessingService(provider=terrain_provider)

    assert service.provider is terrain_provider


def test_defaults_to_phase_one_target_crs(terrain_provider):
    """The processing CRS defaults to EPSG:32643, the Phase 1 target."""
    service = TerrainProcessingService(provider=terrain_provider)

    assert service.target_epsg == TARGET_CRS_EPSG
    assert service.target_epsg == 32643


def test_construction_creates_no_whitebox_instance(terrain_provider):
    """
    Constructing the service starts no external tool.

    The WhiteboxTools instance is built on first use, so a service can be
    created in an environment without it - which is what lets the API construct
    one per request.
    """
    created = []

    def tracking_factory():
        created.append(True)
        return RecordingWhitebox()

    TerrainProcessingService(
        provider=terrain_provider, whitebox_factory=tracking_factory
    )

    assert created == []


def test_construction_performs_no_environment_check(terrain_provider, monkeypatch):
    """Construction does not inspect the environment."""
    calls = []

    def tracking_report():
        calls.append(True)
        return _available_environment()

    monkeypatch.setattr(processing_module, "get_environment_report", tracking_report)

    TerrainProcessingService(provider=terrain_provider)

    assert calls == []


# ----------------------------------------------------------------------
# Input resolution
# ----------------------------------------------------------------------


def test_missing_input_raises_input_error(service):
    """
    A nonexistent input raises ProcessingInputError.

    Resolution happens before the environment gate, so this is reported as a
    missing input rather than as a toolchain problem.
    """
    with pytest.raises(ProcessingInputError):
        service.process_terrain(filename="absent.tif", source="Test Fixture")


def test_missing_input_runs_no_tool(service, gdal_stub, whitebox_stub):
    """A missing input aborts before any tool is invoked."""
    with pytest.raises(ProcessingInputError):
        service.process_terrain(filename="absent.tif", source="Test Fixture")

    assert gdal_stub.calls == []
    assert whitebox_stub.calls == []


def test_input_traversal_is_rejected(service):
    """A filename escaping the stage directory raises ProcessingInputError."""
    with pytest.raises(ProcessingInputError):
        service.process_terrain(
            filename="../../etc/passwd", source="Test Fixture"
        )


def test_input_resolved_from_requested_stage(service, data_root, available_environment):
    """
    The stage argument selects which directory the input is read from.

    A file present at the raw stage is not found when the processed stage is
    requested, confirming the stage is honoured rather than ignored.
    """
    raw_only = data_root / "raw" / "terrain" / "stage_test.tif"
    _write_raster(raw_only, np.arange(16, dtype="float32").reshape(4, 4))

    with pytest.raises(ProcessingInputError):
        service.process_terrain(
            filename="stage_test.tif",
            source="Test Fixture",
            stage=TerrainStage.PROCESSED,
        )


# ----------------------------------------------------------------------
# Environment gate
# ----------------------------------------------------------------------


def test_missing_environment_raises_environment_error(
    service, raw_input, monkeypatch
):
    """
    An incomplete toolchain raises ProcessingEnvironmentError.

    The service refuses to start rather than producing a pipeline that reports
    failure, because nothing was attempted.
    """
    monkeypatch.setattr(
        processing_module, "get_environment_report", _unavailable_environment
    )

    with pytest.raises(ProcessingEnvironmentError, match="Missing"):
        _run(service, raw_input)


def test_missing_environment_runs_no_tool(
    service, raw_input, gdal_stub, whitebox_stub, monkeypatch
):
    """The environment gate fires before any tool is invoked."""
    monkeypatch.setattr(
        processing_module, "get_environment_report", _unavailable_environment
    )

    with pytest.raises(ProcessingEnvironmentError):
        _run(service, raw_input)

    assert gdal_stub.calls == []
    assert whitebox_stub.calls == []


def test_missing_environment_names_absent_tools(service, raw_input, monkeypatch):
    """The error names which tools are missing."""
    monkeypatch.setattr(
        processing_module, "get_environment_report", _unavailable_environment
    )

    with pytest.raises(ProcessingEnvironmentError) as excinfo:
        _run(service, raw_input)

    message = str(excinfo.value)
    assert "GDAL" in message
    assert "WhiteboxTools" in message


# ----------------------------------------------------------------------
# Successful orchestration
# ----------------------------------------------------------------------


def test_pipeline_completes(service, raw_input, available_environment):
    """A pipeline over valid input with working tools completes."""
    pipeline = _run(service, raw_input)

    assert pipeline.status is ProcessingStatus.COMPLETED
    assert pipeline.error_message is None


def test_pipeline_runs_operations_in_order(service, raw_input, available_environment):
    """
    Every Phase 1 operation runs exactly once, in the documented order.

    The sequence is not incidental: slope and flow direction are derived from
    the filled surface, which must exist first, and accumulation consumes the
    pointer.
    """
    pipeline = _run(service, raw_input)

    assert [step.operation for step in pipeline.steps] == [
        ProcessingOperation.VALIDATION,
        ProcessingOperation.REPROJECTION,
        ProcessingOperation.DEPRESSION_FILLING,
        ProcessingOperation.SLOPE,
        ProcessingOperation.D8_FLOW_DIRECTION,
        ProcessingOperation.D8_FLOW_ACCUMULATION,
    ]


def test_every_step_completes(service, raw_input, available_environment):
    """No step is left pending or running in a completed pipeline."""
    pipeline = _run(service, raw_input)

    for step in pipeline.steps:
        assert step.status is ProcessingStatus.COMPLETED, step.step_id
        assert step.error_message is None


def test_pipeline_reports_final_output(service, raw_input, available_environment):
    """
    The pipeline's output is the flow-accumulation product.

    That is the last product of the Phase 1 chain, so it is what a caller
    continues from.
    """
    pipeline = _run(service, raw_input)

    assert pipeline.output_dataset_id is not None
    assert "d8_accumulation" in pipeline.output_dataset_id


def test_pipeline_identifies_its_input(service, raw_input, available_environment):
    """The pipeline records the dataset it started from."""
    pipeline = _run(service, raw_input)

    assert pipeline.input_dataset_id == raw_input.stem


def test_pipeline_id_is_traceable_to_input(service, raw_input, available_environment):
    """
    A generated pipeline id contains the input stem.

    A random identifier would sever the link between a run and the data it
    processed.
    """
    pipeline = _run(service, raw_input)

    assert raw_input.stem in pipeline.pipeline_id


def test_pipeline_records_timings(service, raw_input, available_environment):
    """Timings are recorded and internally consistent."""
    pipeline = _run(service, raw_input)

    assert pipeline.started_at is not None
    assert pipeline.completed_at is not None
    assert pipeline.completed_at >= pipeline.started_at

    for step in pipeline.steps:
        assert step.duration_seconds is not None
        assert step.duration_seconds >= 0


def test_step_chain_links_input_to_output(service, raw_input, available_environment):
    """
    Each producing step consumes the previous step's output.

    Lineage is what makes the chain reproducible: a step reading something
    other than its declared parent would break provenance.
    """
    pipeline = _run(service, raw_input)
    by_operation = {step.operation: step for step in pipeline.steps}

    reproject = by_operation[ProcessingOperation.REPROJECTION]
    fill = by_operation[ProcessingOperation.DEPRESSION_FILLING]
    slope = by_operation[ProcessingOperation.SLOPE]
    pointer = by_operation[ProcessingOperation.D8_FLOW_DIRECTION]
    accumulation = by_operation[ProcessingOperation.D8_FLOW_ACCUMULATION]

    assert fill.input_dataset_id == reproject.output_dataset_id
    assert slope.input_dataset_id == fill.output_dataset_id
    assert pointer.input_dataset_id == fill.output_dataset_id
    assert accumulation.input_dataset_id == pointer.output_dataset_id


def test_validation_step_produces_no_dataset(service, raw_input, available_environment):
    """
    The input validation step declares no output.

    It inspects a dataset rather than producing one, so an output id would
    misrepresent what happened.
    """
    pipeline = _run(service, raw_input)
    validation = pipeline.steps[0]

    assert validation.operation is ProcessingOperation.VALIDATION
    assert validation.output_dataset_id is None


# ----------------------------------------------------------------------
# GDAL invocation
# ----------------------------------------------------------------------


def test_gdal_invoked_for_reprojection_and_slope(
    service, raw_input, available_environment, gdal_stub
):
    """GDAL is used for exactly the two operations it owns."""
    _run(service, raw_input)

    assert gdal_stub.command_names() == ["gdalwarp", "gdaldem"]


def test_reprojection_targets_phase_one_crs(
    service, raw_input, available_environment, gdal_stub
):
    """
    gdalwarp is given EPSG:32643 as the target CRS.

    Metric coordinates are required for slope and flow analysis; the raw
    product is geographic.
    """
    _run(service, raw_input)

    call = gdal_stub.call_containing("-t_srs")
    index = call.index("-t_srs")

    assert call[index + 1] == f"EPSG:{TARGET_CRS_EPSG}"
    assert call[index + 1] == "EPSG:32643"


def test_reprojection_does_not_overwrite_source(
    service, raw_input, available_environment, gdal_stub
):
    """
    The gdalwarp source and destination differ.

    Writing over the input would destroy the raw dataset, which the pipeline
    must never modify.
    """
    _run(service, raw_input)

    call = gdal_stub.call_containing("-t_srs")

    assert call[-2] != call[-1]
    assert str(raw_input.resolve()) == call[-2]


def test_slope_uses_horn_algorithm(
    service, raw_input, available_environment, gdal_stub
):
    """gdaldem slope is invoked with the Horn algorithm."""
    _run(service, raw_input)

    call = gdal_stub.call_containing("slope")
    index = call.index("-alg")

    assert call[0] == "gdaldem"
    assert call[1] == "slope"
    assert call[index + 1] == "Horn"


def test_slope_computes_edges(service, raw_input, available_environment, gdal_stub):
    """
    gdaldem slope is invoked with -compute_edges.

    Without it gdaldem leaves a one-pixel nodata border, which would appear
    downstream as missing terrain at the study-area boundary.
    """
    _run(service, raw_input)

    call = gdal_stub.call_containing("slope")

    assert "-compute_edges" in call


def test_gdal_receives_explicit_argument_list(
    service, raw_input, available_environment, gdal_stub
):
    """
    Commands arrive as argument lists, not shell strings.

    A list is passed to the process directly, so a filename cannot be
    reinterpreted as shell syntax.
    """
    _run(service, raw_input)

    for call in gdal_stub.calls:
        assert isinstance(call, list)
        assert all(isinstance(argument, str) for argument in call)
        assert len(call) > 1


def test_gdal_receives_positive_timeout(
    service, raw_input, available_environment, gdal_stub
):
    """Every GDAL command is given a bounded timeout."""
    _run(service, raw_input)

    assert gdal_stub.timeouts
    for timeout in gdal_stub.timeouts:
        assert isinstance(timeout, int)
        assert timeout > 0


def test_custom_timeout_is_applied(terrain_provider, raw_input, available_environment):
    """A configured timeout reaches the command runner."""
    gdal = RecordingGdal()
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=gdal,
        whitebox_factory=RecordingWhitebox,
        command_timeout_seconds=42,
    )

    _run(service, raw_input)

    assert gdal.timeouts
    assert all(timeout == 42 for timeout in gdal.timeouts)


# ----------------------------------------------------------------------
# WhiteboxTools invocation
# ----------------------------------------------------------------------


def test_whitebox_invoked_for_its_three_operations(
    service, raw_input, available_environment, whitebox_stub
):
    """WhiteboxTools performs exactly the three operations it owns, in order."""
    _run(service, raw_input)

    assert whitebox_stub.tool_names() == [
        "fill_depressions",
        "d8_pointer",
        "d8_flow_accumulation",
    ]


def test_fill_depressions_receives_input_and_output(
    service, raw_input, available_environment, whitebox_stub
):
    """fill_depressions is given distinct input and output paths."""
    _run(service, raw_input)

    args, _ = whitebox_stub.call_for("fill_depressions")

    assert len(args) == 2
    assert args[0] != args[1]


def test_d8_pointer_uses_default_encoding(
    service, raw_input, available_environment, whitebox_stub
):
    """
    d8_pointer is invoked without esri_pntr.

    The default encoding is what D8_POINTER_VALID_VALUES describes; requesting
    the ESRI variant would produce codes the categorical check rejects.
    """
    _run(service, raw_input)

    _, kwargs = whitebox_stub.call_for("d8_pointer")

    assert "esri_pntr" not in kwargs


def test_d8_accumulation_declares_pointer_input(
    service, raw_input, available_environment, whitebox_stub
):
    """
    d8_flow_accumulation is told its input is a pointer, and to count cells.

    Without pntr=True the tool reads pointer codes as elevations and silently
    returns a meaningless result, so this flag is not optional.
    """
    _run(service, raw_input)

    _, kwargs = whitebox_stub.call_for("d8_flow_accumulation")

    assert kwargs["pntr"] is True
    assert kwargs["out_type"] == "cells"


def test_d8_accumulation_consumes_pointer_output(
    service, raw_input, available_environment, whitebox_stub
):
    """Accumulation reads the pointer raster the previous step produced."""
    _run(service, raw_input)

    pointer_args, _ = whitebox_stub.call_for("d8_pointer")
    accumulation_args, _ = whitebox_stub.call_for("d8_flow_accumulation")

    # The pointer is promoted from working/ to processed/ before accumulation
    # reads it, so the paths share a filename but not a directory.
    assert Path(accumulation_args[0]).name == Path(pointer_args[1]).name
    assert Path(accumulation_args[0]).parent.name == "terrain"
    assert "processed" in Path(accumulation_args[0]).parts


def test_whitebox_instance_is_reused(
    service, raw_input, available_environment, whitebox_stub
):
    """
    One WhiteboxTools instance serves the whole pipeline.

    Rebuilding it per operation would repeat the tool's startup cost three
    times for no benefit.
    """
    _run(service, raw_input)

    assert len(whitebox_stub.calls) == 3


# ----------------------------------------------------------------------
# Output products and validation
# ----------------------------------------------------------------------


def test_products_are_written_to_processed_stage(
    service, raw_input, available_environment, data_root
):
    """Every product lands in the processed terrain directory."""
    _run(service, raw_input)

    processed = data_root / "processed" / "terrain"
    names = {path.name for path in processed.glob("*.tif")}

    assert len(names) == 5


def test_product_names_are_deterministic(
    service, raw_input, available_environment, data_root
):
    """
    Product names derive from the input stem and the operation.

    Deterministic naming means a product can be located without consulting a
    registry, and a rerun overwrites rather than accumulating duplicates.
    """
    _run(service, raw_input)

    processed = data_root / "processed" / "terrain"
    names = {path.name for path in processed.glob("*.tif")}
    stem = raw_input.stem

    assert names == {
        f"{stem}_utm43n.tif",
        f"{stem}_filled_utm43n.tif",
        f"{stem}_slope_deg_utm43n.tif",
        f"{stem}_d8_pointer_utm43n.tif",
        f"{stem}_d8_accumulation_utm43n.tif",
    }


def test_working_directory_is_removed(
    service, raw_input, available_environment, data_root
):
    """The pipeline's scratch directory does not survive the run."""
    _run(service, raw_input)

    working = data_root / "working"
    assert list(working.iterdir()) == []


def test_raw_input_is_never_modified(service, raw_input, available_environment):
    """
    The raw input is unchanged after a run.

    The raw stage is the one copy of the source data; a pipeline that altered
    it would make the run unrepeatable.
    """
    before = raw_input.read_bytes()

    _run(service, raw_input)

    assert raw_input.read_bytes() == before


def test_invalid_output_fails_the_step(
    terrain_provider, raw_input, available_environment
):
    """
    A structurally invalid product fails its step.

    The stub writes a slope raster full of NaN, which the service's post-stage
    validation must reject rather than promote.
    """

    class NanSlopeGdal(RecordingGdal):
        def __call__(self, args, timeout):
            arguments = list(args)
            if arguments[0] == "gdaldem":
                self.calls.append(arguments)
                self.timeouts.append(timeout)
                _write_raster(
                    _gdal_destination(arguments),
                    np.full((4, 4), np.nan, dtype="float32"),
                )
                return subprocess.CompletedProcess(arguments, 0, "", "")
            return super().__call__(args, timeout)

    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=NanSlopeGdal(),
        whitebox_factory=RecordingWhitebox,
    )

    pipeline = _run(service, raw_input, validate_pixel_values=True)

    assert pipeline.status is ProcessingStatus.FAILED
    slope = [s for s in pipeline.steps if s.operation is ProcessingOperation.SLOPE][0]
    assert slope.status is ProcessingStatus.FAILED


def test_invalid_d8_values_fail_the_step(terrain_provider, raw_input, available_environment):
    """
    A pointer raster with codes outside the D8 set fails.

    The value 3 is not a D8 direction. This check reads pixels regardless of
    validate_pixel_values, because membership in an exact code set cannot be
    inferred from metadata.
    """

    class BadPointerWhitebox(RecordingWhitebox):
        def d8_pointer(self, *args, **kwargs):
            self.calls.append(("d8_pointer", args, kwargs))
            values = np.full((4, 4), 3.0, dtype="float32")
            _write_raster(Path(args[1]), values)
            return None

    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(),
        whitebox_factory=BadPointerWhitebox,
    )

    pipeline = _run(service, raw_input, validate_pixel_values=False)

    assert pipeline.status is ProcessingStatus.FAILED

    pointer = [
        s for s in pipeline.steps if s.operation is ProcessingOperation.D8_FLOW_DIRECTION
    ][0]
    assert pointer.status is ProcessingStatus.FAILED
    assert "3" in pointer.error_message


def test_valid_d8_codes_are_the_documented_set():
    """The accepted D8 codes are exactly the WhiteboxTools pointer values."""
    assert D8_POINTER_VALID_VALUES == frozenset({0, 1, 2, 4, 8, 16, 32, 64, 128})


# ----------------------------------------------------------------------
# Failure handling
# ----------------------------------------------------------------------


def test_gdal_failure_fails_the_pipeline(
    terrain_provider, raw_input, available_environment
):
    """A non-zero GDAL exit code fails the pipeline, not just the command."""
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(fail_on="gdalwarp"),
        whitebox_factory=RecordingWhitebox,
    )

    pipeline = _run(service, raw_input)

    assert pipeline.status is ProcessingStatus.FAILED
    assert pipeline.output_dataset_id is None


def test_gdal_failure_message_carries_stderr(
    terrain_provider, raw_input, available_environment
):
    """
    The recorded error includes the tool's stderr.

    Discarding it would leave an operator with a failure and no diagnosis.
    """
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(fail_on="gdalwarp"),
        whitebox_factory=RecordingWhitebox,
    )

    pipeline = _run(service, raw_input)
    reproject = [
        s for s in pipeline.steps if s.operation is ProcessingOperation.REPROJECTION
    ][0]

    assert "simulated failure" in reproject.error_message


def test_gdal_timeout_fails_the_step(
    terrain_provider, raw_input, available_environment
):
    """A timed-out command fails the step rather than hanging or passing."""
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(timeout_on="gdalwarp"),
        whitebox_factory=RecordingWhitebox,
    )

    pipeline = _run(service, raw_input)
    reproject = [
        s for s in pipeline.steps if s.operation is ProcessingOperation.REPROJECTION
    ][0]

    assert reproject.status is ProcessingStatus.FAILED
    assert "timed out" in reproject.error_message


def test_whitebox_nonzero_return_fails_the_step(
    terrain_provider, raw_input, available_environment
):
    """A non-zero WhiteboxTools return code fails the step."""
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(),
        whitebox_factory=lambda: RecordingWhitebox(fail_on="fill_depressions"),
    )

    pipeline = _run(service, raw_input)
    fill = [
        s
        for s in pipeline.steps
        if s.operation is ProcessingOperation.DEPRESSION_FILLING
    ][0]

    assert fill.status is ProcessingStatus.FAILED
    assert "return code 1" in fill.error_message


def test_whitebox_exception_fails_the_step(
    terrain_provider, raw_input, available_environment
):
    """An exception from WhiteboxTools fails the step rather than escaping."""
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(),
        whitebox_factory=lambda: RecordingWhitebox(raise_on="d8_pointer"),
    )

    pipeline = _run(service, raw_input)
    pointer = [
        s
        for s in pipeline.steps
        if s.operation is ProcessingOperation.D8_FLOW_DIRECTION
    ][0]

    assert pointer.status is ProcessingStatus.FAILED
    assert "simulated d8_pointer crash" in pointer.error_message


def test_pipeline_stops_at_first_failure(
    terrain_provider, raw_input, available_environment
):
    """
    A failed step halts the pipeline.

    Continuing would build derivatives on a raster already known to be wrong,
    producing products that look complete but are not.
    """
    whitebox = RecordingWhitebox(fail_on="fill_depressions")
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(),
        whitebox_factory=lambda: whitebox,
    )

    pipeline = _run(service, raw_input)

    assert [step.operation for step in pipeline.steps] == [
        ProcessingOperation.VALIDATION,
        ProcessingOperation.REPROJECTION,
        ProcessingOperation.DEPRESSION_FILLING,
    ]
    assert whitebox.tool_names() == ["fill_depressions"]


def test_failed_step_reports_no_output(
    terrain_provider, raw_input, available_environment
):
    """A failed step declares no output dataset."""
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(fail_on="gdalwarp"),
        whitebox_factory=RecordingWhitebox,
    )

    pipeline = _run(service, raw_input)
    failed = [s for s in pipeline.steps if s.status is ProcessingStatus.FAILED][0]

    assert failed.output_dataset_id is None


def test_failed_pipeline_carries_the_error(
    terrain_provider, raw_input, available_environment
):
    """The pipeline's error message is that of the step that failed."""
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(fail_on="gdalwarp"),
        whitebox_factory=RecordingWhitebox,
    )

    pipeline = _run(service, raw_input)
    failed = [s for s in pipeline.steps if s.status is ProcessingStatus.FAILED][0]

    assert pipeline.error_message == failed.error_message


def test_failed_step_leaves_no_partial_product(
    terrain_provider, raw_input, available_environment, data_root
):
    """
    A failed step's output does not appear among the products.

    Promotion happens only after validation, so a raster the tool half-wrote is
    never visible as a finished product.
    """
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(),
        whitebox_factory=lambda: RecordingWhitebox(fail_on="d8_pointer"),
    )

    _run(service, raw_input)

    processed = data_root / "processed" / "terrain"
    names = {path.name for path in processed.glob("*.tif")}

    assert not any("d8_pointer" in name for name in names)


def test_failure_preserves_earlier_products(
    terrain_provider, raw_input, available_environment, data_root
):
    """
    Products validated before the failure remain on disk.

    They are complete and correct; discarding them would force a rerun of work
    that succeeded.
    """
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(),
        whitebox_factory=lambda: RecordingWhitebox(fail_on="d8_pointer"),
    )

    _run(service, raw_input)

    processed = data_root / "processed" / "terrain"
    names = {path.name for path in processed.glob("*.tif")}
    stem = raw_input.stem

    assert f"{stem}_utm43n.tif" in names
    assert f"{stem}_filled_utm43n.tif" in names
    assert f"{stem}_slope_deg_utm43n.tif" in names


def test_failure_cleans_the_working_directory(
    terrain_provider, raw_input, available_environment, data_root
):
    """Scratch space is reclaimed even when the pipeline fails."""
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(fail_on="gdalwarp"),
        whitebox_factory=RecordingWhitebox,
    )

    _run(service, raw_input)

    assert list((data_root / "working").iterdir()) == []


def test_failure_does_not_delete_raw_input(
    terrain_provider, raw_input, available_environment
):
    """Cleanup after a failure never touches the raw input."""
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(fail_on="gdalwarp"),
        whitebox_factory=RecordingWhitebox,
    )

    _run(service, raw_input)

    assert raw_input.is_file()


def test_service_errors_share_a_base_class():
    """Every processing error derives from ProcessingServiceError."""
    for error in (
        ProcessingEnvironmentError,
        ProcessingInputError,
        ProcessingExecutionError,
    ):
        assert issubclass(error, ProcessingServiceError)


# ----------------------------------------------------------------------
# Overwrite behaviour
# ----------------------------------------------------------------------


def test_existing_product_is_not_replaced_by_default(
    service, raw_input, available_environment, data_root
):
    """
    An existing product is not silently replaced.

    Overwriting by default would destroy a validated product on an accidental
    rerun, so the pipeline fails instead.
    """
    existing = data_root / "processed" / "terrain" / f"{raw_input.stem}_utm43n.tif"
    _write_raster(existing, np.zeros((4, 4), dtype="float32"))
    original = existing.read_bytes()

    pipeline = _run(service, raw_input)

    assert pipeline.status is ProcessingStatus.FAILED
    assert existing.read_bytes() == original


def test_refusal_to_overwrite_explains_the_option(
    service, raw_input, available_environment, data_root
):
    """The refusal message names the flag that would permit replacement."""
    existing = data_root / "processed" / "terrain" / f"{raw_input.stem}_utm43n.tif"
    _write_raster(existing, np.zeros((4, 4), dtype="float32"))

    pipeline = _run(service, raw_input)

    assert "overwrite=True" in pipeline.error_message


def test_overwrite_replaces_existing_product(
    service, raw_input, available_environment, data_root
):
    """With overwrite=True the pipeline completes and replaces the product."""
    existing = data_root / "processed" / "terrain" / f"{raw_input.stem}_utm43n.tif"
    _write_raster(existing, np.zeros((4, 4), dtype="float32"))
    original = existing.read_bytes()

    pipeline = _run(service, raw_input, overwrite=True)

    assert pipeline.status is ProcessingStatus.COMPLETED
    assert existing.read_bytes() != original


def test_overwrite_preserves_product_when_replacement_fails(
    terrain_provider, raw_input, available_environment, data_root
):
    """
    A replacement that fails validation leaves the existing product intact.

    The old file is displaced only after the new output validates, so a bad
    rerun cannot destroy a good product.
    """
    existing = data_root / "processed" / "terrain" / f"{raw_input.stem}_slope_deg_utm43n.tif"
    _write_raster(existing, np.full((4, 4), 5.0, dtype="float32"))
    original = existing.read_bytes()

    class NanSlopeGdal(RecordingGdal):
        def __call__(self, args, timeout):
            arguments = list(args)
            if arguments[0] == "gdaldem":
                self.calls.append(arguments)
                self.timeouts.append(timeout)
                _write_raster(
                    _gdal_destination(arguments),
                    np.full((4, 4), np.nan, dtype="float32"),
                )
                return subprocess.CompletedProcess(arguments, 0, "", "")
            return super().__call__(args, timeout)

    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=NanSlopeGdal(),
        whitebox_factory=RecordingWhitebox,
    )

    pipeline = _run(
        service, raw_input, overwrite=True, validate_pixel_values=True
    )

    assert pipeline.status is ProcessingStatus.FAILED
    assert existing.read_bytes() == original


def test_no_replacement_sidecar_survives(
    service, raw_input, available_environment, data_root
):
    """
    No sidecar file is left behind after a successful replacement.

    The displaced product is removed once the new one is in place.
    """
    existing = data_root / "processed" / "terrain" / f"{raw_input.stem}_utm43n.tif"
    _write_raster(existing, np.zeros((4, 4), dtype="float32"))

    _run(service, raw_input, overwrite=True)

    processed = data_root / "processed" / "terrain"
    assert list(processed.glob("*.replaced")) == []


# ----------------------------------------------------------------------
# Provenance
# ----------------------------------------------------------------------


def test_metadata_written_for_each_product(
    service, raw_input, available_environment, data_root
):
    """A provenance record is written for every product."""
    _run(service, raw_input)

    records = list((data_root / "metadata").glob("*.json"))

    assert len(records) == 5


def test_metadata_records_source_and_lineage(
    service, raw_input, available_environment, data_root
):
    """
    A record names its source and the dataset it came from.

    Together these make a product traceable back through the chain to the raw
    input rather than appearing to arrive from nowhere.
    """
    _run(service, raw_input)

    path = data_root / "metadata" / f"{raw_input.stem}_slope_deg_utm43n.json"
    record = json.loads(path.read_text())

    assert record["source"] == "Test Fixture"
    assert record["parent_dataset_id"] == f"{raw_input.stem}_filled_utm43n"
    assert record["processing_status"] == "processed"


def test_metadata_records_tool_and_parameters(
    service, raw_input, available_environment, data_root
):
    """
    A record carries the tool, its version, and the parameters used.

    These are what make a run reproducible: the same tool at the same version
    with the same settings.
    """
    _run(service, raw_input)

    path = data_root / "metadata" / f"{raw_input.stem}_slope_deg_utm43n.json"
    record = json.loads(path.read_text())
    step = record["processing_steps"][0]

    assert step["step"] == "slope"
    assert step["tool"] == "GDAL"
    assert step["version"] == "3.8.1"
    assert step["parameters"]["algorithm"] == "Horn"
    assert step["parameters"]["compute_edges"] is True


def test_metadata_records_reported_tool_versions(
    service, raw_input, available_environment, data_root
):
    """Tool versions come from the environment report, not from a constant."""
    _run(service, raw_input)

    path = data_root / "metadata" / f"{raw_input.stem}_filled_utm43n.json"
    record = json.loads(path.read_text())
    step = record["processing_steps"][0]

    assert step["tool"] == "WhiteboxTools"
    assert step["version"] == "2.0.0"


def test_metadata_is_strictly_serializable(
    service, raw_input, available_environment, data_root
):
    """
    Written records parse under strict JSON.

    allow_nan=False rejects NaN and Infinity, which Python emits by default but
    other parsers refuse.
    """
    _run(service, raw_input)

    for path in (data_root / "metadata").glob("*.json"):
        json.dumps(json.loads(path.read_text()), allow_nan=False)


def test_no_metadata_for_a_failed_step(
    terrain_provider, raw_input, available_environment, data_root
):
    """
    A failed step produces no provenance record.

    A record for a product that does not exist would describe work that never
    completed.
    """
    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(),
        whitebox_factory=lambda: RecordingWhitebox(fail_on="d8_pointer"),
    )

    _run(service, raw_input)

    names = {path.stem for path in (data_root / "metadata").glob("*.json")}

    assert not any("d8_pointer" in name for name in names)


# ----------------------------------------------------------------------
# Validation depth
# ----------------------------------------------------------------------


def test_pixel_scanning_is_off_by_default(
    service, raw_input, available_environment
):
    """
    Pixel scanning is disabled by default and recorded in the step parameters.

    Scanning a city-scale raster at every stage is expensive, so the default
    keeps validation structural.
    """
    pipeline = _run(service, raw_input)
    validation = pipeline.steps[0]

    assert validation.parameters["check_pixel_values"] is False


def test_pixel_scanning_can_be_enabled(service, raw_input, available_environment):
    """The caller can request full pixel validation."""
    pipeline = _run(service, raw_input, validate_pixel_values=True)
    validation = pipeline.steps[0]

    assert validation.parameters["check_pixel_values"] is True


def test_d8_check_runs_regardless_of_pixel_setting(
    terrain_provider, raw_input, available_environment
):
    """
    D8 codes are checked even when pixel scanning is off.

    Whether a pointer raster is correct is defined by membership in an exact
    code set, which no metadata check can establish.
    """

    class BadPointerWhitebox(RecordingWhitebox):
        def d8_pointer(self, *args, **kwargs):
            self.calls.append(("d8_pointer", args, kwargs))
            _write_raster(Path(args[1]), np.full((4, 4), 7.0, dtype="float32"))
            return None

    service = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=RecordingGdal(),
        whitebox_factory=BadPointerWhitebox,
    )

    pipeline = _run(service, raw_input, validate_pixel_values=False)

    pointer = [
        s
        for s in pipeline.steps
        if s.operation is ProcessingOperation.D8_FLOW_DIRECTION
    ][0]
    assert pointer.status is ProcessingStatus.FAILED


def test_expected_input_crs_is_checked_when_supplied(
    service, raw_input, available_environment
):
    """
    A supplied expected input CRS is enforced.

    The raw fixture is geographic WGS84, so declaring UTM 43N must fail the
    input validation step rather than proceed.
    """
    pipeline = _run(service, raw_input, expected_input_crs=32643)

    validation = pipeline.steps[0]
    assert validation.status is ProcessingStatus.FAILED
    assert pipeline.status is ProcessingStatus.FAILED


def test_no_input_crs_is_assumed(service, raw_input, available_environment):
    """
    Omitting the expected CRS lets a geographic input through.

    The service assumes no input projection: the raw product's CRS is inspected
    and reprojected, not required to be anything in particular.
    """
    pipeline = _run(service, raw_input)

    assert pipeline.steps[0].status is ProcessingStatus.COMPLETED