"""
Terrain provider tests for FlowSight Phase 1

Exercises app.data.terrain_provider.TerrainProvider: how it resolves terrain
products across lifecycle stages, confines paths to its data root, reports
availability, and delegates raster access.

Scope:
    Provider behaviour only — path resolution, stage mapping, confinement,
    listing, and the shape of what it delegates. The correctness of raster
    reading belongs to test_crs.py, validation rules to test_validators.py,
    metadata content to test_metadata.py, and pipeline orchestration to
    test_processing_service.py.

    Where the provider delegates, the real component runs. verify_terrain
    invokes the actual validator rather than a stub returning True: the point
    is to prove the integration works, and a stub would prove only that a mock
    was called.

Model terrain stage compatibility:
    The provider accepts stage values from app.models.terrain.TerrainStage as
    well as its own, since both carry the same string values. This is a public
    compatibility guarantee worth testing on its own terms; it does not require
    asserting anything about how the two enums are implemented internally.

No external calls:
    Every raster is synthetic and confined to tmp_path. No GDAL CLI, no
    WhiteboxTools, no network access, no pipeline execution.
"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.data.terrain_provider import (
    TERRAIN_SUBDIRECTORY,
    TerrainNotFoundError,
    TerrainProvider,
    TerrainProviderError,
    TerrainStage,
    UnsupportedTerrainStageError,
)
from app.data.validators import RasterValidationReport
from app.models.terrain import TerrainStage as ModelTerrainStage


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _write_raster(
    path: Path,
    width: int = 4,
    height: int = 4,
    crs: str | None = "EPSG:32643",
) -> Path:
    """
    Write a small synthetic GeoTIFF at a given path.

    Used where the provider needs a real readable raster rather than a
    placeholder byte file.

    Args:
        path: Destination path, created along with any missing parents.
        width: Raster width in pixels.
        height: Raster height in pixels.
        crs: CRS to write, or None to omit it.

    Returns:
        The path written.
    """
    array = np.arange(width * height, dtype="float32").reshape(height, width)

    path.parent.mkdir(parents=True, exist_ok=True)

    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": "float32",
        "transform": from_origin(0.0, height * 30.0, 30.0, 30.0),
    }
    if crs is not None:
        profile["crs"] = crs

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)

    return path


def _place(provider: TerrainProvider, stage: TerrainStage, name: str) -> Path:
    """
    Write a synthetic raster into a provider's stage directory.

    Args:
        provider: The provider whose data root is used.
        stage: Lifecycle stage to place the product at.
        name: Filename inside the stage directory.

    Returns:
        The path written.
    """
    return _write_raster(provider.get_stage_directory(stage) / name)


# ----------------------------------------------------------------------
# Construction and data root
# ----------------------------------------------------------------------


def test_uses_supplied_data_root(data_root):
    """The provider is rooted at the data root it was given."""
    provider = TerrainProvider(data_root=data_root)

    assert provider.data_root == data_root


def test_accepts_string_data_root(data_root):
    """A data root given as a string is normalized to a Path."""
    provider = TerrainProvider(data_root=str(data_root))

    assert provider.data_root == data_root
    assert isinstance(provider.data_root, Path)


def test_rejects_non_path_data_root():
    """
    A data root of an unusable type raises rather than being coerced.

    Coercing an arbitrary object would produce a nonsense path that only
    surfaces later as a confusing missing-file error.
    """
    with pytest.raises(TerrainProviderError, match="must be a str or Path"):
        TerrainProvider(data_root=42)


def test_data_root_need_not_exist(tmp_path):
    """
    Construction succeeds against a directory that does not exist yet.

    The provider resolves paths; creating directories is the caller's business,
    and refusing to construct would make it impossible to describe a location
    before populating it.
    """
    absent = tmp_path / "not_created_yet"

    provider = TerrainProvider(data_root=absent)

    assert provider.data_root == absent
    assert not absent.exists()


def test_construction_creates_no_directories(tmp_path):
    """Constructing a provider does not write to disk."""
    absent = tmp_path / "untouched"

    TerrainProvider(data_root=absent)

    assert not absent.exists()


# ----------------------------------------------------------------------
# Stage directories
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "directory"),
    [
        (TerrainStage.RAW, "raw"),
        (TerrainStage.VALIDATED, "validated"),
        (TerrainStage.PROCESSED, "processed"),
    ],
)
def test_each_stage_maps_to_its_directory(terrain_provider, stage, directory):
    """Each lifecycle stage resolves to its own directory under the root."""
    resolved = terrain_provider.get_stage_directory(stage)

    assert resolved == terrain_provider.data_root / directory / TERRAIN_SUBDIRECTORY


def test_stages_resolve_to_distinct_directories(terrain_provider):
    """
    No two stages share a directory.

    Products must never mix between stages: a raw product appearing among
    processed ones would misrepresent what has been done to it.
    """
    directories = {
        terrain_provider.get_stage_directory(stage) for stage in TerrainStage
    }

    assert len(directories) == len(list(TerrainStage))


def test_stage_accepts_its_string_value(terrain_provider):
    """A stage given as its string value resolves identically to the enum."""
    from_enum = terrain_provider.get_stage_directory(TerrainStage.PROCESSED)
    from_string = terrain_provider.get_stage_directory("processed")

    assert from_string == from_enum


def test_stage_string_is_case_insensitive(terrain_provider):
    """A stage string is matched without regard to case."""
    assert terrain_provider.get_stage_directory("PROCESSED") == (
        terrain_provider.get_stage_directory(TerrainStage.PROCESSED)
    )


def test_stage_accepts_the_model_enum(terrain_provider):
    """
    The domain model's TerrainStage is accepted as well as the provider's.

    Both are str enums with the same values, so a caller holding either can
    address the provider. This is asserted rather than assumed, because the
    two enums are separate classes.
    """
    assert terrain_provider.get_stage_directory(ModelTerrainStage.PROCESSED) == (
        terrain_provider.get_stage_directory(TerrainStage.PROCESSED)
    )


def test_unknown_stage_raises(terrain_provider):
    """An unrecognised stage raises, and the message lists the valid ones."""
    with pytest.raises(UnsupportedTerrainStageError, match="Unsupported terrain stage"):
        terrain_provider.get_stage_directory("archived")


def test_unknown_stage_error_is_a_provider_error():
    """Stage errors derive from the provider's base exception."""
    assert issubclass(UnsupportedTerrainStageError, TerrainProviderError)
    assert issubclass(TerrainNotFoundError, TerrainProviderError)


