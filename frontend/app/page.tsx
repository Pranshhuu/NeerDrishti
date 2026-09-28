import { MainLayout } from "@/components/layout/MainLayout";
import { DashboardHero } from "@/components/dashboard/DashboardHero";
import { RiskOverviewCards } from "@/components/dashboard/RiskOverviewCards";
import { FloodRiskMap } from "@/components/dashboard/FloodRiskMap";
import { RainfallTrend } from "@/components/dashboard/RainfallTrend";
import { WaterLevelTrend } from "@/components/dashboard/WaterLevelTrend";
import { AlertsPanel } from "@/components/dashboard/AlertsPanel";
import { WeatherProvider } from "@/components/dashboard/WeatherProvider";
import { WardRunoffOverview } from "@/components/dashboard/WardRunoffOverview";
import { FloodRiskOverview } from "@/components/dashboard/FloodRiskOverview";

import {
  mockSystemStatus,
  mockWaterLevelData,
  mockAlerts,
} from "@/data/mockData";

export default function Home() {
  const lastUpdated = new Date().toISOString();

  return (
    <MainLayout lastUpdated={lastUpdated}>
      <div id="dashboard" className="min-h-screen bg-slate-950">
        <DashboardHero systemStatus={mockSystemStatus} />

        <WeatherProvider>
          <RiskOverviewCards />

          <WardRunoffOverview />

          <FloodRiskOverview />

          <div className="px-6 py-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div id="flood-map" className="lg:col-span-2 scroll-mt-24">
              <FloodRiskMap />
            </div>
            <div id="alerts" className="scroll-mt-24">
              <AlertsPanel alerts={mockAlerts} />
            </div>
          </div>

          <div id="analytics" className="px-6 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6 scroll-mt-24">
            <div id="rainfall-trend" className="scroll-mt-24">
              <RainfallTrend />
            </div>
            <div>
              <WaterLevelTrend data={mockWaterLevelData} />
            </div>
          </div>
        </WeatherProvider>

        <div id="settings" className="mx-6 mb-6 p-6 bg-slate-900 border border-slate-800 rounded-lg scroll-mt-24">
          <h2 className="text-lg font-semibold text-slate-100">System Settings</h2>
          <p className="text-sm text-slate-400 mt-2">FlowSight configuration and monitoring preferences.</p>
          <div className="mt-4 flex items-center gap-2">
            <div className="w-2 h-2 bg-green-500 rounded-full" />
            <span className="text-sm text-slate-300">System operational</span>
          </div>
        </div>

        <div className="h-12" />
      </div>
    </MainLayout>
  );
}