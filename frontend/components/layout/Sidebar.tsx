"use client";

import React, { useState } from "react";
import {
  LayoutDashboard,
  Map,
  TrendingUp,
  BarChart3,
  AlertCircle,
  Settings,
  ChevronRight,
} from "lucide-react";

interface NavItem {
  label: string;
  icon: React.ReactNode;
  targetId: string;
  badge?: number;
}

export const Sidebar: React.FC = () => {
  const [activeItem, setActiveItem] = useState("Dashboard");

  const navItems: NavItem[] = [
    {
      label: "Dashboard",
      icon: <LayoutDashboard size={20} />,
      targetId: "dashboard",
    },
    {
      label: "Live Map",
      icon: <Map size={20} />,
      targetId: "flood-map",
    },
    {
      label: "Predictions",
      icon: <TrendingUp size={20} />,
      targetId: "rainfall-trend",
    },
    {
      label: "Analytics",
      icon: <BarChart3 size={20} />,
      targetId: "analytics",
    },
    {
      label: "Alerts",
      icon: <AlertCircle size={20} />,
      targetId: "alerts",
      badge: 4,
    },
    {
      label: "Settings",
      icon: <Settings size={20} />,
      targetId: "settings",
    },
  ];

  const handleNavigation = (item: NavItem) => {
    setActiveItem(item.label);

    const element = document.getElementById(item.targetId);

    if (element) {
      element.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  };

  return (
    <aside className="w-64 bg-slate-950 border-r border-slate-800 flex flex-col h-screen sticky top-0">
      <div className="p-6 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 bg-cyan-600 rounded-md flex items-center justify-center shadow-[0_0_12px_-2px_rgba(6,182,212,0.6)]">
            <span className="text-white font-bold text-sm">ND</span>
          </div>

          <div>
            <h1 className="text-lg font-bold text-slate-100">
              NeerDrishti
            </h1>
            <p className="text-xs text-slate-400">
              Flood Intelligence
            </p>
          </div>
        </div>
      </div>

      <nav className="flex-1 p-4 space-y-2">
        {navItems.map((item) => {
          const isActive = activeItem === item.label;
          return (
            <button
              key={item.label}
              onClick={() => handleNavigation(item)}
              className={`
                group w-full flex items-center gap-3 px-4 py-3 rounded-md
                transition-all duration-200 relative overflow-hidden
                ${
                  isActive
                    ? "bg-cyan-600/20 text-cyan-400 border-l-2 border-cyan-400"
                    : "text-slate-300 border-l-2 border-transparent hover:bg-slate-900 hover:text-slate-100 hover:border-slate-700"
                }
              `}
            >
              <span className="flex-shrink-0 transition-transform duration-200 group-hover:scale-110">
                {item.icon}
              </span>

              <span className="flex-1 text-left text-sm font-medium">
                {item.label}
              </span>

              {item.badge && (
                <span className="bg-red-600 text-white text-xs rounded-full w-5 h-5 flex items-center justify-center">
                  {item.badge}
                </span>
              )}

              {isActive && (
                <ChevronRight
                  size={16}
                  className="text-cyan-400"
                />
              )}
            </button>
          );
        })}
      </nav>

      <div className="p-4 border-t border-slate-800">
        <div className="bg-slate-900 rounded-md p-3">
          <p className="text-xs text-slate-400 mb-2">
            System Status
          </p>

          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-green-500 rounded-full" />

            <span className="text-xs text-slate-300">
              Operational
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;