# ----------------------------------------------------------------------
# Path resolution
# ----------------------------------------------------------------------


def test_resolves_filename_under_its_stage(terrain_provider):
    """A filename resolves to a path inside the requested stage directory."""
    resolved = terrain_provider.get_terrain_path("dem.tif", TerrainStage.RAW)

    assert resolved == terrain_provider.get_stage_directory(TerrainStage.RAW) / "dem.tif"


def test_resolution_does_not_require_existence(terrain_provider):
    """
    A path resolves for a product that does not exist.

    Resolution answers where a product would live, which a caller needs before
    writing one.
    """
    resolved = terrain_provider.get_terrain_path("future.tif", TerrainStage.PROCESSED)

    assert resolved.name == "future.tif"
    assert not resolved.exists()


def test_resolution_creates_nothing(terrain_provider):
    """Resolving a path writes nothing to disk."""
    stage_directory = terrain_provider.get_stage_directory(TerrainStage.PROCESSED)
    before = set(stage_directory.iterdir())

    terrain_provider.get_terrain_path("nothing.tif", TerrainStage.PROCESSED)

    assert set(stage_directory.iterdir()) == before


def test_resolution_defaults_to_processed_stage(terrain_provider):
    """
    Path resolution defaults to the processed stage.

    Processed products are what downstream consumers read, so that is the
    stage a caller most often means.
    """
    default = terrain_provider.get_terrain_path("dem.tif")
    explicit = terrain_provider.get_terrain_path("dem.tif", TerrainStage.PROCESSED)

    assert default == explicit


