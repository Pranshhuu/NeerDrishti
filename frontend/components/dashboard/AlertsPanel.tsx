"use client";

import React, { useState } from "react";
import {
  AlertCircle,
  AlertTriangle,
  Info,
  Clock,
  CheckCircle2,
  ArrowRight,
  ChevronUp,
} from "lucide-react";

import { Card } from "@/components/common/Card";
import { Alert, formatDateTime } from "@/data/mockData";

interface AlertsPanelProps {
  alerts: Alert[];
}

const getSeverityIcon = (severity: string) => {
  switch (severity) {
    case "critical":
      return <AlertTriangle size={18} className="text-red-400" />;

    case "warning":
      return <AlertCircle size={18} className="text-orange-400" />;

    case "info":
      return <Info size={18} className="text-cyan-400" />;

    default:
      return <Info size={18} className="text-slate-400" />;
  }
};

const getBgColor = (severity: string): string => {
  switch (severity) {
    case "critical":
      return "bg-red-950 border-red-900";

    case "warning":
      return "bg-orange-950 border-orange-900";

    case "info":
      return "bg-cyan-950 border-cyan-900";

    default:
      return "bg-slate-900 border-slate-800";
  }
};

export const AlertsPanel: React.FC<AlertsPanelProps> = ({ alerts }) => {
  const [showHistory, setShowHistory] = useState(false);

  const criticalAlerts = alerts.filter(
    (alert) => alert.severity === "critical"
  );

  const warningAlerts = alerts.filter(
    (alert) => alert.severity === "warning"
  );

  const infoAlerts = alerts.filter(
    (alert) => alert.severity === "info"
  );

  return (
    <Card noPadding animate>
      <div className="flex flex-col h-full">
        {/* HEADER */}

        <div className="p-6 border-b border-slate-800">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-lg font-semibold text-slate-100">
              Active Alerts
            </h3>

            <span className="text-sm text-slate-400">
              {alerts.length} active
            </span>
          </div>

          <p className="text-sm text-slate-400">
            Decision-support alerts from nowcasting system
          </p>
        </div>

        {/* ALERT COUNTS */}

        <div className="p-4 bg-slate-900 bg-opacity-50 border-b border-slate-800 grid grid-cols-3 gap-4">
          <div className="text-center">
            <p className="text-2xl font-bold text-red-400">
              {criticalAlerts.length}
            </p>

            <p className="text-xs text-slate-400 mt-1">
              Critical
            </p>
          </div>

          <div className="text-center">
            <p className="text-2xl font-bold text-orange-400">
              {warningAlerts.length}
            </p>

            <p className="text-xs text-slate-400 mt-1">
              Warning
            </p>
          </div>

          <div className="text-center">
            <p className="text-2xl font-bold text-cyan-400">
              {infoAlerts.length}
            </p>

            <p className="text-xs text-slate-400 mt-1">
              Info
            </p>
          </div>
        </div>

        {/* ALERT LIST */}

        <div className="flex-1 overflow-y-auto">
          <div className="p-4 space-y-3">
            {alerts.map((alert, index) => (
              <div
                key={alert.id}
                className={`
                  border rounded-md p-4 nd-animate-in
                  transition-transform duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/20
                  ${getBgColor(alert.severity)}
                `}
                style={{ animationDelay: `${Math.min(index, 6) * 60}ms` }}
              >
                {/* ALERT TITLE */}

                <div className="flex items-start gap-3 mb-2">
                  <div className="flex-shrink-0 mt-0.5">
                    {getSeverityIcon(alert.severity)}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-semibold text-slate-100 text-sm leading-tight">
                        {alert.title}
                      </p>

                      {alert.actionRequired && (
                        <span className="text-xs font-bold px-2 py-1 bg-red-600 text-white rounded whitespace-nowrap flex-shrink-0">
                          ACTION
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                {/* DESCRIPTION */}

                <p className="text-xs text-slate-300 mb-3 ml-6">
                  {alert.description}
                </p>

                {/* LOCATION AND TIME */}

                <div className="ml-6 flex items-center gap-4 text-xs text-slate-400 mb-3 flex-wrap">
                  <div className="flex items-center gap-1">
                    <AlertCircle size={12} />

                    <span>
                      {alert.location}
                    </span>
                  </div>

                  <div className="flex items-center gap-1">
                    <Clock size={12} />

                    <span>
                      {formatDateTime(alert.timestamp)}
                    </span>
                  </div>
                </div>

                {/* RECOMMENDED ACTION */}

                {alert.recommendedAction && (
                  <div className="ml-6 p-3 bg-slate-900 bg-opacity-50 rounded border border-slate-700">
                    <p className="text-xs text-slate-400 mb-1 font-semibold">
                      Recommended Action:
                    </p>

                    <p className="text-xs text-slate-300 leading-relaxed">
                      {alert.recommendedAction}
                    </p>
                  </div>
                )}

                {/* MONITORING */}

                {!alert.actionRequired && (
                  <div className="ml-6 flex items-center gap-2 text-xs text-green-400 mt-2">
                    <CheckCircle2 size={12} />

                    <span>
                      Monitoring
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* ALERT HISTORY */}

        {showHistory && (
          <div className="border-t border-slate-800 bg-slate-950 nd-dropdown-in">
            <div className="p-5">
              <h3 className="text-base font-semibold text-slate-100 mb-1">
                Alert History
              </h3>

              <p className="text-xs text-slate-400 mb-4">
                Previously recorded alerts and monitoring activity
              </p>

              <div className="space-y-3">
                {alerts.map((alert) => (
                  <div
                    key={`history-${alert.id}`}
                    className="p-3 bg-slate-900 border border-slate-800 rounded-md hover:border-slate-700 transition-colors"
                  >
                    <div className="flex items-start gap-2">
                      {getSeverityIcon(alert.severity)}

                      <div className="flex-1">
                        <p className="text-sm font-medium text-slate-200">
                          {alert.title}
                        </p>

                        <p className="text-xs text-slate-400 mt-1">
                          {alert.description}
                        </p>

                        <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
                          <span>
                            {alert.location}
                          </span>

                          <span>
                            {formatDateTime(alert.timestamp)}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* BUTTON */}

        <div className="p-4 border-t border-slate-800 bg-slate-900 bg-opacity-50">
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="
              w-full
              flex
              items-center
              justify-between
              px-4
              py-3
              bg-slate-800
              hover:bg-slate-700
              transition-colors
              rounded-md
              text-sm
              text-slate-300
            "
          >
            <span>
              {showHistory
                ? "Hide Alert History"
                : "View All Alerts & History"}
            </span>

            {showHistory ? (
              <ChevronUp size={16} />
            ) : (
              <ArrowRight size={16} />
            )}
          </button>
        </div>
      </div>
    </Card>
  );
};

