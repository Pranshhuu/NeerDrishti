"""
Tests for the baseline Rational Method runoff engine.

Exercises app.services.runoff.engine.calculate_peak_discharge: the formula
itself (Q = C x i x A, with rainfall intensity converted from mm/hour to
m/s), and its input validation. These are focused unit tests for the
formula and its guardrails, not a hydrologic model validation suite.
"""

import pytest

from app.services.runoff.engine import calculate_peak_discharge


def test_normal_valid_calculation():
    """
    A typical valid input set produces the expected peak discharge.

    Expected value derived directly from the Rational Method formula:
        intensity_m_s = (25.0 mm/h * 0.001 m/mm) / 3600 s/h = 1 / 144000
        Q = 0.65 * (1 / 144000) * 150000 = 0.6770833333333334 m^3/s
    """
    result = calculate_peak_discharge(
        rainfall_intensity_mm_h=25.0,
        runoff_coefficient=0.65,
        drainage_area_m2=150000.0,
    )

    assert result.peak_discharge_m3s == pytest.approx(0.6770833333333334)
    assert result.rainfall_intensity_mm_h == 25.0
    assert result.runoff_coefficient == 0.65
    assert result.drainage_area_m2 == 150000.0


def test_zero_rainfall_produces_zero_discharge():
    """Zero rainfall intensity yields exactly zero peak discharge."""
    result = calculate_peak_discharge(
        rainfall_intensity_mm_h=0.0,
        runoff_coefficient=0.5,
        drainage_area_m2=1000.0,
    )

    assert result.peak_discharge_m3s == pytest.approx(0.0)


def test_runoff_coefficient_below_zero_raises():
    """A runoff coefficient below 0 is rejected."""
    with pytest.raises(ValueError, match="runoff_coefficient"):
        calculate_peak_discharge(
            rainfall_intensity_mm_h=25.0,
            runoff_coefficient=-0.1,
            drainage_area_m2=150000.0,
        )


def test_runoff_coefficient_above_one_raises():
    """A runoff coefficient above 1 is rejected."""
    with pytest.raises(ValueError, match="runoff_coefficient"):
        calculate_peak_discharge(
            rainfall_intensity_mm_h=25.0,
            runoff_coefficient=1.1,
            drainage_area_m2=150000.0,
        )


def test_negative_rainfall_intensity_raises():
    """A negative rainfall intensity is rejected."""
    with pytest.raises(ValueError, match="rainfall_intensity_mm_h"):
        calculate_peak_discharge(
            rainfall_intensity_mm_h=-5.0,
            runoff_coefficient=0.65,
            drainage_area_m2=150000.0,
        )


def test_zero_drainage_area_raises():
    """A drainage area of exactly zero is rejected (must be strictly positive)."""
    with pytest.raises(ValueError, match="drainage_area_m2"):
        calculate_peak_discharge(
            rainfall_intensity_mm_h=25.0,
            runoff_coefficient=0.65,
            drainage_area_m2=0.0,
        )


def test_negative_drainage_area_raises():
    """A negative drainage area is rejected."""
    with pytest.raises(ValueError, match="drainage_area_m2"):
        calculate_peak_discharge(
            rainfall_intensity_mm_h=25.0,
            runoff_coefficient=0.65,
            drainage_area_m2=-100.0,
        )