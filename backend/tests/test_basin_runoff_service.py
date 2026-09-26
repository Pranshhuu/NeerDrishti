"""
Integration-style tests for the basin-level runoff service.

These tests use the project's prepared terrain-derived basin and aligned
WorldCover products. They verify the real data pipeline without treating the
result as hydrologic validation.
"""

import pytest

from app.services.runoff.basin_service import BasinRunoffService


def test_real_basin_runoff_scenario():
    """The prepared Mumbai basin/WorldCover data produces the expected scenario."""
    service = BasinRunoffService()

    result = service.calculate(
        rainfall_intensity_mm_h=25.3,
        rainfall_source="ERA5-Land via Open-Meteo historical/reanalysis data",
        rainfall_scenario=(
            "2024-06-27T07:00 historical/reanalysis hourly "
            "precipitation maximum"
        ),
    )

    assert result.eligible_basin_count == 438
    assert result.worldcover_support_threshold == pytest.approx(0.90)
    assert result.coefficient_status == "provisional"

    assert result.runoff_coefficient_low["mean"] == pytest.approx(
        0.273044385522353
    )
    assert result.runoff_coefficient_high["mean"] == pytest.approx(
        0.5889939339540227
    )

    assert result.peak_discharge_low_m3s["median"] == pytest.approx(
        1.8530456819905532
    )
    assert result.peak_discharge_high_m3s["median"] == pytest.approx(
        3.8329765550203936
    )

    assert result.peak_discharge_high_m3s["maximum"] == pytest.approx(
        6.967269101798778
    )


def test_zero_rainfall_produces_zero_basin_discharge():
    """Zero rainfall preserves the basin/coefficient calculation but gives Q=0."""
    service = BasinRunoffService()

    result = service.calculate(
        rainfall_intensity_mm_h=0.0,
        rainfall_source="test source",
        rainfall_scenario="zero rainfall validation scenario",
    )

    assert result.eligible_basin_count == 438
    assert result.peak_discharge_low_m3s["maximum"] == pytest.approx(0.0)
    assert result.peak_discharge_high_m3s["maximum"] == pytest.approx(0.0)


def test_negative_rainfall_is_rejected():
    """Negative rainfall intensity is invalid."""
    service = BasinRunoffService()

    with pytest.raises(ValueError, match="rainfall_intensity_mm_h"):
        service.calculate(
            rainfall_intensity_mm_h=-1.0,
            rainfall_source="test source",
            rainfall_scenario="invalid scenario",
        )


def test_blank_rainfall_source_is_rejected():
    """Rainfall provenance cannot be blank."""
    service = BasinRunoffService()

    with pytest.raises(ValueError, match="rainfall_source"):
        service.calculate(
            rainfall_intensity_mm_h=25.3,
            rainfall_source=" ",
            rainfall_scenario="test scenario",
        )