def test_resolves_a_nested_relative_path(terrain_provider):
    """A relative subdirectory is preserved beneath the stage directory."""
    resolved = terrain_provider.get_terrain_path("tiles/north.tif", TerrainStage.RAW)

    expected = terrain_provider.get_stage_directory(TerrainStage.RAW) / "tiles" / "north.tif"
    assert resolved == expected


def test_returns_a_path_object(terrain_provider):
    """Resolution returns a Path, not a string."""
    resolved = terrain_provider.get_terrain_path("dem.tif", TerrainStage.RAW)

    assert isinstance(resolved, Path)


# ----------------------------------------------------------------------
# Path confinement
# ----------------------------------------------------------------------


def test_rejects_parent_traversal(terrain_provider):
    """
    A filename containing '..' is refused.

    Resolving it would read outside the managed data root, which is the whole
    reason products are addressed by filename rather than by path.
    """
    with pytest.raises(TerrainProviderError, match="traverse parent directories"):
        terrain_provider.get_terrain_path("../outside.tif", TerrainStage.RAW)


def test_rejects_deep_parent_traversal(terrain_provider):
    """Repeated traversal segments are refused."""
    with pytest.raises(TerrainProviderError, match="traverse parent directories"):
        terrain_provider.get_terrain_path("../../etc/passwd", TerrainStage.RAW)


def test_rejects_traversal_hidden_mid_path(terrain_provider):
    """
    Traversal is refused wherever it appears, not only at the start.

    A check that only inspected the leading segment would let
    'tiles/../../escape.tif' through.
    """
    with pytest.raises(TerrainProviderError, match="traverse parent directories"):
        terrain_provider.get_terrain_path("tiles/../../escape.tif", TerrainStage.RAW)


def test_rejects_absolute_posix_path(terrain_provider):
    """An absolute path is refused rather than resolved."""
    with pytest.raises(TerrainProviderError, match="must be relative"):
        terrain_provider.get_terrain_path("/etc/passwd", TerrainStage.RAW)


def test_rejects_absolute_path_to_a_real_file(terrain_provider, tmp_path):
    """
    An absolute path is refused even when it points at an existing file.

    Refusal is about confinement, not existence: a path outside the root must
    not be reachable whether or not something is there.
    """
    outside = _write_raster(tmp_path / "outside" / "real.tif")

    with pytest.raises(TerrainProviderError, match="must be relative"):
        terrain_provider.get_terrain_path(str(outside), TerrainStage.RAW)


def test_rejects_empty_filename(terrain_provider):
    """An empty filename is refused."""
    with pytest.raises(TerrainProviderError, match="must not be empty"):
        terrain_provider.get_terrain_path("", TerrainStage.RAW)


def test_rejects_whitespace_filename(terrain_provider):
    """A whitespace-only filename is refused."""
    with pytest.raises(TerrainProviderError, match="must not be empty"):
        terrain_provider.get_terrain_path("   ", TerrainStage.RAW)


def test_rejects_non_string_filename(terrain_provider):
    """A filename of an unusable type is refused."""
    with pytest.raises(TerrainProviderError, match="must be a str or Path"):
        terrain_provider.get_terrain_path(42, TerrainStage.RAW)


def test_confinement_applies_to_existence_checks(terrain_provider):
    """
    Confinement is enforced by terrain_exists, not only by resolution.

    A caller probing existence must not be able to reach outside the root
    either, so the check is refused rather than answered.
    """
    with pytest.raises(TerrainProviderError):
        terrain_provider.terrain_exists("../outside.tif", TerrainStage.RAW)


def test_confinement_applies_to_retrieval(terrain_provider):
    """Confinement is enforced when requiring a product."""
    with pytest.raises(TerrainProviderError):
        terrain_provider.require_terrain("/etc/passwd", TerrainStage.RAW)


# ----------------------------------------------------------------------
# Existence
# ----------------------------------------------------------------------


def test_reports_an_existing_product(terrain_provider):
    """A product present at a stage is reported as existing."""
    _place(terrain_provider, TerrainStage.RAW, "present.tif")

    assert terrain_provider.terrain_exists("present.tif", TerrainStage.RAW) is True


