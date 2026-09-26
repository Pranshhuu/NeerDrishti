/**
 * Typed client for FlowSight's BMC ward boundary endpoint.
 *
 * Talks to GET {NEXT_PUBLIC_API_BASE_URL}/api/v1/runoff/wards/boundaries,
 * which returns the real BMC administrative ward GeoJSON FeatureCollection
 * unmodified from the backend's source file. This module only fetches and
 * types the response; it decides nothing about how boundaries are drawn or
 * styled.
 *
 * Types are the real 'geojson' package types (Feature/FeatureCollection),
 * not a hand-rolled lookalike, so react-leaflet's <GeoJSON> component can
 * consume this data directly without a type cast.
 *
 * These are administrative boundaries only - not drainage catchments, flood
 * risk zones, or any hydrological delineation.
 */

import type { Feature, FeatureCollection, MultiPolygon, Polygon } from "geojson";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

export interface WardProperties {
  gid: number;
  name: string;
}

export type WardGeometry = Polygon | MultiPolygon;

export type WardFeature = Feature<WardGeometry, WardProperties>;

export type WardFeatureCollection = FeatureCollection<WardGeometry, WardProperties>;

/**
 * Raised for any failure to obtain or parse the ward boundary response:
 * network failure, a non-OK HTTP status, or a body that isn't valid JSON.
 */
export class WardApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "WardApiError";
    this.status = status;
  }
}

/**
 * Fetch the real BMC administrative ward boundaries from the backend.
 *
 * @throws WardApiError if the backend is unreachable, returns a non-OK
 *   status, or returns a body that cannot be parsed as JSON.
 */
export async function getWardBoundaries(): Promise<WardFeatureCollection> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}/api/v1/runoff/wards/boundaries`, {
      cache: "no-store",
    });
  } catch {
    throw new WardApiError(
      "Unable to reach the ward boundary service. Is the backend running?"
    );
  }

  if (!response.ok) {
    throw new WardApiError(
      `Ward boundary service returned an error (HTTP ${response.status}).`,
      response.status
    );
  }

  try {
    return (await response.json()) as WardFeatureCollection;
  } catch {
    throw new WardApiError(
      "Ward boundary service returned an unreadable response."
    );
  }
}