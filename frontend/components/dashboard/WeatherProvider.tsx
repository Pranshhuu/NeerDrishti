"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { getWeather, WeatherApiError } from "@/lib/api/weather";
import { toLiveRainfallView, type LiveRainfallView } from "@/lib/weather/adapter";

export type WeatherState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: LiveRainfallView };

const WeatherContext = createContext<WeatherState | null>(null);

interface WeatherProviderProps {
  children: React.ReactNode;
}

export function WeatherProvider({ children }: WeatherProviderProps) {
  const [state, setState] = useState<WeatherState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getWeather()
      .then((response) => {
        if (cancelled) return;
        setState({ status: "ready", data: toLiveRainfallView(response) });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof WeatherApiError ? err.message : "Unable to load live weather data.";
        setState({ status: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return <WeatherContext.Provider value={state}>{children}</WeatherContext.Provider>;
}

export function useWeather(): WeatherState {
  const context = useContext(WeatherContext);
  if (context === null) {
    throw new Error("useWeather must be used within a WeatherProvider");
  }
  return context;
}