def test_reports_a_missing_product(terrain_provider):
    """A product absent from a stage is reported as missing."""
    assert terrain_provider.terrain_exists("absent.tif", TerrainStage.RAW) is False


def test_existence_is_stage_specific(terrain_provider):
    """
    A product at one stage is not visible at another.

    Stage isolation is what makes the lifecycle meaningful: a raw product must
    not appear to have been processed.
    """
    _place(terrain_provider, TerrainStage.RAW, "staged.tif")

    assert terrain_provider.terrain_exists("staged.tif", TerrainStage.RAW) is True
    assert terrain_provider.terrain_exists("staged.tif", TerrainStage.PROCESSED) is False
    assert terrain_provider.terrain_exists("staged.tif", TerrainStage.VALIDATED) is False


def test_directory_is_not_a_product(terrain_provider):
    """A directory is not reported as an existing product."""
    stage_directory = terrain_provider.get_stage_directory(TerrainStage.RAW)
    (stage_directory / "a_directory.tif").mkdir(parents=True)

    assert terrain_provider.terrain_exists("a_directory.tif", TerrainStage.RAW) is False


def test_existence_check_creates_nothing(terrain_provider):
    """Checking existence writes nothing to disk."""
    stage_directory = terrain_provider.get_stage_directory(TerrainStage.RAW)

    terrain_provider.terrain_exists("absent.tif", TerrainStage.RAW)

    assert list(stage_directory.iterdir()) == []


# ----------------------------------------------------------------------
# Required retrieval
# ----------------------------------------------------------------------


def test_requires_an_existing_product(terrain_provider):
    """Requiring a present product returns its path."""
    written = _place(terrain_provider, TerrainStage.RAW, "present.tif")

    assert terrain_provider.require_terrain("present.tif", TerrainStage.RAW) == written


def test_requiring_a_missing_product_raises(terrain_provider):
    """A missing product raises TerrainNotFoundError."""
    with pytest.raises(TerrainNotFoundError):
        terrain_provider.require_terrain("absent.tif", TerrainStage.RAW)


def test_missing_product_error_names_the_stage(terrain_provider):
    """
    The error names the stage and the directory to place the file in.

    An operator seeing this needs to know where the product was expected, not
    only that it was absent.
    """
    with pytest.raises(TerrainNotFoundError) as excinfo:
        terrain_provider.require_terrain("absent.tif", TerrainStage.RAW)

    message = str(excinfo.value)
    assert "raw" in message
    assert "absent.tif" in message


def test_missing_product_error_states_nothing_is_generated(terrain_provider):
    """
    The error makes clear that the provider will not create the product.

    A message implying data might be fetched would misrepresent Phase 1: no
    terrain is ever downloaded or synthesized.
    """
    with pytest.raises(TerrainNotFoundError) as excinfo:
        terrain_provider.require_terrain("absent.tif", TerrainStage.RAW)

    assert "No terrain data is generated here" in str(excinfo.value)


def test_stage_accessors_target_their_stages(terrain_provider):
    """Each stage accessor retrieves from its own stage."""
    raw = _place(terrain_provider, TerrainStage.RAW, "sample.tif")
    validated = _place(terrain_provider, TerrainStage.VALIDATED, "sample.tif")
    processed = _place(terrain_provider, TerrainStage.PROCESSED, "sample.tif")

    assert terrain_provider.get_raw_terrain("sample.tif") == raw
    assert terrain_provider.get_validated_terrain("sample.tif") == validated
    assert terrain_provider.get_processed_terrain("sample.tif") == processed


def test_stage_accessor_raises_for_wrong_stage(terrain_provider):
    """
    A stage accessor does not fall back to another stage.

    Silently returning a raw product when a processed one was requested would
    hand the caller unprocessed terrain without saying so.
    """
    _place(terrain_provider, TerrainStage.RAW, "raw_only.tif")

    with pytest.raises(TerrainNotFoundError):
        terrain_provider.get_processed_terrain("raw_only.tif")


# ----------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------


def test_lists_nothing_for_an_empty_stage(terrain_provider):
    """An empty stage lists no products."""
    assert terrain_provider.list_terrain(TerrainStage.RAW) == []


