"use client";

/**
 * LeafletWardMap
 *
 * The actual Leaflet map: tiles, the ward GeoJSON layer, the terrain-derived
 * flow-concentration overlay, the ward-level flood-risk indicator overlay,
 * hover/click/selection behavior, and fitBounds. This module imports
 * react-leaflet and leaflet at the top level, both of which reference
 * `window` as soon as they are imported - so this file must NEVER be
 * imported directly by anything that Next.js might evaluate during server
 * rendering.
 *
 * FloodRiskMap.tsx is the only consumer, and it must load this component
 * via next/dynamic with { ssr: false }, so the import itself is deferred
 * to the browser.
 *
 * LAYER 2 - TERRAIN FLOW-CONCENTRATION OVERLAY:
 *   This renders the precomputed BMC flow-concentration visualization PNG
 *   as a georeferenced ImageOverlay, using the exact EPSG:4326 bounds of
 *   the full mosaic PNG. This is a terrain-derived D8 flow-accumulation
 *   indicator only.
 *
 * LAYER 3 - WARD FLOOD-RISK INDICATOR:
 *   Fetched independently from ward boundaries, via getFloodRisk() from
 *   the existing lib/api/runoff.ts. Each ward feature's fill/border color
 *   reflects its EXACT API risk_level (LOW/MODERATE/HIGH/VERY_HIGH) - no
 *   score is calculated or reinterpreted in the frontend. Ward-boundary
 *   loading and flood-risk loading are entirely independent: if flood-risk
 *   fails or is still loading, wards render with the original neutral
 *   boundary styling and the map remains fully functional.
 *
 *   Matching API ward_id to a GeoJSON feature: wards.ts's WardProperties
 *   already carries `gid`, the exact same field the backend's ward
 *   service reads directly off each ward's GeoJSON properties to key
 *   every ward-indexed result, including flood-risk. The API's ward_id is
 *   that same value under the API's own field name. So the match is a
 *   direct equality - feature.properties.gid === result.ward_id - via one
 *   Record<number, WardFloodRiskResult> built once flood-risk data loads.
 *
 *   STYLE FRESHNESS: Leaflet's GeoJSON.resetStyle(layer) re-applies the
 *   `style` option snapshot captured when that layer was FIRST CREATED -
 *   it does not re-invoke the current style function. Since the ward
 *   GeoJSON layer is created once, before flood-risk data has necessarily
 *   finished loading, resetStyle() would silently restore the original
 *   neutral snapshot rather than today's risk-level color. To avoid this,
 *   every "return this layer to its base style" path below calls
 *   layer.setStyle(styleFeature(layer.feature)) instead of resetStyle().
 *
 *   STABLE STYLE FUNCTION: styleFeature is defined with useCallback,
 *   depending on [floodRisk] directly (via riskColorForWard) rather than a
 *   ref - this makes it a single, always-current function that every
 *   consumer below (the effect, the handlers) can call or list as a
 *   dependency without a stale closure and without an eslint-disable.
 *
 *   This is NOT flood depth, flood extent, a flood probability, a
 *   calibrated prediction, a drainage network, drainage capacity, an
 *   official catchment, or measured discharge.
 *
 * PANE ASSIGNMENT:
 *   ImageOverlay renders with zIndex 450. The GeoJSON ward layer is pinned
 *   to Leaflet's markerPane (default z ~600), which sits above overlayPane
 *   (default z ~400) in Leaflet's fixed pane hierarchy - this guarantees
 *   ward boundaries stay visually above the flow-concentration overlay
 *   regardless of render/mount order.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, ImageOverlay } from "react-leaflet";
import type {
  Layer,
  Map as LeafletMap,
  GeoJSON as LeafletGeoJSON,
  PathOptions,
  Path,
  LatLngBoundsExpression,
} from "leaflet";
import type { Feature, Geometry } from "geojson";
import { MapPin, Layers, AlertTriangle } from "lucide-react";
import type { WardFeatureCollection, WardProperties } from "@/lib/api/wards";
import {
  getFlowConcentrationImageUrl,
  getFloodRisk,
  RunoffApiError,
  type WardFloodRiskResult,
} from "@/lib/api/runoff";

interface LeafletWardMapProps {
  data: WardFeatureCollection;
}

interface SelectedWard {
  gid: number;
  name: string;
}

type FloodRiskState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; byWardId: Record<number, WardFloodRiskResult> };

// A Leaflet Path layer built from a GeoJSON feature always carries the
// source feature back on itself as `.feature`. Typed narrowly here so
// styleFeature can be called directly against it.
type WardPathLayer = Path & { feature?: Feature<Geometry, WardProperties> };

// Mumbai-centered fallback view, used only until the real ward bounds load.
const FALLBACK_CENTER: [number, number] = [19.076, 72.8777];
const FALLBACK_ZOOM = 11;

// Neutral fallback styling, used whenever flood-risk data is not (yet, or
// ever) available for a ward - the map must remain fully legible without it.
const NEUTRAL_BORDER = "#22d3ee"; // cyan-400
const NEUTRAL_FILL = "#155e75"; // cyan-800

// Ward fill/border colors keyed on the EXACT risk_level strings the API
// returns. Colors only change visual presentation - the underlying value
// rendered in the info panel and legend is always the literal API string.
const RISK_COLORS: Record<string, { border: string; fill: string }> = {
  LOW: { border: "#2dd4bf", fill: "#134e4a" }, // teal
  MODERATE: { border: "#fbbf24", fill: "#78350f" }, // amber
  HIGH: { border: "#fb923c", fill: "#7c2d12" }, // orange
  VERY_HIGH: { border: "#f87171", fill: "#7f1d1d" }, // red
};

const BASE_WEIGHT = 1.5;
const HOVER_WEIGHT = 2.5;
const SELECTED_WEIGHT = 3;
const BASE_FILL_OPACITY = 0.45;
const HOVER_FILL_OPACITY = 0.55;
const SELECTED_FILL_OPACITY = 0.55;
const SELECTED_BORDER = "#ffffff";

// Exact EPSG:4326 bounds of the full-mosaic flow-concentration PNG, as
// supplied by the backend/data team. The PNG is already geospatially
// aligned to the authoritative GeoTIFF - these bounds are NOT derived from
// pixel dimensions and must not be recalculated here.
const FLOW_CONCENTRATION_BOUNDS: LatLngBoundsExpression = [
  [17.9872810559, 72.0000786555],
  [20.0142583839, 72.9996946295],
];

const FLOW_CONCENTRATION_OPACITY = 0.68;

// Empirical BMC accumulation-cell percentiles backing the flow-concentration
// legend classes below. These describe terrain-derived D8 flow accumulation,
// not a flood, drainage, or discharge threshold.
const P95_CELLS = 166;
const P99_CELLS = 3861;

const LeafletWardMap: React.FC<LeafletWardMapProps> = ({ data }) => {
  const [selectedWard, setSelectedWard] = useState<SelectedWard | null>(null);
  const [showFlowConcentration, setShowFlowConcentration] = useState(true);
  const [floodRisk, setFloodRisk] = useState<FloodRiskState>({ status: "loading" });

  const mapRef = useRef<LeafletMap | null>(null);
  const geoJsonRef = useRef<LeafletGeoJSON<WardProperties> | null>(null);
  const selectedGidRef = useRef<number | null>(null);

  // Flood-risk is fetched independently of ward boundaries (which are
  // already loaded by the time this component mounts, via FloodRiskMap).
  // A failure here never blocks or unmounts the map itself - only the
  // risk-based coloring and info-panel fields are affected.
  useEffect(() => {
    let cancelled = false;

    getFloodRisk()
      .then((response) => {
        if (cancelled) return;
        const byWardId: Record<number, WardFloodRiskResult> = {};
        for (const ward of response.wards) {
          byWardId[ward.ward_id] = ward;
        }
        setFloodRisk({ status: "ready", byWardId });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof RunoffApiError
            ? err.message
            : "Unable to load ward flood-risk data.";
        setFloodRisk({ status: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const riskColorForWard = useCallback(
    (gid: number): { border: string; fill: string } => {
      if (floodRisk.status !== "ready") {
        return { border: NEUTRAL_BORDER, fill: NEUTRAL_FILL };
      }
      const result = floodRisk.byWardId[gid];
      if (!result) return { border: NEUTRAL_BORDER, fill: NEUTRAL_FILL };
      return RISK_COLORS[result.risk_level] ?? { border: NEUTRAL_BORDER, fill: NEUTRAL_FILL };
    },
    [floodRisk]
  );

  // Declared with useCallback, depending directly on [floodRisk] (via
  // riskColorForWard, which itself depends on [floodRisk]) rather than a
  // ref - this makes styleFeature a single, always-current function that
  // every consumer below (the effect, the handlers) can call or list as a
  // dependency without a stale closure and without an eslint-disable.
  const styleFeature = useCallback(
    (feature?: Feature<Geometry, WardProperties>): PathOptions => {
      if (!feature) {
        return {
          color: NEUTRAL_BORDER,
          fillColor: NEUTRAL_FILL,
          weight: BASE_WEIGHT,
          fillOpacity: BASE_FILL_OPACITY,
        };
      }

      const gid = feature.properties.gid;
      const { border, fill } = riskColorForWard(gid);
      const isSelected = selectedGidRef.current === gid;

      return {
        color: isSelected ? SELECTED_BORDER : border,
        fillColor: fill,
        weight: isSelected ? SELECTED_WEIGHT : BASE_WEIGHT,
        fillOpacity: isSelected ? SELECTED_FILL_OPACITY : BASE_FILL_OPACITY,
      };
    },
    [riskColorForWard]
  );

  // When floodRisk changes, re-invoke the CURRENT styleFeature across every
  // already-rendered ward layer (Leaflet's GeoJSON.setStyle re-applies a
  // style function to all current layers, computed fresh - not a snapshot).
  useEffect(() => {
    if (geoJsonRef.current) {
      geoJsonRef.current.setStyle(styleFeature);
    }
  }, [styleFeature]);

  useEffect(() => {
    selectedGidRef.current = selectedWard?.gid ?? null;
    // Restore every layer to its CURRENT style via the current styleFeature,
    // not resetStyle()'s originally-created snapshot - see STYLE FRESHNESS
    // note in the module docstring.
    geoJsonRef.current?.eachLayer((layer) => {
      const wardLayer = layer as WardPathLayer;
      wardLayer.setStyle(styleFeature(wardLayer.feature));
    });
  }, [selectedWard, styleFeature]);

  useEffect(() => {
    const layer = geoJsonRef.current;
    const map = mapRef.current;
    if (!layer || !map) return;

    const bounds = layer.getBounds();
    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [16, 16] });
    }
    // Runs once after this component mounts with real data - the map and
    // GeoJSON layer only exist once, since `data` doesn't change post-mount.
  }, []);

  const onEachWardFeature = useCallback(
    (feature: Feature<Geometry, WardProperties>, layer: Layer) => {
      const gid = feature.properties.gid;

      layer.on({
        mouseover: (event) => {
          if (gid === selectedGidRef.current) return;
          const target = event.target as Path;
          const { border, fill } = riskColorForWard(gid);
          target.setStyle({
            color: border,
            fillColor: fill,
            weight: HOVER_WEIGHT,
            fillOpacity: HOVER_FILL_OPACITY,
          });
        },
        mouseout: (event) => {
          // Return to the CURRENT risk-level style, not resetStyle()'s
          // originally-created snapshot - see STYLE FRESHNESS note above.
          const target = event.target as WardPathLayer;
          target.setStyle(styleFeature(target.feature));
        },
        click: () => {
          setSelectedWard({
            gid: feature.properties.gid,
            name: feature.properties.name,
          });
        },
      });
    },
    [riskColorForWard, styleFeature]
  );

  const selectedRisk =
    selectedWard && floodRisk.status === "ready"
      ? floodRisk.byWardId[selectedWard.gid]
      : undefined;

  return (
    <div className="relative w-full h-full min-h-0">
      <MapContainer
        ref={mapRef}
        center={FALLBACK_CENTER}
        zoom={FALLBACK_ZOOM}
        scrollWheelZoom={false}
        className="w-full h-full"
        style={{ background: "#0f172a" }}
      >
        {/* Base layer: OSM tiles, zIndex implicit at the bottom (~200) */}
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {/* Layer 2: terrain flow-concentration overlay, above tiles, below wards */}
        {showFlowConcentration && (
          <ImageOverlay
            url={getFlowConcentrationImageUrl()}
            bounds={FLOW_CONCENTRATION_BOUNDS}
            opacity={FLOW_CONCENTRATION_OPACITY}
            zIndex={450}
          />
        )}

        {/* Ward boundaries + flood-risk coloring: pinned to markerPane so
            they stay above the overlay's zIndex 450 regardless of
            mount/render order */}
        <GeoJSON
          ref={geoJsonRef}
          data={data}
          style={styleFeature}
          onEachFeature={onEachWardFeature}
          pane="markerPane"
        />
      </MapContainer>

      {/* Legend / layer control - upper right */}
      <div className="absolute top-3 right-3 z-[1000] bg-slate-900/90 border border-slate-700 rounded-md shadow-lg p-3 w-56 backdrop-blur-sm">
        <div className="flex items-center gap-2 mb-1">
          <Layers size={14} className="text-cyan-400 flex-shrink-0" />
          <p className="text-xs font-semibold text-slate-100">
            Terrain Flow Concentration
          </p>
        </div>
        <p className="text-[10px] text-slate-500 mb-2">
          Terrain-derived indicator
        </p>

        <div className="space-y-1.5 mb-2">
          <div className="flex items-center gap-2">
            <span
              className="w-3 h-3 rounded-sm flex-shrink-0"
              style={{ backgroundColor: "#22d3ee" }}
            />
            <span className="text-[10px] text-slate-300">Below P95</span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="w-3 h-3 rounded-sm flex-shrink-0"
              style={{ backgroundColor: "#f59e0b" }}
            />
            <span className="text-[10px] text-slate-300">
              P95 to below P99
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="w-3 h-3 rounded-sm flex-shrink-0"
              style={{ backgroundColor: "#ef4444" }}
            />
            <span className="text-[10px] text-slate-300">P99 and above</span>
          </div>
        </div>

        <p className="text-[9px] text-slate-500 mb-2 leading-snug">
          P95 = {P95_CELLS.toLocaleString()} accumulation cells
          <br />
          P99 = {P99_CELLS.toLocaleString()} accumulation cells
        </p>

        <div className="pt-2 border-t border-slate-700 mb-2">
          <p className="text-xs font-semibold text-slate-100 mb-1.5">
            Ward Flood Risk
          </p>
          {floodRisk.status === "loading" && (
            <p className="text-[10px] text-slate-500">Loading…</p>
          )}
          {floodRisk.status === "error" && (
            <div className="flex items-start gap-1.5 text-orange-400">
              <AlertTriangle size={11} className="mt-0.5 flex-shrink-0" />
              <p className="text-[10px]">Risk data unavailable</p>
            </div>
          )}
          {floodRisk.status === "ready" && (
            <div className="space-y-1.5">
              {(["LOW", "MODERATE", "HIGH", "VERY_HIGH"] as const).map((level) => (
                <div key={level} className="flex items-center gap-2">
                  <span
                    className="w-3 h-3 rounded-sm flex-shrink-0"
                    style={{ backgroundColor: RISK_COLORS[level].border }}
                  />
                  <span className="text-[10px] text-slate-300">{level}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <label className="flex items-center gap-2 text-[10px] text-slate-300 pt-2 border-t border-slate-700 cursor-pointer">
          <input
            type="checkbox"
            checked={showFlowConcentration}
            onChange={(e) => setShowFlowConcentration(e.target.checked)}
            className="w-3 h-3 accent-cyan-500"
          />
          Terrain Flow Concentration
        </label>
      </div>

      {selectedWard && (
        <div className="absolute bottom-4 left-4 z-[1000] bg-slate-900 border border-slate-700 rounded-md shadow-lg p-3 max-w-xs">
          <div className="flex items-start gap-2">
            <MapPin size={14} className="text-teal-400 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-semibold text-slate-100">
                {selectedWard.name}
              </p>
              <p className="text-xs text-slate-400 mt-0.5">
                Ward ID: {selectedWard.gid}
              </p>
              <p className="text-[10px] text-slate-500 mt-1">
                Administrative boundary
              </p>

              {floodRisk.status === "loading" && (
                <p className="text-[10px] text-slate-500 mt-2">
                  Loading flood-risk indicator…
                </p>
              )}

              {floodRisk.status === "error" && (
                <p className="text-[10px] text-orange-400 mt-2">
                  Flood-risk data unavailable
                </p>
              )}

              {floodRisk.status === "ready" && !selectedRisk && (
                <p className="text-[10px] text-slate-500 mt-2">
                  No flood-risk result for this ward
                </p>
              )}

              {selectedRisk && (
                <div className="mt-2 pt-2 border-t border-slate-700 space-y-1">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[10px] text-slate-400">Risk Level:</span>
                    <span
                      className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                      style={{
                        backgroundColor: `${RISK_COLORS[selectedRisk.risk_level]?.fill ?? NEUTRAL_FILL}`,
                        color: RISK_COLORS[selectedRisk.risk_level]?.border ?? NEUTRAL_BORDER,
                      }}
                    >
                      {selectedRisk.risk_level}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[10px] text-slate-400">Risk Score:</span>
                    <span className="text-[10px] text-slate-200 font-semibold">
                      {selectedRisk.risk_score.toFixed(0)} / 100
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[10px] text-slate-400">Runoff (high):</span>
                    <span className="text-[10px] text-slate-200 font-semibold">
                      {selectedRisk.runoff_high_m3s.toFixed(2)} m³/s
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[10px] text-slate-400">Very-high flow conc.:</span>
                    <span className="text-[10px] text-slate-200 font-semibold">
                      {(selectedRisk.flow_concentration_very_high_fraction * 100).toFixed(0)}%
                    </span>
                  </div>
                  <p className="text-[9px] text-slate-500 italic pt-1">
                    Provisional terrain + forecast indicator · not a
                    flood-depth or probability prediction.
                  </p>
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => setSelectedWard(null)}
              className="ml-auto text-slate-500 hover:text-slate-300 text-xs"
              aria-label="Close ward info"
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default LeafletWardMap;