"""
API endpoint tests for FlowSight Phase 1

Verifies that the Phase 1 API is wired correctly: routes resolve at their
expected paths, responses carry the documented structure, and the no-data state
behaves as designed.

Scope:
    Behaviour at the HTTP boundary only. Raster validation, CRS handling,
    metadata generation, provider path resolution, and pipeline execution are
    covered by their own test modules and are not re-tested here.

No external dependencies:
    No test downloads data, reaches the network, or executes GDAL or
    WhiteboxTools. Processing tests use a FastAPI dependency override so no
    real pipeline runs; terrain tests point the service at an empty temporary
    data root.
"""

from types import SimpleNamespace

import pytest
from fastapi import status

from app.api.v1.endpoints import processing as processing_endpoints
from app.api.v1.endpoints import terrain as terrain_endpoints
from app.core.constants import API_VERSION_PREFIX
from app.main import app


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def isolated_client(client, terrain_service, processing_service):
    """
    Return a TestClient whose terrain and processing services use tmp_path.

    Without this override the endpoints resolve against the real project data
    directory, which would make results depend on whatever happens to be on
    disk. Pointing both services at an empty temporary root makes the no-data
    tests deterministic and keeps the real data untouched.

    Overrides are removed afterwards so they cannot leak into another test.

    Args:
        client: Shared TestClient fixture.
        terrain_service: TerrainService rooted at tmp_path.
        processing_service: TerrainProcessingService rooted at tmp_path.

    Yields:
        The TestClient with dependencies overridden.
    """
    app.dependency_overrides[terrain_endpoints.get_terrain_service] = (
        lambda: terrain_service
    )
    app.dependency_overrides[processing_endpoints.get_processing_service] = (
        lambda: processing_service
    )

    yield client

    app.dependency_overrides.pop(terrain_endpoints.get_terrain_service, None)
    app.dependency_overrides.pop(processing_endpoints.get_processing_service, None)


# ----------------------------------------------------------------------
# Application-level endpoints
# ----------------------------------------------------------------------


def test_root_endpoint_responds(client):
    """The root path returns a successful JSON response."""
    response = client.get("/")

    assert response.status_code == status.HTTP_200_OK
    assert isinstance(response.json(), dict)


def test_legacy_health_endpoint_responds(client):
    """The application-level /health path returns a successful JSON response."""
    response = client.get("/health")

    assert response.status_code == status.HTTP_200_OK
    assert isinstance(response.json(), dict)


def test_openapi_schema_is_available(client):
    """
    The OpenAPI schema generates without error.

    This is a cheap guard against a malformed response_model anywhere in the
    API: FastAPI builds the schema from every route, so a broken annotation
    fails here rather than only when that one endpoint is called.
    """
    response = client.get("/openapi.json")

    assert response.status_code == status.HTTP_200_OK
    assert "paths" in response.json()


# ----------------------------------------------------------------------
# System endpoints
# ----------------------------------------------------------------------


def test_system_health_returns_expected_shape(client):
    """
    /system/health reports a recognised status and identifies the service.

    The status value is asserted against the allowed set rather than a single
    expected value: whether the environment is 'ok' or 'degraded' depends on
    which geospatial tools are installed on the machine running the tests, and
    both are correct answers.
    """
    response = client.get(f"{API_VERSION_PREFIX}/system/health")

    assert response.status_code == status.HTTP_200_OK

    body = response.json()
    assert body["status"] in {"ok", "degraded", "unavailable"}
    assert body["service"]
    assert body["version"]


def test_system_status_includes_component_breakdown(client):
    """
    /system/status reports per-component availability.

    The component breakdown is the reason this endpoint exists separately from
    /health, so its presence and shape are what the test checks. Each entry
    must carry an 'available' flag; the value itself depends on the machine.
    """
    response = client.get(f"{API_VERSION_PREFIX}/system/status")

    assert response.status_code == status.HTTP_200_OK

    body = response.json()
    assert body["status"] in {"ok", "degraded", "unavailable"}
    assert body["message"]

    components = body["components"]
    assert isinstance(components, dict)
    assert "environment" in components

    for name in ("gdal_cli", "whitebox", "rasterio", "numpy"):
        assert name in components, f"missing component: {name}"
        assert "available" in components[name]


def test_system_status_reports_environment_availability(client):
    """The environment block reports availability and any missing tools."""
    response = client.get(f"{API_VERSION_PREFIX}/system/status")

    environment = response.json()["components"]["environment"]

    assert isinstance(environment["available"], bool)
    assert isinstance(environment["missing_tools"], list)


# ----------------------------------------------------------------------
# Terrain endpoints - no-data state
# ----------------------------------------------------------------------


