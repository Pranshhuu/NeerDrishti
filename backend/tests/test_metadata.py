"""
Metadata and provenance tests for FlowSight Phase 1

Exercises app.data.metadata directly: how DatasetMetadata is created,
validated, serialized, and round-tripped.

Scope:
    The question here is whether the metadata module correctly creates,
    normalizes, serializes, and preserves provenance. Raster CRS reading is
    covered by test_crs.py and raster validation by test_validators.py; where
    those appear below it is only to verify what metadata.py does with their
    output — chiefly that a live pyproj.CRS never reaches serialized JSON.

Serialization strictness:
    to_dict and to_json use allow_nan=False. Python's json encoder emits bare
    NaN and Infinity by default, which are not valid JSON and would produce
    files that other parsers reject. The tests assert rejection at construction
    rather than at write time.

No external dependencies:
    Every raster is synthetic and confined to tmp_path. No network access, no
    GDAL CLI, no WhiteboxTools, no pipeline execution.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.data.metadata import (
    METADATA_VERSION,
    VALID_PROCESSING_STATUSES,
    DatasetMetadata,
    MetadataError,
)


def _minimal_kwargs(**overrides) -> dict:
    """
    Return the required fields for a DatasetMetadata, with optional overrides.

    Keeps each test focused on the one field it exercises rather than repeating
    the four mandatory identity fields.

    Args:
        **overrides: Fields to add or replace.

    Returns:
        Keyword arguments suitable for DatasetMetadata(...).
    """
    kwargs = {
        "dataset_id": "test_dataset",
        "dataset_name": "Test Dataset",
        "source": "Test Fixture",
        "dataset_type": "terrain_dsm",
    }
    kwargs.update(overrides)
    return kwargs


# ----------------------------------------------------------------------
# Construction and required fields
# ----------------------------------------------------------------------


def test_creates_record_from_required_fields_only():
    """
    The four identity fields are sufficient to construct a record.

    Optional fields stay None rather than being filled with placeholder values,
    so a caller can tell "not recorded" from a real measurement.
    """
    record = DatasetMetadata(**_minimal_kwargs())

    assert record.dataset_id == "test_dataset"
    assert record.dataset_name == "Test Dataset"
    assert record.source == "Test Fixture"
    assert record.dataset_type == "terrain_dsm"

    assert record.crs is None
    assert record.bounds is None
    assert record.resolution is None
    assert record.dimensions is None
    assert record.acquisition_date is None
    assert record.parent_dataset_id is None


def test_defaults_processing_status_to_raw():
    """A record defaults to the raw lifecycle stage."""
    record = DatasetMetadata(**_minimal_kwargs())

    assert record.processing_status == "raw"


def test_defaults_metadata_version_to_current():
    """The schema version defaults to the module's current version."""
    record = DatasetMetadata(**_minimal_kwargs())

    assert record.metadata_version == METADATA_VERSION
    assert record.metadata_version == "1.0"


def test_defaults_processing_steps_to_empty_list():
    """Processing steps default to an empty list, not None."""
    record = DatasetMetadata(**_minimal_kwargs())

    assert record.processing_steps == []


def test_processing_steps_are_not_shared_between_records():
    """
    Each record owns its own processing_steps list.

    A shared mutable default would let a step appended to one record appear in
    every other, silently corrupting provenance across datasets.
    """
    first = DatasetMetadata(**_minimal_kwargs(dataset_id="first"))
    second = DatasetMetadata(**_minimal_kwargs(dataset_id="second"))

    first.add_processing_step(step="reprojection")

    assert len(first.processing_steps) == 1
    assert second.processing_steps == []


def test_rejects_blank_required_field():
    """A whitespace-only identity field is rejected."""
    with pytest.raises(MetadataError, match="non-empty string"):
        DatasetMetadata(**_minimal_kwargs(dataset_id="   "))


def test_rejects_empty_required_field():
    """An empty identity field is rejected."""
    with pytest.raises(MetadataError, match="non-empty string"):
        DatasetMetadata(**_minimal_kwargs(dataset_name=""))


def test_rejects_non_string_required_field():
    """A required field of the wrong type is rejected."""
    with pytest.raises(MetadataError, match="non-empty string"):
        DatasetMetadata(**_minimal_kwargs(source=123))


