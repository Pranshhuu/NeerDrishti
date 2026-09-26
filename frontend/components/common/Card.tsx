import React from "react";

type CardAccent =
  | "none"
  | "cyan"
  | "red"
  | "orange"
  | "amber"
  | "blue"
  | "purple"
  | "teal";

interface CardProps {
  children: React.ReactNode;
  className?: string;
  title?: string;
  subtitle?: string;
  noPadding?: boolean;
  accent?: CardAccent;
  hoverable?: boolean;
  animate?: boolean;
  animationDelay?: 1 | 2 | 3 | 4;
}

const ACCENT_BORDER: Record<CardAccent, string> = {
  none: "",
  cyan: "border-t-2 border-t-cyan-500",
  red: "border-t-2 border-t-red-500",
  orange: "border-t-2 border-t-orange-500",
  amber: "border-t-2 border-t-amber-500",
  blue: "border-t-2 border-t-blue-500",
  purple: "border-t-2 border-t-purple-500",
  teal: "border-t-2 border-t-teal-500",
};

const ACCENT_HOVER_BORDER: Record<CardAccent, string> = {
  none: "hover:border-slate-700",
  cyan: "hover:border-cyan-800",
  red: "hover:border-red-900",
  orange: "hover:border-orange-900",
  amber: "hover:border-amber-900",
  blue: "hover:border-blue-900",
  purple: "hover:border-purple-900",
  teal: "hover:border-teal-900",
};

export const Card: React.FC<CardProps> = ({
  children,
  className = "",
  title,
  subtitle,
  noPadding = false,
  accent = "none",
  hoverable = false,
  animate = false,
  animationDelay,
}) => {
  const animateClass = animate
    ? `nd-animate-in ${animationDelay ? `nd-animate-in-delay-${animationDelay}` : ""}`
    : "";

  const hoverClass = hoverable
    ? `nd-hover-lift ${ACCENT_HOVER_BORDER[accent]} hover:shadow-lg hover:shadow-black/20`
    : "";

  return (
    <div
      className={`
        bg-slate-900 border border-slate-800 rounded-md
        ${!noPadding ? "p-6" : ""}
        ${ACCENT_BORDER[accent]}
        ${hoverClass}
        ${animateClass}
        ${className}
      `}
    >
      {(title || subtitle) && (
        <div className="mb-4">
          {title && <h3 className="text-lg font-semibold text-slate-100">{title}</h3>}
          {subtitle && <p className="text-sm text-slate-400 mt-1">{subtitle}</p>}
        </div>
      )}
      {children}
    </div>
  );
};

export default Card;