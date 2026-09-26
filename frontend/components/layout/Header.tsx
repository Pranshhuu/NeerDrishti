"use client";

import React, { useState } from "react";
import {
  MapPin,
  Bell,
  User,
  ChevronDown,
  Zap,
  Settings,
  LogOut,
} from "lucide-react";

import { formatTime } from "@/data/mockData";

interface HeaderProps {
  lastUpdated?: string;
}

export const Header: React.FC<HeaderProps> = ({
  lastUpdated = new Date().toISOString(),
}) => {
  const [showLocationDropdown, setShowLocationDropdown] =
    useState(false);

  const [showNotifications, setShowNotifications] =
    useState(false);

  const [showProfileMenu, setShowProfileMenu] =
    useState(false);

  const [selectedLocation, setSelectedLocation] =
    useState("Mumbai Metropolitan");

  const locations = [
    "Mumbai Metropolitan",
    "Dharavi Ward",
    "Eastern Suburbs",
    "Western Suburbs",
    "South Mumbai",
  ];

  const scrollToAlerts = () => {
    const element = document.getElementById("alerts");

    if (element) {
      element.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }

    setShowNotifications(false);
  };

  return (
    <header className="bg-slate-950 border-b border-slate-800 sticky top-0 z-40">
      <div className="px-6 py-4">
        <div className="flex items-center justify-between">

          {/* LOCATION DROPDOWN */}

          <div className="relative">
            <button
              onClick={() => {
                setShowLocationDropdown(
                  !showLocationDropdown
                );

                setShowNotifications(false);
                setShowProfileMenu(false);
              }}
              className="flex items-center gap-2 px-4 py-2 bg-slate-900 border border-slate-700 rounded-md hover:bg-slate-800 hover:border-slate-600 transition-colors text-slate-300"
            >
              <MapPin
                size={18}
                className="text-cyan-400"
              />

              <span className="font-medium text-sm">
                {selectedLocation}
              </span>

              <ChevronDown
                size={16}
                className={`transition-transform duration-200 ${showLocationDropdown ? "rotate-180" : ""}`}
              />
            </button>

            {showLocationDropdown && (
              <div className="nd-dropdown-in absolute top-full left-0 mt-2 w-56 bg-slate-900 border border-slate-700 rounded-md shadow-lg z-50 overflow-hidden">
                {locations.map((location) => (
                  <button
                    key={location}
                    onClick={() => {
                      setSelectedLocation(location);
                      setShowLocationDropdown(false);
                    }}
                    className={`
                      w-full text-left px-4 py-3 text-sm transition-colors
                      ${
                        selectedLocation === location
                          ? "bg-cyan-600/20 text-cyan-300"
                          : "text-slate-300 hover:bg-slate-800"
                      }
                    `}
                  >
                    {location}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* RIGHT SIDE */}

          <div className="flex items-center gap-4">

            <div className="hidden md:flex items-center gap-2 text-sm">
              <Zap
                size={16}
                className="text-cyan-400"
              />

              <span className="text-slate-400">
                Updated:{" "}
                <span className="text-slate-300 font-medium">
                  {formatTime(lastUpdated)}
                </span>
              </span>
            </div>

            {/* NOTIFICATIONS */}

            <div className="relative">
              <button
                onClick={() => {
                  setShowNotifications(
                    !showNotifications
                  );

                  setShowLocationDropdown(false);
                  setShowProfileMenu(false);
                }}
                className="relative p-2 hover:bg-slate-900 rounded-md transition-colors"
              >
                <Bell
                  size={20}
                  className="text-slate-400 hover:text-slate-200"
                />

                <span className="absolute top-1 right-1 w-2 h-2 bg-red-600 rounded-full" />
              </button>

              {showNotifications && (
                <div className="nd-dropdown-in absolute right-0 top-full mt-3 w-80 bg-slate-900 border border-slate-700 rounded-lg shadow-xl z-50">

                  <div className="p-4 border-b border-slate-800">
                    <div className="flex items-center justify-between">
                      <h3 className="font-semibold text-slate-100">
                        Notifications
                      </h3>

                      <span className="text-xs text-red-400">
                        4 active
                      </span>
                    </div>
                  </div>

                  <div className="p-3 space-y-2">

                    <button
                      onClick={scrollToAlerts}
                      className="w-full text-left p-3 rounded-md hover:bg-slate-800 transition-colors"
                    >
                      <p className="text-sm text-slate-200">
                        🚨 Flash Flood Warning
                      </p>

                      <p className="text-xs text-slate-400 mt-1">
                        Critical alert detected in Dadar East.
                      </p>
                    </button>

                    <button
                      onClick={scrollToAlerts}
                      className="w-full text-left p-3 rounded-md hover:bg-slate-800 transition-colors"
                    >
                      <p className="text-sm text-slate-200">
                        🌧 Rainfall Increasing
                      </p>

                      <p className="text-xs text-slate-400 mt-1">
                        Rainfall expected to peak soon.
                      </p>
                    </button>

                  </div>

                  <button
                    onClick={scrollToAlerts}
                    className="w-full p-3 border-t border-slate-800 text-sm text-cyan-400 hover:bg-slate-800"
                  >
                    View All Alerts
                  </button>

                </div>
              )}
            </div>

            {/* PROFILE */}

            <div className="relative">
              <button
                onClick={() => {
                  setShowProfileMenu(!showProfileMenu);

                  setShowLocationDropdown(false);
                  setShowNotifications(false);
                }}
                className="flex items-center gap-2 px-3 py-2 hover:bg-slate-900 rounded-md transition-colors"
              >
                <div className="w-8 h-8 bg-gradient-to-br from-cyan-600 to-blue-600 rounded-full flex items-center justify-center">
                  <User
                    size={16}
                    className="text-white"
                  />
                </div>

                <ChevronDown
                  size={16}
                  className={`text-slate-400 transition-transform duration-200 ${showProfileMenu ? "rotate-180" : ""}`}
                />
              </button>

              {showProfileMenu && (
                <div className="nd-dropdown-in absolute right-0 top-full mt-3 w-52 bg-slate-900 border border-slate-700 rounded-lg shadow-xl z-50 overflow-hidden">

                  <div className="p-4 border-b border-slate-800">
                    <p className="text-sm font-medium text-slate-100">
                      NeerDrishti User
                    </p>

                    <p className="text-xs text-slate-400 mt-1">
                      System Administrator
                    </p>
                  </div>

                  <button
                    className="w-full flex items-center gap-3 px-4 py-3 text-sm text-slate-300 hover:bg-slate-800"
                    onClick={() => {
                      const element =
                        document.getElementById("settings");

                      if (element) {
                        element.scrollIntoView({
                          behavior: "smooth",
                        });
                      }

                      setShowProfileMenu(false);
                    }}
                  >
                    <Settings size={16} />
                    Settings
                  </button>

                  <button
                    onClick={() => {
                      alert(
                        "Demo mode: Logout functionality can be connected to authentication later."
                      );

                      setShowProfileMenu(false);
                    }}
                    className="w-full flex items-center gap-3 px-4 py-3 text-sm text-red-400 hover:bg-slate-800"
                  >
                    <LogOut size={16} />
                    Logout
                  </button>

                </div>
              )}
            </div>

          </div>
        </div>
      </div>
    </header>
  );
};

export default Header;