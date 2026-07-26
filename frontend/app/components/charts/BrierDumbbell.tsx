"use client";

/**
 * Model vs closing line per league, as a dumbbell plot: two dots on a shared
 * Brier axis (lower = better), connected. The visible story is the SIZE of the
 * gap — the honest claim is "close to the market", so the chart is built to
 * show exactly how close, not to hide it.
 */

export type LeagueBrier = {
  league: string;
  model: number;
  market: number;
};

const MODEL = "var(--series-model)";
const MARKET = "var(--series-market)";

export default function BrierDumbbell({ rows }: { rows: LeagueBrier[] }) {
  if (!rows.length) return null;
  const values = rows.flatMap((r) => [r.model, r.market]);
  const min = Math.min(...values) - 0.003;
  const max = Math.max(...values) + 0.003;
  const px = (v: number) => ((v - min) / (max - min)) * 100;

  return (
    <div className="space-y-3">
      {rows.map((r) => (
        <div key={r.league} className="grid grid-cols-[110px_1fr_84px] items-center gap-3">
          <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{r.league}</span>
          <div className="relative h-6">
            {/* track */}
            <div className="absolute top-1/2 -translate-y-1/2 h-px w-full" style={{ background: "var(--border)" }} />
            {/* connector */}
            <div
              className="absolute top-1/2 -translate-y-1/2 h-0.5 rounded"
              style={{
                left: `${Math.min(px(r.model), px(r.market))}%`,
                width: `${Math.abs(px(r.model) - px(r.market))}%`,
                background: "var(--text-muted)",
                opacity: 0.5,
              }}
            />
            {[{ v: r.market, c: MARKET, label: "closing line" },
              { v: r.model, c: MODEL, label: "model" }].map((d) => (
              <div
                key={d.label}
                title={`${r.league} ${d.label}: ${d.v.toFixed(4)}`}
                className="absolute top-1/2 w-3 h-3 rounded-full -translate-y-1/2 -translate-x-1/2"
                style={{ left: `${px(d.v)}%`, background: d.c, border: "2px solid var(--bg-card)" }}
              />
            ))}
          </div>
          <span className="text-xs text-right" style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
            gap {(r.model - r.market).toFixed(4)}
          </span>
        </div>
      ))}
      <div className="flex gap-5 pt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
        <span className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full" style={{ background: MODEL }} /> model error
        </span>
        <span className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full" style={{ background: MARKET }} /> bookmakers&apos; closing line
        </span>
        <span className="ml-auto" style={{ color: "var(--text-muted)" }}>error = Brier score · lower is better</span>
      </div>
    </div>
  );
}
