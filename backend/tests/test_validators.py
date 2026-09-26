"""
Validator tests for FlowSight Phase 1

Exercises app.data.validators directly, without the API or the service layer.

Scope:
    The validator's own decisions: whether a raster is structurally sound, has
    the expected CRS, sits within supplied resolution bounds, declares usable
    nodata, and contains usable pixel values.

    CRS interpretation itself belongs to test_crs.py. What is tested here is
    the validator's verdict — that a mismatch produces valid=False with a
    failing 'crs' check — not how pyproj compares two CRS objects.

Design notes:
    validate_terrain_raster defaults check_pixel_values to False, so tests
    covering pixel behaviour pass it explicitly.

    The validator has two distinct failure channels, and the tests keep them
    separate: a ValidationResult with valid=False means the raster failed a
    check, while a raised ValidationError means the check could not run at all.

No external dependencies:
    Every raster is synthetic, deterministic, and confined to tmp_path. No
    network access, no GDAL CLI, no WhiteboxTools.
"""

import math

import numpy as np
import pytest

from app.data.validators import (
    CHECK_CRS,
    CHECK_FILE,
    CHECK_NODATA,
    CHECK_PIXEL_VALUES,
    CHECK_RESOLUTION,
    CHECK_STRUCTURE,
    RasterValidationReport,
    RasterValidator,
    ValidationError,
    ValidationResult,
)


def _check_named(report: RasterValidationReport, name: str) -> ValidationResult:
    """
    Return the single result for a named check.

    Fails the test rather than returning None when the check is absent, so a
    validator that silently skips a check cannot be mistaken for one that
    passed it.

    Args:
        report: The validation report to search.
        name: Check name, e.g. CHECK_CRS.

    Returns:
        The matching ValidationResult.
    """
    matches = [result for result in report.checks if result.check == name]
    assert matches, f"report contains no '{name}' check: {[c.check for c in report.checks]}"
    assert len(matches) == 1, f"report contains {len(matches)} '{name}' checks"
    return matches[0]


def _check_names(report: RasterValidationReport) -> list[str]:
    """Return the names of every check the report executed, in order."""
    return [result.check for result in report.checks]


# ----------------------------------------------------------------------
# File check
# ----------------------------------------------------------------------


def test_validate_file_accepts_readable_raster(make_raster):
    """A readable GeoTIFF passes the file check."""
    path = make_raster()

    result = RasterValidator.validate_file(path)

    assert result.valid is True
    assert result.check == CHECK_FILE
    assert result.details["exists"] is True
    assert result.details["openable"] is True


def test_validate_file_rejects_missing_path(tmp_path):
    """A path that does not exist fails, and is reported as not existing."""
    result = RasterValidator.validate_file(tmp_path / "absent.tif")

    assert result.valid is False
    assert result.check == CHECK_FILE
    assert result.details["exists"] is False


def test_validate_file_rejects_directory(tmp_path):
    """A directory is not a raster, even though the path exists."""
    directory = tmp_path / "a_directory"
    directory.mkdir()

    result = RasterValidator.validate_file(directory)

    assert result.valid is False
    assert result.details["exists"] is False


def test_validate_file_rejects_unreadable_file(tmp_path):
    """
    A file that exists but is not a raster fails at the open step.

    The distinction matters: 'exists' is True while 'openable' is False, so the
    message points at a corrupt or wrong-format file rather than a missing one.
    """
    path = tmp_path / "not_a_raster.tif"
    path.write_bytes(b"this is not a GeoTIFF")

    result = RasterValidator.validate_file(path)

    assert result.valid is False
    assert result.details["exists"] is True
    assert result.details["openable"] is False


def test_validate_file_rejects_empty_file(tmp_path):
    """A zero-byte file cannot be opened as a raster."""
    path = tmp_path / "empty.tif"
    path.write_bytes(b"")

    result = RasterValidator.validate_file(path)

    assert result.valid is False
    assert result.details["openable"] is False


# ----------------------------------------------------------------------
# Structure check
# ----------------------------------------------------------------------


def test_validate_structure_accepts_well_formed_raster(make_raster):
    """
    A well-formed raster passes and reports its structural properties.

    Dimensions are asserted against the fixture's known 4x4 single-band grid.
    """
    path = make_raster(width=4, height=4)

    result = RasterValidator.validate_raster_structure(path)

    assert result.valid is True
    assert result.check == CHECK_STRUCTURE
    assert result.details["width"] == 4
    assert result.details["height"] == 4
    assert result.details["count"] == 1
    assert result.details["dtype"] == "float32"