# ----------------------------------------------------------------------
# Processing status
# ----------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(VALID_PROCESSING_STATUSES))
def test_accepts_every_valid_processing_status(status):
    """Each documented lifecycle status is accepted."""
    record = DatasetMetadata(**_minimal_kwargs(processing_status=status))

    assert record.processing_status == status


def test_rejects_unknown_processing_status():
    """
    A status outside the allowed set is rejected, and the message lists them.

    Accepting an arbitrary string would let a typo produce a record that no
    downstream consumer can interpret.
    """
    with pytest.raises(MetadataError, match="Invalid processing_status"):
        DatasetMetadata(**_minimal_kwargs(processing_status="in_progress"))


def test_valid_statuses_are_exactly_the_documented_five():
    """The allowed status set matches the documented lifecycle."""
    assert VALID_PROCESSING_STATUSES == frozenset(
        {"raw", "validated", "processed", "derived", "failed"}
    )


# ----------------------------------------------------------------------
# Timestamps
# ----------------------------------------------------------------------


def test_defaults_processing_date_to_timezone_aware_utc():
    """
    An omitted processing date defaults to the current UTC time.

    The value is timezone-aware: a naive timestamp cannot be compared across
    records produced in different environments.
    """
    before = datetime.now(timezone.utc)
    record = DatasetMetadata(**_minimal_kwargs())
    after = datetime.now(timezone.utc)

    parsed = datetime.fromisoformat(record.processing_date)

    assert parsed.tzinfo is not None
    assert before <= parsed <= after


def test_preserves_supplied_processing_date():
    """A supplied processing date is stored unchanged."""
    supplied = "2026-01-15T10:30:00+00:00"

    record = DatasetMetadata(**_minimal_kwargs(processing_date=supplied))

    assert record.processing_date == supplied


def test_never_infers_acquisition_date():
    """
    Acquisition date stays None when not supplied.

    When the source data was captured is a fact about the provider, not
    something the processing date can stand in for. Inventing it would put a
    false claim into a provenance record.
    """
    record = DatasetMetadata(**_minimal_kwargs())

    assert record.acquisition_date is None


def test_preserves_supplied_acquisition_date():
    """A supplied acquisition date is stored unchanged."""
    record = DatasetMetadata(**_minimal_kwargs(acquisition_date="2021-06-01"))

    assert record.acquisition_date == "2021-06-01"


# ----------------------------------------------------------------------
# Numeric field validation
# ----------------------------------------------------------------------


def test_accepts_valid_dimensions():
    """Positive integer dimensions are accepted."""
    record = DatasetMetadata(
        **_minimal_kwargs(dimensions={"width": 100, "height": 50})
    )

    assert record.dimensions == {"width": 100, "height": 50}


def test_rejects_zero_dimension():
    """A zero dimension is rejected: a raster cannot have no columns."""
    with pytest.raises(MetadataError, match="must be positive"):
        DatasetMetadata(**_minimal_kwargs(dimensions={"width": 0, "height": 50}))


def test_rejects_negative_dimension():
    """A negative dimension is rejected."""
    with pytest.raises(MetadataError, match="must be positive"):
        DatasetMetadata(**_minimal_kwargs(dimensions={"width": 100, "height": -5}))


def test_rejects_incomplete_dimensions():
    """Dimensions missing a required key are rejected."""
    with pytest.raises(MetadataError, match="missing required key"):
        DatasetMetadata(**_minimal_kwargs(dimensions={"width": 100}))


def test_normalizes_resolution_to_float_tuple():
    """
    Resolution is normalized to a two-float tuple.

    Integers are accepted and converted, so downstream consumers see a
    consistent type regardless of how the caller expressed the pixel size.
    """
    record = DatasetMetadata(**_minimal_kwargs(resolution=[30, 30]))

    assert record.resolution == (30.0, 30.0)
    assert all(isinstance(value, float) for value in record.resolution)


def test_rejects_zero_resolution():
    """A zero pixel size is rejected."""
    with pytest.raises(MetadataError, match="must be positive"):
        DatasetMetadata(**_minimal_kwargs(resolution=(0.0, 30.0)))


def test_rejects_negative_resolution():
    """A negative pixel size is rejected."""
    with pytest.raises(MetadataError, match="must be positive"):
        DatasetMetadata(**_minimal_kwargs(resolution=(30.0, -30.0)))


