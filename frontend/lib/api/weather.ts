/**
 * Typed client for FlowSight's live weather endpoint.
 *
 * Talks to GET {NEXT_PUBLIC_API_BASE_URL}/api/v1/weather, which returns
 * Open-Meteo / ECMWF IFS HRES 9km forecast data normalized by the backend
 * (see backend/app/schemas/weather.py). This module only fetches and types
 * the response; it does not decide how the data is displayed or worded -
 * that belongs to lib/weather/adapter.ts and the consuming components.
 *
 * No axios, no SWR/React Query: plain fetch, matching the rest of this
 * project's dependency footprint. The browser never calls Open-Meteo
 * directly - only this backend endpoint.
 */

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

export interface WeatherLocation {
  latitude: number;
  longitude: number;
  timezone: string;
}

export interface CurrentWeather {
  timestamp: string;
  precipitation_mm: number;
  rain_mm: number;
  weather_code: number;
}

export interface HourlyForecastPoint {
  timestamp: string;
  precipitation_mm: number;
  rain_mm: number;
}

export interface WeatherSource {
  provider: string;
  model: string;
  source_url: string;
}

export interface WeatherResponse {
  location: WeatherLocation;
  current: CurrentWeather;
  hourly: HourlyForecastPoint[];
  source: WeatherSource;
  fetched_at: string;
  status: string;
}

/**
 * Raised for any failure to obtain or parse a weather response: network
 * failure, a non-OK HTTP status, or a body that isn't valid JSON. Consuming
 * components should catch this specifically to render an error state rather
 * than letting a fetch failure propagate as an unhandled exception.
 */
export class WeatherApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "WeatherApiError";
    this.status = status;
  }
}

/**
 * Fetch the current combined weather response (current conditions + hourly
 * forecast) from the FlowSight backend.
 *
 * Always requests the backend's default location and forecast window; the
 * backend itself defaults to Mumbai. No parameters are exposed here because
 * nothing in this phase's UI needs to vary them.
 *
 * @throws WeatherApiError if the backend is unreachable, returns a non-OK
 *   status, or returns a body that cannot be parsed as JSON.
 */
export async function getWeather(): Promise<WeatherResponse> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}/api/v1/weather`, {
      cache: "no-store",
    });
  } catch {
    throw new WeatherApiError(
      "Unable to reach the weather service. Is the backend running?"
    );
  }

  if (!response.ok) {
    throw new WeatherApiError(
      `Weather service returned an error (HTTP ${response.status}).`,
      response.status
    );
  }

  try {
    return (await response.json()) as WeatherResponse;
  } catch {
    throw new WeatherApiError("Weather service returned an unreadable response.");
  }
}