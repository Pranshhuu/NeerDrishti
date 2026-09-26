"use client";

/**
 * Mumbai Flood Intelligence Map (server-safe wrapper)
 *
 * This component owns: fetching the real BMC ward boundaries, the
 * loading/error states, the card header and disclaimer, and handing the
 * loaded GeoJSON to the actual Leaflet map.
 *
 * It deliberately does NOT import react-leaflet or leaflet directly, and
 * never will: both reference `window` at module-evaluation time, which
 * crashes Next.js's server-side prerendering pass with
 * "ReferenceError: window is not defined" - regardless of any runtime
 * mount-guard, since the crash happens during module import, before any
 * component code (including a useState/useEffect guard) ever runs.
 *
 * The actual map lives in LeafletWardMap.tsx and is loaded here via
 * next/dynamic with { ssr: false }, which defers the import() itself to
 * the browser rather than deferring only what gets rendered.
 *
 * IMPORTANT - what this map currently is and is not:
 *   This is BMC administrative ward geometry only. It is NOT flood depth,
 *   inundation extent, a drainage network, an official drainage catchment,
 *   flood probability, measured discharge, or an operational flood
 *   prediction. A terrain-derived flow-concentration layer will be added
 *   separately in a later phase.
 *
 * Leaflet's CSS is imported once, globally, from app/layout.tsx - not here.
 */

import React, { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { Info, Loader2, AlertTriangle } from "lucide-react";
import { Card } from "@/components/common/Card";
import {
  getWardBoundaries,
  WardApiError,
  type WardFeatureCollection,
} from "@/lib/api/wards";

const LeafletWardMap = dynamic(
  () => import("@/components/dashboard/LeafletWardMap"),
  {
    ssr: false,
    loading: () => (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-900 text-slate-500">
        <Loader2 size={24} className="animate-spin" />
        <span className="text-sm">Loading map…</span>
      </div>
    ),
  }
);

type MapState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: WardFeatureCollection };

export const FloodRiskMap: React.FC = () => {
  const [state, setState] = useState<MapState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    getWardBoundaries()
      .then((data) => {
        if (cancelled) return;
        setState({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof WardApiError
            ? err.message
            : "The BMC boundary service could not be reached.";
        setState({ status: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card noPadding accent="cyan" animate>
      <div className="flex flex-col h-full">
        <div className="p-6 border-b border-slate-800">
          <h3 className="text-lg font-semibold text-slate-100 mb-2">
            Mumbai Flood Intelligence Map
          </h3>
          <p className="text-sm text-slate-400">
            BMC administrative wards · terrain-derived analysis
          </p>
        </div>

        <div className="px-6 py-3 border-b border-slate-800 bg-slate-900 bg-opacity-50 flex items-start gap-2">
          <Info size={14} className="text-cyan-400 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-slate-400">
            Boundaries shown are{" "}
            <span className="text-slate-300 font-medium">
              BMC administrative wards
            </span>
            , not drainage catchments. A terrain-derived flow-concentration
            layer will be added separately as its own analytical layer.
          </p>
        </div>

        <div className="relative w-full h-[500px] md:h-[550px] lg:h-[600px]">
          {state.status === "loading" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-900 text-slate-500">
              <Loader2 size={24} className="animate-spin" />
              <span className="text-sm">Loading BMC ward boundaries…</span>
            </div>
          )}

          {state.status === "error" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-slate-900 text-center px-6">
              <AlertTriangle size={24} className="text-orange-400" />
              <p className="text-sm text-slate-300">
                The BMC boundary service could not be reached.
              </p>
              <p className="text-xs text-slate-500">{state.message}</p>
            </div>
          )}

          {state.status === "ready" && <LeafletWardMap data={state.data} />}
        </div>
      </div>
    </Card>
  );
};

export default FloodRiskMap;