def test_rejects_nan_resolution():
    """
    A NaN pixel size is rejected as non-finite.

    NaN is neither positive nor negative, so a bare positivity check would let
    it through; it is also not representable in strict JSON.
    """
    with pytest.raises(MetadataError, match="must be finite"):
        DatasetMetadata(**_minimal_kwargs(resolution=(float("nan"), 30.0)))


def test_rejects_infinite_resolution():
    """An infinite pixel size is rejected as non-finite."""
    with pytest.raises(MetadataError, match="must be finite"):
        DatasetMetadata(**_minimal_kwargs(resolution=(float("inf"), 30.0)))


def test_rejects_wrong_length_resolution():
    """Resolution must carry exactly two values."""
    with pytest.raises(MetadataError, match="exactly two values"):
        DatasetMetadata(**_minimal_kwargs(resolution=(30.0, 30.0, 30.0)))


def test_accepts_valid_bounds():
    """Well-ordered finite bounds are accepted."""
    bounds = {"min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 100.0}

    record = DatasetMetadata(**_minimal_kwargs(bounds=bounds))

    assert record.bounds == bounds


def test_accepts_negative_bounds_coordinates():
    """
    Negative coordinates are accepted.

    Bounds are in the dataset's own CRS, where negative values are ordinary —
    western longitudes, southern latitudes, or projected coordinates.
    """
    bounds = {"min_x": -100.0, "max_x": -50.0, "min_y": -20.0, "max_y": -10.0}

    record = DatasetMetadata(**_minimal_kwargs(bounds=bounds))

    assert record.bounds == bounds


def test_rejects_inverted_x_bounds():
    """Bounds with min_x above max_x are rejected."""
    bounds = {"min_x": 100.0, "max_x": 0.0, "min_y": 0.0, "max_y": 100.0}

    with pytest.raises(MetadataError, match="min_x"):
        DatasetMetadata(**_minimal_kwargs(bounds=bounds))


def test_rejects_inverted_y_bounds():
    """Bounds with min_y above max_y are rejected."""
    bounds = {"min_x": 0.0, "max_x": 100.0, "min_y": 100.0, "max_y": 0.0}

    with pytest.raises(MetadataError, match="min_y"):
        DatasetMetadata(**_minimal_kwargs(bounds=bounds))


def test_rejects_nan_bounds():
    """A non-finite bound is rejected."""
    bounds = {"min_x": float("nan"), "max_x": 100.0, "min_y": 0.0, "max_y": 100.0}

    with pytest.raises(MetadataError, match="must be finite"):
        DatasetMetadata(**_minimal_kwargs(bounds=bounds))


def test_rejects_incomplete_bounds():
    """Bounds missing a required key are rejected."""
    with pytest.raises(MetadataError, match="missing required key"):
        DatasetMetadata(**_minimal_kwargs(bounds={"min_x": 0.0, "max_x": 100.0}))


def test_accepts_finite_nodata_value():
    """A finite nodata value is accepted, including a large negative sentinel."""
    record = DatasetMetadata(**_minimal_kwargs(nodata_value=-9999.0))

    assert record.nodata_value == -9999.0


def test_rejects_nan_nodata_value():
    """
    A NaN nodata value is rejected.

    NaN never compares equal to itself, so a NaN sentinel could not be used to
    identify nodata pixels even if it serialized.
    """
    with pytest.raises(MetadataError, match="must be finite"):
        DatasetMetadata(**_minimal_kwargs(nodata_value=float("nan")))


def test_rejects_infinite_nodata_value():
    """An infinite nodata value is rejected."""
    with pytest.raises(MetadataError, match="must be finite"):
        DatasetMetadata(**_minimal_kwargs(nodata_value=float("-inf")))


# ----------------------------------------------------------------------
# Processing steps
# ----------------------------------------------------------------------


def test_adds_processing_step_with_full_provenance():
    """
    A step records the operation, tool, version, and parameters.

    Together these answer what ran, with which tool, and under what settings —
    the information a run needs to be reproducible.
    """
    record = DatasetMetadata(**_minimal_kwargs())

    record.add_processing_step(
        step="reprojection",
        description="Reprojected to UTM 43N",
        tool="GDAL",
        version="3.8.1",
        parameters={"target_crs": "EPSG:32643"},
    )

    assert len(record.processing_steps) == 1

    entry = record.processing_steps[0]
    assert entry["step"] == "reprojection"
    assert entry["description"] == "Reprojected to UTM 43N"
    assert entry["tool"] == "GDAL"
    assert entry["version"] == "3.8.1"
    assert entry["parameters"] == {"target_crs": "EPSG:32643"}
    assert entry["timestamp"]


def test_processing_step_omits_absent_optional_fields():
    """
    Optional step fields are omitted rather than stored as None.

    An absent key says the detail was not recorded; a null value would read as
    a recorded absence.
    """
    record = DatasetMetadata(**_minimal_kwargs())

    record.add_processing_step(step="validation")

    entry = record.processing_steps[0]
    assert set(entry) == {"step", "timestamp"}


def test_processing_steps_preserve_order():
    """
    Steps are appended in order.

    Provenance is a sequence: slope derived before depression filling would
    describe a different pipeline than the one that ran.
    """
    record = DatasetMetadata(**_minimal_kwargs())

    for step in ("reprojection", "depression_filling", "slope"):
        record.add_processing_step(step=step)

    assert [entry["step"] for entry in record.processing_steps] == [
        "reprojection",
        "depression_filling",
        "slope",
    ]


def test_processing_step_defaults_timestamp_to_utc():
    """An omitted step timestamp defaults to the current UTC time."""
    record = DatasetMetadata(**_minimal_kwargs())

    before = datetime.now(timezone.utc)
    record.add_processing_step(step="reprojection")
    after = datetime.now(timezone.utc)

    parsed = datetime.fromisoformat(record.processing_steps[0]["timestamp"])

    assert parsed.tzinfo is not None
    assert before <= parsed <= after


def test_processing_step_preserves_supplied_timestamp():
    """A supplied step timestamp is stored unchanged."""
    record = DatasetMetadata(**_minimal_kwargs())

    record.add_processing_step(
        step="reprojection", timestamp="2026-01-15T10:30:00+00:00"
    )

    assert record.processing_steps[0]["timestamp"] == "2026-01-15T10:30:00+00:00"


def test_rejects_blank_step_name():
    """A whitespace-only step name is rejected."""
    record = DatasetMetadata(**_minimal_kwargs())

    with pytest.raises(MetadataError, match="non-empty string"):
        record.add_processing_step(step="   ")


def test_rejects_non_dict_step_parameters():
    """Step parameters must be a dictionary."""
    record = DatasetMetadata(**_minimal_kwargs())

    with pytest.raises(MetadataError, match="must be a dictionary"):
        record.add_processing_step(step="reprojection", parameters=["EPSG:32643"])


def test_rejects_unserializable_step_parameters():
    """
    A step carrying an unserializable parameter is rejected on append.

    Rejecting at append time points at the offending call; discovering it at
    write time would surface long after the pipeline had moved on.
    """
    record = DatasetMetadata(**_minimal_kwargs())

    with pytest.raises(MetadataError, match="not JSON-serializable"):
        record.add_processing_step(
            step="reprojection", parameters={"path": Path("/tmp/dem.tif")}
        )


def test_rejects_nan_in_step_parameters():
    """A NaN parameter is rejected, since it is not valid JSON."""
    record = DatasetMetadata(**_minimal_kwargs())

    with pytest.raises(MetadataError, match="not JSON-serializable"):
        record.add_processing_step(
            step="slope", parameters={"threshold": float("nan")}
        )


def test_rejects_unserializable_steps_at_construction():
    """
    Steps supplied directly to the constructor are checked too.

    Validating only add_processing_step would leave a bypass: a record built
    with a bad step list would construct successfully and fail later.
    """
    with pytest.raises(MetadataError, match="not JSON-serializable"):
        DatasetMetadata(
            **_minimal_kwargs(
                processing_steps=[{"step": "slope", "value": {1, 2, 3}}]
            )
        )


def test_rejects_non_list_processing_steps():
    """Processing steps must be a list."""
    with pytest.raises(MetadataError, match="must be a list"):
        DatasetMetadata(**_minimal_kwargs(processing_steps={"step": "slope"}))


def test_rejects_non_dict_step_entry():
    """Each step entry must be a dictionary."""
    with pytest.raises(MetadataError, match="must be a dictionary"):
        DatasetMetadata(**_minimal_kwargs(processing_steps=["reprojection"]))


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------


def test_to_dict_returns_json_safe_structure():
    """
    A record serializes under strict JSON rules.

    allow_nan=False is the strict setting: Python's encoder would otherwise
    emit bare NaN and Infinity, producing files other parsers reject.
    """
    record = DatasetMetadata(
        **_minimal_kwargs(
            resolution=(30.0, 30.0),
            dimensions={"width": 100, "height": 50},
            bounds={"min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 100.0},
            nodata_value=-9999.0,
        )
    )

    data = record.to_dict()
    json.dumps(data, allow_nan=False)

    assert data["dataset_id"] == "test_dataset"
    assert data["nodata_value"] == -9999.0


def test_to_dict_converts_resolution_tuple_to_list():
    """
    Resolution serializes as a list.

    JSON has no tuple type, so the tuple must become a list for the output to
    round-trip through any JSON parser.
    """
    record = DatasetMetadata(**_minimal_kwargs(resolution=(30.0, 30.0)))

    data = record.to_dict()

    assert data["resolution"] == [30.0, 30.0]
    assert isinstance(data["resolution"], list)


def test_to_json_produces_parseable_output():
    """to_json emits a string that parses back to the same content."""
    record = DatasetMetadata(**_minimal_kwargs(description="A test dataset"))

    parsed = json.loads(record.to_json())

    assert parsed["dataset_id"] == "test_dataset"
    assert parsed["description"] == "A test dataset"


def test_to_json_is_deterministic():
    """
    Serializing one record twice gives identical output.

    A record that serialized differently on each call would produce spurious
    diffs in version-controlled metadata.
    """
    record = DatasetMetadata(**_minimal_kwargs(processing_date="2026-01-15T10:30:00+00:00"))

    assert record.to_json() == record.to_json()


def test_to_dict_includes_every_declared_field():
    """
    Serialization emits all declared fields, including those left None.

    A consumer can then rely on a stable key set rather than probing for
    optional keys.
    """
    record = DatasetMetadata(**_minimal_kwargs())

    data = record.to_dict()

    for field in (
        "dataset_id",
        "dataset_name",
        "source",
        "dataset_type",
        "source_url",
        "description",
        "acquisition_date",
        "processing_date",
        "processing_status",
        "crs",
        "resolution",
        "dimensions",
        "bounds",
        "file_path",
        "file_format",
        "vertical_reference",
        "horizontal_reference",
        "nodata_value",
        "units",
        "processing_steps",
        "parent_dataset_id",
        "metadata_version",
    ):
        assert field in data, f"serialized output is missing '{field}'"


# ----------------------------------------------------------------------
# Round-tripping
# ----------------------------------------------------------------------


def test_from_dict_restores_a_serialized_record():
    """A serialized record reconstructs with every field preserved."""
    original = DatasetMetadata(
        **_minimal_kwargs(
            processing_status="processed",
            resolution=(30.0, 30.0),
            dimensions={"width": 100, "height": 50},
            bounds={"min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 100.0},
            nodata_value=-9999.0,
            units="meters",
            parent_dataset_id="parent_dataset",
        )
    )

    restored = DatasetMetadata.from_dict(original.to_dict())

    assert restored.dataset_id == original.dataset_id
    assert restored.processing_status == original.processing_status
    assert restored.resolution == original.resolution
    assert restored.dimensions == original.dimensions
    assert restored.bounds == original.bounds
    assert restored.nodata_value == original.nodata_value
    assert restored.parent_dataset_id == original.parent_dataset_id


def test_round_trip_preserves_processing_steps():
    """
    Provenance survives a serialization round trip intact.

    Steps are the part of a record most likely to be lost in conversion, and
    losing them would defeat the point of recording them.
    """
    original = DatasetMetadata(**_minimal_kwargs())
    original.add_processing_step(
        step="reprojection",
        tool="GDAL",
        version="3.8.1",
        parameters={"target_crs": "EPSG:32643"},
    )
    original.add_processing_step(step="slope", tool="GDAL", version="3.8.1")

    restored = DatasetMetadata.from_dict(original.to_dict())

    assert restored.processing_steps == original.processing_steps
    assert len(restored.processing_steps) == 2


def test_round_trip_through_json_preserves_content():
    """A record survives a full serialize-parse-reconstruct cycle."""
    original = DatasetMetadata(
        **_minimal_kwargs(
            processing_status="derived",
            source_url="https://example.invalid/dataset",
            resolution=(30.0, 30.0),
        )
    )

    restored = DatasetMetadata.from_dict(json.loads(original.to_json()))

    assert restored.to_dict() == original.to_dict()


def test_round_trip_preserves_processing_date():
    """The processing date is not regenerated on reconstruction."""
    original = DatasetMetadata(**_minimal_kwargs())
    original_date = original.processing_date

    restored = DatasetMetadata.from_dict(original.to_dict())

    assert restored.processing_date == original_date


# ----------------------------------------------------------------------
# from_dict validation
# ----------------------------------------------------------------------


def test_from_dict_rejects_non_dict_input():
    """Input that is not a dictionary is rejected."""
    with pytest.raises(MetadataError, match="must be a dictionary"):
        DatasetMetadata.from_dict("not a dictionary")


def test_from_dict_rejects_missing_required_field():
    """
    A dictionary missing a required field is rejected, naming what is absent.

    Constructing a partial record would produce provenance that cannot identify
    the dataset it describes.
    """
    with pytest.raises(MetadataError, match="missing required field"):
        DatasetMetadata.from_dict(
            {"dataset_id": "test", "dataset_name": "Test"}
        )


def test_from_dict_rejects_unknown_field():
    """
    An unrecognized field is rejected rather than ignored.

    Silently dropping a key would hide a schema mismatch: a caller supplying
    'datasetId' would see it vanish without warning.
    """
    with pytest.raises(MetadataError, match="unknown field"):
        DatasetMetadata.from_dict(
            _minimal_kwargs(unexpected_field="value")
        )


def test_from_dict_applies_the_same_validation():
    """
    Reconstruction enforces the same rules as construction.

    A record loaded from disk must be no less valid than one built in memory,
    or the file becomes a way to bypass validation.
    """
    with pytest.raises(MetadataError, match="Invalid processing_status"):
        DatasetMetadata.from_dict(
            _minimal_kwargs(processing_status="not_a_status")
        )


def test_from_dict_does_not_require_the_file_to_exist(tmp_path):
    """
    A record referencing a nonexistent file still reconstructs.

    file_path is provenance, not a live handle: a record describing an archived
    or relocated product must remain readable.
    """
    absent = str(tmp_path / "long_gone.tif")

    restored = DatasetMetadata.from_dict(_minimal_kwargs(file_path=absent))

    assert restored.file_path == absent
    assert not Path(absent).exists()


# ----------------------------------------------------------------------
# from_raster
# ----------------------------------------------------------------------


def test_from_raster_populates_spatial_fields(make_raster):
    """
    Spatial fields are read from the file rather than supplied by the caller.

    Reading them removes the chance of a record describing a raster it does not
    match.
    """
    path = make_raster(width=4, height=4, crs="EPSG:32643", pixel_size=30.0)

    record = DatasetMetadata.from_raster(
        filepath=str(path),
        dataset_id="from_raster",
        dataset_name="From Raster",
        source="Test Fixture",
        dataset_type="terrain_dsm",
    )

    assert record.dimensions == {"width": 4, "height": 4}
    assert record.resolution == (30.0, 30.0)
    assert record.bounds is not None
    assert record.file_path == str(path)


def test_from_raster_stores_crs_as_json_safe_dictionary(make_raster):
    """
    The CRS is stored as a plain dictionary, not a live object.

    CRSHandler returns a pyproj.CRS alongside the scalar fields. Keeping that
    object would make the record unserializable, so it is reduced to EPSG, WKT,
    and related values here.
    """
    path = make_raster(crs="EPSG:32643")

    record = DatasetMetadata.from_raster(
        filepath=str(path),
        dataset_id="crs_record",
        dataset_name="CRS Record",
        source="Test Fixture",
        dataset_type="terrain_dsm",
    )

    assert isinstance(record.crs, dict)
    assert record.crs["epsg"] == 32643
    assert "pyproj_crs" not in record.crs


def test_from_raster_output_serializes_strictly(make_raster):
    """
    A record built from a raster serializes under strict JSON.

    This is the path where a live CRS object would leak, so the check runs
    against the full serialized output rather than the CRS block alone.
    """
    path = make_raster(crs="EPSG:32643")

    record = DatasetMetadata.from_raster(
        filepath=str(path),
        dataset_id="serializable",
        dataset_name="Serializable",
        source="Test Fixture",
        dataset_type="terrain_dsm",
    )

    json.dumps(record.to_dict(), allow_nan=False)


def test_from_raster_defaults_to_raw_status(make_raster):
    """A record built from a raster defaults to the raw stage."""
    path = make_raster()

    record = DatasetMetadata.from_raster(
        filepath=str(path),
        dataset_id="default_status",
        dataset_name="Default Status",
        source="Test Fixture",
        dataset_type="terrain_dsm",
    )

    assert record.processing_status == "raw"


def test_from_raster_accepts_supplied_provenance(make_raster):
    """Optional provenance passed through is preserved."""
    path = make_raster()

    record = DatasetMetadata.from_raster(
        filepath=str(path),
        dataset_id="with_provenance",
        dataset_name="With Provenance",
        source="Copernicus GLO-30",
        dataset_type="terrain_dsm",
        processing_status="processed",
        source_url="https://dataspace.copernicus.eu/",
        acquisition_date="2021-06-01",
        units="meters",
        vertical_reference="EGM2008 ellipsoidal height",
        parent_dataset_id="parent_dataset",
    )

    assert record.source == "Copernicus GLO-30"
    assert record.processing_status == "processed"
    assert record.acquisition_date == "2021-06-01"
    assert record.vertical_reference == "EGM2008 ellipsoidal height"
    assert record.parent_dataset_id == "parent_dataset"


def test_from_raster_raises_for_missing_file(tmp_path):
    """
    A nonexistent raster raises MetadataError, not the underlying CRSError.

    Callers of the metadata layer handle MetadataError; letting a data-layer
    exception escape would leak an implementation detail into their error
    handling.
    """
    with pytest.raises(MetadataError, match="Cannot inspect raster"):
        DatasetMetadata.from_raster(
            filepath=str(tmp_path / "absent.tif"),
            dataset_id="missing",
            dataset_name="Missing",
            source="Test Fixture",
            dataset_type="terrain_dsm",
        )


def test_from_raster_raises_for_unreadable_file(tmp_path):
    """A file that is not a raster raises MetadataError."""
    path = tmp_path / "not_a_raster.tif"
    path.write_bytes(b"this is not a GeoTIFF")

    with pytest.raises(MetadataError, match="Cannot inspect raster"):
        DatasetMetadata.from_raster(
            filepath=str(path),
            dataset_id="unreadable",
            dataset_name="Unreadable",
            source="Test Fixture",
            dataset_type="terrain_dsm",
        )


def test_from_raster_record_round_trips(make_raster):
    """A record built from a raster survives a serialization round trip."""
    path = make_raster(crs="EPSG:32643")

    original = DatasetMetadata.from_raster(
        filepath=str(path),
        dataset_id="round_trip",
        dataset_name="Round Trip",
        source="Test Fixture",
        dataset_type="terrain_dsm",
    )

    restored = DatasetMetadata.from_dict(json.loads(original.to_json()))

    assert restored.to_dict() == original.to_dict()


# ----------------------------------------------------------------------
# Provenance lineage
# ----------------------------------------------------------------------


def test_records_derivation_lineage():
    """
    A derived record names the dataset it came from.

    parent_dataset_id is what makes a chain of products traceable back to the
    source terrain rather than each appearing to arrive from nowhere.
    """
    record = DatasetMetadata(
        **_minimal_kwargs(
            dataset_id="slope_utm43n",
            dataset_type="slope",
            processing_status="processed",
            parent_dataset_id="dsm_filled_utm43n",
        )
    )

    assert record.parent_dataset_id == "dsm_filled_utm43n"
    assert record.to_dict()["parent_dataset_id"] == "dsm_filled_utm43n"


def test_full_provenance_record_serializes(make_raster):
    """
    A record with lineage and a step history serializes strictly.

    This is the shape the processing pipeline writes for each product, so it
    exercises the combination rather than each part alone.
    """
    path = make_raster(crs="EPSG:32643")

    record = DatasetMetadata.from_raster(
        filepath=str(path),
        dataset_id="slope_utm43n",
        dataset_name="Slope (UTM 43N)",
        source="Copernicus GLO-30",
        dataset_type="slope",
        processing_status="processed",
        parent_dataset_id="dsm_filled_utm43n",
        units="degrees",
    )
    record.add_processing_step(
        step="slope",
        description="Horn slope from the filled surface",
        tool="GDAL",
        version="3.8.1",
        parameters={"algorithm": "Horn", "compute_edges": True},
    )

    data = record.to_dict()
    json.dumps(data, allow_nan=False)

    assert data["parent_dataset_id"] == "dsm_filled_utm43n"
    assert data["units"] == "degrees"
    assert len(data["processing_steps"]) == 1
    assert data["processing_steps"][0]["tool"] == "GDAL"