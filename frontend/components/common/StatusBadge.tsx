import React from "react";

interface StatusBadgeProps {
  level: string;
  size?: "sm" | "md" | "lg";
  className?: string;
  /**
   * Adds a small pulsing dot before the label to indicate the underlying
   * value is actively updating. Defaults to false, preserving the original
   * appearance. StatusBadge itself makes no claim about data freshness -
   * callers decide when this is semantically true.
   */
  pulse?: boolean;
}

const getBadgeStyles = (level: string, size: string): string => {
  const sizeClasses = {
    sm: "px-2 py-1 text-xs",
    md: "px-3 py-1.5 text-sm",
    lg: "px-4 py-2 text-base",
  };

  const colorClasses: Record<string, string> = {
    critical: "bg-red-950 text-red-200",
    high: "bg-orange-950 text-orange-200",
    moderate: "bg-yellow-950 text-yellow-200",
    low: "bg-green-950 text-green-200",
    operational: "bg-green-950 text-green-200",
    degraded: "bg-yellow-950 text-yellow-200",
    offline: "bg-red-950 text-red-200",
  };

  return `${sizeClasses[size as keyof typeof sizeClasses]} ${
    colorClasses[level] || colorClasses.low
  } rounded font-medium inline-flex items-center gap-1.5`;
};

const getDotColor = (level: string): string => {
  const dotColors: Record<string, string> = {
    critical: "bg-red-400",
    high: "bg-orange-400",
    moderate: "bg-yellow-400",
    low: "bg-green-400",
    operational: "bg-green-400",
    degraded: "bg-yellow-400",
    offline: "bg-red-400",
  };
  return dotColors[level] || dotColors.low;
};

const getDisplayLabel = (level: string): string => {
  const labels: Record<string, string> = {
    critical: "CRITICAL",
    high: "HIGH",
    moderate: "MODERATE",
    low: "LOW",
    operational: "OPERATIONAL",
    degraded: "DEGRADED",
    offline: "OFFLINE",
  };
  return labels[level] || level.toUpperCase();
};

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  level,
  size = "md",
  className = "",
  pulse = false,
}) => {
  return (
    <span className={`${getBadgeStyles(level, size)} ${className}`}>
      {pulse && (
        <span
          className={`inline-flex w-1.5 h-1.5 rounded-full ${getDotColor(level)} nd-pulse-dot`}
          aria-hidden="true"
        />
      )}
      {getDisplayLabel(level)}
    </span>
  );
};

export default StatusBadge;