def test_validate_structure_reports_bounds_and_resolution(make_raster):
    """
    Structural details carry finite bounds and resolution.

    These are read from the geotransform, so a raster written at a 30-unit
    pixel size must report exactly that.
    """
    path = make_raster(width=4, height=4, origin=(0.0, 120.0), pixel_size=30.0)

    result = RasterValidator.validate_raster_structure(path)

    assert result.valid is True

    bounds = result.details["bounds"]
    assert bounds["min_x"] == 0.0
    assert bounds["max_x"] == 120.0
    assert bounds["min_y"] == 0.0
    assert bounds["max_y"] == 120.0

    assert result.details["resolution"] == [30.0, 30.0]


def test_validate_structure_omits_server_path_free_fields(make_raster):
    """
    Transform coefficients are reported as six finite numbers.

    A None among them would mean a non-finite coefficient, which the structure
    check treats as an invalid transform.
    """
    path = make_raster()

    result = RasterValidator.validate_raster_structure(path)

    transform = result.details["transform"]
    assert len(transform) == 6
    assert all(coefficient is not None for coefficient in transform)


def test_validate_structure_rejects_unreadable_file(tmp_path):
    """A file that cannot be opened fails the structure check."""
    path = tmp_path / "broken.tif"
    path.write_bytes(b"not a raster")

    result = RasterValidator.validate_raster_structure(path)

    assert result.valid is False
    assert result.check == CHECK_STRUCTURE


def test_validate_structure_accepts_raster_without_crs(make_raster):
    """
    A raster with no CRS still passes the structure check.

    Structure and CRS are separate concerns: a CRS-less raster is structurally
    sound, and it is validate_crs that must reject it. Conflating the two would
    make the failure message point at the wrong problem.
    """
    path = make_raster(name="no_crs.tif", crs=None)

    result = RasterValidator.validate_raster_structure(path)

    assert result.valid is True


# ----------------------------------------------------------------------
# CRS check
# ----------------------------------------------------------------------


def test_validate_crs_accepts_present_crs_without_expectation(make_raster):
    """
    With no expected CRS supplied, a raster that has any CRS passes.

    The validator assumes no projection: presence is all that can be checked
    when the caller supplies nothing to compare against.
    """
    path = make_raster(crs="EPSG:32643")

    result = RasterValidator.validate_crs(path)

    assert result.valid is True
    assert result.check == CHECK_CRS
    assert result.details["crs_present"] is True
    assert result.details["epsg"] == 32643


def test_validate_crs_rejects_missing_crs(make_raster):
    """A raster with no CRS fails, and is reported as having none."""
    path = make_raster(name="no_crs.tif", crs=None)

    result = RasterValidator.validate_crs(path)

    assert result.valid is False
    assert result.details["crs_present"] is False


def test_validate_crs_accepts_matching_expected_crs(make_raster):
    """An expected CRS equal to the raster's CRS passes."""
    path = make_raster(crs="EPSG:32643")

    result = RasterValidator.validate_crs(path, expected_crs=32643)

    assert result.valid is True
    assert result.details["expected_crs_supplied"] is True


def test_validate_crs_rejects_mismatched_expected_crs(make_raster):
    """
    An expected CRS different from the raster's CRS fails.

    The raster is written in EPSG:32643 and checked against EPSG:4326, so a
    passing result here would mean CRS validation is not working at all.
    """
    path = make_raster(crs="EPSG:32643")

    result = RasterValidator.validate_crs(path, expected_crs=4326)

    assert result.valid is False
    assert result.details["epsg"] == 32643
    assert result.message


def test_validate_crs_accepts_equivalent_crs_representations(make_raster):
    """
    An integer EPSG and its string form are treated as the same CRS.

    Comparison is semantic rather than textual, so 32643 and "EPSG:32643" must
    both match a raster written in that projection.
    """
    path = make_raster(crs="EPSG:32643")

    from_int = RasterValidator.validate_crs(path, expected_crs=32643)
    from_string = RasterValidator.validate_crs(path, expected_crs="EPSG:32643")

    assert from_int.valid is True
    assert from_string.valid is True


def test_validate_crs_fails_when_missing_crs_has_expectation(make_raster):
    """A CRS-less raster fails even when an expected CRS is supplied."""
    path = make_raster(name="no_crs.tif", crs=None)

    result = RasterValidator.validate_crs(path, expected_crs=32643)

    assert result.valid is False
    assert result.details["crs_present"] is False


