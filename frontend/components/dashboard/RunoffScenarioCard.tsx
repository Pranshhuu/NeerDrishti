"use client";

import React, { useEffect, useState } from "react";
import { Droplets, Loader2, AlertTriangle } from "lucide-react";
import Card from "@/components/common/Card";
import { getWardRunoff, RunoffApiError } from "@/lib/api/runoff";
import type { WardRunoffResponse } from "@/lib/api/runoff";

type RunoffScenarioState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: WardRunoffResponse };

export const RunoffScenarioCard: React.FC = () => {
  const [state, setState] = useState<RunoffScenarioState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    getWardRunoff()
      .then((response) => {
        if (cancelled) return;
        setState({ status: "ready", data: response });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof RunoffApiError
            ? err.message
            : "Unable to load runoff scenario data.";
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

        <p className="text-slate-400 text-sm mb-3">Runoff Scenario</p>

        {state.status === "loading" && (
          <div className="flex items-center gap-2 text-slate-500 mb-4">
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">Loading runoff scenario…</span>
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
            const totalLow =
              state.data.allocations.reduce(
                (sum, allocation) => sum + allocation.runoff_low_m3s,
                0
              ) + state.data.outside_bmc_runoff_low_m3s;

            const totalHigh =
              state.data.allocations.reduce(
                (sum, allocation) => sum + allocation.runoff_high_m3s,
                0
              ) + state.data.outside_bmc_runoff_high_m3s;

            return (
              <>
                <div className="mb-4">
                  <p className="text-xs text-slate-500 uppercase tracking-wide mb-2">
                    Estimated Runoff
                  </p>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <p className="text-2xl font-bold text-slate-100">
                        {totalLow.toFixed(2)}
                      </p>
                      <p className="text-[10px] text-slate-500 mt-0.5">
                        Low Range (m³/s)
                      </p>
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-slate-100">
                        {totalHigh.toFixed(2)}
                      </p>
                      <p className="text-[10px] text-slate-500 mt-0.5">
                        High Range (m³/s)
                      </p>
                    </div>
                  </div>
                </div>

                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Rainfall Intensity:</span>
                    <span className="text-slate-200 font-semibold">
                      {state.data.rainfall_intensity_mm_h.toFixed(1)} mm/h
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Wards Covered:</span>
                    <span className="text-slate-200 font-semibold">
                      {state.data.ward_count}
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
                  {state.data.source.rainfall_source} • {state.data.source.rainfall_scenario}
                </p>
                <p className="text-[10px] text-slate-500 mt-1 italic">
                  Rational Method estimate · not measured discharge
                </p>
              </>
            );
          })()}
      </div>
    </Card>
  );
};

export default RunoffScenarioCard;