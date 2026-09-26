"use client";

import React from "react";
import { Cloud, Info, Loader2, AlertTriangle } from "lucide-react";
import { Card } from "@/components/common/Card";
import { formatTime } from "@/data/mockData";
import { useWeather } from "@/components/dashboard/WeatherProvider";

export const RainfallTrend: React.FC = () => {
  const weather = useWeather();

  if (weather.status === "loading") {
    return (
      <Card noPadding animate>
        <div className="p-6 flex items-center justify-center gap-2 text-slate-500 min-h-[300px]">
          <Loader2 size={18} className="animate-spin" />
          <span className="text-sm">Loading live rainfall forecast…</span>
        </div>
      </Card>
    );
  }

  if (weather.status === "error") {
    return (
      <Card noPadding animate>
        <div className="p-6 flex flex-col items-center justify-center gap-2 text-center min-h-[300px]">
          <AlertTriangle size={24} className="text-orange-400" />
          <p className="text-sm text-slate-300">Unable to load live rainfall forecast</p>
          <p className="text-xs text-slate-500">{weather.message}</p>
        </div>
      </Card>
    );
  }

  const { forecast, currentPrecipitationMm, providerLabel, fetchedAtIso } = weather.data;

  const maxValue = Math.max(...forecast.map((p) => p.precipitationMm), 1);
  const padding = 40;
  const chartWidth = 300;
  const chartHeight = 180;
  const denom = Math.max(forecast.length - 1, 1);

  const points = forecast.map((point, index) => {
    const x = padding + (index / denom) * chartWidth;
    const y = chartHeight + padding - (point.precipitationMm / maxValue) * chartHeight;
    return { x, y, ...point };
  });

  const pathD = points.length
    ? points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ")
    : "";
  const fillPathD =
    points.length > 0
      ? pathD +
        ` L ${points[points.length - 1].x} ${chartHeight + padding} L ${points[0].x} ${chartHeight + padding} Z`
      : "";

  const peakPoint =
    points.length > 0
      ? points.reduce(
          (max, point) => (point.precipitationMm > max.precipitationMm ? point : max),
          points[0]
        )
      : null;

  return (
    <Card noPadding accent="cyan" animate hoverable>
      <div className="flex flex-col h-full">
        <div className="p-6 border-b border-slate-800 flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <h3 className="text-lg font-semibold text-slate-100">Rainfall Forecast</h3>
              <Cloud size={18} className="text-cyan-400" />
            </div>
            <p className="text-sm text-slate-400">
              {forecast.length}-hour precipitation forecast
            </p>
          </div>
          <div className="text-right">
            <p className="text-2xl font-bold text-cyan-400">
              {currentPrecipitationMm.toFixed(1)}
            </p>
            <p className="text-xs text-slate-400 mt-1">mm (current)</p>
          </div>
        </div>

        <div className="flex-1 p-6 flex items-center justify-center">
          <div className="w-full">
            <svg
              className="w-full"
              height="250"
              viewBox={`0 0 ${chartWidth + padding * 2} ${chartHeight + padding * 2}`}
            >
              <defs>
                <linearGradient id="nd-rainfall-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#06b6d4" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#06b6d4" stopOpacity="0" />
                </linearGradient>
              </defs>

              {[0, 25, 50, 75, 100].map((percent) => (
                <g key={`grid-${percent}`}>
                  <line
                    x1={padding}
                    y1={padding + ((100 - percent) / 100) * chartHeight}
                    x2={chartWidth + padding}
                    y2={padding + ((100 - percent) / 100) * chartHeight}
                    stroke="#334155"
                    strokeDasharray="4"
                    opacity="0.5"
                  />
                  <text
                    x={padding - 10}
                    y={padding + ((100 - percent) / 100) * chartHeight + 4}
                    textAnchor="end"
                    className="text-xs fill-slate-500"
                  >
                    {((percent / 100) * maxValue).toFixed(1)}
                  </text>
                </g>
              ))}
              {points.length > 0 && (
                <>
                  <path d={fillPathD} fill="url(#nd-rainfall-fill)" className="nd-chart-fill" />
                  <path
                    d={pathD}
                    fill="none"
                    stroke="#06b6d4"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    pathLength={1}
                    className="nd-chart-path"
                  />
                  {points.map((point, i) => {
                    const isLast = i === points.length - 1;
                    return (
                      <g key={`point-${i}`}>
                        <title>{`${point.time}: ${point.precipitationMm.toFixed(1)} mm`}</title>
                        <circle cx={point.x} cy={point.y} r={isLast ? 5 : 4} fill="#06b6d4" opacity="0.5" />
                        <circle cx={point.x} cy={point.y} r={isLast ? 3 : 2} fill="#06b6d4" />
                      </g>
                    );
                  })}
                </>
              )}
              <line
                x1={padding}
                y1={chartHeight + padding}
                x2={chartWidth + padding}
                y2={chartHeight + padding}
                stroke="#475569"
                strokeWidth="1"
              />
              <line
                x1={padding}
                y1={padding}
                x2={padding}
                y2={chartHeight + padding}
                stroke="#475569"
                strokeWidth="1"
              />
              {points.map((point, i) => (
                <text
                  key={`label-${i}`}
                  x={point.x}
                  y={chartHeight + padding + 20}
                  textAnchor="middle"
                  className="text-xs fill-slate-500"
                >
                  {point.time}
                </text>
              ))}
            </svg>
          </div>
        </div>

        <div className="p-4 border-t border-slate-800 bg-slate-900 bg-opacity-50 flex items-start gap-2">
          <Info size={14} className="text-slate-400 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-slate-400">
            {peakPoint ? (
              <>
                Highest forecast precipitation in this window is{" "}
                <span className="text-cyan-400 font-semibold">
                  {peakPoint.precipitationMm.toFixed(1)} mm
                </span>{" "}
                around {peakPoint.time}.
              </>
            ) : (
              "No forecast data available."
            )}
          </p>
        </div>

        <div className="px-4 pb-4 flex items-center justify-between text-[11px] text-slate-500">
          <span>{providerLabel}</span>
          <span>Updated {formatTime(fetchedAtIso)}</span>
        </div>
      </div>
    </Card>
  );
};

export default RainfallTrend;