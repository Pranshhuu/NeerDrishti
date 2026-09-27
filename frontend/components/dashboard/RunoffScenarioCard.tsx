"use client";

import React, { useEffect, useState } from "react";
import { Droplets, Loader2, AlertTriangle } from "lucide-react";
import { Card } from "@/components/common/Card";
import { getForecastRunoff, RunoffApiError } from "@/lib/api/runoff";
import type { ForecastRunoffResponse } from "@/lib/api/runoff";

type RunoffScenarioState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: ForecastRunoffResponse };

export const RunoffScenarioCard: React.FC = () => {
  const [state, setState] = useState<RunoffScenarioState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    getForecastRunoff()
      .then((response) => {
        if (cancelled) return;
        setState({ status: "ready", data: response });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof RunoffApiError
            ? err.message
            : "Unable to load forecast runoff scenario data.";
        setState({ status: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card accent="teal" hoverable animate animationDelay={3} className="relative overflow-hidden">
      <div className="absolute top-0 right-0 w-32 h-32 bg-teal-600 opacity-5 rounded-full -mr-16 -mt-16"></div>
      <div className="relative">
        <div className="flex items-center justify-between mb-4">
          <div className="p-2 bg-teal-950 rounded-md">
            <Droplets size={20} className="text-teal-400" />
          </div>
          <span className="text-xs font-bold px-2 py-1 bg-amber-950 text-amber-300 rounded tracking-wide">
            PROVISIONAL
          </span>
        </div>

        <p className="text-slate-400 text-sm mb-3">Forecast-driven Provisional Runoff</p>

        {state.status === "loading" && (
          <div className="flex items-center gap-2 text-slate-500 mb-4">
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">Loading forecast runoff scenario…</span>
          </div>
        )}

        {state.status === "error" && (
          <div className="flex items-start gap-2 text-orange-400 mb-4">
            <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
            <span className="text-sm">{state.message}</span>
          </div>
        )}

        {state.status === "ready" &&
          (() => {
            const low = state.data.peak_discharge_low_m3s;
            const high = state.data.peak_discharge_high_m3s;
            const forecastTime = new Date(
              state.data.selected_forecast_timestamp
            ).toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            });

            return (
              <>
                <div className="mb-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">
                    Peak Discharge (mean)
                  </p>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <p className="text-2xl font-bold text-slate-100">
                        {low.mean.toFixed(2)}
                      </p>
                      <p className="text-[10px] text-slate-500 mt-0.5">
                        Low (m³/s) · range {low.minimum.toFixed(2)}–{low.maximum.toFixed(2)}
                      </p>
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-slate-100">
                        {high.mean.toFixed(2)}
                      </p>
                      <p className="text-[10px] text-slate-500 mt-0.5">
                        High (m³/s) · range {high.minimum.toFixed(2)}–{high.maximum.toFixed(2)}
                      </p>
                    </div>
                  </div>
                </div>

                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Forecast Rainfall:</span>
                    <span className="text-slate-200 font-semibold">
                      {state.data.rainfall_intensity_mm_h.toFixed(1)} mm/h
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Forecast Time:</span>
                    <span className="text-slate-200 font-semibold">
                      {forecastTime}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Eligible Basins:</span>
                    <span className="text-slate-200 font-semibold">
                      {state.data.eligible_basin_count}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Coefficient Status:</span>
                    <span className="text-slate-200 font-semibold">
                      {state.data.source.coefficient_status}
                    </span>
                  </div>
                </div>

                <p className="text-[10px] text-slate-500 mt-3">
                  {state.data.source.rainfall_source}
                </p>
                <p className="text-[10px] text-slate-500 mt-1 italic">
                  Provisional Rational Method estimate · not measured discharge,
                  flood depth, inundation extent, drainage capacity, or an
                  operational flood prediction
                </p>
              </>
            );
          })()}
      </div>
    </Card>
  );
};

export default RunoffScenarioCard;