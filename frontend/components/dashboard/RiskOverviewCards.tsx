import React from "react";
import { AlertTriangle, Zap, Waves, TrendingUp, TrendingDown } from "lucide-react";
import Card from "@/components/common/Card";
import { FloodRiskMetrics, DrainageStatus, WaterLevelData } from "@/data/mockData";
import { RainfallCard } from "@/components/dashboard/RainfallCard";

interface RiskOverviewCardsProps {
  floodRisk: FloodRiskMetrics;
  drainage: DrainageStatus;
  waterLevel: WaterLevelData;
}

export const RiskOverviewCards: React.FC<RiskOverviewCardsProps> = ({
  floodRisk,
  drainage,
  waterLevel,
}) => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 px-6 py-6">
      <Card accent="red" hoverable animate animationDelay={1} className="relative overflow-hidden">
        <div className="absolute top-0 right-0 w-32 h-32 bg-red-600 opacity-5 rounded-full -mr-16 -mt-16"></div>
        <div className="relative">
          <div className="flex items-center justify-between mb-4">
            <div className="p-2 bg-red-950 rounded-md"><AlertTriangle size={20} className="text-red-400" /></div>
            <span className="text-xs font-bold px-2 py-1 bg-red-950 text-red-300 rounded">{floodRisk.currentRisk.toUpperCase()}</span>
          </div>
          <p className="text-slate-400 text-sm mb-3">Current Flood Risk</p>
          <div className="mb-4"><p className="text-3xl font-bold text-slate-100">{floodRisk.riskScore}</p><p className="text-xs text-slate-500 mt-1">out of 100</p></div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between"><span className="text-slate-400">Affected Areas:</span><span className="text-slate-200 font-semibold">{floodRisk.affectedAreas}</span></div>
            <div className="flex justify-between"><span className="text-slate-400">Time to Flood:</span><span className="text-orange-400 font-semibold">{floodRisk.estimatedTimeToFlood}</span></div>
          </div>
          <div className="mt-4 w-full bg-slate-800 rounded-full h-1.5 overflow-hidden"><div className="bg-red-600 h-1.5 rounded-full nd-bar-grow" style={{ width: `${floodRisk.riskScore}%` }}></div></div>
        </div>
      </Card>

      <RainfallCard />

      <Card accent="amber" hoverable animate animationDelay={3} className="relative overflow-hidden">
        <div className="absolute top-0 right-0 w-32 h-32 bg-orange-600 opacity-5 rounded-full -mr-16 -mt-16"></div>
        <div className="relative">
          <div className="flex items-center justify-between mb-4">
            <div className="p-2 bg-orange-950 rounded-md"><Zap size={20} className="text-orange-400" /></div>
            <span className="text-xs font-bold px-2 py-1 bg-orange-950 text-orange-300 rounded">{drainage.status.toUpperCase()}</span>
          </div>
          <p className="text-slate-400 text-sm mb-3">Drainage Capacity</p>
          <div className="mb-4"><p className="text-3xl font-bold text-slate-100">{drainage.capacity_utilization}%</p><p className="text-xs text-slate-500 mt-1">of max capacity</p></div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between"><span className="text-slate-400">Pumping Rate:</span><span className="text-slate-200 font-semibold">{drainage.pumpingRate}%</span></div>
            <div className="flex justify-between"><span className="text-slate-400">Flooded Sections:</span><span className="text-orange-400 font-semibold">{drainage.floodedSections}</span></div>
          </div>
          <div className="mt-4 w-full bg-slate-800 rounded-full h-1.5 overflow-hidden"><div className="bg-orange-500 h-1.5 rounded-full nd-bar-grow" style={{ width: `${drainage.capacity_utilization}%` }}></div></div>
        </div>
      </Card>

      <Card accent="blue" hoverable animate animationDelay={4} className="relative overflow-hidden">
        <div className="absolute top-0 right-0 w-32 h-32 bg-blue-600 opacity-5 rounded-full -mr-16 -mt-16"></div>
        <div className="relative">
          <div className="flex items-center justify-between mb-4">
            <div className="p-2 bg-blue-950 rounded-md"><Waves size={20} className="text-blue-400" /></div>
            <span className="text-xs font-bold px-2 py-1 rounded flex items-center gap-1" style={{ backgroundColor: waterLevel.trend === "rising" ? "#dc262622" : "#22c55e22", color: waterLevel.trend === "rising" ? "#dc2626" : "#22c55e" }}>
              {waterLevel.trend === "rising" ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
              {waterLevel.trend.toUpperCase()}
            </span>
          </div>
          <p className="text-slate-400 text-sm mb-3">Current Water Level</p>
          <div className="mb-4"><p className="text-3xl font-bold text-slate-100">{waterLevel.current.toFixed(2)}</p><p className="text-xs text-slate-500 mt-1">meters</p></div>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between"><span className="text-slate-400">Rate of Change:</span><span className="text-slate-200 font-semibold">+{waterLevel.rateOfChange} m/hr</span></div>
            <div className="flex justify-between"><span className="text-slate-400">Critical Threshold:</span><span className="text-blue-400 font-semibold">{waterLevel.critical_threshold}m</span></div>
          </div>
          <div className="mt-4 w-full bg-slate-800 rounded-full h-1.5 overflow-hidden"><div className="bg-blue-500 h-1.5 rounded-full nd-bar-grow" style={{ width: `${Math.min((waterLevel.current / waterLevel.critical_threshold) * 100, 100)}%` }}></div></div>
        </div>
      </Card>
    </div>
  );
};

export default RiskOverviewCards;