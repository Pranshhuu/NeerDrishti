/**
 * Flood Risk Map Component
 * Urban inundation risk visualization with geographic zones
 *
 * FIXED: Risk Legend now positioned in a dedicated non-overlapping section
 * FIXED: bg-gradient-to-br class name typo (was `bg-gradient\-to-br`, an
 *   escaped hyphen that Tailwind never matched, so the gradient never
 *   rendered).
 * FIXED: RiskLegend now reads colors from the shared getRiskColor helper
 *   instead of a second, independent hardcoded hex map.
 *
 * "use client" is added here (only) so zone cards/rows can track a local
 * selected-zone state for a clearer selected/hover interaction. This is a
 * contained UI interaction - no new map library, no new data source, and
 * highRiskZones itself is unchanged.
 */

"use client";

import React, { useState } from "react";
import { AlertTriangle, Users } from "lucide-react";
import Card from "@/components/common/Card";
import { HighRiskZone, RiskLevel, getRiskColor } from "@/data/mockData";

interface FloodRiskMapProps {
  highRiskZones: HighRiskZone[];
}

const LEGEND_ITEMS: { label: string; level: RiskLevel }[] = [
  { label: "Critical", level: "critical" },
  { label: "High", level: "high" },
  { label: "Moderate", level: "moderate" },
  { label: "Low", level: "low" },
];

// Risk Legend Component (extracted for clarity)
const RiskLegend: React.FC = () => {
  return (
    <div className="bg-slate-900 border border-slate-700 rounded-md p-4">
      <p className="text-xs text-slate-400 mb-3 font-semibold uppercase tracking-wide">
        Risk Legend
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2">
        {LEGEND_ITEMS.map((item) => (
          <div key={item.level} className="flex items-center gap-2">
            <div
              className="w-3 h-3 rounded-sm flex-shrink-0"
              style={{ backgroundColor: getRiskColor(item.level) }}
            ></div>
            <span className="text-xs text-slate-300 whitespace-nowrap">{item.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

export const FloodRiskMap: React.FC<FloodRiskMapProps> = ({ highRiskZones }) => {
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);

  const toggleZone = (zoneId: string) => {
    setSelectedZoneId((current) => (current === zoneId ? null : zoneId));
  };

  return (
    <Card noPadding accent="orange" animate>
      <div className="flex flex-col h-full">
        {/* ===== HEADER ===== */}
        <div className="p-6 border-b border-slate-800">
          <h3 className="text-lg font-semibold text-slate-100 mb-2">
            Urban Inundation Risk Map
          </h3>
          <p className="text-sm text-slate-400">
            High-risk geographic zones with critical flood inundation potential
          </p>
        </div>

        {/* ===== LEGEND SECTION (non-overlapping, dedicated space) ===== */}
        <div className="px-6 py-4 border-b border-slate-800 bg-gradient-to-b from-slate-800 to-slate-900">
          <RiskLegend />
        </div>

        {/* ===== MAP VISUALIZATION CONTAINER ===== */}
        <div className="flex-1 bg-gradient-to-br from-slate-800 to-slate-900 p-6 relative overflow-hidden">
          {/* Background SVG Map */}
          <svg
            className="w-full h-full absolute top-0 left-0 opacity-20 pointer-events-none"
            viewBox="0 0 400 300"
            aria-hidden="true"
          >
            {/* Simplified Mumbai shape */}
            <path
              d="M 80 50 Q 150 40 200 60 L 220 120 Q 200 180 150 200 L 100 180 Q 70 150 80 100 Z"
              stroke="#06b6d4"
              strokeWidth="2"
              fill="none"
            />
            {/* Water body */}
            <circle cx="280" cy="150" r="40" fill="#06b6d4" opacity="0.1" />
          </svg>

          {/* Risk Zones Grid */}
          <div className="relative z-10 h-full flex flex-col justify-center">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {highRiskZones.slice(0, 4).map((zone) => {
                const color = getRiskColor(zone.riskLevel);
                const isSelected = selectedZoneId === zone.id;
                return (
                  <button
                    key={zone.id}
                    type="button"
                    onClick={() => toggleZone(zone.id)}
                    aria-pressed={isSelected}
                    className={`
                      text-left bg-slate-900 border rounded-md p-4
                      transition-all duration-200 shadow-lg
                      hover:-translate-y-0.5 hover:bg-slate-800
                    `}
                    style={{
                      borderColor: isSelected ? color : `${color}44`,
                      boxShadow: isSelected
                        ? `0 0 0 2px ${color}, 0 10px 15px -3px rgba(0,0,0,0.3)`
                        : undefined,
                    }}
                  >
                    <div className="flex items-start gap-3">
                      <div
                        className="p-2 rounded-md flex-shrink-0"
                        style={{ backgroundColor: `${color}22` }}
                      >
                        <AlertTriangle size={16} style={{ color }} />
                      </div>

                      <div className="min-w-0 flex-1">
                        <p className="font-semibold text-slate-100 text-sm">{zone.name}</p>
                        <p
                          className="text-xs font-bold mt-1"
                          style={{ color }}
                        >
                          {zone.riskLevel.toUpperCase()}
                        </p>

                        <div className="flex items-center gap-1 mt-2 text-xs text-slate-400">
                          <Users size={12} />
                          <span>{(zone.population_at_risk / 1000).toFixed(0)}K at risk</span>
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* ===== ZONE DETAILS FOOTER ===== */}
        <div className="border-t border-slate-800 p-6 bg-slate-900 bg-opacity-50">
          <p className="text-xs text-slate-400 mb-3 font-semibold uppercase tracking-wide">
            High-Risk Zones Summary
          </p>
          <div className="space-y-2">
            {highRiskZones.map((zone) => {
              const isSelected = selectedZoneId === zone.id;
              return (
                <button
                  key={zone.id}
                  type="button"
                  onClick={() => toggleZone(zone.id)}
                  aria-pressed={isSelected}
                  className={`
                    w-full flex items-center justify-between py-1.5 px-2 rounded
                    transition-colors text-left
                    ${isSelected ? "bg-slate-800" : "hover:bg-slate-800"}
                  `}
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <div
                      className="w-2 h-2 rounded-full flex-shrink-0"
                      style={{ backgroundColor: getRiskColor(zone.riskLevel) }}
                    ></div>
                    <span className={`text-sm truncate ${isSelected ? "text-slate-100 font-medium" : "text-slate-300"}`}>
                      {zone.name}
                    </span>
                  </div>
                  <span className="text-xs text-slate-400 ml-2 flex-shrink-0">
                    {(zone.population_at_risk / 1000).toFixed(0)}K affected
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </Card>
  );
};

export default FloodRiskMap;