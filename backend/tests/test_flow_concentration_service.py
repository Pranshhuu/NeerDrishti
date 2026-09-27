"""
Tests for FlowConcentrationService.visualization_path and the
GET /api/v1/runoff/flow-concentration/image endpoint.

Endpoint tests override the existing get_flow_concentration_service
dependency with a lightweight fake, so they exercise only the endpoint's
own file-existence check and response handling - not the real terrain
raster, the real precomputed PNG, or any of FlowConcentrationService's
internal calculation/validation logic.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.v1.endpoints.runoff import get_flow_concentration_service
from app.services.flow_concentration_service import (
    FLOW_CONCENTRATION_VISUALIZATION_FILENAME,
    FlowConcentrationService,
)

client = TestClient(app)


class _FakeFlowConcentrationService:
    """
    Minimal stand-in exposing only what the /image endpoint touches.

    Deliberately not a subclass of FlowConcentrationService: the endpoint
    only ever reads .visualization_path, so duck-typing that one attribute
    is enough, and avoids depending on the real class's constructor.
    """

    def __init__(self, visualization_path: Path) -> None:
        self.visualization_path = visualization_path


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Ensure no override leaks between tests or into other test modules."""
    yield
    app.dependency_overrides.pop(get_flow_concentration_service, None)


def test_visualization_path_resolves_to_expected_sibling_png(tmp_path):
    """
    visualization_path must resolve to:
        data_root / "processed" / "terrain" /
        Copernicus_Mumbai_GLO30_mosaic_flow_concentration_bmc.png
    """
    service = FlowConcentrationService(data_root=tmp_path)

    expected = (
        tmp_path
        / "processed"
        / "terrain"
        / FLOW_CONCENTRATION_VISUALIZATION_FILENAME
    )
    assert service.visualization_path == expected
    assert (
        service.visualization_path.name
        == "Copernicus_Mumbai_GLO30_mosaic_flow_concentration_bmc.png"
    )


def test_flow_concentration_image_returns_200_when_png_exists(tmp_path):
    png_path = tmp_path / "flow_concentration_bmc.png"
    # FileResponse only needs a readable file at this path, not a
    # decodable image, so minimal placeholder bytes are sufficient.
    png_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    app.dependency_overrides[get_flow_concentration_service] = (
        lambda: _FakeFlowConcentrationService(png_path)
    )

    response = client.get("/api/v1/runoff/flow-concentration/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_flow_concentration_image_returns_503_when_png_missing(tmp_path):
    missing_path = tmp_path / "does_not_exist.png"

    app.dependency_overrides[get_flow_concentration_service] = (
        lambda: _FakeFlowConcentrationService(missing_path)
    )

    response = client.get("/api/v1/runoff/flow-concentration/image")

    assert response.status_code == 503