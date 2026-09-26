"""
CRS utility tests for FlowSight Phase 1

Exercises app.data.crs directly: how the CRS handler interprets, normalizes,
and compares coordinate reference systems.

Scope:
    This module asks "does our CRS utility manipulate CRS objects correctly?"
    Whether the terrain validator acts on those results belongs in
    test_validators.py. validate_crs appears here only for its CRS reasoning —
    semantic comparison, mismatch detection, rejection of an unusable expected
    CRS — not for its role in a validation report.

    Raster reading is covered only where CRS extraction requires a file.
    Bounds, resolution, and dimensions are read through this module but are
    plain rasterio passthroughs; they are asserted just enough to confirm the
    CRS handler returns them in the documented shape.

Optional dependencies:
    crs.py is importable without rasterio or pyproj and raises CRSError with
    installation guidance when an operation needs a missing one. Those branches
    are reached by monkeypatching the module-level availability flags, which is
    safe because the module reads them at call time and monkeypatch restores
    them after each test. The flags are never mutated globally.

No external dependencies:
    Every raster is synthetic and confined to tmp_path. No network access, no
    GDAL CLI, no WhiteboxTools.
"""

import pyproj
import pytest

from app.data import crs as crs_module
from app.data.crs import CRSError, CRSHandler


# ----------------------------------------------------------------------
# Module import contract
# ----------------------------------------------------------------------


def test_module_binds_optional_dependencies():
    """
    Optional dependency names are always bound, never left undefined.

    The except branches assign None rather than leaving the name unbound. An
    unbound name would raise NameError at import time wherever it appears in an
    annotation, which is the failure this binding prevents.
    """
    assert hasattr(crs_module, "pyproj")
    assert hasattr(crs_module, "rasterio")
    assert isinstance(crs_module.PYPROJ_AVAILABLE, bool)
    assert isinstance(crs_module.RASTERIO_AVAILABLE, bool)


def test_normalize_crs_annotation_is_deferred():
    """
    The pyproj.CRS return annotation is stored as a string, not evaluated.

    from __future__ import annotations defers evaluation, so the annotation
    survives even when pyproj is absent. If it were evaluated eagerly the
    module would fail to import in a pyproj-less environment.
    """
    annotation = CRSHandler.normalize_crs.__annotations__["return"]

    assert isinstance(annotation, str)
    assert annotation == "pyproj.CRS"


# ----------------------------------------------------------------------
# normalize_crs - accepted representations
# ----------------------------------------------------------------------


def test_normalize_crs_from_epsg_integer():
    """An EPSG code given as an integer normalizes to the matching CRS."""
    result = CRSHandler.normalize_crs(32643)

    assert isinstance(result, pyproj.CRS)
    assert result.to_epsg() == 32643


def test_normalize_crs_from_epsg_string():
    """An 'EPSG:code' string normalizes to the matching CRS."""
    result = CRSHandler.normalize_crs("EPSG:4326")

    assert isinstance(result, pyproj.CRS)
    assert result.to_epsg() == 4326


def test_normalize_crs_from_wkt_string():
    """A WKT definition normalizes back to the CRS it describes."""
    wkt = pyproj.CRS.from_epsg(32643).to_wkt()

    result = CRSHandler.normalize_crs(wkt)

    assert isinstance(result, pyproj.CRS)
    assert result.to_epsg() == 32643


def test_normalize_crs_from_proj_string():
    """A PROJ string normalizes to an equivalent CRS."""
    proj = "+proj=utm +zone=43 +datum=WGS84 +units=m +no_defs"

    result = CRSHandler.normalize_crs(proj)

    assert isinstance(result, pyproj.CRS)
    assert result == pyproj.CRS.from_epsg(32643)


def test_normalize_crs_returns_existing_pyproj_object():
    """
    An existing pyproj.CRS is returned as-is.

    Already-normalized input needs no conversion, and returning the same object
    avoids a needless round-trip through WKT.
    """
    original = pyproj.CRS.from_epsg(32643)

    result = CRSHandler.normalize_crs(original)

    assert result is original