# ----------------------------------------------------------------------
# Resolution check
# ----------------------------------------------------------------------


def test_validate_resolution_accepts_positive_pixel_size(make_raster):
    """A raster with positive finite pixel size passes with no bounds given."""
    path = make_raster(pixel_size=30.0)

    result = RasterValidator.validate_resolution(path)

    assert result.valid is True
    assert result.check == CHECK_RESOLUTION
    assert result.details["pixel_width"] == 30.0
    assert result.details["pixel_height"] == 30.0


def test_validate_resolution_accepts_pixel_size_within_bounds(make_raster):
    """A pixel size inside the supplied range passes."""
    path = make_raster(pixel_size=30.0)

    result = RasterValidator.validate_resolution(
        path, min_resolution=10.0, max_resolution=50.0
    )

    assert result.valid is True
    assert result.details["min_resolution"] == 10.0
    assert result.details["max_resolution"] == 50.0


def test_validate_resolution_rejects_pixel_size_below_minimum(make_raster):
    """
    A pixel size finer than the minimum allowed fails.

    min_resolution is a minimum allowed PIXEL SIZE, so a 10-unit pixel against
    a 30-unit minimum is finer than permitted and must fail.
    """
    path = make_raster(pixel_size=10.0)

    result = RasterValidator.validate_resolution(path, min_resolution=30.0)

    assert result.valid is False
    assert result.details["pixel_width"] == 10.0


def test_validate_resolution_rejects_pixel_size_above_maximum(make_raster):
    """
    A pixel size coarser than the maximum allowed fails.

    A 100-unit pixel against a 30-unit maximum is coarser than permitted.
    """
    path = make_raster(pixel_size=100.0)

    result = RasterValidator.validate_resolution(path, max_resolution=30.0)

    assert result.valid is False
    assert result.details["pixel_width"] == 100.0


def test_validate_resolution_applies_no_bounds_by_default(make_raster):
    """
    An unusual pixel size passes when the caller supplies no bounds.

    The validator imposes no resolution assumption of its own: a 1000-unit
    pixel is unusual for terrain but is not the validator's decision to reject
    unless a bound was given.
    """
    path = make_raster(pixel_size=1000.0)

    result = RasterValidator.validate_resolution(path)

    assert result.valid is True
    assert result.details["min_resolution"] is None
    assert result.details["max_resolution"] is None


# ----------------------------------------------------------------------
# Nodata check
# ----------------------------------------------------------------------


def test_validate_nodata_accepts_declared_finite_value(make_raster):
    """A finite declared nodata value passes and is reported."""
    path = make_raster(nodata=-9999.0)

    result = RasterValidator.validate_nodata(path)

    assert result.valid is True
    assert result.check == CHECK_NODATA
    assert result.details["nodata_defined"] is True
    assert result.details["nodata_value"] == -9999.0


def test_validate_nodata_accepts_absent_declaration(make_raster):
    """
    A raster declaring no nodata value passes.

    Absence of a nodata declaration is a legitimate state, not a defect, so
    this must not fail. The check reports nodata_defined False so a caller can
    still notice.
    """
    path = make_raster(nodata=None)

    result = RasterValidator.validate_nodata(path)

    assert result.valid is True
    assert result.details["nodata_defined"] is False
    assert result.details["nodata_value"] is None


def test_validate_nodata_rejects_missing_band(make_raster):
    """Inspecting a band that does not exist fails."""
    path = make_raster()

    result = RasterValidator.validate_nodata(path, band=2)

    assert result.valid is False
    assert result.check == CHECK_NODATA
    assert result.details["band"] == 2


def test_validate_nodata_does_not_assert_pixel_validity(make_raster):
    """
    Passing the nodata check says nothing about pixel values.

    A raster whose pixels are entirely NaN still passes the nodata check,
    because that check reads metadata only. Pixel validity is a separate
    concern, and conflating them would let a caller believe the data is usable.
    """
    values = np.full((4, 4), np.nan, dtype="float32")
    path = make_raster(name="all_nan.tif", values=values, nodata=-9999.0)

    nodata_result = RasterValidator.validate_nodata(path)
    pixel_result = RasterValidator.validate_pixel_values(path)

    assert nodata_result.valid is True
    assert pixel_result.valid is False


# ----------------------------------------------------------------------
# Pixel-value check
# ----------------------------------------------------------------------


