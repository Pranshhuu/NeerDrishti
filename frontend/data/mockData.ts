export type RiskLevel = "low" | "moderate" | "high" | "critical";

export interface SystemStatus {
  status: "operational" | "degraded" | "offline";
  lastUpdated: string;
  nowcastHorizon: string;
  location: string;
}

export interface FloodRiskMetrics {
  currentRisk: RiskLevel;
  riskScore: number;
  inundationRisk: RiskLevel;
  affectedAreas: number;
  estimatedTimeToFlood: string;
}

export interface RainfallData {
  intensity: number;
  accumulated: number;
  intensity_level: RiskLevel;
  nowcast_trend: "increasing" | "stable" | "decreasing";
  forecastNext2Hours: Array<{ time: string; intensity: number }>;
}

export interface DrainageStatus {
  capacity_utilization: number;
  status: "normal" | "stressed" | "critical";
  pumpingRate: number;
  floodedSections: number;
}

export interface WaterLevelData {
  current: number;
  normal: number;
  critical_threshold: number;
  trend: "rising" | "stable" | "falling";
  rateOfChange: number;
  historicalData: Array<{ time: string; level: number }>;
}

export interface HighRiskZone {
  id: string;
  name: string;
  riskLevel: RiskLevel;
  population_at_risk: number;
  priority: "high" | "medium" | "low";
  lat: number;
  lng: number;
}

export interface Alert {
  id: string;
  severity: "info" | "warning" | "critical";
  title: string;
  description: string;
  location: string;
  timestamp: string;
  actionRequired: boolean;
  recommendedAction?: string;
}

export const mockSystemStatus: SystemStatus = {
  status: "operational",
  lastUpdated: new Date(Date.now() - 5 * 60000).toISOString(),
  nowcastHorizon: "2 hours",
  location: "Mumbai Metropolitan Area",
};

export const mockFloodRiskMetrics: FloodRiskMetrics = {
  currentRisk: "high",
  riskScore: 72,
  inundationRisk: "moderate",
  affectedAreas: 8,
  estimatedTimeToFlood: "1.5 hours",
};

export const mockRainfallData: RainfallData = {
  intensity: 45.2,
  accumulated: 127.8,
  intensity_level: "high",
  nowcast_trend: "increasing",
  forecastNext2Hours: [
    { time: "Now", intensity: 45.2 },
    { time: "+30min", intensity: 52.1 },
    { time: "+60min", intensity: 58.3 },
    { time: "+90min", intensity: 61.5 },
    { time: "+120min", intensity: 58.9 },
  ],
};

export const mockDrainageStatus: DrainageStatus = {
  capacity_utilization: 78,
  status: "stressed",
  pumpingRate: 85,
  floodedSections: 3,
};

export const mockWaterLevelData: WaterLevelData = {
  current: 1.85,
  normal: 0.5,
  critical_threshold: 2.2,
  trend: "rising",
  rateOfChange: 0.12,
  historicalData: [
    { time: "0:00", level: 0.45 },
    { time: "3:00", level: 0.68 },
    { time: "6:00", level: 1.02 },
    { time: "9:00", level: 1.34 },
    { time: "12:00", level: 1.58 },
    { time: "15:00", level: 1.72 },
    { time: "18:00", level: 1.85 },
  ],
};

export const mockHighRiskZones: HighRiskZone[] = [
  {
    id: "zone_1",
    name: "Dadar East",
    riskLevel: "critical",
    population_at_risk: 45000,
    priority: "high",
    lat: 19.0176,
    lng: 72.8479,
  },
  {
    id: "zone_2",
    name: "Parel Industrial Zone",
    riskLevel: "high",
    population_at_risk: 28000,
    priority: "high",
    lat: 19.0055,
    lng: 72.8363,
  },
  {
    id: "zone_3",
    name: "Mahim-Bandra Link Road",
    riskLevel: "high",
    population_at_risk: 35000,
    priority: "high",
    lat: 19.0596,
    lng: 72.8295,
  },
  {
    id: "zone_4",
    name: "Lower Parel",
    riskLevel: "moderate",
    population_at_risk: 22000,
    priority: "medium",
    lat: 19.0086,
    lng: 72.8246,
  },
  {
    id: "zone_5",
    name: "Colaba Industrial Estate",
    riskLevel: "moderate",
    population_at_risk: 18000,
    priority: "medium",
    lat: 18.9606,
    lng: 72.8247,
  },
  {
    id: "zone_6",
    name: "Worli Sea Face",
    riskLevel: "low",
    population_at_risk: 12000,
    priority: "low",
    lat: 19.0057,
    lng: 72.8194,
  },
];

export const mockAlerts: Alert[] = [
  {
    id: "alert_1",
    severity: "critical",
    title: "Flash Flood Warning - Dadar East",
    description: "Rainfall intensity exceeds 60mm/hr. Drainage capacity at 85%. Inundation risk high.",
    location: "Dadar East Zone",
    timestamp: new Date(Date.now() - 12 * 60000).toISOString(),
    actionRequired: true,
    recommendedAction: "Issue evacuation alerts for low-lying areas. Activate emergency pumping stations.",
  },
  {
    id: "alert_2",
    severity: "warning",
    title: "Drainage Capacity Alert",
    description: "Storm drain network operating at 78% capacity. Water levels rising at 0.12 m/hr.",
    location: "Parel Industrial Zone",
    timestamp: new Date(Date.now() - 28 * 60000).toISOString(),
    actionRequired: true,
    recommendedAction: "Redirect traffic from waterlogged streets. Pre-position dewatering equipment.",
  },
  {
    id: "alert_3",
    severity: "warning",
    title: "Nowcast: Rainfall Expected to Peak in 90 Minutes",
    description: "Current rainfall 45.2 mm/hr, forecast peak 61.5 mm/hr at +90 min.",
    location: "Mumbai Metropolitan Area",
    timestamp: new Date(Date.now() - 45 * 60000).toISOString(),
    actionRequired: false,
    recommendedAction: "Prepare contingency plans. Ensure pumping stations are at full capacity.",
  },
  {
    id: "alert_4",
    severity: "info",
    title: "System Status: Operational",
    description: "All monitoring stations operational. Data assimilation running normally. Next update in 5 minutes.",
    location: "System",
    timestamp: new Date(Date.now() - 5 * 60000).toISOString(),
    actionRequired: false,
  },
];

export const getRiskColor = (level: RiskLevel): string => {
  switch (level) {
    case "critical":
      return "#dc2626";
    case "high":
      return "#f97316";
    case "moderate":
      return "#eab308";
    case "low":
      return "#22c55e";
    default:
      return "#64748b";
  }
};

export const getRiskBgColor = (level: RiskLevel): string => {
  switch (level) {
    case "critical":
      return "bg-red-950";
    case "high":
      return "bg-orange-950";
    case "moderate":
      return "bg-yellow-950";
    case "low":
      return "bg-green-950";
    default:
      return "bg-slate-900";
  }
};

export const formatTime = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
  });
};

export const formatDateTime = (isoString: string): string => {
  const date = new Date(isoString);
  return date.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};








