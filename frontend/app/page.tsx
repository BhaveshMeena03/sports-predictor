"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Card from "./components/Card";
import ReliabilityChart, { Series } from "./components/charts/ReliabilityChart";
import BrierDumbbell, { LeagueBrier } from "./components/charts/BrierDumbbell";
import LoadError from "./components/LoadError";
import { API } from "./utils/api";

/**
 * Public landing: the evidence, not the tools.
 *
 * Two kinds of proof, clearly labelled and never blurred:
 *  - the WORLD CUP log (wc_match_log): walk-forward. Each probability came
 *    from ratings that had not seen that match, and ratings only update once
 *    the row is scored. Generated at ingest though, not before kickoff — the
 *    API reports that per row as prelogged=0, and so does this page;
 *  - the CLUB log, from 2026-27: written before the match is played and
 *    filled in afterwards, so it cannot be revised in hindsight;
 *  - the WALK-FORWARD BACKTEST behind the reliability curves, labelled as such.
 */

const LEAGUES = [
  { key: "premier_league", label: "Premier League" },
  { key: "la_liga", label: "La Liga" },
  { key: "serie_a", label: "Serie A" },
  { key: "bundesliga", label: "Bundesliga" },
  { key: "ligue_1", label: "Ligue 1" },
];

type Report = any;
type Track = any;

