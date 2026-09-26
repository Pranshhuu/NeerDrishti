import React from "react";
import { RainfallCard } from "@/components/dashboard/RainfallCard";
import { FlowConcentrationCard } from "@/components/dashboard/FlowConcentrationCard";
import { RunoffScenarioCard } from "@/components/dashboard/RunoffScenarioCard";

export const RiskOverviewCards: React.FC = () => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 px-6 py-6">
      <RainfallCard />
      <FlowConcentrationCard />
      <RunoffScenarioCard />
    </div>
  );
};

export default RiskOverviewCards;