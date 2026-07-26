"use client";
import { useCallback, useRef, useState } from "react";

/**
 * Reliability curve: stated probability (x) vs observed frequency (y).
 * A perfectly calibrated model sits on the dashed y=x diagonal; points below
 * it are overconfidence, points above it underconfidence. Model and market
 * are drawn as two series over the same bins so the comparison is direct.
 *
 * Colors are the validated dark-surface categorical slots (CVD-checked):
 * series 1 blue for the model, series 2 orange for the market.
 */

export type CurveBin = {
  bin: string;
  n: number;
  predicted: number;
  observed: number;
  gap: number;
};

export type Series = { name: string; color: string; bins: CurveBin[] };

const W = 560;
const H = 480;
const PAD = { top: 18, right: 18, bottom: 44, left: 52 };
const PW = W - PAD.left - PAD.right;
const PH = H - PAD.top - PAD.bottom;

const x = (p: number) => PAD.left + p * PW;
const y = (p: number) => PAD.top + (1 - p) * PH;

type Hover = { sx: number; sy: number; series: string; bin: CurveBin; color: string };

export default function ReliabilityChart({ series }: { series: Series[] }) {
  const [hover, setHover] = useState<Hover | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const show = useCallback(
    (e: React.MouseEvent, s: Series, b: CurveBin) => {
      const rect = wrapRef.current?.getBoundingClientRect();
      if (!rect) return;
      setHover({
        sx: e.clientX - rect.left,
        sy: e.clientY - rect.top,
        series: s.name,
        bin: b,
        color: s.color,
      });
    },
    [],
  );

  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div ref={wrapRef} className="relative w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        role="img"
        aria-label="Reliability curve: stated probability versus observed frequency for model and market"
      >
        {/* grid — recessive */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth="1" />
            <line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(1)} stroke="var(--border)" strokeWidth="1" />
            <text x={PAD.left - 8} y={y(t) + 4} textAnchor="end" fontSize="11"
              fill="var(--text-muted)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {Math.round(t * 100)}%
            </text>
            <text x={x(t)} y={H - PAD.bottom + 20} textAnchor="middle" fontSize="11"
              fill="var(--text-muted)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}

        {/* perfect-calibration diagonal */}
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)}
          stroke="var(--text-muted)" strokeWidth="1.5" strokeDasharray="6 5" opacity="0.7" />
        <text x={x(0.76)} y={y(0.76) - 10} fontSize="11" fill="var(--text-muted)"
          transform={`rotate(-38 ${x(0.76)} ${y(0.76) - 10})`}>
          perfect calibration
        </text>

        {/* series: 2px lines, 8px markers (r=4.5 + hover ring) */}
        {series.map((s) => {
          const pts = [...s.bins].sort((a, b) => a.predicted - b.predicted);
          const path = pts.map((b, i) => `${i ? "L" : "M"}${x(b.predicted)},${y(b.observed)}`).join(" ");
          return (
            <g key={s.name}>
              <path d={path} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
              {pts.map((b) => (
                <g key={b.bin}>
                  {/* generous invisible hit target over the small mark */}
                  <circle cx={x(b.predicted)} cy={y(b.observed)} r="14" fill="transparent"
                    style={{ cursor: "pointer" }}
                    onMouseEnter={(e) => show(e, s, b)}
                    onMouseMove={(e) => show(e, s, b)}
                    onMouseLeave={() => setHover(null)} />
                  <circle cx={x(b.predicted)} cy={y(b.observed)} r="4.5" fill={s.color}
                    stroke="var(--bg-card)" strokeWidth="2" pointerEvents="none" />
                </g>
              ))}
            </g>
          );
        })}

        {/* axis titles — text tokens, never series colors */}
        <text x={PAD.left + PW / 2} y={H - 6} textAnchor="middle" fontSize="12" fill="var(--text-secondary)">
          stated probability
        </text>
        <text x={14} y={PAD.top + PH / 2} textAnchor="middle" fontSize="12" fill="var(--text-secondary)"
          transform={`rotate(-90 14 ${PAD.top + PH / 2})`}>
          observed frequency
        </text>
      </svg>

      {/* legend */}
      <div className="flex gap-5 justify-center mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
        {series.map((s) => (
          <span key={s.name} className="flex items-center gap-2">
            <span className="inline-block w-3 h-3 rounded-full" style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
      </div>

      {/* tooltip */}
      {hover && (
        <div
          className="absolute z-10 pointer-events-none rounded-lg px-3 py-2 text-xs shadow-xl"
          style={{
            left: Math.min(hover.sx + 14, (wrapRef.current?.clientWidth ?? 300) - 190),
            top: hover.sy - 10,
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          <div className="flex items-center gap-2 font-semibold mb-1">
            <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: hover.color }} />
            {hover.series} · bin {hover.bin.bin}
          </div>
          <div>stated {(hover.bin.predicted * 100).toFixed(1)}% → observed {(hover.bin.observed * 100).toFixed(1)}%</div>
          <div style={{ color: "var(--text-secondary)" }}>
            gap {(hover.bin.gap * 100).toFixed(1)} pts · n={hover.bin.n}
          </div>
        </div>
      )}
    </div>
  );
}
