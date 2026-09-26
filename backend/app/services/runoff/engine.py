"""
Baseline Rational Method runoff engine for NeerDrishti (Phase 1 foundation).

This module implements the Rational Method, a baseline formula for
estimating peak discharge from a drainage area:

    Q = C * i * A

where:
    Q = peak discharge (m^3/s)
    C = runoff coefficient (dimensionless, 0-1)
    i = rainfall intensity (mm/hour)
    A = contributing drainage area (m^2)

IMPORTANT SCOPE NOTE:

This module estimates a single PEAK DISCHARGE value from steady-state
rainfall intensity. It is NOT a complete runoff hydrograph model, does NOT
represent time-varying flow, does NOT perform flood routing, and does NOT
account for infiltration dynamics, time of concentration, storage, or
channel/network attenuation.

It is a baseline hydrologic building block intended to later feed terrain
and drainage coupling, not a standalone flood model.

The runoff coefficient C is never assumed or hard-coded here. Land-use and
imperviousness data has not yet been integrated into NeerDrishti, so C must
always be supplied by the caller.
"""

from dataclasses import dataclass


# Conversion factors for rainfall intensity from mm/hour to m/s.
_MM_TO_M = 1.0 / 1000.0
_SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class RunoffResult:
    """Result of a single Rational Method peak-discharge calculation."""

    peak_discharge_m3s: float
    rainfall_intensity_mm_h: float
    runoff_coefficient: float
    drainage_area_m2: float


def calculate_peak_discharge(
    rainfall_intensity_mm_h: float,
    runoff_coefficient: float,
    drainage_area_m2: float,
) -> RunoffResult:
    """Estimate peak discharge using the Rational Method.

    Rainfall intensity is supplied in mm/hour and converted internally
    to m/s so that the resulting discharge is in m^3/s.

    Args:
        rainfall_intensity_mm_h: Rainfall intensity in millimetres per
            hour. Must be greater than or equal to 0.
        runoff_coefficient: Dimensionless runoff coefficient describing
            the fraction of rainfall contributing to runoff. Must be
            between 0 and 1 inclusive.
        drainage_area_m2: Contributing drainage area in square metres.
            Must be greater than 0.

    Returns:
        A RunoffResult containing the calculated peak discharge and
        the input values used.

    Raises:
        ValueError: If any input violates its required range.
    """

    if rainfall_intensity_mm_h < 0:
        raise ValueError(
            f"rainfall_intensity_mm_h must be >= 0, "
            f"got {rainfall_intensity_mm_h!r}."
        )

    if not 0.0 <= runoff_coefficient <= 1.0:
        raise ValueError(
            f"runoff_coefficient must be between 0 and 1 inclusive, "
            f"got {runoff_coefficient!r}."
        )

    if drainage_area_m2 <= 0:
        raise ValueError(
            f"drainage_area_m2 must be > 0, got {drainage_area_m2!r}."
        )

    # Convert rainfall intensity:
    # mm/hour -> m/hour -> m/second
    intensity_m_s = (
        rainfall_intensity_mm_h * _MM_TO_M
    ) / _SECONDS_PER_HOUR

    # Rational Method:
    # Q = C * i * A
    peak_discharge_m3s = (
        runoff_coefficient
        * intensity_m_s
        * drainage_area_m2
    )

    return RunoffResult(
        peak_discharge_m3s=peak_discharge_m3s,
        rainfall_intensity_mm_h=rainfall_intensity_mm_h,
        runoff_coefficient=runoff_coefficient,
        drainage_area_m2=drainage_area_m2,
    )