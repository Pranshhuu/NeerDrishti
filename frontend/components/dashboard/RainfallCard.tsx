"use client";

import React from "react";
import { Cloud, TrendingUp, TrendingDown, Minus, Loader2, AlertTriangle } from "lucide-react";
import { Card } from "@/components/common/Card";
import { useWeather } from "@/components/dashboard/WeatherProvider";
import type { RainfallTrendDirection } from "@/lib/weather/adapter";

type TrendStyle = { bg: string; color: string; Icon: typeof TrendingUp };

const TREND_STYLE: Record<RainfallTrendDirection, TrendStyle> = {
  increasing: { bg: "#06b6d422", color: "#06b6d4", Icon: TrendingUp },
  decreasing: { bg: "#64748b22", color: "#94a3b8", Icon: TrendingDown },
  stable: { bg: "#64748b22", color: "#94a3b8", Icon: Minus },
};

export const RainfallCard: React.FC = () => {
  const weather = useWeather();

  return (
    <Card accent="cyan" hoverable animate animationDelay={2} className="relative overflow-hidden">
      <div className="absolute top-0 right-0 w-32 h-32 bg-cyan-600 opacity-5 rounded-full -mr-16 -mt-16"></div>
      <div className="relative">
        <div className="flex items-center justify-between mb-4">
          <div className="p-2 bg-cyan-950 rounded-md">
            <Cloud size={20} className="text-cyan-400" />
          </div>

          {weather.status === "ready" &&
            (() => {
              const style = TREND_STYLE[weather.data.trend];
              const Icon = style.Icon;
              return (
                <span
                  className="text-xs font-bold px-2 py-1 rounded flex items-center gap-1"
                  style={{ backgroundColor: style.bg, color: style.color }}
                >
                  <Icon size={12} />
                  {weather.data.trend.toUpperCase()}
                </span>
              );
            })()}
        </div>

        <div className="flex items-center gap-2 mb-3">
          <p className="text-slate-400 text-sm">Current Precipitation (forecast)</p>
          {weather.status === "ready" && (
            <span
              className="inline-flex items-center gap-1 text-[10px] font-bold text-cyan-400 uppercase tracking-wide"
              title="Connected to the live Open-Meteo weather feed"
            >
              <span className="inline-flex w-1.5 h-1.5 rounded-full bg-cyan-400 nd-pulse-dot" aria-hidden="true" />
              Live
            </span>
          )}
        </div>

        {weather.status === "loading" && (
          <div className="flex items-center gap-2 text-slate-500 mb-4">
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">Loading live weather…</span>
          </div>
        )}

        {weather.status === "error" && (
          <div className="flex items-start gap-2 text-orange-400 mb-4">
            <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
            <span className="text-sm">{weather.message}</span>
          </div>
        )}

        {weather.status === "ready" && (
          <>
            <div className="mb-4">
              <p className="text-3xl font-bold text-slate-100">
                {weather.data.currentPrecipitationMm.toFixed(1)}
              </p>
              <p className="text-xs text-slate-500 mt-1">mm (forecast)</p>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-400">Forecast Total:</span>
                <span className="text-slate-200 font-semibold">
                  {weather.data.forecastTotalMm.toFixed(1)} mm
                </span>
              </div>
            </div>
            <div className="mt-4 w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
              <div
                className="bg-cyan-500 h-1.5 rounded-full nd-bar-grow"
                style={{
                  width: `${Math.min((weather.data.currentPrecipitationMm / 20) * 100, 100)}%`,
                }}
              ></div>
            </div>
            <p className="text-[10px] text-slate-500 mt-3">{weather.data.providerLabel}</p>
          </>
        )}
      </div>
    </Card>
  );
};

export default RainfallCard;