def test_validate_pixel_values_accepts_finite_data(make_raster):
    """
    A raster of finite values passes and reports accurate statistics.

    The fixture writes 0..15 across a 4x4 grid, so the reported minimum,
    maximum, and counts are exactly predictable.
    """
    path = make_raster(width=4, height=4)

    result = RasterValidator.validate_pixel_values(path)

    assert result.valid is True
    assert result.check == CHECK_PIXEL_VALUES
    assert result.details["total_pixels"] == 16
    assert result.details["valid_pixels"] == 16
    assert result.details["non_finite_pixels"] == 0
    assert result.details["min"] == 0.0
    assert result.details["max"] == 15.0


def test_validate_pixel_values_rejects_nan(make_raster):
    """
    A raster containing NaN fails, with the NaN count reported.

    NaN is not ordinary terrain: it propagates through every downstream
    calculation, so it must be caught here rather than surfacing later.
    """
    values = np.arange(16, dtype="float32").reshape(4, 4)
    values[0, 0] = np.nan
    path = make_raster(name="has_nan.tif", values=values)

    result = RasterValidator.validate_pixel_values(path)

    assert result.valid is False
    assert result.details["nan_pixels"] == 1
    assert result.details["non_finite_pixels"] == 1


def test_validate_pixel_values_rejects_positive_infinity(make_raster):
    """A raster containing +Inf fails, counted separately from NaN."""
    values = np.arange(16, dtype="float32").reshape(4, 4)
    values[1, 1] = np.inf
    path = make_raster(name="has_posinf.tif", values=values)

    result = RasterValidator.validate_pixel_values(path)

    assert result.valid is False
    assert result.details["positive_infinity_pixels"] == 1
    assert result.details["nan_pixels"] == 0


def test_validate_pixel_values_rejects_negative_infinity(make_raster):
    """A raster containing -Inf fails, counted separately."""
    values = np.arange(16, dtype="float32").reshape(4, 4)
    values[2, 2] = -np.inf
    path = make_raster(name="has_neginf.tif", values=values)

    result = RasterValidator.validate_pixel_values(path)

    assert result.valid is False
    assert result.details["negative_infinity_pixels"] == 1


def test_validate_pixel_values_excludes_nodata_from_statistics(make_raster):
    """
    Declared nodata pixels are excluded from the valid-data statistics.

    Four of sixteen pixels are set to the declared nodata value, so twelve
    remain valid and the reported minimum must come from real data rather than
    from the nodata sentinel.
    """
    values = np.arange(16, dtype="float32").reshape(4, 4)
    values[0, :] = -9999.0
    path = make_raster(name="with_nodata.tif", values=values, nodata=-9999.0)

    result = RasterValidator.validate_pixel_values(path)

    assert result.valid is True
    assert result.details["total_pixels"] == 16
    assert result.details["nodata_pixels"] == 4
    assert result.details["valid_pixels"] == 12
    assert result.details["min"] == 4.0
    assert result.details["max"] == 15.0


def test_validate_pixel_values_rejects_all_nodata_raster(make_raster):
    """
    A raster whose pixels are entirely nodata fails.

    Every pixel is the declared nodata value, leaving no data at all. A raster
    with nothing in it cannot be processed, so this must not pass.
    """
    values = np.full((4, 4), -9999.0, dtype="float32")
    path = make_raster(name="all_nodata.tif", values=values, nodata=-9999.0)

    result = RasterValidator.validate_pixel_values(path)

    assert result.valid is False
    assert result.details["valid_pixels"] == 0


def test_validate_pixel_values_applies_no_range_by_default(make_raster):
    """
    Extreme but finite values pass when no range is supplied.

    An elevation of 8000 is unusual for a coastal city but is not the
    validator's decision to reject: no elevation range is ever assumed.
    """
    values = np.full((4, 4), 8000.0, dtype="float32")
    path = make_raster(name="high_values.tif", values=values)

    result = RasterValidator.validate_pixel_values(path)

    assert result.valid is True
    assert result.details["min_value_constraint"] is None
    assert result.details["max_value_constraint"] is None


def test_validate_pixel_values_rejects_value_below_supplied_minimum(make_raster):
    """A value below the caller's minimum fails."""
    values = np.arange(16, dtype="float32").reshape(4, 4)
    path = make_raster(name="below_min.tif", values=values)

    result = RasterValidator.validate_pixel_values(path, min_value=5.0)

    assert result.valid is False
    assert result.details["min"] == 0.0
    assert result.details["min_value_constraint"] == 5.0