def test_normalize_crs_converts_rasterio_object(make_raster):
    """
    A rasterio CRS object is converted to pyproj.CRS rather than passed through.

    pyproj is the canonical internal representation. Returning a rasterio
    object unchanged would leave two CRS types circulating and make equality
    comparisons unreliable.
    """
    import rasterio

    path = make_raster(crs="EPSG:32643")

    with rasterio.open(path) as src:
        rasterio_crs = src.crs

    result = CRSHandler.normalize_crs(rasterio_crs)

    assert isinstance(result, pyproj.CRS)
    assert not isinstance(result, type(rasterio_crs))
    assert result.to_epsg() == 32643


def test_normalize_crs_is_idempotent():
    """Normalizing an already-normalized CRS yields an equal CRS."""
    once = CRSHandler.normalize_crs(32643)
    twice = CRSHandler.normalize_crs(once)

    assert twice == once


def test_normalize_crs_unifies_equivalent_representations():
    """
    Different spellings of one CRS normalize to equal objects.

    This is the property that makes downstream comparison reliable: a caller
    supplying 32643, "EPSG:32643", or WKT must produce the same CRS.
    """
    from_int = CRSHandler.normalize_crs(32643)
    from_string = CRSHandler.normalize_crs("EPSG:32643")
    from_wkt = CRSHandler.normalize_crs(pyproj.CRS.from_epsg(32643).to_wkt())

    assert from_int == from_string
    assert from_string == from_wkt


# ----------------------------------------------------------------------
# normalize_crs - rejected input
# ----------------------------------------------------------------------


def test_normalize_crs_rejects_unknown_epsg_code():
    """An EPSG code with no definition raises CRSError."""
    with pytest.raises(CRSError, match="Invalid EPSG code"):
        CRSHandler.normalize_crs(999999999)


def test_normalize_crs_rejects_unparseable_string():
    """A string that is not a CRS definition raises CRSError."""
    with pytest.raises(CRSError, match="Cannot parse CRS string"):
        CRSHandler.normalize_crs("this is not a coordinate system")


def test_normalize_crs_rejects_empty_string():
    """An empty string raises CRSError rather than yielding a default CRS."""
    with pytest.raises(CRSError):
        CRSHandler.normalize_crs("")


def test_normalize_crs_rejects_none():
    """
    None raises CRSError naming the unsupported type.

    Silently treating None as "no CRS specified" would let a caller's omission
    pass as a valid comparison.
    """
    with pytest.raises(CRSError, match="Unsupported CRS format"):
        CRSHandler.normalize_crs(None)


def test_normalize_crs_rejects_unsupported_type():
    """An object of an unsupported type raises CRSError naming that type."""
    with pytest.raises(CRSError, match="Unsupported CRS format"):
        CRSHandler.normalize_crs([32643])


def test_normalize_crs_rejects_float():
    """
    A float is rejected even when numerically an EPSG code.

    32643.0 is not an EPSG code, and coercing it would accept a caller's type
    error as valid input.
    """
    with pytest.raises(CRSError, match="Unsupported CRS format"):
        CRSHandler.normalize_crs(32643.0)


# ----------------------------------------------------------------------
# get_raster_crs
# ----------------------------------------------------------------------


def test_get_raster_crs_returns_all_representations(make_raster):
    """
    Reading a raster's CRS returns EPSG, WKT, PROJ, and a pyproj object.

    Each is present so callers with different needs — a metadata record, a
    comparison, a display string — do not each re-derive it.
    """
    path = make_raster(crs="EPSG:32643")

    result = CRSHandler.get_raster_crs(str(path))

    assert result is not None
    assert result["epsg"] == 32643
    assert isinstance(result["pyproj_crs"], pyproj.CRS)
    assert result["wkt"]
    assert result["proj4"]