def test_lists_nothing_for_a_missing_directory(tmp_path):
    """
    A stage directory that does not exist lists nothing rather than raising.

    An unpopulated stage is a normal state before any data arrives.
    """
    provider = TerrainProvider(data_root=tmp_path / "empty_root")

    assert provider.list_terrain(TerrainStage.RAW) == []


def test_lists_a_single_product(terrain_provider):
    """A stage holding one product lists it."""
    _place(terrain_provider, TerrainStage.RAW, "only.tif")

    assert terrain_provider.list_terrain(TerrainStage.RAW) == ["only.tif"]


def test_lists_every_product(terrain_provider):
    """A stage holding several products lists all of them."""
    for name in ("alpha.tif", "beta.tif", "gamma.tif"):
        _place(terrain_provider, TerrainStage.RAW, name)

    assert set(terrain_provider.list_terrain(TerrainStage.RAW)) == {
        "alpha.tif",
        "beta.tif",
        "gamma.tif",
    }


def test_listing_is_sorted(terrain_provider):
    """
    Listings are sorted, so repeated calls agree.

    Filesystem iteration order is not guaranteed; sorting makes the output
    stable enough for an API response and for comparison across runs.
    """
    for name in ("gamma.tif", "alpha.tif", "beta.tif"):
        _place(terrain_provider, TerrainStage.RAW, name)

    listing = terrain_provider.list_terrain(TerrainStage.RAW)

    assert listing == sorted(listing)
    assert listing == terrain_provider.list_terrain(TerrainStage.RAW)


def test_listing_is_stage_isolated(terrain_provider):
    """A product at one stage does not appear in another stage's listing."""
    _place(terrain_provider, TerrainStage.RAW, "raw_product.tif")
    _place(terrain_provider, TerrainStage.PROCESSED, "processed_product.tif")

    assert terrain_provider.list_terrain(TerrainStage.RAW) == ["raw_product.tif"]
    assert terrain_provider.list_terrain(TerrainStage.PROCESSED) == [
        "processed_product.tif"
    ]


def test_listing_defaults_to_processed_stage(terrain_provider):
    """Listing defaults to the processed stage."""
    _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")
    _place(terrain_provider, TerrainStage.RAW, "raw.tif")

    assert terrain_provider.list_terrain() == ["product.tif"]


def test_listing_applies_a_glob_pattern(terrain_provider):
    """A glob pattern narrows the listing."""
    _place(terrain_provider, TerrainStage.PROCESSED, "dem_utm43n.tif")
    _place(terrain_provider, TerrainStage.PROCESSED, "dem_slope_utm43n.tif")
    _place(terrain_provider, TerrainStage.PROCESSED, "other.tif")

    listing = terrain_provider.list_terrain(TerrainStage.PROCESSED, pattern="dem_*.tif")

    assert set(listing) == {"dem_utm43n.tif", "dem_slope_utm43n.tif"}


def test_listing_includes_non_raster_files(terrain_provider):
    """
    Listing reports every file, filtering by pattern rather than by format.

    The provider does not open files to decide what to list, so a stray
    document appears unless a pattern excludes it. Pinning this documents that
    a caller wanting only rasters must pass a pattern.
    """
    _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")
    (terrain_provider.get_stage_directory(TerrainStage.PROCESSED) / "notes.txt").write_text(
        "not a raster"
    )

    assert set(terrain_provider.list_terrain(TerrainStage.PROCESSED)) == {
        "product.tif",
        "notes.txt",
    }


def test_listing_excludes_directories(terrain_provider):
    """A subdirectory is not listed as a product."""
    stage_directory = terrain_provider.get_stage_directory(TerrainStage.PROCESSED)
    (stage_directory / "tiles").mkdir(parents=True)
    _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")

    assert terrain_provider.list_terrain(TerrainStage.PROCESSED) == ["product.tif"]