def test_validate_pixel_values_rejects_value_above_supplied_maximum(make_raster):
    """A value above the caller's maximum fails."""
    values = np.arange(16, dtype="float32").reshape(4, 4)
    path = make_raster(name="above_max.tif", values=values)

    result = RasterValidator.validate_pixel_values(path, max_value=10.0)

    assert result.valid is False
    assert result.details["max"] == 15.0
    assert result.details["max_value_constraint"] == 10.0


def test_validate_pixel_values_accepts_integer_raster(make_raster):
    """
    An integer raster passes without non-finite counts.

    Integer arrays cannot hold NaN or infinity, so those counts must be zero
    rather than the check attempting a finiteness test that does not apply.
    """
    values = np.arange(16, dtype="int32").reshape(4, 4)
    path = make_raster(name="integer.tif", values=values, dtype="int32")

    result = RasterValidator.validate_pixel_values(path)

    assert result.valid is True
    assert result.details["dtype"] == "int32"
    assert result.details["non_finite_pixels"] == 0
    assert result.details["nan_pixels"] == 0


def test_validate_pixel_values_rejects_missing_band(make_raster):
    """Reading a band that does not exist fails."""
    path = make_raster()

    result = RasterValidator.validate_pixel_values(path, band=2)

    assert result.valid is False
    assert result.details["band"] == 2


# ----------------------------------------------------------------------
# Combined validation
# ----------------------------------------------------------------------


def test_validate_terrain_raster_runs_full_sequence(make_raster):
    """
    A sound raster passes every check when pixel scanning is enabled.

    All six checks must appear: a report that silently skipped one would be
    reporting a weaker guarantee than it claims.
    """
    path = make_raster(crs="EPSG:32643", pixel_size=30.0, nodata=-9999.0)

    report = RasterValidator.validate_terrain_raster(
        path, expected_crs=32643, check_pixel_values=True
    )

    assert report.valid is True
    assert report.errors == []
    assert _check_names(report) == [
        CHECK_FILE,
        CHECK_STRUCTURE,
        CHECK_CRS,
        CHECK_RESOLUTION,
        CHECK_NODATA,
        CHECK_PIXEL_VALUES,
    ]


def test_validate_terrain_raster_skips_pixel_check_by_default(make_raster):
    """
    Pixel scanning is off by default and its absence is recorded as a warning.

    Scanning a city-scale raster is expensive, so the default skips it. The
    warning exists so a caller cannot mistake a cheap pass for a thorough one.
    """
    path = make_raster(crs="EPSG:32643")

    report = RasterValidator.validate_terrain_raster(path, expected_crs=32643)

    assert report.valid is True
    assert CHECK_PIXEL_VALUES not in _check_names(report)
    assert any("Pixel values were not validated" in w for w in report.warnings)


def test_validate_terrain_raster_stops_at_missing_file(tmp_path):
    """
    A missing file stops validation immediately.

    Running the remaining checks against a file that is not there would
    produce meaningless results, so only the file check appears.
    """
    report = RasterValidator.validate_terrain_raster(tmp_path / "absent.tif")

    assert report.valid is False
    assert _check_names(report) == [CHECK_FILE]
    assert len(report.errors) == 1


def test_validate_terrain_raster_stops_at_broken_structure(tmp_path):
    """An unopenable file stops after the file check."""
    path = tmp_path / "corrupt.tif"
    path.write_bytes(b"not a raster at all")

    report = RasterValidator.validate_terrain_raster(path)

    assert report.valid is False
    assert _check_names(report) == [CHECK_FILE]


def test_validate_terrain_raster_reports_crs_mismatch(make_raster):
    """
    A CRS mismatch fails the report while later checks still run.

    Unlike a missing file, a wrong CRS does not invalidate the remaining
    checks, so the sequence continues and the caller sees every problem at
    once rather than one per run.
    """
    path = make_raster(crs="EPSG:32643")

    report = RasterValidator.validate_terrain_raster(path, expected_crs=4326)

    assert report.valid is False
    assert _check_named(report, CHECK_CRS).valid is False
    assert _check_named(report, CHECK_RESOLUTION).valid is True
    assert any(f"[{CHECK_CRS}]" in error for error in report.errors)


def test_validate_terrain_raster_reports_missing_crs(make_raster):
    """A raster with no CRS fails the combined report."""
    path = make_raster(name="no_crs.tif", crs=None)

    report = RasterValidator.validate_terrain_raster(path)

    assert report.valid is False
    assert _check_named(report, CHECK_CRS).valid is False


