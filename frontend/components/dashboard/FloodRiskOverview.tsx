"use client";

import React, { useEffect, useState } from "react";
import { ShieldAlert, Loader2, AlertTriangle } from "lucide-react";
import { Card } from "@/components/common/Card";
import { getFloodRisk, RunoffApiError } from "@/lib/api/runoff";
import type { FloodRiskResponse, WardFloodRiskResult } from "@/lib/api/runoff";

type FloodRiskState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: FloodRiskResponse };

// Risk-level styling keyed on the exact strings the API returns. Values
// are never reworded or remapped - only their visual color treatment is
// controlled here, so "VERY_HIGH" always renders as that literal string.
const RISK_LEVEL_STYLE: Record<string, { bg: string; text: string; dot: string }> = {
  LOW: { bg: "bg-teal-950", text: "text-teal-300", dot: "bg-teal-400" },
  MODERATE: { bg: "bg-amber-950", text: "text-amber-300", dot: "bg-amber-400" },
  HIGH: { bg: "bg-orange-950", text: "text-orange-300", dot: "bg-orange-400" },
  VERY_HIGH: { bg: "bg-red-950", text: "text-red-300", dot: "bg-red-400" },
};

const DEFAULT_RISK_STYLE = { bg: "bg-slate-800", text: "text-slate-300", dot: "bg-slate-400" };

function riskStyle(level: string) {
  return RISK_LEVEL_STYLE[level] ?? DEFAULT_RISK_STYLE;
}

function formatForecastTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const WardRow: React.FC<{ ward: WardFloodRiskResult }> = ({ ward }) => {
  const style = riskStyle(ward.risk_level);

  return (
    <div className="flex items-center justify-between gap-3 py-2 px-3 rounded-md hover:bg-slate-800/60 transition-colors">
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${style.dot}`} />
        <span className="text-sm text-slate-200 truncate">{ward.ward_name}</span>
      </div>

      <div className="flex items-center gap-3 flex-shrink-0">
        <div className="text-right">
          <p className="text-xs text-slate-500 leading-none">Runoff (high)</p>
          <p className="text-xs text-slate-300 font-semibold leading-tight">
            {ward.runoff_high_m3s.toFixed(2)} m³/s
          </p>
        </div>
        <div className="text-right w-16">
          <p className="text-xs text-slate-500 leading-none">Very-high %</p>
          <p className="text-xs text-slate-300 font-semibold leading-tight">
            {(ward.flow_concentration_very_high_fraction * 100).toFixed(0)}%
          </p>
        </div>
        <div className="text-right w-10">
          <p className="text-xs text-slate-500 leading-none">Score</p>
          <p className="text-xs text-slate-300 font-semibold leading-tight">
            {ward.risk_score.toFixed(0)}
          </p>
        </div>
        <span
          className={`text-[10px] font-bold px-2 py-1 rounded ${style.bg} ${style.text} whitespace-nowrap`}
        >
          {ward.risk_level}
        </span>
      </div>
    </div>
  );
};

export const FloodRiskOverview: React.FC = () => {
  const [state, setState] = useState<FloodRiskState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;

    getFloodRisk()
      .then((data) => {
        if (cancelled) return;
        setState({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message =
          err instanceof RunoffApiError
            ? err.message
            : "Unable to load ward flood-risk data.";
        setState({ status: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Card noPadding accent="orange" hoverable animate className="mx-6 mb-6">
      <div className="p-6 border-b border-slate-800">
        <div className="flex items-center gap-2 mb-1">
          <div className="p-1.5 bg-orange-950 rounded-md">
            <ShieldAlert size={16} className="text-orange-400" />
          </div>
          <h3 className="text-lg font-semibold text-slate-100">Ward Flood Risk</h3>
        </div>
        <p className="text-sm text-slate-400">
          Terrain + forecast-runoff indicator, per BMC ward
        </p>
      </div>

      {state.status === "loading" && (
        <div className="flex items-center gap-2 text-slate-500 p-6">
          <Loader2 size={18} className="animate-spin" />
          <span className="text-sm">Loading ward flood-risk indicator…</span>
        </div>
      )}

      {state.status === "error" && (
        <div className="flex items-start gap-2 text-orange-400 p-6">
          <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
          <span className="text-sm">{state.message}</span>
        </div>
      )}

      {state.status === "ready" && (
        <>
          <div className="px-6 py-3 border-b border-slate-800 bg-slate-900 bg-opacity-50 flex flex-wrap items-center gap-x-6 gap-y-1">
            <div className="flex items-baseline gap-1.5">
              <span className="text-xs text-slate-500">Forecast time:</span>
              <span className="text-xs text-slate-200 font-semibold">
                {formatForecastTime(state.data.selected_forecast_timestamp)}
              </span>
            </div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-xs text-slate-500">Forecast rainfall:</span>
              <span className="text-xs text-slate-200 font-semibold">
                {state.data.rainfall_intensity_mm_h.toFixed(1)} mm/h
              </span>
            </div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-xs text-slate-500">Wards:</span>
              <span className="text-xs text-slate-200 font-semibold">
                {state.data.ward_count}
              </span>
            </div>
          </div>

          <div className="max-h-80 overflow-y-auto px-3 py-2">
            {state.data.wards.map((ward) => (
              <WardRow key={ward.ward_id} ward={ward} />
            ))}
          </div>

          <div className="px-6 py-3 border-t border-slate-800 bg-slate-900 bg-opacity-50">
            <p className="text-[10px] text-slate-500 italic">
              Provisional terrain + forecast indicator · not flood depth,
              inundation extent, drainage capacity, or a calibrated
              flood-risk probability.
            </p>
          </div>
        </>
      )}
    </Card>
  );
};

export default FloodRiskOverview;