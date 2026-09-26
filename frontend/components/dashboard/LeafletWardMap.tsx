"use client";

/**
 * LeafletWardMap
 *
 * The actual Leaflet map: tiles, the ward GeoJSON layer, hover/click/
 * selection behavior, and fitBounds. This module imports react-leaflet
 * and leaflet at the top level, both of which reference `window` as soon
 * as they are imported - so this file must NEVER be imported directly by
 * anything that Next.js might evaluate during server rendering.
 *
 * FloodRiskMap.tsx is the only consumer, and it must load this component
 * via next/dynamic with { ssr: false }, so the import itself is deferred
 * to the browser.
 */

import React, { useEffect, useRef, useState } from "react";
import { MapContainer, TileLayer, GeoJSON } from "react-leaflet";
import type {
  Layer,
  Map as LeafletMap,
  GeoJSON as LeafletGeoJSON,
  PathOptions,
  Path,
} from "leaflet";
import type { Feature, Geometry } from "geojson";
import { MapPin } from "lucide-react";
import type { WardFeatureCollection, WardProperties } from "@/lib/api/wards";

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

const LeafletWardMap: React.FC<LeafletWardMapProps> = ({ data }) => {
  const [selectedWard, setSelectedWard] = useState<SelectedWard | null>(null);

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
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <GeoJSON
          ref={geoJsonRef}
          data={data}
          style={styleFeature}
          onEachFeature={onEachWardFeature}
        />
      </MapContainer>

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