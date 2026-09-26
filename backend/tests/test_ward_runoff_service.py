import math

import pytest

from app.services.runoff.basin_service import BasinRunoffInputError
from app.services.runoff.ward_service import WardRunoffService


@pytest.fixture
def service():
    return WardRunoffService()


def test_real_ward_runoff_scenario(service):
    result = service.calculate(
        rainfall_intensity_mm_h=25.3,
        rainfall_source="ERA5-Land",
        rainfall_scenario="2024-06-27T07:00",
    )

    assert result.eligible_basin_count == 438
    assert result.ward_count == 24

    assert len(result.allocations) == 24
    assert {ward.ward_id for ward in result.allocations} == set(range(1, 25))

    assert result.outside_bmc_runoff_low_m3s > 0.0
    assert result.outside_bmc_runoff_high_m3s > 0.0

    for ward in result.allocations:
        assert ward.basin_count > 0
        assert ward.contributing_area_m2 > 0.0
        assert ward.runoff_low_m3s >= 0.0
        assert ward.runoff_high_m3s >= ward.runoff_low_m3s


def test_ward_runoff_conserves_basin_runoff(service):
    result = service.calculate(
        rainfall_intensity_mm_h=25.3,
        rainfall_source="ERA5-Land",
        rainfall_scenario="2024-06-27T07:00",
    )

    ward_low = sum(
        ward.runoff_low_m3s
        for ward in result.allocations
    )
    ward_high = sum(
        ward.runoff_high_m3s
        for ward in result.allocations
    )

    total_low = (
        ward_low
        + result.outside_bmc_runoff_low_m3s
    )
    total_high = (
        ward_high
        + result.outside_bmc_runoff_high_m3s
    )

    expected_low = 742.346539829138
    expected_high = 1590.554470946311

    assert math.isclose(
        total_low,
        expected_low,
        rel_tol=1e-10,
        abs_tol=1e-10,
    )

    assert math.isclose(
        total_high,
        expected_high,
        rel_tol=1e-10,
        abs_tol=1e-10,
    )


def test_zero_rainfall_produces_zero_ward_runoff(service):
    result = service.calculate(
        rainfall_intensity_mm_h=0.0,
        rainfall_source="ERA5-Land",
        rainfall_scenario="zero-rainfall-test",
    )

    assert result.eligible_basin_count == 438

    assert all(
        ward.runoff_low_m3s == 0.0
        for ward in result.allocations
    )

    assert all(
        ward.runoff_high_m3s == 0.0
        for ward in result.allocations
    )

    assert result.outside_bmc_runoff_low_m3s == 0.0
    assert result.outside_bmc_runoff_high_m3s == 0.0


@pytest.mark.parametrize(
    "rainfall",
    [-1.0, float("nan"), float("inf"), -float("inf")],
)
def test_invalid_rainfall_is_rejected(service, rainfall):
    with pytest.raises(BasinRunoffInputError):
        service.calculate(
            rainfall_intensity_mm_h=rainfall,
            rainfall_source="ERA5-Land",
            rainfall_scenario="test",
        )


def test_blank_source_is_rejected(service):
    with pytest.raises(BasinRunoffInputError):
        service.calculate(
            rainfall_intensity_mm_h=25.3,
            rainfall_source="",
            rainfall_scenario="test",
        )


def test_blank_scenario_is_rejected(service):
    with pytest.raises(BasinRunoffInputError):
        service.calculate(
            rainfall_intensity_mm_h=25.3,
            rainfall_source="ERA5-Land",
            rainfall_scenario="",
        )