def test_listing_creates_nothing(terrain_provider):
    """
    Listing an absent stage directory does not create it.

    A read operation that created directories would leave a data root littered
    with empty stages after a status query.
    """
    provider = TerrainProvider(data_root=terrain_provider.data_root / "nested")

    provider.list_terrain(TerrainStage.RAW)

    assert not (provider.data_root / "raw").exists()


def test_listing_rejects_an_unknown_stage(terrain_provider):
    """An unrecognised stage raises rather than listing nothing."""
    with pytest.raises(UnsupportedTerrainStageError):
        terrain_provider.list_terrain("archived")


# ----------------------------------------------------------------------
# Raster access
# ----------------------------------------------------------------------


def test_opens_an_existing_product(terrain_provider):
    """
    A present product opens as a dataset the caller closes.

    The handle is returned rather than consumed so a caller can read windows
    without the provider loading pixels itself.
    """
    _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")

    with terrain_provider.open_terrain("product.tif", TerrainStage.PROCESSED) as src:
        assert src.width == 4
        assert src.height == 4


def test_opening_a_missing_product_raises(terrain_provider):
    """Opening an absent product raises TerrainNotFoundError."""
    with pytest.raises(TerrainNotFoundError):
        terrain_provider.open_terrain("absent.tif", TerrainStage.PROCESSED)


def test_opening_an_unreadable_product_raises_provider_error(terrain_provider):
    """
    A file that is not a raster raises a provider error, not a loader error.

    Callers handle TerrainProviderError; letting a data-layer exception escape
    would leak an implementation detail into their error handling.
    """
    stage_directory = terrain_provider.get_stage_directory(TerrainStage.PROCESSED)
    stage_directory.mkdir(parents=True, exist_ok=True)
    (stage_directory / "broken.tif").write_bytes(b"not a GeoTIFF")

    with pytest.raises(TerrainProviderError, match="Cannot open terrain product"):
        terrain_provider.open_terrain("broken.tif", TerrainStage.PROCESSED)


def test_reports_product_information(terrain_provider):
    """
    Product information reports the raster's structure.

    Dimensions come from the file, so they confirm the provider inspected the
    product it resolved rather than reporting a default.
    """
    _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")

    info = terrain_provider.get_terrain_info("product.tif", TerrainStage.PROCESSED)

    assert info["width"] == 4
    assert info["height"] == 4
    assert info["count"] == 1


def test_product_information_records_stage_and_filename(terrain_provider):
    """
    Information carries the resolved stage and the filename requested.

    These let a caller identify which product an information payload describes
    without re-deriving it.
    """
    _place(terrain_provider, TerrainStage.VALIDATED, "product.tif")

    info = terrain_provider.get_terrain_info("product.tif", TerrainStage.VALIDATED)

    assert info["stage"] == "validated"
    assert info["filename"] == "product.tif"


def test_product_information_reads_no_pixels(terrain_provider):
    """
    Information is structural and leaves the file unchanged.

    A read that rewrote the file would corrupt provenance, since the metadata
    layer hashes products.
    """
    written = _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")
    before = written.read_bytes()

    terrain_provider.get_terrain_info("product.tif", TerrainStage.PROCESSED)

    assert written.read_bytes() == before


def test_information_for_a_missing_product_raises(terrain_provider):
    """Requesting information about an absent product raises."""
    with pytest.raises(TerrainNotFoundError):
        terrain_provider.get_terrain_info("absent.tif", TerrainStage.PROCESSED)


# ----------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------


def test_verification_returns_a_validation_report(terrain_provider):
    """
    Verification returns the validator's report unchanged.

    The real validator runs: replacing it with a stub would test only that a
    mock was invoked, not that the provider integrates with it.
    """
    _place(terrain_provider, TerrainStage.RAW, "product.tif")

    report = terrain_provider.verify_terrain("product.tif", TerrainStage.RAW)

    assert isinstance(report, RasterValidationReport)


def test_verification_passes_a_sound_raster(terrain_provider):
    """A structurally sound raster verifies successfully."""
    _place(terrain_provider, TerrainStage.RAW, "sound.tif")

    report = terrain_provider.verify_terrain("sound.tif", TerrainStage.RAW)

    assert report.valid is True
    assert report.errors == []


