"""
Tests for FloodRiskService.

WardRunoffService and FlowConcentrationService are both replaced with
fakes, so these tests touch no real terrain/WorldCover/flow-concentration
raster and make no network call.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.schemas.weather import (
    CurrentWeather,
    HourlyForecast,
    WeatherLocation,
    WeatherResponse,
    WeatherSource,
)
from app.services.flood_risk_service import (
    FloodRiskDataError,
    FloodRiskService,
    FloodRiskTimestampNotFoundError,
    RISK_METHODOLOGY,
)
from app.services.runoff.basin_service import BasinRunoffInputError
from app.services.runoff.ward_service import (
    WardRunoffAllocation,
    WardRunoffSummary,
)
from app.services.flow_concentration_service import FlowConcentrationSummary


FETCHED_AT = datetime(2026, 6, 27, 6, 0, tzinfo=timezone.utc)
TS = datetime(2026, 6, 27, 7, 0, tzinfo=timezone.utc)

WARD_IDS = list(range(1, 25))  # 24 BMC wards, matching the real dataset

# Interior margin (in pixels) kept between every ward polygon and the
# raster's absolute outer edge. See raster_paths' docstring for why this
# margin is required, not optional.
MARGIN_PX = 1


class _FakeWardRunoffService:
    def __init__(self, allocations=None, error=None):
        self._allocations = allocations
        self._error = error
        self.calculate_calls: list[dict] = []

    def calculate(self, rainfall_intensity_mm_h, rainfall_source, rainfall_scenario):
        self.calculate_calls.append(
            {
                "rainfall_intensity_mm_h": rainfall_intensity_mm_h,
                "rainfall_source": rainfall_source,
                "rainfall_scenario": rainfall_scenario,
            }
        )
        if self._error is not None:
            raise self._error
        return WardRunoffSummary(
            eligible_basin_count=438,
            ward_count=len(self._allocations),
            rainfall_intensity_mm_h=rainfall_intensity_mm_h,
            rainfall_source=rainfall_source,
            rainfall_scenario=rainfall_scenario,
            allocations=tuple(self._allocations),
            outside_bmc_runoff_low_m3s=1.0,
            outside_bmc_runoff_high_m3s=2.0,
            terrain_source="Copernicus GLO-30 terrain-derived isobasins",
            landcover_source="ESA WorldCover 2021",
            coefficient_status="provisional",
        )

    def get_ward_boundaries(self):
        # Minimal valid FeatureCollection: 24 adjacent, non-overlapping
        # 0.02-degree-wide strips, matching the class strips painted onto
        # the raster in raster_paths below.
        features = []
        for i, ward_id in enumerate(WARD_IDS):
            lon0 = 72.0 + i * 0.02
            features.append(
                {
                    "type": "Feature",
                    "properties": {"gid": ward_id, "name": f"Ward {ward_id}"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [lon0, 19.0],
                                [lon0 + 0.02, 19.0],
                                [lon0 + 0.02, 19.02],
                                [lon0, 19.02],
                                [lon0, 19.0],
                            ]
                        ],
                    },
                }
            )
        return {"type": "FeatureCollection", "features": features}


def _make_allocations(high_values: dict[int, float]) -> list[WardRunoffAllocation]:
    return [
        WardRunoffAllocation(
            ward_id=ward_id,
            ward_name=f"Ward {ward_id}",
            basin_count=3,
            contributing_area_m2=100000.0,
            runoff_low_m3s=high_values.get(ward_id, 0.0) * 0.5,
            runoff_high_m3s=high_values.get(ward_id, 0.0),
        )
        for ward_id in WARD_IDS
    ]


def _make_weather_response(
    hourly: list[HourlyForecast], fetched_at: datetime = FETCHED_AT
) -> WeatherResponse:
    return WeatherResponse(
        location=WeatherLocation(latitude=19.076, longitude=72.8777, timezone="Asia/Kolkata"),
        current=CurrentWeather(
            timestamp=fetched_at, precipitation_mm=0.0, rain_mm=0.0, weather_code=1
        ),
        hourly=hourly,
        source=WeatherSource(
            provider="Open-Meteo",
            model="ECMWF IFS HRES 9km",
            source_url="https://api.open-meteo.com/v1/forecast",
        ),
        fetched_at=fetched_at,
        status="ok",
    )


@pytest.fixture
def raster_paths(tmp_path):
    """
    Write a tiny real GeoTIFF whose classified cells align with the ward
    polygons produced by _FakeWardRunoffService.get_ward_boundaries(), so
    the real rasterize/overlay code path is genuinely exercised rather
    than mocked away.

    ROOT CAUSE THIS FIXTURE PREVIOUSLY TRIGGERED (ward 1 and ward 24 both
    returning flow_concentration_very_high_fraction == 0.0):

    Each ward polygon spans exactly 0.02 degrees of longitude, and 24
    wards laid consecutively span exactly 0.48 degrees (72.00 to 72.48).
    An earlier version of this fixture sized the raster's extent to match
    that span EXACTLY, with zero margin - meaning ward 1's polygon
    boundary coincided exactly with the raster's left edge, and ward 24's
    coincided exactly with the raster's right edge.

    rasterize()'s default pixel-center-inclusion test, combined with
    binary floating-point representation of the affine transform and the
    transformed polygon coordinates, can disagree by a sub-pixel amount
    about exactly where such an exactly-coincident boundary falls. For an
    interior ward this is harmless (an adjacent pixel still belongs to the
    same ward either way); for the two wards whose polygon boundary
    coincides with the raster's OUTER edge, that same disagreement can
    push their classified strip partly or fully outside the array,
    producing 0.0 regardless of the actual intended class.

    FIX: give the raster a full MARGIN_PX-pixel border on every side, so
    no ward polygon's boundary ever coincides with the raster's absolute
    outer edge. Per-ward pixel width remains exactly 10px = exactly 0.02
    degrees (matching each polygon's true width with zero drift); the
    margin only shifts where that block of 10*24 columns sits within a
    slightly larger canvas.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    wards_width_px = 10 * len(WARD_IDS)  # 240: exactly 10 px per ward, no drift
    wards_height_px = 20

    width = wards_width_px + 2 * MARGIN_PX
    height = wards_height_px + 2 * MARGIN_PX

    pixel_width = 0.02 / 10  # 0.002 degrees/px - exact, matches ward width
    pixel_height = 0.02 / wards_height_px  # 0.001 degrees/px

    raster_west = 72.0 - MARGIN_PX * pixel_width
    raster_north = 19.02 + MARGIN_PX * pixel_height
    transform = from_origin(raster_west, raster_north, pixel_width, pixel_height)

    classes = np.zeros((height, width), dtype="uint8")

    row0 = MARGIN_PX
    row1 = MARGIN_PX + wards_height_px
    for i, ward_id in enumerate(WARD_IDS):
        col0 = MARGIN_PX + i * 10
        col1 = col0 + 10
        # First half of wards mostly class 1, remainder split class 2 / 3,
        # so tests can assert on meaningfully different fractions.
        if ward_id <= 12:
            classes[row0:row1, col0:col1] = 1
        elif ward_id <= 20:
            classes[row0:row1, col0:col1] = 2
        else:
            classes[row0:row1, col0:col1] = 3

    tif_path = tmp_path / "flow_concentration.tif"
    with rasterio.open(
        tif_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=transform,
        nodata=0,
    ) as dst:
        dst.write(classes, 1)
        dst.update_tags(
            p95_accumulation_cells="166", p99_accumulation_cells="3861"
        )

    return tif_path


class _FakeFlowConcentrationService:
    def __init__(self, raster_path: Path, error=None):
        self.raster_path = raster_path
        self._error = error

    def calculate(self):
        if self._error is not None:
            raise self._error
        return FlowConcentrationSummary(
            dataset_filename=self.raster_path.name,
            width=242,
            height=22,
            class_0_cells=524,
            class_1_cells=2400,
            class_2_cells=1600,
            class_3_cells=800,
            p95_accumulation_cells=166.0,
            p99_accumulation_cells=3861.0,
            terrain_source="Copernicus GLO-30 D8 flow accumulation",
            boundary_source="BMC_admin_wards.geojson",
            interpretation=(
                "terrain-derived flow-concentration indicator; "
                "not flood depth or drainage network"
            ),
        )


def test_successful_risk_calculation(raster_paths):
    high_values = {ward_id: float(ward_id) for ward_id in WARD_IDS}
    ward_service = _FakeWardRunoffService(allocations=_make_allocations(high_values))
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    weather = _make_weather_response(hourly)

    result = service.calculate_for_forecast(weather)

    assert result.ward_count == 24
    assert result.selected_forecast_timestamp == TS
    assert result.rainfall_intensity_mm_h == 25.3


def test_forecast_provenance_is_preserved(raster_paths):
    ward_service = _FakeWardRunoffService(allocations=_make_allocations({1: 5.0}))
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    result = service.calculate_for_forecast(_make_weather_response(hourly))

    assert result.rainfall_source == "Open-Meteo / ECMWF IFS HRES 9km forecast"
    assert TS.isoformat() in result.rainfall_scenario
    assert result.rainfall_data_type == "forecast"
    assert "measured" not in result.rainfall_source.lower()
    assert ward_service.calculate_calls[0]["rainfall_intensity_mm_h"] == 25.3


def test_past_forecast_hours_are_not_selected(raster_paths):
    ward_service = _FakeWardRunoffService(allocations=_make_allocations({1: 5.0}))
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    past_ts = FETCHED_AT - timedelta(hours=2)
    future_ts = FETCHED_AT + timedelta(hours=1)
    hourly = [
        HourlyForecast(timestamp=past_ts, precipitation_mm=5.0, rain_mm=5.0),
        HourlyForecast(timestamp=future_ts, precipitation_mm=40.0, rain_mm=40.0),
    ]

    result = service.calculate_for_forecast(_make_weather_response(hourly))

    assert result.selected_forecast_timestamp == future_ts
    assert result.rainfall_intensity_mm_h == 40.0


def test_empty_forecast_raises_timestamp_not_found(raster_paths):
    ward_service = _FakeWardRunoffService(allocations=_make_allocations({1: 5.0}))
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    with pytest.raises(FloodRiskTimestampNotFoundError):
        service.calculate_for_forecast(_make_weather_response([]))


def test_all_past_forecast_raises_timestamp_not_found(raster_paths):
    ward_service = _FakeWardRunoffService(allocations=_make_allocations({1: 5.0}))
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    hourly = [
        HourlyForecast(
            timestamp=FETCHED_AT - timedelta(hours=1), precipitation_mm=5.0, rain_mm=5.0
        )
    ]

    with pytest.raises(FloodRiskTimestampNotFoundError):
        service.calculate_for_forecast(_make_weather_response(hourly))


def test_all_wards_are_represented(raster_paths):
    ward_service = _FakeWardRunoffService(
        allocations=_make_allocations({ward_id: 1.0 for ward_id in WARD_IDS})
    )
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    result = service.calculate_for_forecast(_make_weather_response(hourly))

    assert {w.ward_id for w in result.wards} == set(WARD_IDS)
    assert len(result.wards) == 24


def test_concentration_fractions_are_bounded(raster_paths):
    ward_service = _FakeWardRunoffService(
        allocations=_make_allocations({ward_id: 1.0 for ward_id in WARD_IDS})
    )
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    result = service.calculate_for_forecast(_make_weather_response(hourly))

    for ward in result.wards:
        assert 0.0 <= ward.flow_concentration_high_fraction <= 1.0
        assert 0.0 <= ward.flow_concentration_very_high_fraction <= 1.0
        assert 0.0 <= ward.risk_score <= 100.0

    # Ward 24 sits in the class-3 ("very high") strip; ward 1 sits in the
    # class-1 ("below P95") strip. With the interior margin in place,
    # neither polygon touches the raster's outer edge, so both should now
    # rasterize correctly and show a clear, non-degenerate difference.
    by_id = {w.ward_id: w for w in result.wards}
    assert by_id[24].flow_concentration_very_high_fraction > by_id[1].flow_concentration_very_high_fraction
    assert by_id[24].flow_concentration_very_high_fraction > 0.9
    assert by_id[1].flow_concentration_very_high_fraction == 0.0


def test_risk_categorization_is_deterministic(raster_paths):
    ward_service = _FakeWardRunoffService(
        allocations=_make_allocations({ward_id: 1.0 for ward_id in WARD_IDS})
    )
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    result_a = service.calculate_for_forecast(_make_weather_response(hourly))
    result_b = service.calculate_for_forecast(_make_weather_response(hourly))

    levels_a = {w.ward_id: (w.risk_score, w.risk_level) for w in result_a.wards}
    levels_b = {w.ward_id: (w.risk_score, w.risk_level) for w in result_b.wards}
    assert levels_a == levels_b

    for ward in result_a.wards:
        if ward.risk_score < 25:
            assert ward.risk_level == "LOW"
        elif ward.risk_score < 50:
            assert ward.risk_level == "MODERATE"
        elif ward.risk_score < 75:
            assert ward.risk_level == "HIGH"
        else:
            assert ward.risk_level == "VERY_HIGH"


def test_invalid_precipitation_input_is_rejected(raster_paths):
    ward_service = _FakeWardRunoffService(
        error=BasinRunoffInputError("rainfall_intensity_mm_h must be finite and >= 0.")
    )
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]

    with pytest.raises(BasinRunoffInputError):
        service.calculate_for_forecast(_make_weather_response(hourly))