def test_validate_terrain_raster_warns_without_expected_crs(make_raster):
    """
    Omitting an expected CRS passes but is recorded as a warning.

    Presence alone is a weaker guarantee than a match, and the warning makes
    that visible rather than leaving a caller to assume more was checked.
    """
    path = make_raster(crs="EPSG:32643")

    report = RasterValidator.validate_terrain_raster(path)

    assert report.valid is True
    assert any("No expected CRS was supplied" in w for w in report.warnings)


def test_validate_terrain_raster_warns_without_nodata(make_raster):
    """A raster declaring no nodata passes with a warning."""
    path = make_raster(crs="EPSG:32643", nodata=None)

    report = RasterValidator.validate_terrain_raster(path, expected_crs=32643)

    assert report.valid is True
    assert any("declares no nodata value" in w for w in report.warnings)


def test_validate_terrain_raster_reports_non_finite_pixels(make_raster):
    """A raster containing NaN fails the combined report on the pixel check."""
    values = np.arange(16, dtype="float32").reshape(4, 4)
    values[0, 0] = np.nan
    path = make_raster(name="report_nan.tif", values=values, crs="EPSG:32643")

    report = RasterValidator.validate_terrain_raster(
        path, expected_crs=32643, check_pixel_values=True
    )

    assert report.valid is False
    assert _check_named(report, CHECK_PIXEL_VALUES).valid is False


def test_validate_terrain_raster_accumulates_multiple_failures(make_raster):
    """
    Independent failures are all reported, not just the first.

    A raster with no CRS checked against a resolution bound it violates must
    surface both problems: fixing one and rediscovering the other on the next
    run wastes a full validation pass on a large raster.
    """
    path = make_raster(name="two_faults.tif", crs=None, pixel_size=100.0)

    report = RasterValidator.validate_terrain_raster(
        path, expected_crs=32643, max_resolution=30.0
    )

    assert report.valid is False
    assert _check_named(report, CHECK_CRS).valid is False
    assert _check_named(report, CHECK_RESOLUTION).valid is False
    assert len(report.errors) >= 2


def test_validate_terrain_raster_exposes_structural_info(make_raster):
    """The report carries the structural details for the validated raster."""
    path = make_raster(width=4, height=4, crs="EPSG:32643")

    report = RasterValidator.validate_terrain_raster(path, expected_crs=32643)

    assert report.info is not None
    assert report.info["width"] == 4
    assert report.info["height"] == 4


# ----------------------------------------------------------------------
# Result serialization
# ----------------------------------------------------------------------


def test_validation_result_serializes_to_json_safe_dict(make_raster):
    """
    A single result converts to a JSON-safe dictionary.

    Results travel to the API, so anything not serializable would fail at
    response time rather than here.
    """
    import json

    path = make_raster()
    result = RasterValidator.validate_file(path)

    data = result.to_dict()
    json.dumps(data, allow_nan=False)

    assert data["check"] == CHECK_FILE
    assert data["valid"] is True


def test_validation_report_serializes_to_json_safe_dict(make_raster):
    """
    A full report converts to a JSON-safe dictionary.

    allow_nan=False is deliberate: the report must not carry NaN or Infinity,
    which are not valid JSON even though Python's encoder emits them by
    default.
    """
    import json

    path = make_raster(crs="EPSG:32643")
    report = RasterValidator.validate_terrain_raster(
        path, expected_crs=32643, check_pixel_values=True
    )

    data = report.to_dict()
    json.dumps(data, allow_nan=False)

    assert data["valid"] is True
    assert isinstance(data["checks"], list)
    assert isinstance(data["errors"], list)
    assert isinstance(data["warnings"], list)


def test_validation_report_serializes_when_pixels_are_non_finite(make_raster):
    """
    A report on a NaN-containing raster is still JSON-safe.

    This is the case most likely to leak a raw NaN into the output, since the
    statistics are computed from data that contains one.
    """
    import json

    values = np.arange(16, dtype="float32").reshape(4, 4)
    values[0, 0] = np.nan
    path = make_raster(name="serialize_nan.tif", values=values, crs="EPSG:32643")

    report = RasterValidator.validate_terrain_raster(
        path, expected_crs=32643, check_pixel_values=True
    )

    json.dumps(report.to_dict(), allow_nan=False)

    assert report.valid is False