def test_verification_fails_a_raster_without_crs(terrain_provider):
    """
    A raster with no CRS fails verification.

    This is the integration under test: the provider resolved the product and
    the validator reached a real verdict about it.
    """
    _write_raster(
        terrain_provider.get_stage_directory(TerrainStage.RAW) / "no_crs.tif",
        crs=None,
    )

    report = terrain_provider.verify_terrain("no_crs.tif", TerrainStage.RAW)

    assert report.valid is False
    assert len(report.errors) > 0


def test_verification_checks_a_supplied_crs(terrain_provider):
    """
    An expected CRS is passed through to the validator.

    The product is EPSG:32643 and the expectation is EPSG:4326, so a passing
    verdict would mean the argument was dropped.
    """
    _place(terrain_provider, TerrainStage.RAW, "utm.tif")

    report = terrain_provider.verify_terrain(
        "utm.tif", TerrainStage.RAW, expected_crs=4326
    )

    assert report.valid is False


def test_verification_assumes_no_crs_by_default(terrain_provider):
    """
    Without an expected CRS, a product is not judged against one.

    The correct CRS depends on the stage and the city, so the provider imposes
    none of its own.
    """
    _place(terrain_provider, TerrainStage.RAW, "utm.tif")

    report = terrain_provider.verify_terrain("utm.tif", TerrainStage.RAW)

    assert report.valid is True


def test_verification_skips_pixel_scanning_by_default(terrain_provider):
    """
    Pixel scanning is off by default, and the report says so.

    Scanning a city-scale raster is expensive, and this convenience wrapper is
    meant to be cheap enough to call before relying on a product.
    """
    _place(terrain_provider, TerrainStage.RAW, "product.tif")

    report = terrain_provider.verify_terrain("product.tif", TerrainStage.RAW)

    assert any("Pixel values were not validated" in w for w in report.warnings)


def test_verification_can_scan_pixels(terrain_provider):
    """Pixel scanning runs when the caller requests it."""
    _place(terrain_provider, TerrainStage.RAW, "product.tif")

    report = terrain_provider.verify_terrain(
        "product.tif", TerrainStage.RAW, check_pixel_values=True
    )

    assert not any("Pixel values were not validated" in w for w in report.warnings)


def test_verification_of_a_missing_product_raises(terrain_provider):
    """
    A missing product raises rather than returning an invalid report.

    Absence is not a validation failure: nothing was examined.
    """
    with pytest.raises(TerrainNotFoundError):
        terrain_provider.verify_terrain("absent.tif", TerrainStage.RAW)


def test_verification_leaves_the_product_unchanged(terrain_provider):
    """Verification does not modify the raster it inspects."""
    written = _place(terrain_provider, TerrainStage.RAW, "product.tif")
    before = written.read_bytes()

    terrain_provider.verify_terrain(
        "product.tif", TerrainStage.RAW, check_pixel_values=True
    )

    assert written.read_bytes() == before


def test_verification_is_stage_specific(terrain_provider):
    """Verification inspects the product at the stage requested."""
    _place(terrain_provider, TerrainStage.RAW, "sample.tif")

    with pytest.raises(TerrainNotFoundError):
        terrain_provider.verify_terrain("sample.tif", TerrainStage.PROCESSED)


# ----------------------------------------------------------------------
# Metadata delegation
# ----------------------------------------------------------------------


def test_metadata_takes_its_status_from_the_stage(terrain_provider):
    """
    A record's processing status comes from the stage it was read at.

    Taking it from the stage rather than from the caller means a product
    described from the raw directory can never be labelled processed.
    """
    _place(terrain_provider, TerrainStage.RAW, "product.tif")

    record = terrain_provider.build_terrain_metadata(
        "product.tif",
        dataset_id="product",
        dataset_name="Product",
        source="Test Fixture",
        dataset_type="terrain_dsm",
        stage=TerrainStage.RAW,
    )

    assert record.processing_status == "raw"