def test_terrain_listing_is_empty_with_no_data(isolated_client):
    """
    Listing an empty data root returns every stage with an empty list.

    An empty stage is a normal state, not an error: the stage keys must still
    be present so a client can tell 'no products here' from 'unknown stage'.
    """
    response = isolated_client.get(f"{API_VERSION_PREFIX}/terrain")

    assert response.status_code == status.HTTP_200_OK

    body = response.json()
    assert set(body) == {"raw", "validated", "processed"}
    assert all(products == [] for products in body.values())


def test_terrain_listing_accepts_stage_filter(isolated_client):
    """A stage filter narrows the listing to that stage alone."""
    response = isolated_client.get(
        f"{API_VERSION_PREFIX}/terrain", params={"stage": "processed"}
    )

    assert response.status_code == status.HTTP_200_OK
    assert set(response.json()) == {"processed"}


def test_terrain_listing_rejects_unknown_stage(isolated_client):
    """An unrecognised stage is rejected by request validation."""
    response = isolated_client.get(
        f"{API_VERSION_PREFIX}/terrain", params={"stage": "not_a_stage"}
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_terrain_info_missing_product_returns_404(isolated_client):
    """Requesting a product that does not exist returns 404, not 500."""
    response = isolated_client.get(
        f"{API_VERSION_PREFIX}/terrain/info",
        params={"filename": "absent.tif", "stage": "raw"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"]


def test_terrain_info_requires_filename(isolated_client):
    """The filename parameter is required."""
    response = isolated_client.get(f"{API_VERSION_PREFIX}/terrain/info")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_terrain_info_rejects_absolute_path(isolated_client):
    """
    An absolute path is refused rather than resolved.

    The API addresses terrain by filename within a managed root. Accepting an
    absolute path would let a caller read outside it, so this must not return
    200 regardless of whether the path happens to exist.
    """
    response = isolated_client.get(
        f"{API_VERSION_PREFIX}/terrain/info",
        params={"filename": "/etc/passwd", "stage": "raw"},
    )

    assert response.status_code in {
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_404_NOT_FOUND,
    }


def test_terrain_info_rejects_parent_traversal(isolated_client):
    """A filename containing '..' is refused."""
    response = isolated_client.get(
        f"{API_VERSION_PREFIX}/terrain/info",
        params={"filename": "../../secret.tif", "stage": "raw"},
    )

    assert response.status_code in {
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_404_NOT_FOUND,
    }


def test_terrain_verify_missing_product_returns_404(isolated_client):
    """Verifying a product that does not exist returns 404."""
    response = isolated_client.post(
        f"{API_VERSION_PREFIX}/terrain/verify",
        params={"filename": "absent.tif", "stage": "raw"},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ----------------------------------------------------------------------
# Terrain endpoints - with a synthetic product
# ----------------------------------------------------------------------


def test_terrain_listing_reports_present_product(
    isolated_client, raw_terrain_file
):
    """A product placed at the raw stage appears in the listing."""
    response = isolated_client.get(f"{API_VERSION_PREFIX}/terrain")

    assert response.status_code == status.HTTP_200_OK
    assert raw_terrain_file.name in response.json()["raw"]


def test_terrain_info_returns_raster_structure(
    isolated_client, raw_terrain_file
):
    """
    Info reports the raster's structure and omits the server path.

    Dimensions are checked against the fixture's known 4x4 grid. The absence of
    'path' and 'filepath' is asserted because the endpoint strips them: server
    layout must not reach a client.
    """
    response = isolated_client.get(
        f"{API_VERSION_PREFIX}/terrain/info",
        params={"filename": raw_terrain_file.name, "stage": "raw"},
    )

    assert response.status_code == status.HTTP_200_OK

    body = response.json()
    assert body["width"] == 4
    assert body["height"] == 4
    assert body["count"] == 1
    assert body["stage"] == "raw"
    assert body["filename"] == raw_terrain_file.name

    assert "path" not in body
    assert "filepath" not in body


def test_terrain_verify_returns_check_results(
    isolated_client, raw_terrain_file
):
    """
    Verification returns every executed check with its verdict.

    Pixel scanning is left off, which is the endpoint's default: structural,
    CRS, resolution, and nodata checks still run, and the response must list
    them individually rather than only a summary verdict.
    """
    response = isolated_client.post(
        f"{API_VERSION_PREFIX}/terrain/verify",
        params={"filename": raw_terrain_file.name, "stage": "raw"},
    )

    assert response.status_code == status.HTTP_200_OK

    body = response.json()
    assert body["dataset_id"] == raw_terrain_file.name
    assert body["stage"] == "raw"
    assert isinstance(body["valid"], bool)
    assert len(body["checks"]) > 0

    for check in body["checks"]:
        assert check["check"]
        assert isinstance(check["valid"], bool)
        assert check["message"]


def test_terrain_verify_detects_crs_mismatch(
    isolated_client, data_root, make_raster
):
    """
    An expected CRS that does not match the product is reported as invalid.

    The fixture raster is written in EPSG:32643, so requesting EPSG:4326 must
    produce a failing verdict with a recorded error. A verification that
    reported valid here would be silently useless.
    """
    source = make_raster(name="utm_terrain.tif", crs="EPSG:32643")
    destination = data_root / "raw" / "terrain" / source.name
    destination.write_bytes(source.read_bytes())

    response = isolated_client.post(
        f"{API_VERSION_PREFIX}/terrain/verify",
        params={
            "filename": source.name,
            "stage": "raw",
            "expected_crs": 4326,
        },
    )

    assert response.status_code == status.HTTP_200_OK

    body = response.json()
    assert body["valid"] is False
    assert len(body["errors"]) > 0

    crs_checks = [c for c in body["checks"] if c["check"] == "crs"]
    assert crs_checks, "verification did not run a CRS check"
    assert crs_checks[0]["valid"] is False


# ----------------------------------------------------------------------
# Processing endpoints
# ----------------------------------------------------------------------


def test_processing_missing_input_returns_404(isolated_client):
    """
    Requesting processing for a nonexistent input returns 404.

    The input is resolved before any tool is invoked, so this path never
    reaches GDAL or WhiteboxTools regardless of what is installed.
    """
    response = isolated_client.post(
        f"{API_VERSION_PREFIX}/processing/terrain",
        params={
            "filename": "absent.tif",
            "source": "Test Fixture",
            "stage": "raw",
        },
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"]


def test_processing_requires_filename_and_source(isolated_client):
    """
    Both filename and source are required.

    Source is mandatory rather than defaulted so provenance records what was
    actually supplied instead of an assumption, and the API must enforce that.
    """
    missing_source = isolated_client.post(
        f"{API_VERSION_PREFIX}/processing/terrain",
        params={"filename": "something.tif"},
    )
    assert missing_source.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    missing_filename = isolated_client.post(
        f"{API_VERSION_PREFIX}/processing/terrain",
        params={"source": "Test Fixture"},
    )
    assert missing_filename.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_processing_rejects_unknown_stage(isolated_client):
    """An unrecognised stage is rejected by request validation."""
    response = isolated_client.post(
        f"{API_VERSION_PREFIX}/processing/terrain",
        params={
            "filename": "something.tif",
            "source": "Test Fixture",
            "stage": "not_a_stage",
        },
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_processing_missing_environment_returns_503(
    client, data_root, terrain_provider, monkeypatch
):
    """
    A present input with an unavailable toolchain returns exactly 503.

    Determinism:
        The environment report is stubbed to report every required tool
        missing, so the gate fires whether or not GDAL and WhiteboxTools are
        installed on this machine. The patch targets the name inside
        processing_service, because that module binds get_environment_report
        into its own namespace with a from-import; patching app.core.environment
        would leave the already-bound reference untouched.

    Input file:
        A dummy byte file is sufficient. process_terrain resolves the input by
        existence alone and reaches the environment gate before anything opens
        the raster, so this test needs neither a valid GeoTIFF nor rasterio,
        and therefore still runs in exactly the degraded environment it exists
        to verify.

    Distinguishing 503 from 404:
        The input exists, so a 404 here would mean the input check wrongly
        rejected a present file rather than the environment gate firing.
    """
    from app.services import processing_service as processing_service_module
    from app.services.processing_service import TerrainProcessingService

    terrain_file = data_root / "raw" / "terrain" / "gate_input.tif"
    terrain_file.write_bytes(b"placeholder; never opened before the gate")

    def unavailable_environment():
        """Report a toolchain with nothing installed."""
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

    monkeypatch.setattr(
        processing_service_module,
        "get_environment_report",
        unavailable_environment,
    )

    def unreachable_runner(args, timeout):
        raise AssertionError(f"a real command was invoked: {args}")

    def unreachable_whitebox():
        raise AssertionError("WhiteboxTools was instantiated during an API test")

    stubbed = TerrainProcessingService(
        provider=terrain_provider,
        command_runner=unreachable_runner,
        whitebox_factory=unreachable_whitebox,
    )

    app.dependency_overrides[processing_endpoints.get_processing_service] = (
        lambda: stubbed
    )

    try:
        response = client.post(
            f"{API_VERSION_PREFIX}/processing/terrain",
            params={
                "filename": terrain_file.name,
                "source": "Test Fixture",
                "stage": "raw",
            },
        )
    finally:
        app.dependency_overrides.pop(
            processing_endpoints.get_processing_service, None
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    # The message must identify the environment gate specifically, so that an
    # unrelated 503 from elsewhere in the stack cannot satisfy this test.
    detail = response.json()["detail"]
    assert "Missing" in detail
    assert "GDAL" in detail