def test_get_raster_crs_returns_canonical_pyproj_object(make_raster):
    """
    The returned CRS object is pyproj, converted from rasterio's.

    Callers compare against this object directly, so it must be the canonical
    type rather than whatever rasterio happened to produce.
    """
    path = make_raster(crs="EPSG:4326")

    result = CRSHandler.get_raster_crs(str(path))

    assert isinstance(result["pyproj_crs"], pyproj.CRS)
    assert result["pyproj_crs"] == pyproj.CRS.from_epsg(4326)


def test_get_raster_crs_returns_none_for_raster_without_crs(make_raster):
    """
    A raster with no CRS returns None rather than raising.

    An absent CRS is a fact about the file, not an error in reading it. The
    caller decides whether that is acceptable.
    """
    path = make_raster(name="no_crs.tif", crs=None)

    result = CRSHandler.get_raster_crs(str(path))

    assert result is None


def test_get_raster_crs_reads_geographic_crs(make_raster):
    """A geographic CRS is read as accurately as a projected one."""
    path = make_raster(name="geographic.tif", crs="EPSG:4326", pixel_size=0.001)

    result = CRSHandler.get_raster_crs(str(path))

    assert result["epsg"] == 4326
    assert result["pyproj_crs"].is_geographic is True


def test_get_raster_crs_reads_projected_crs(make_raster):
    """A projected CRS is identified as projected."""
    path = make_raster(crs="EPSG:32643")

    result = CRSHandler.get_raster_crs(str(path))

    assert result["epsg"] == 32643
    assert result["pyproj_crs"].is_projected is True


def test_get_raster_crs_raises_for_missing_file(tmp_path):
    """A path that does not exist raises CRSError naming the file."""
    with pytest.raises(CRSError, match="File not found"):
        CRSHandler.get_raster_crs(str(tmp_path / "absent.tif"))


def test_get_raster_crs_raises_for_unreadable_file(tmp_path):
    """A file that is not a raster raises CRSError."""
    path = tmp_path / "not_a_raster.tif"
    path.write_bytes(b"this is not a GeoTIFF")

    with pytest.raises(CRSError):
        CRSHandler.get_raster_crs(str(path))


# ----------------------------------------------------------------------
# validate_crs - CRS reasoning only
# ----------------------------------------------------------------------


def test_validate_crs_matches_identical_crs(make_raster):
    """A raster checked against its own CRS matches, with no message."""
    path = make_raster(crs="EPSG:32643")

    matches, message = CRSHandler.validate_crs(str(path), expected_crs=32643)

    assert matches is True
    assert message is None


def test_validate_crs_detects_mismatch(make_raster):
    """
    A different CRS does not match, and the message names both.

    The raster is projected UTM 43N and the expectation is geographic WGS84 —
    a match here would mean comparison is not happening at all.
    """
    path = make_raster(crs="EPSG:32643")

    matches, message = CRSHandler.validate_crs(str(path), expected_crs=4326)

    assert matches is False
    assert "32643" in message or "UTM" in message


def test_validate_crs_compares_semantically_not_textually(make_raster):
    """
    Equivalent CRS spellings compare equal.

    A raster written in EPSG:32643 must match a PROJ string describing the same
    projection. String comparison would fail this, which is why comparison goes
    through pyproj equality.
    """
    path = make_raster(crs="EPSG:32643")
    proj = "+proj=utm +zone=43 +datum=WGS84 +units=m +no_defs"

    matches, message = CRSHandler.validate_crs(str(path), expected_crs=proj)

    assert matches is True
    assert message is None


def test_validate_crs_accepts_equivalent_input_forms(make_raster):
    """An integer EPSG and its string form give identical verdicts."""
    path = make_raster(crs="EPSG:32643")

    from_int = CRSHandler.validate_crs(str(path), expected_crs=32643)
    from_string = CRSHandler.validate_crs(str(path), expected_crs="EPSG:32643")

    assert from_int == (True, None)
    assert from_string == (True, None)


