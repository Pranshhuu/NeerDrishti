/**
 * Converts the raw backend WeatherResponse into a presentation-focused shape
 * for the live rainfall UI.
 *
 * Deliberately does NOT reuse data/mockData.ts's RainfallData interface: that
 * type has two fields with no honest backend equivalent -
 *
 *   - accumulated: nothing in WeatherResponse represents cumulative rainfall.
 *   - intensity_level: a RiskLevel (low/moderate/high/critical) used
 *     elsewhere in this app to color actual flood-risk cards. Computing a
 *     risk classification from raw millimeters here would misrepresent
 *     forecast weather as a risk assessment.
 *
 * forecastTotalMm is a real, disclosed derivation (the sum of the hourly
 * precipitation values actually returned), deliberately not labeled
 * "accumulated" since that word implies an observed, backward-looking total
 * rather than a forecast sum.
 */

import type { WeatherResponse } from "@/lib/api/weather";

export interface LiveRainfallPoint {
  time: string;
  precipitationMm: number;
}

export type RainfallTrendDirection = "increasing" | "stable" | "decreasing";

export interface LiveRainfallView {
  currentPrecipitationMm: number;
  currentRainMm: number;
  trend: RainfallTrendDirection;
  forecast: LiveRainfallPoint[];
  forecastTotalMm: number;
  providerLabel: string;
  fetchedAtIso: string;
}

// Below this delta (in mm) between the first and last hourly forecast
// values, the trend is reported as "stable" rather than up/down, so that
// floating-point noise near zero doesn't flip the badge unnecessarily.
const TREND_THRESHOLD_MM = 0.05;

function formatHourLabel(isoTimestamp: string): string {
  const date = new Date(isoTimestamp);
  return date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export function toLiveRainfallView(response: WeatherResponse): LiveRainfallView {
  const forecast: LiveRainfallPoint[] = response.hourly.map((point) => ({
    time: formatHourLabel(point.timestamp),
    precipitationMm: point.precipitation_mm,
  }));

  const forecastTotalMm = response.hourly.reduce(
    (total, point) => total + point.precipitation_mm,
    0
  );

  let trend: RainfallTrendDirection = "stable";
  if (response.hourly.length >= 2) {
    const first = response.hourly[0].precipitation_mm;
    const last = response.hourly[response.hourly.length - 1].precipitation_mm;
    const delta = last - first;
    if (delta > TREND_THRESHOLD_MM) {
      trend = "increasing";
    } else if (delta < -TREND_THRESHOLD_MM) {
      trend = "decreasing";
    }
  }

  return {
    currentPrecipitationMm: response.current.precipitation_mm,
    currentRainMm: response.current.rain_mm,
    trend,
    forecast,
    forecastTotalMm,
    providerLabel: `${response.source.provider} \u2022 ${response.source.model}`,
    fetchedAtIso: response.fetched_at,
  };
}