def _assert_no_affirmative_claim(text: str, phrase: str) -> None:
    """
    Assert every occurrence of `phrase` in `text` is part of a negation
    (e.g. "not flood depth"), never an unqualified/affirmative claim.

    A plain substring ban can't distinguish "claims to be X" from
    "explicitly states it is NOT X" - the latter is required disclaimer
    language and must not fail this check. This walks every occurrence of
    `phrase` and requires the word "not" to appear in the ~20 characters
    immediately preceding it. If `phrase` never appears at all, that is
    also acceptable (nothing to flag).
    """
    lower_text = text.lower()
    lower_phrase = phrase.lower()
    start = 0
    while True:
        idx = lower_text.find(lower_phrase, start)
        if idx == -1:
            return
        preceding = lower_text[max(0, idx - 20):idx]
        assert re.search(r"\bnot\b", preceding), (
            f"Found an unqualified (non-negated) use of {phrase!r} near: "
            f"...{lower_text[max(0, idx - 40):idx + 40]}..."
        )
        start = idx + len(lower_phrase)


def test_no_claim_of_flood_depth_or_probability_in_methodology(raster_paths):
    ward_service = _FakeWardRunoffService(allocations=_make_allocations({1: 5.0}))
    flow_service = _FakeFlowConcentrationService(raster_paths)
    service = FloodRiskService(ward_service=ward_service, flow_concentration_service=flow_service)

    hourly = [HourlyForecast(timestamp=TS, precipitation_mm=25.3, rain_mm=25.3)]
    result = service.calculate_for_forecast(_make_weather_response(hourly))

    combined_text = result.risk_methodology + " " + result.flow_concentration_methodology
    for forbidden in (
        "flood depth",
        "flood probability",
        "inundation extent",
        "operational flood prediction",
        "measured discharge",
    ):
        _assert_no_affirmative_claim(combined_text, forbidden)

    assert "not a calibrated flood-risk probability" in result.risk_methodology.lower()
    assert result.risk_methodology == RISK_METHODOLOGY