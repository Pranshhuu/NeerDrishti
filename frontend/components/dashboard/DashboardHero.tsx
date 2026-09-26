import React from "react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { SystemStatus } from "@/data/mockData";
import { StatusBadge } from "@/components/common/StatusBadge";

interface DashboardHeroProps {
  systemStatus: SystemStatus;
}

export const DashboardHero: React.FC<DashboardHeroProps> = ({ systemStatus }) => {
  const isOperational = systemStatus.status === "operational";

  return (
    <div className="relative bg-gradient-to-r from-slate-900 via-slate-900 to-slate-950 nd-hero-gradient border-b border-slate-800 px-6 py-8 overflow-hidden">
      <svg
        className="absolute inset-0 w-full h-full opacity-[0.04] pointer-events-none"
        aria-hidden="true"
        preserveAspectRatio="none"
      >
        <defs>
          <pattern id="nd-hero-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#06b6d4" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#nd-hero-grid)" />
      </svg>

      <div className="relative flex items-start justify-between">
        <div className="nd-animate-in">
          <h1 className="text-3xl font-bold text-slate-100 mb-2">
            Flood Intelligence Dashboard
          </h1>
          <p className="text-slate-400 max-w-2xl mb-4">
            Real-time urban flood nowcasting and decision-support system combining rainfall and drainage data.
            Nowcast horizon: <span className="font-semibold text-slate-300">{systemStatus.nowcastHorizon}</span>
          </p>

          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2">
              {isOperational ? (
                <>
                  <CheckCircle2 size={18} className="text-green-400" />
                  <StatusBadge level={systemStatus.status} size="md" pulse={isOperational} />
                </>
              ) : (
                <>
                  <AlertTriangle size={18} className="text-red-400" />
                  <StatusBadge level={systemStatus.status} size="md" />
                </>
              )}
            </div>

            <div className="text-sm">
              <span className="text-slate-400">Location:</span>{" "}
              <span className="text-slate-300 font-semibold">{systemStatus.location}</span>
            </div>
          </div>
        </div>

        <div className="relative bg-slate-900 border border-slate-700 rounded-md p-4 max-w-xs nd-animate-in nd-animate-in-delay-1">
          <p className="text-xs text-slate-400 uppercase tracking-wide mb-2">Real-Time Status</p>
          <div className="space-y-3">
            <div>
              <p className="text-xs text-slate-400">System Health</p>
              <p className="text-lg font-semibold text-green-400">Optimal</p>
            </div>
            <div>
              <p className="text-xs text-slate-400">Data Assimilation</p>
              <p className="text-sm text-slate-300">Active · 47 stations</p>
            </div>
            <div className="pt-2 border-t border-slate-700">
              <p className="text-xs text-slate-400">Next Update</p>
              <p className="text-sm text-cyan-400">In 5 minutes</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DashboardHero;