import math

import pytest

from app.services.flow_concentration_service import (
    FlowConcentrationDataError,
    FlowConcentrationService,
)


def test_flow_concentration_real_dataset():
    result = FlowConcentrationService().calculate()

    assert result.dataset_filename == (
        "Copernicus_Mumbai_GLO30_mosaic_flow_concentration_bmc.tif"
    )
    assert result.width == 3560
    assert result.height == 7318

    assert result.class_0_cells == 25_558_400
    assert result.class_1_cells == 468_993
    assert result.class_2_cells == 19_750
    assert result.class_3_cells == 4_937

    assert math.isclose(
        result.p95_accumulation_cells,
        166.0,
        rel_tol=0.0,
        abs_tol=1e-9,
    )
    assert math.isclose(
        result.p99_accumulation_cells,
        3861.21,
        rel_tol=0.0,
        abs_tol=1e-9,
    )


def test_flow_concentration_classes_cover_entire_raster():
    result = FlowConcentrationService().calculate()

    total = (
        result.class_0_cells
        + result.class_1_cells
        + result.class_2_cells
        + result.class_3_cells
    )

    assert total == result.width * result.height


def test_flow_concentration_has_expected_interpretation():
    result = FlowConcentrationService().calculate()

    assert result.terrain_source == (
        "Copernicus GLO-30 D8 flow accumulation"
    )
    assert result.boundary_source == "BMC_admin_wards.geojson"
    assert "not flood depth" in result.interpretation
    assert "drainage network" in result.interpretation


def test_missing_dataset_raises(tmp_path):
    service = FlowConcentrationService(data_root=tmp_path)

    with pytest.raises(FlowConcentrationDataError, match="not found"):
        service.calculate()
