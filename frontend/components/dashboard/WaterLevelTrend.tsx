import React from "react";
import { Waves, Info } from "lucide-react";
import Card from "@/components/common/Card";
import { WaterLevelData } from "@/data/mockData";

interface WaterLevelTrendProps {
  data: WaterLevelData;
}

export const WaterLevelTrend: React.FC<WaterLevelTrendProps> = ({ data }) => {
  const maxLevel = Math.max(data.critical_threshold * 1.1, 2.5);
  const padding = 40;
  const chartWidth = 300;
  const chartHeight = 180;

  const points = data.historicalData.map((point, index) => {
    const x = padding + (index / (data.historicalData.length - 1)) * chartWidth;
    const y = chartHeight + padding - (point.level / maxLevel) * chartHeight;
    return { x, y, ...point };
  });

  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  const fillPathD = pathD + ` L ${points[points.length - 1].x} ${chartHeight + padding} L ${points[0].x} ${chartHeight + padding} Z`;
  const thresholdY = chartHeight + padding - (data.critical_threshold / maxLevel) * chartHeight;
  const normalY = chartHeight + padding - (data.normal / maxLevel) * chartHeight;

  return (
    <Card noPadding accent="blue" hoverable animate>
      <div className="flex flex-col h-full">
        <div className="p-6 border-b border-slate-800 flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <h3 className="text-lg font-semibold text-slate-100">Water Level Monitoring</h3>
              <Waves size={18} className="text-blue-400" />
            </div>
            <p className="text-sm text-slate-400">24-hour water level trend</p>
          </div>
          <div className="text-right">
            <p className="text-2xl font-bold text-blue-400">{data.current.toFixed(2)}</p>
            <p className="text-xs text-slate-400 mt-1">meters (current)</p>
          </div>
        </div>

        <div className="flex-1 p-6 flex items-center justify-center">
          <div className="w-full">
            <svg className="w-full" height="250" viewBox={`0 0 ${chartWidth + padding * 2} ${chartHeight + padding * 2}`}>
              <defs>
                <linearGradient id="nd-water-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3b82f6" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#3b82f6" stopOpacity="0" />
                </linearGradient>
              </defs>
              <rect x={padding} y={padding} width={chartWidth} height={thresholdY - padding} fill="#dc2626" opacity="0.05" />
              <line x1={padding} y1={thresholdY} x2={chartWidth + padding} y2={thresholdY} stroke="#dc2626" strokeWidth="2" strokeDasharray="4" opacity="0.7" />
              <text x={padding + 5} y={thresholdY - 5} className="text-xs fill-red-500 font-semibold">Critical</text>
              <line x1={padding} y1={normalY} x2={chartWidth + padding} y2={normalY} stroke="#22c55e" strokeWidth="1" strokeDasharray="4" opacity="0.5" />
              <text x={padding + 5} y={normalY + 15} className="text-xs fill-green-600">Normal</text>
              {[0, 25, 50, 75, 100].map((percent) => (
                <g key={`grid-${percent}`}>
                  <line x1={padding} y1={padding + ((100 - percent) / 100) * chartHeight} x2={chartWidth + padding} y2={padding + ((100 - percent) / 100) * chartHeight} stroke="#334155" strokeDasharray="4" opacity="0.3" />
                  <text x={padding - 10} y={padding + ((100 - percent) / 100) * chartHeight + 4} textAnchor="end" className="text-xs fill-slate-500">{(percent / 100) * maxLevel}</text>
                </g>
              ))}
              <path d={fillPathD} fill="url(#nd-water-fill)" className="nd-chart-fill" />
              <path d={pathD} fill="none" stroke="#3b82f6" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" pathLength={1} className="nd-chart-path" />
              {points.map((point, i) => {
                const isLast = i === points.length - 1;
                return (
                  <g key={`point-${i}`}>
                    <title>{`${point.time}: ${point.level.toFixed(2)} m`}</title>
                    <circle cx={point.x} cy={point.y} r={isLast ? 5 : 3} fill="#3b82f6" opacity={isLast ? 1 : 0.4} />
                  </g>
                );
              })}
              <line x1={padding} y1={chartHeight + padding} x2={chartWidth + padding} y2={chartHeight + padding} stroke="#475569" strokeWidth="1" />
              <line x1={padding} y1={padding} x2={padding} y2={chartHeight + padding} stroke="#475569" strokeWidth="1" />
              {data.historicalData.map((point, i) => (
                <text key={`label-${i}`} x={padding + (i / (data.historicalData.length - 1)) * chartWidth} y={chartHeight + padding + 20} textAnchor="middle" className="text-xs fill-slate-500">{point.time}</text>
              ))}
            </svg>
          </div>
        </div>

        <div className="p-4 border-t border-slate-800 bg-slate-900 bg-opacity-50 flex items-start gap-2">
          <Info size={14} className="text-slate-400 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-slate-400">Water level <span className="text-blue-400 font-semibold">rising at {data.rateOfChange} m/hr</span>. Current level is <span className="text-orange-400 font-semibold">{((data.current / data.critical_threshold) * 100).toFixed(0)}% of critical threshold</span>.</p>
        </div>
      </div>
    </Card>
  );
};

export default WaterLevelTrend;