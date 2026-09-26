"use client";

import React, { useEffect, useState } from "react";
import { Activity, Loader2, AlertTriangle } from "lucide-react";
import Card from "@/components/common/Card";
import {
  getFlowConcentration,
  RunoffApiError,
  type FlowConcentrationResponse,
} from "@/lib/api/runoff";

export const FlowConcentrationCard: React.FC = () => {
  const [data, setData] = useState<FlowConcentrationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    getFlowConcentration()
      .then((response) => {
        if (!cancelled) {
          setData(response);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;

        setError(
          err instanceof RunoffApiError
            ? err.message
            : "Unable to load flow concentration data."
        );
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card
      accent="amber"
      hoverable
      animate
      animationDelay={3}
      className="relative overflow-hidden"
    >
      <div className="absolute top-0 right-0 w-32 h-32 bg-orange-600 opacity-5 rounded-full -mr-16 -mt-16" />

      <div className="relative">
        <div className="flex items-center justify-between mb-4">
          <div className="p-2 bg-orange-950 rounded-md">
            <Activity size={20} className="text-orange-400" />
          </div>

          {data && (
            <span className="text-xs font-bold px-2 py-1 bg-orange-950 text-orange-300 rounded">
              TERRAIN
            </span>
          )}
        </div>

        <p className="text-slate-400 text-sm mb-3">
          Flow Concentration
        </p>

        {error && (
          <div className="flex items-start gap-2 text-orange-400 mb-4">
            <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
            <span className="text-sm">{error}</span>
          </div>
        )}

        {!data && !error && (
          <div className="flex items-center gap-2 text-slate-500 mb-4">
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">Loading terrain analysis…</span>
          </div>
        )}

        {data && (
          <>
            <div className="mb-4">
              <p className="text-3xl font-bold text-slate-100">
                {data.p99_accumulation_cells.toLocaleString(undefined, {
                  maximumFractionDigits: 0,
                })}
              </p>
              <p className="text-xs text-slate-500 mt-1">
                P99 flow accumulation cells
              </p>
            </div>

            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-400">
                  P95 Accumulation:
                </span>
                <span className="text-slate-200 font-semibold">
                  {data.p95_accumulation_cells.toLocaleString(undefined, {
                    maximumFractionDigits: 0,
                  })}
                </span>
              </div>

              <div className="flex justify-between">
                <span className="text-slate-400">
                  High-Concentration Cells:
                </span>
                <span className="text-orange-400 font-semibold">
                  {data.class_3_cells.toLocaleString()}
                </span>
              </div>
            </div>

            <div className="mt-4 w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
              <div
                className="bg-orange-500 h-1.5 rounded-full nd-bar-grow"
                style={{
                  width: `${Math.min(
                    (data.class_3_cells / 5000) * 100,
                    100
                  )}%`,
                }}
              />
            </div>

            <p className="text-[10px] text-slate-500 mt-3">
              Terrain-derived indicator · not drainage network or flood depth
            </p>
          </>
        )}
      </div>
    </Card>
  );
};

export default FlowConcentrationCard;