def test_validate_crs_reports_missing_crs(make_raster):
    """A raster with no CRS does not match, whatever was expected."""
    path = make_raster(name="no_crs.tif", crs=None)

    matches, message = CRSHandler.validate_crs(str(path), expected_crs=32643)

    assert matches is False
    assert message == "No CRS found in raster"


def test_validate_crs_raises_for_invalid_expected_crs(make_raster):
    """
    An unusable expected CRS raises rather than returning False.

    A caller passing garbage has made a programming error, not discovered a
    mismatch. Returning False would disguise the bug as a validation result.
    """
    path = make_raster(crs="EPSG:32643")

    with pytest.raises(CRSError, match="Invalid expected CRS"):
        CRSHandler.validate_crs(str(path), expected_crs="nonsense")


def test_validate_crs_raises_for_missing_file(tmp_path):
    """Validating a nonexistent file raises CRSError."""
    with pytest.raises(CRSError, match="File not found"):
        CRSHandler.validate_crs(str(tmp_path / "absent.tif"), expected_crs=32643)


# ----------------------------------------------------------------------
# Raster geometry accessors
# ----------------------------------------------------------------------


def test_get_raster_bounds_returns_extent_in_native_crs(make_raster):
    """
    Bounds are reported in the raster's own CRS, not reprojected.

    A 4x4 grid at 30-unit pixels from origin (0, 120) spans exactly 120 units
    in each direction.
    """
    path = make_raster(width=4, height=4, origin=(0.0, 120.0), pixel_size=30.0)

    bounds = CRSHandler.get_raster_bounds(str(path))

    assert bounds == {"min_x": 0.0, "max_x": 120.0, "min_y": 0.0, "max_y": 120.0}


def test_get_raster_resolution_returns_absolute_pixel_size(make_raster):
    """
    Resolution is returned as positive values.

    A north-up geotransform carries a negative y step; pixel size is a
    magnitude, so the negative sign must not surface.
    """
    path = make_raster(pixel_size=30.0)

    resolution = CRSHandler.get_raster_resolution(str(path))

    assert resolution == (30.0, 30.0)


def test_get_raster_dimensions_returns_pixel_counts(make_raster):
    """Dimensions report width and height in pixels."""
    path = make_raster(width=6, height=3)

    dimensions = CRSHandler.get_raster_dimensions(str(path))

    assert dimensions == {"width": 6, "height": 3}


def test_geometry_accessors_raise_for_missing_file(tmp_path):
    """Each geometry accessor raises CRSError for a nonexistent file."""
    absent = str(tmp_path / "absent.tif")

    with pytest.raises(CRSError, match="File not found"):
        CRSHandler.get_raster_bounds(absent)

    with pytest.raises(CRSError, match="File not found"):
        CRSHandler.get_raster_resolution(absent)

    with pytest.raises(CRSError, match="File not found"):
        CRSHandler.get_raster_dimensions(absent)


# ----------------------------------------------------------------------
# get_raster_metadata
# ----------------------------------------------------------------------


def test_get_raster_metadata_combines_every_accessor(make_raster):
    """
    Combined metadata carries CRS, bounds, resolution, and dimensions.

    This is the shape the metadata layer consumes, so all four keys must be
    present rather than omitted when a value is unavailable.
    """
    path = make_raster(width=4, height=4, crs="EPSG:32643", pixel_size=30.0)

    metadata = CRSHandler.get_raster_metadata(str(path))

    assert set(metadata) == {"crs", "bounds", "resolution", "dimensions"}
    assert metadata["crs"]["epsg"] == 32643
    assert metadata["resolution"] == (30.0, 30.0)
    assert metadata["dimensions"] == {"width": 4, "height": 4}