export default function Landing() {
  const [reports, setReports] = useState<Record<string, Report>>({});
  const [track, setTrack] = useState<Track | null>(null);
  const [league, setLeague] = useState("premier_league");
  const [showAllWc, setShowAllWc] = useState(false);
  const [failed, setFailed] = useState(false);

  // Named so the Retry button can re-run it. Failures set `failed` rather than
  // being swallowed: a silent catch renders identically to "still loading",
  // which is how a broken deploy once looked like an empty page for hours.
  const load = useCallback(() => {
    setFailed(false);
    fetch(`${API}/trackrecord`)
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then(setTrack)
      .catch(() => setFailed(true));

    LEAGUES.forEach(({ key }) =>
      fetch(`${API}/calibration/reliability?league=${key}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => d && setReports((prev) => ({ ...prev, [key]: d })))
        .catch(() => setFailed(true)),
    );
  }, []);

  useEffect(() => { load(); }, [load]);

  const report = reports[league];
  const wc = track?.world_cup;
  const clubs = track?.clubs;

  const curves: Series[] = useMemo(() => {
    if (!report) return [];
    const out: Series[] = [
      { name: "model", color: "var(--series-model)", bins: report.model.reliability_curve },
    ];
    if (report.market)
      out.push({
        name: "closing line",
        color: "var(--series-market)",
        bins: report.market.reliability_curve,
      });
    return out;
  }, [report]);

  const dumbbell: LeagueBrier[] = useMemo(
    () =>
      LEAGUES.filter(({ key }) => reports[key]?.vs_market).map(({ key, label }) => ({
        league: label,
        model: reports[key].vs_market.model_brier,
        market: reports[key].vs_market.market_brier,
      })),
    [reports],
  );

  const wcRows = showAllWc ? wc?.matches : wc?.matches?.slice(0, 8);

  return (
    <div className="space-y-8 animate-fade-in max-w-5xl">
      {/* ── Hero ─────────────────────────────────────────── */}
      <section className="pt-2">
        <p className="text-xs tracking-widest uppercase mb-3" style={{ color: "var(--text-muted)" }}>
          Live since World Cup 2026 · self-updating daily
        </p>
        <h1 className="text-3xl md:text-4xl font-bold leading-tight" style={{ color: "var(--text-primary)" }}>
          Calibrated football probabilities,
          <br />
          with the receipts published.
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          An ensemble of Elo ratings and a Dixon-Coles goals model, scored on every
          prediction it makes. Its prediction error sits within a fraction of a percent of the bookmakers&apos; closing line — and the same backtests prove it doesn&apos;t beat it.
          That honesty is the product: well-calibrated probabilities you can verify,
          not picks that promise profit.
        </p>

        {failed && (
          <div className="mt-6">
            <LoadError
              message="Couldn't reach the prediction API — the numbers below may be missing."
              onRetry={load}
            />
          </div>
        )}

        {/* KPI row — live log numbers */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-8">
          {/* "pre-kickoff" was wrong here: the World Cup rows were predicted when
              the result was ingested, not before the match. Still walk-forward --
              the model had not seen that match and ratings update only after
              scoring -- but claiming a forward call the log cannot support is
              worse than stating what it is. The API reports prelogged per row. */}
          <Kpi
            label="World Cup 2026, live"
            value={wc?.summary?.n != null ? String(wc.summary.n) : "—"}
            sub={wc?.summary?.prelogged ? "matches predicted pre-kickoff"
                                        : "matches scored walk-forward"}
          />
          <Kpi
            label="Winner picked"
            value={wc?.summary?.n ? `${Math.round((wc.summary.picked_correct / wc.summary.n) * 100)}%` : "—"}
            sub={wc?.summary?.n ? `${wc.summary.picked_correct} of ${wc.summary.n}, incl. the final` : ""}
          />
          <Kpi label="Prediction error, live" value={wc?.summary?.avg_brier != null ? wc.summary.avg_brier.toFixed(4) : "—"} sub="Brier score: 0 = perfect, 0.2222 = pure guessing" />
          {/* Standing predictions count before a result lands -- they are the
              part that cannot be revised afterwards. */}
          <Kpi
            label="Club season log"
            value={clubs && clubs.summary.n > 0 ? String(clubs.summary.n)
                    : clubs?.pending ? `${clubs.pending} pending` : "starts Aug"}
            sub={clubs?.pending && !clubs.summary.n
                  ? "predictions logged, awaiting kickoff"
                  : "5 leagues, predicted before kickoff"}
          />
        </div>
      </section>

      {/* ── Reliability ──────────────────────────────────── */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 mb-1">
          <div>
            <h2 className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
              Does a stated 60% happen 60% of the time?
            </h2>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
              Reliability curve, 2025-26 walk-forward backtest ({report?.matches ?? "…"} matches) —
              each match predicted blind from ratings that exclude it.
            </p>
          </div>
          <div className="flex flex-wrap gap-1">
            {LEAGUES.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setLeague(key)}
                className="px-3 py-1.5 rounded-md text-xs cursor-pointer transition-colors duration-200"
                style={{
                  background: league === key ? "var(--cyan-glow)" : "var(--bg-secondary)",
                  color: league === key ? "var(--cyan)" : "var(--text-secondary)",
                  border: `1px solid ${league === key ? "var(--cyan-dim)" : "var(--border)"}`,
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {curves.length ? (
          <ReliabilityChart series={curves} />
        ) : (
          <div className="h-64 flex items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>
            {failed ? "unavailable" : "loading curves…"}
          </div>
        )}
        {report && (
          <p className="text-xs mt-2" style={{ color: "var(--text-muted)" }}>
            Reliability {report.model.decomposition?.reliability?.toFixed(5) ?? "—"} (distance from
            perfect calibration) · resolution {report.model.decomposition?.resolution?.toFixed(5) ?? "—"} ·
            n={report.model.decomposition?.n_points ?? 0} points.
          </p>
        )}
      </Card>

      {/* ── vs market ────────────────────────────────────── */}
      <Card>
        <h2 className="text-lg font-semibold mb-1" style={{ color: "var(--text-primary)" }}>
          Against the hardest benchmark there is
        </h2>
        <p className="text-xs mb-5" style={{ color: "var(--text-muted)" }}>
          Prediction error (Brier score — how far stated probabilities land from what actually happened; lower is better) vs the bookmakers&apos; closing line on the same matches, 2025-26. The market stays slightly sharper, and this chart shows by exactly how much.
        </p>
        {dumbbell.length ? (
          <BrierDumbbell rows={dumbbell} />
        ) : (
          <div className="h-24 flex items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>
            {failed ? "unavailable" : "loading…"}
          </div>
        )}
      </Card>

      {/* ── Live track record ────────────────────────────── */}
      <Card>
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
            The prediction log — every match scored, misses included
          </h2>
          <span
            className="text-[10px] uppercase tracking-wider px-2 py-1 rounded"
            style={{ background: "var(--cyan-glow)", color: "var(--cyan)" }}
          >
            live record
          </span>
        </div>
        <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
          World Cup 2026, complete. Every probability came from ratings that had not seen that
          match, and those ratings only moved once the row was scored — nothing here is fitted to
          its own result. The rows were written when the result was ingested rather than before
          kickoff, which the log now records honestly. Club fixtures from 2026-27 are predicted in
          advance and filled in after, so those cannot be revised at all.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs" style={{ fontVariantNumeric: "tabular-nums" }}>
            <thead>
              <tr style={{ color: "var(--text-muted)" }} className="text-left">
                <th className="py-2 pr-3 font-medium">date</th>
                <th className="py-2 pr-3 font-medium">match</th>
                <th className="py-2 pr-3 font-medium">result</th>
                <th className="py-2 pr-3 font-medium">p(H/D/A) stated</th>
                <th className="py-2 pr-3 font-medium">picked</th>
                <th className="py-2 pr-0 font-medium text-right">error (brier)</th>
              </tr>
            </thead>
            <tbody style={{ color: "var(--text-secondary)" }}>
              {(wcRows ?? []).map((m: any) => (
                <tr key={`${m.date}-${m.home}-${m.away}`} style={{ borderTop: "1px solid var(--border)" }}>
                  <td className="py-2 pr-3 whitespace-nowrap" style={{ color: "var(--text-muted)" }}>{m.date}</td>
                  <td className="py-2 pr-3">
                    {m.home} <span style={{ color: "var(--text-muted)" }}>v</span> {m.away}
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap">{m.home_goals}–{m.away_goals}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    {Math.round(m.p_home * 100)} / {Math.round(m.p_draw * 100)} / {Math.round(m.p_away * 100)}
                  </td>
                  <td className="py-2 pr-3">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className="w-1.5 h-1.5 rounded-full inline-block"
                        style={{ background: m.correct ? "var(--green)" : "var(--red)" }}
                      />
                      {m.picked}
                    </span>
                  </td>
                  <td className="py-2 pr-0 text-right">{m.brier?.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {wc?.matches?.length > 8 && (
          <button
            onClick={() => setShowAllWc(!showAllWc)}
            className="mt-3 text-xs cursor-pointer transition-colors duration-200 hover:underline"
            style={{ color: "var(--cyan)" }}
          >
            {showAllWc ? "show fewer" : `show all ${wc.matches.length} matches`}
          </button>
        )}
      </Card>

      {/* ── Honesty strip ────────────────────────────────── */}
      <Card>
        <h2 className="text-sm font-semibold mb-2" style={{ color: "var(--text-primary)" }}>
          What this is not
        </h2>
        <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          Not a tipster and not a money-printer. The backtests on this page show the closing line is
          sharper than the model — that is exactly why they are published. Informational only;
          nothing here is betting advice.
        </p>
      </Card>

      {/* ── Tools ────────────────────────────────────────── */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4 pb-4">
        <ToolLink href="/ask" title="Ask the model" desc="Type a question in plain English — get the model's numbers and its read" />
        <ToolLink href="/log" title="My bet log" desc="Track your own bets and real ROI — saved in your browser, never uploaded" />
        <ToolLink href="/fixtures" title="Fixtures & probabilities" desc="Upcoming matches with model probabilities and derived markets" />
        <ToolLink href="/analyze" title="Match analyzer" desc="Full ensemble + AI write-up for any fixture" />
      </section>
    </div>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}>
      <p className="text-[11px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p
        className="text-2xl font-bold mt-1.5"
        style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}
      >
        {value}
      </p>
      <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>{sub}</p>
    </div>
  );
}

function ToolLink({ href, title, desc }: { href: string; title: string; desc: string }) {
  return (
    <Link
      href={href}
      className="rounded-xl p-4 block cursor-pointer transition-colors duration-200"
      style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-card-hover)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "var(--bg-card)")}
    >
      <h3 className="text-sm font-semibold" style={{ color: "var(--cyan)" }}>{title}</h3>
      <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{desc}</p>
    </Link>
  );
}