def test_metadata_reads_spatial_fields_from_the_product(terrain_provider):
    """
    Spatial fields are read from the file rather than supplied.

    Reading them removes the chance of a record describing a raster it does not
    match.
    """
    _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")

    record = terrain_provider.build_terrain_metadata(
        "product.tif",
        dataset_id="product",
        dataset_name="Product",
        source="Test Fixture",
        dataset_type="terrain_dsm",
        stage=TerrainStage.PROCESSED,
    )

    assert record.dimensions == {"width": 4, "height": 4}
    assert record.crs["epsg"] == 32643


def test_metadata_for_a_missing_product_raises(terrain_provider):
    """Building metadata for an absent product raises."""
    with pytest.raises(TerrainNotFoundError):
        terrain_provider.build_terrain_metadata(
            "absent.tif",
            dataset_id="absent",
            dataset_name="Absent",
            source="Test Fixture",
            dataset_type="terrain_dsm",
        )


def test_metadata_writes_nothing(terrain_provider):
    """
    Building a record does not write it to disk.

    Persistence is the caller's decision; a provider that wrote files during a
    read would surprise anyone inspecting a product.
    """
    written = _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")
    stage_directory = written.parent
    before = set(stage_directory.iterdir())

    terrain_provider.build_terrain_metadata(
        "product.tif",
        dataset_id="product",
        dataset_name="Product",
        source="Test Fixture",
        dataset_type="terrain_dsm",
    )

    assert set(stage_directory.iterdir()) == before


# ----------------------------------------------------------------------
# Data root isolation
# ----------------------------------------------------------------------


def test_providers_with_different_roots_are_isolated(tmp_path):
    """
    A product under one data root is invisible to a provider using another.

    Isolation is what lets tests and deployments coexist without depending on
    global filesystem state.
    """
    first = TerrainProvider(data_root=tmp_path / "first")
    second = TerrainProvider(data_root=tmp_path / "second")

    _place(first, TerrainStage.RAW, "product.tif")

    assert first.terrain_exists("product.tif", TerrainStage.RAW) is True
    assert second.terrain_exists("product.tif", TerrainStage.RAW) is False
    assert second.list_terrain(TerrainStage.RAW) == []


def test_isolated_roots_resolve_to_different_paths(tmp_path):
    """Two providers resolve the same filename to different locations."""
    first = TerrainProvider(data_root=tmp_path / "first")
    second = TerrainProvider(data_root=tmp_path / "second")

    assert first.get_terrain_path("dem.tif", TerrainStage.RAW) != (
        second.get_terrain_path("dem.tif", TerrainStage.RAW)
    )


def test_one_provider_does_not_disturb_another(tmp_path):
    """Operations on one provider leave another's products untouched."""
    first = TerrainProvider(data_root=tmp_path / "first")
    second = TerrainProvider(data_root=tmp_path / "second")

    written = _place(second, TerrainStage.RAW, "product.tif")
    before = written.read_bytes()

    first.list_terrain(TerrainStage.RAW)
    first.terrain_exists("product.tif", TerrainStage.RAW)

    assert written.read_bytes() == before


# ----------------------------------------------------------------------
# Read-only behaviour
# ----------------------------------------------------------------------


def test_read_operations_leave_the_data_root_unchanged(terrain_provider):
    """
    A sequence of reads changes nothing on disk.

    Taken together these are every read path a caller exercises, so this is the
    guarantee that inspecting terrain is safe.
    """
    written = _place(terrain_provider, TerrainStage.PROCESSED, "product.tif")
    before_bytes = written.read_bytes()
    before_files = {
        path for path in terrain_provider.data_root.rglob("*") if path.is_file()
    }

    terrain_provider.get_terrain_path("product.tif", TerrainStage.PROCESSED)
    terrain_provider.terrain_exists("product.tif", TerrainStage.PROCESSED)
    terrain_provider.list_terrain(TerrainStage.PROCESSED)
    terrain_provider.get_terrain_info("product.tif", TerrainStage.PROCESSED)
    terrain_provider.verify_terrain("product.tif", TerrainStage.PROCESSED)

    after_files = {
        path for path in terrain_provider.data_root.rglob("*") if path.is_file()
    }

    assert written.read_bytes() == before_bytes
    assert after_files == before_files