def test_get_raster_metadata_reports_none_crs_without_failing(make_raster):
    """
    A raster with no CRS yields metadata with crs None and geometry intact.

    Geometry is readable regardless of projection, so an absent CRS must not
    discard the rest of the metadata.
    """
    path = make_raster(name="no_crs.tif", crs=None, width=4, height=4)

    metadata = CRSHandler.get_raster_metadata(str(path))

    assert metadata["crs"] is None
    assert metadata["dimensions"] == {"width": 4, "height": 4}
    assert metadata["bounds"] is not None


def test_get_raster_metadata_raises_for_missing_file(tmp_path):
    """Combined metadata raises CRSError for a nonexistent file."""
    with pytest.raises(CRSError, match="File not found"):
        CRSHandler.get_raster_metadata(str(tmp_path / "absent.tif"))


# ----------------------------------------------------------------------
# Optional dependency handling
# ----------------------------------------------------------------------


def test_normalize_crs_raises_when_pyproj_unavailable(monkeypatch):
    """
    Normalization without pyproj raises CRSError with installation guidance.

    The flag is read at call time, so patching it reaches the degradation
    branch on a machine where pyproj is installed. monkeypatch restores it.
    """
    monkeypatch.setattr(crs_module, "PYPROJ_AVAILABLE", False)

    with pytest.raises(CRSError, match="pip install pyproj"):
        CRSHandler.normalize_crs(32643)


def test_get_raster_crs_raises_when_pyproj_unavailable(make_raster, monkeypatch):
    """
    Reading a CRS without pyproj raises CRSError, not NameError.

    This method converts through pyproj.CRS.from_wkt, so an unguarded call
    would fail on the unbound name. The guard turns that into a clear message.
    """
    path = make_raster(crs="EPSG:32643")
    monkeypatch.setattr(crs_module, "PYPROJ_AVAILABLE", False)

    with pytest.raises(CRSError, match="pip install pyproj"):
        CRSHandler.get_raster_crs(str(path))


def test_get_raster_crs_raises_when_rasterio_unavailable(make_raster, monkeypatch):
    """Reading a CRS without rasterio raises CRSError with guidance."""
    path = make_raster(crs="EPSG:32643")
    monkeypatch.setattr(crs_module, "RASTERIO_AVAILABLE", False)

    with pytest.raises(CRSError, match="pip install rasterio"):
        CRSHandler.get_raster_crs(str(path))


def test_geometry_accessors_raise_when_rasterio_unavailable(
    make_raster, monkeypatch
):
    """Each geometry accessor reports the missing rasterio dependency."""
    path = make_raster()
    monkeypatch.setattr(crs_module, "RASTERIO_AVAILABLE", False)

    with pytest.raises(CRSError, match="pip install rasterio"):
        CRSHandler.get_raster_bounds(str(path))

    with pytest.raises(CRSError, match="pip install rasterio"):
        CRSHandler.get_raster_resolution(str(path))

    with pytest.raises(CRSError, match="pip install rasterio"):
        CRSHandler.get_raster_dimensions(str(path))


def test_validate_crs_raises_when_dependencies_unavailable(
    make_raster, monkeypatch
):
    """Validation without both dependencies raises CRSError naming both."""
    path = make_raster(crs="EPSG:32643")
    monkeypatch.setattr(crs_module, "PYPROJ_AVAILABLE", False)

    with pytest.raises(CRSError, match="rasterio and pyproj required"):
        CRSHandler.validate_crs(str(path), expected_crs=32643)


def test_dependency_failure_never_returns_a_verdict(make_raster, monkeypatch):
    """
    A missing dependency raises rather than reporting a CRS mismatch.

    Silently returning False would let an incomplete environment look like a
    projection problem, sending a caller to debug the wrong thing.
    """
    path = make_raster(crs="EPSG:32643")
    monkeypatch.setattr(crs_module, "PYPROJ_AVAILABLE", False)

    with pytest.raises(CRSError):
        CRSHandler.validate_crs(str(path), expected_crs=4326)