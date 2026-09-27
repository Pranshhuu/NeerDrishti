"use client";

/**
 * LeafletWardMap
 *
 * The actual Leaflet map: tiles, the ward GeoJSON layer, the terrain-derived
 * flow-concentration overlay, hover/click/selection behavior, and
 * fitBounds. This module imports react-leaflet and leaflet at the top
 * level, both of which reference `window` as soon as they are imported -
 * so this file must NEVER be imported directly by anything that Next.js
 * might evaluate during server rendering.
 *
 * FloodRiskMap.tsx is the only consumer, and it must load this component
 * via next/dynamic with { ssr: false }, so the import itself is deferred
 * to the browser.
 *
 * LAYER 2 - TERRAIN FLOW-CONCENTRATION OVERLAY:
 *   This renders the precomputed BMC flow-concentration visualization PNG
 *   as a georeferenced ImageOverlay, using the exact EPSG:4326 bounds of
 *   the full mosaic PNG (NOT the BMC ward bounds, and NOT bounds computed
 *   from the PNG's pixel dimensions - the PNG is already aligned to the
 *   authoritative GeoTIFF, so its real-world bounds are supplied directly).
 *
 *   This is a terrain-derived D8 flow-accumulation indicator only. It is
 *   NOT flood depth, flood extent, flood probability, a drainage network,
 *   drainage capacity, an official drainage catchment, measured discharge,
 *   or an operational flood prediction. Class 0 in the PNG is transparent
 *   (no data / below the lowest concentration threshold) and is
 *   deliberately not given a legend entry, since it is not a risk class.
 *
 * PANE ASSIGNMENT:
 *   ImageOverlay renders with zIndex 450. The GeoJSON ward layer is pinned
 *   to Leaflet's markerPane (default z ~600), which sits above overlayPane
 *   (default z ~400) in Leaflet's fixed pane hierarchy - this guarantees
 *   ward boundaries stay visually above the flow-concentration overlay
 *   regardless of render/mount order.
 */

import React, { useEffect, useRef, useState } from "react";
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
import { MapPin, Layers } from "lucide-react";
import type { WardFeatureCollection, WardProperties } from "@/lib/api/wards";
import { getFlowConcentrationImageUrl } from "@/lib/api/runoff";

interface LeafletWardMapProps {
  data: WardFeatureCollection;
}

interface SelectedWard {
  gid: number;
  name: string;
}

// Mumbai-centered fallback view, used only until the real ward bounds load.
const FALLBACK_CENTER: [number, number] = [19.076, 72.8777];
const FALLBACK_ZOOM = 11;

const DEFAULT_STYLE: PathOptions = {
  color: "#22d3ee", // cyan-400
  weight: 1.5,
  fillColor: "#155e75", // cyan-800
  fillOpacity: 0.1,
};

const HOVER_STYLE: PathOptions = {
  color: "#67e8f9", // cyan-300
  weight: 2.5,
  fillOpacity: 0.2,
};

const SELECTED_STYLE: PathOptions = {
  color: "#5eead4", // teal-300
  weight: 3,
  fillColor: "#0f766e", // teal-700
  fillOpacity: 0.3,
};

// Exact EPSG:4326 bounds of the full-mosaic flow-concentration PNG, as
// supplied by the backend/data team. The PNG is already geospatially
// aligned to the authoritative GeoTIFF - these bounds are NOT derived from
// pixel dimensions and must not be recalculated here.
const FLOW_CONCENTRATION_BOUNDS: LatLngBoundsExpression = [
  [17.9872810559, 72.0000786555],
  [20.0142583839, 72.9996946295],
];

const FLOW_CONCENTRATION_OPACITY = 0.68;

// Empirical BMC accumulation-cell percentiles backing the three legend
// classes below. These describe terrain-derived D8 flow accumulation,
// not a flood, drainage, or discharge threshold.
const P95_CELLS = 166;
const P99_CELLS = 3861;

const LeafletWardMap: React.FC<LeafletWardMapProps> = ({ data }) => {
  const [selectedWard, setSelectedWard] = useState<SelectedWard | null>(null);
  const [showFlowConcentration, setShowFlowConcentration] = useState(true);

  const mapRef = useRef<LeafletMap | null>(null);
  const geoJsonRef = useRef<LeafletGeoJSON<WardProperties> | null>(null);
  const selectedGidRef = useRef<number | null>(null);

  useEffect(() => {
    selectedGidRef.current = selectedWard?.gid ?? null;
    geoJsonRef.current?.eachLayer((layer) => {
      geoJsonRef.current?.resetStyle(layer as Path);
    });
  }, [selectedWard]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const styleFeature = (
    feature?: Feature<Geometry, WardProperties>
  ): PathOptions => {
    if (feature && selectedGidRef.current === feature.properties.gid) {
      return SELECTED_STYLE;
    }
    return DEFAULT_STYLE;
  };

  const onEachWardFeature = (
    feature: Feature<Geometry, WardProperties>,
    layer: Layer
  ) => {
    layer.on({
      mouseover: (event) => {
        const target = event.target as Path;
        if (feature.properties.gid !== selectedGidRef.current) {
          target.setStyle(HOVER_STYLE);
        }
      },
      mouseout: () => {
        geoJsonRef.current?.resetStyle(layer as Path);
      },
      click: () => {
        setSelectedWard({
          gid: feature.properties.gid,
          name: feature.properties.name,
        });
      },
    });
  };

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

        {/* Ward boundaries: pinned to markerPane so they stay above the
            overlay's zIndex 450 regardless of mount/render order */}
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

        <p className="text-[9px] text-slate-500 mb-2 leading-snug italic border-t border-slate-700 pt-2">
          Not flood depth, inundation extent, flood probability, a drainage
          network or its capacity, an official catchment, measured
          discharge, or an operational flood prediction.
        </p>

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