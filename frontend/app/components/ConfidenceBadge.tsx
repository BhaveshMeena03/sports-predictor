"use client";

export default function ConfidenceBadge({ value }: { value: number }) {
  let color = "var(--red)";
  let bg = "rgba(255, 23, 68, 0.15)";
  let label = "HIGH RISK";

  if (value >= 80) {
    color = "var(--green)";
    bg = "rgba(0, 230, 118, 0.15)";
    label = "SAFE";
  } else if (value >= 65) {
    color = "var(--cyan)";
    bg = "rgba(0, 229, 255, 0.15)";
    label = "MODERATE";
  } else if (value >= 50) {
    color = "var(--yellow)";
    bg = "rgba(255, 234, 0, 0.15)";
    label = "RISKY";
  }

  return (
    <div className="flex items-center gap-3">
      <div className="relative w-16 h-16">
        <svg className="w-16 h-16 -rotate-90" viewBox="0 0 36 36">
          <path
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
            fill="none"
            stroke="var(--border)"
            strokeWidth="3"
          />
          <path
            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
            fill="none"
            stroke={color}
            strokeWidth="3"
            strokeDasharray={`${value}, 100`}
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-sm font-bold" style={{ color }}>{value}%</span>
        </div>
      </div>
      <div>
        <span className="text-xs font-bold px-2 py-1 rounded" style={{ background: bg, color }}>
          {label}
        </span>
      </div>
    </div>
  );
}
