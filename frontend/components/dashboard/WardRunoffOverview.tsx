"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Droplets, Loader2, AlertTriangle, MapPin } from "lucide-react";
import Card from "@/components/common/Card";
import {
  getForecastRunoff,
  getWardRunoff,
  RunoffApiError,
  type ForecastRunoffResponse,
  type WardRunoffResponse,
} from "@/lib/api/runoff";

function formatArea(areaM2: number): string {
  return `${(areaM2 / 1_000_000).toFixed(1)} km²`;
}

function formatRunoff(value: number): string {
  return `${value.toFixed(1)} m³/s`;
}

export const WardRunoffOverview: React.FC = () => {
  const [data, setData] = useState<WardRunoffResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    // Use the same forecast scenario as the forecast-driven flood-risk
    // system: fetch the selected forecast hour first, then request ward
    // runoff for exactly that rainfall intensity, source, and timestamp.
    async function load() {
      try {
        const forecast: ForecastRunoffResponse = await getForecastRunoff();
        if (cancelled) return;

        const response = await getWardRunoff(
          forecast.rainfall_intensity_mm_h,
          forecast.source.rainfall_source,
          forecast.selected_forecast_timestamp
        );
        if (cancelled) return;

        setData(response);
      } catch (err: unknown) {
        if (cancelled) return;

        setError(
          err instanceof RunoffApiError
            ? err.message
            : "Unable to load ward runoff data."
        );
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  const highestRunoffWard = useMemo(() => {
    if (!data) return null;

    return [...data.allocations].sort(
      (a, b) => b.runoff_high_m3s - a.runoff_high_m3s
    )[0];
  }, [data]);

  return (
    <Card
      accent="cyan"
      className="mx-6 mb-6 overflow-hidden"
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <div className="p-2 rounded-md bg-cyan-950">
                <Droplets size={18} className="text-cyan-400" />
              </div>

              <div>
                <h2 className="text-lg font-semibold text-slate-100">
                  Ward Runoff Overview
                </h2>
                <p className="text-xs text-slate-500">
                  Terrain-derived basin runoff allocated across BMC wards
                </p>
              </div>
            </div>
          </div>

          {data && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="px-2 py-1 rounded bg-cyan-950 text-cyan-300">
                {data.rainfall_intensity_mm_h.toFixed(1)} mm/h
              </span>

              <span className="px-2 py-1 rounded bg-amber-950 text-amber-300">
                PROVISIONAL
              </span>
            </div>
          )}
        </div>

        {error && (
          <div className="flex items-start gap-2 rounded-md border border-orange-900/50 bg-orange-950/30 p-4 text-orange-300">
            <AlertTriangle size={17} className="mt-0.5 shrink-0" />
            <div>
              <p className="text-sm font-medium">
                Ward runoff unavailable
              </p>
              <p className="text-xs mt-1 text-orange-400/80">
                {error}
              </p>
            </div>
          </div>
        )}

        {!data && !error && (
          <div className="flex items-center gap-2 py-8 text-slate-500">
            <Loader2 size={18} className="animate-spin" />
            <span className="text-sm">
              Loading ward runoff from backend…
            </span>
          </div>
        )}

        {data && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div className="rounded-md border border-slate-800 bg-slate-950/60 p-4">
                <p className="text-xs text-slate-500 uppercase tracking-wide">
                  Eligible Basins
                </p>
                <p className="mt-2 text-2xl font-bold text-slate-100">
                  {data.eligible_basin_count}
                </p>
              </div>

              <div className="rounded-md border border-slate-800 bg-slate-950/60 p-4">
                <p className="text-xs text-slate-500 uppercase tracking-wide">
                  BMC Wards
                </p>
                <p className="mt-2 text-2xl font-bold text-slate-100">
                  {data.ward_count}
                </p>
              </div>

              <div className="rounded-md border border-slate-800 bg-slate-950/60 p-4">
                <p className="text-xs text-slate-500 uppercase tracking-wide">
                  Highest High-Range Runoff
                </p>
                <p className="mt-2 text-2xl font-bold text-orange-400">
                  {highestRunoffWard
                    ? formatRunoff(highestRunoffWard.runoff_high_m3s)
                    : "—"}
                </p>
                {highestRunoffWard && (
                  <p className="text-xs text-slate-500 mt-1">
                    Ward {highestRunoffWard.ward_name}
                  </p>
                )}
              </div>
            </div>

            <div className="overflow-x-auto rounded-md border border-slate-800">
              <table className="w-full text-sm">
                <thead className="bg-slate-950">
                  <tr className="border-b border-slate-800">
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase">
                      Ward
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase">
                      Basins
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase">
                      Area
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase">
                      Low Runoff
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase">
                      High Runoff
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {data.allocations.map((ward) => (
                    <tr
                      key={ward.ward_id}
                      className="border-b border-slate-900 last:border-0 hover:bg-slate-900/60"
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <MapPin size={14} className="text-cyan-500" />
                          <div>
                            <span className="font-medium text-slate-200">
                              Ward {ward.ward_name}
                            </span>
                            <span className="block text-[10px] text-slate-600">
                              ID {ward.ward_id}
                            </span>
                          </div>
                        </div>
                      </td>

                      <td className="px-4 py-3 text-right text-slate-400">
                        {ward.basin_count}
                      </td>

                      <td className="px-4 py-3 text-right text-slate-400">
                        {formatArea(ward.contributing_area_m2)}
                      </td>

                      <td className="px-4 py-3 text-right text-cyan-300">
                        {formatRunoff(ward.runoff_low_m3s)}
                      </td>

                      <td className="px-4 py-3 text-right text-orange-400 font-medium">
                        {formatRunoff(ward.runoff_high_m3s)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex flex-col gap-1 border-t border-slate-800 pt-4 text-[10px] text-slate-500">
              <p>
                Rainfall: {data.source.rainfall_source} · Scenario:{" "}
                {data.source.rainfall_scenario}
              </p>
              <p>
                Terrain: {data.source.terrain_source}
              </p>
              <p>
                Land cover: {data.source.landcover_source} · Coefficients:{" "}
                {data.source.coefficient_status}
              </p>
              <p className="text-amber-500/80">
                This is a terrain-derived Rational Method runoff allocation,
                not an official BMC drainage-network discharge.
              </p>
            </div>
          </>
        )}
      </div>
    </Card>
  );
};

export default WardRunoffOverview;