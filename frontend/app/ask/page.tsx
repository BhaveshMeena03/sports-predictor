"use client";
import { useState } from "react";
import Card from "../components/Card";
import { API } from "../utils/api";

/**
 * Ask the model in plain English. The backend resolves the fixture with free
 * heuristics, runs the (free) math prediction, and only then spends one small
 * LLM call writing the answer — so this stays behind the paid-endpoint rate
 * limits and daily budget.
 */

const SUGGESTIONS = [
  "First PL match — what do you think is gonna happen?",
  "Can Arsenal beat Coventry?",
  "First La Liga game prediction",
  "What happens when Spurs play?",
  "Build me a 2.5x multi, nothing too risky",
];

export default function AskPage() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask(question: string) {
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(null);
    setRes(null);
    try {
      const r = await fetch(`${API}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (r.status === 429) {
        setError("The model is at capacity right now — try again in a minute.");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRes(await r.json());
    } catch {
      setError("Something went wrong — try again.");
    } finally {
      setBusy(false);
    }
  }

  const p = res?.prediction;
  const fx = res?.fixture;

  return (
    <div className="space-y-6 animate-fade-in max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--cyan)" }}>Ask the Model</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Plain English in, the model&apos;s actual numbers out. Answers use only what the
          Elo + Poisson ensemble computes — never invented probabilities, never betting advice.
        </p>
      </div>

      <Card>
        <form
          onSubmit={(e) => { e.preventDefault(); ask(q); }}
          className="flex gap-2"
        >
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. first PL match — what happens?"
            maxLength={200}
            className="flex-1 px-4 py-3 rounded-lg text-sm outline-none"
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
            }}
            aria-label="Your question for the model"
          />
          <button
            type="submit"
            disabled={busy || !q.trim()}
            className="px-5 py-3 rounded-lg text-sm font-bold cursor-pointer transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ background: "var(--cyan)", color: "var(--bg-primary)" }}
          >
            {busy ? "Thinking…" : "Ask"}
          </button>
        </form>
        <div className="flex flex-wrap gap-2 mt-3">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => { setQ(s); ask(s); }}
              className="text-xs px-3 py-1.5 rounded-full cursor-pointer transition-colors duration-200"
              style={{
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                color: "var(--text-secondary)",
              }}
            >
              {s}
            </button>
          ))}
        </div>
      </Card>

      {error && (
        <Card>
          <p className="text-sm" style={{ color: "var(--red)" }}>{error}</p>
        </Card>
      )}

      {res && !res.resolved && (
        <Card>
          <p className="text-sm mb-3" style={{ color: "var(--text-secondary)" }}>{res.note}</p>
          <div className="flex flex-wrap gap-2">
            {res.fixtures?.map((m: any) => (
              <button
                key={`${m.date}-${m.home}`}
                onClick={() => {
                  const question = `${m.home} vs ${m.away} — what happens?`;
                  setQ(question);
                  ask(question);
                }}
                className="text-xs px-3 py-1.5 rounded-lg cursor-pointer transition-colors duration-200"
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-secondary)",
                }}
              >
                {m.home} v {m.away} <span style={{ color: "var(--text-muted)" }}>· {m.date}</span>
              </button>
            ))}
          </div>
        </Card>
      )}

      {res?.resolved && res.kind === "multi" && (
        <Card>
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
              {res.multi.legs.length}-leg multi
            </h2>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              fair {res.multi.fair_multiplier}x
              {!res.multi.reached_target && " (below target)"}
            </span>
          </div>

          {/* The number people skip — so it is the biggest thing on the card. */}
          <div className="rounded-lg p-4 my-3 text-center"
            style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
            <p className="text-[11px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
              chance all {res.multi.legs.length} legs land
            </p>
            <p className="text-3xl font-bold mt-1"
              style={{ color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
              {Math.round(res.multi.combined_probability * 100)}%
            </p>
          </div>

          <div className="space-y-2 mb-4">
            {res.multi.legs.map((l: any) => (
              <div key={l.match} className="flex items-center justify-between gap-3 text-xs"
                style={{ borderTop: "1px solid var(--border)", paddingTop: "0.5rem" }}>
                <div>
                  <span style={{ color: "var(--text-primary)" }}>{l.match}</span>
                  <span style={{ color: "var(--text-muted)" }}> · {l.league} · {l.date}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0"
                  style={{ fontVariantNumeric: "tabular-nums" }}>
                  <span style={{ color: "var(--cyan)" }}>{l.pick}</span>
                  <span style={{ color: "var(--text-secondary)" }}>
                    {Math.round(l.probability * 100)}%
                  </span>
                  <span style={{ color: "var(--text-muted)" }}>@{l.fair_odds}</span>
                </div>
              </div>
            ))}
          </div>

          <p className="text-sm leading-relaxed whitespace-pre-line"
            style={{ color: "var(--text-primary)" }}>
            {res.answer}
          </p>

          <p className="text-[11px] mt-4" style={{ color: "var(--text-muted)" }}>
            Fair odds are the model&apos;s own (1 ÷ probability), not bookmaker prices.
            Informational only — not betting advice.
          </p>
        </Card>
      )}

      {res?.resolved && res.kind !== "multi" && (
        <Card>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
              {fx.home} v {fx.away}
            </h2>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              {fx.league_label} · {fx.date}
            </span>
          </div>

          {/* probability bar */}
          <div className="mb-1 flex h-3 rounded-full overflow-hidden" role="img"
            aria-label={`Win probabilities: ${fx.home} ${Math.round(p.p_home * 100)}%, draw ${Math.round(p.p_draw * 100)}%, ${fx.away} ${Math.round(p.p_away * 100)}%`}>
            <div style={{ width: `${p.p_home * 100}%`, background: "var(--series-model)" }} />
            <div style={{ width: `${p.p_draw * 100}%`, background: "var(--text-muted)", opacity: 0.55 }} />
            <div style={{ width: `${p.p_away * 100}%`, background: "var(--series-market)" }} />
          </div>
          <div className="flex justify-between text-xs mb-4" style={{ color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>
            <span>{fx.home} {Math.round(p.p_home * 100)}%</span>
            <span>draw {Math.round(p.p_draw * 100)}%</span>
            <span>{fx.away} {Math.round(p.p_away * 100)}%</span>
          </div>

          <p className="text-sm leading-relaxed whitespace-pre-line mb-4" style={{ color: "var(--text-primary)" }}>
            {res.answer}
          </p>

          <div className="flex flex-wrap gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
            <Chip label={`expected goals ${p.xg_home} – ${p.xg_away}`} />
            <Chip label={`over 2.5 goals ${Math.round((p.totals_2_5?.over ?? 0) * 100)}%`} />
            <Chip label={`both score ${Math.round((p.btts?.yes ?? 0) * 100)}%`} />
            {p.top_scores?.[0] && <Chip label={`most likely ${p.top_scores[0].score}`} />}
          </div>

          <p className="text-[11px] mt-4" style={{ color: "var(--text-muted)" }}>
            Probabilities from the same ensemble scored on the public track record. Informational
            only — not betting advice.
          </p>
        </Card>
      )}
    </div>
  );
}

function Chip({ label }: { label: string }) {
  return (
    <span
      className="px-3 py-1.5 rounded-full"
      style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", fontVariantNumeric: "tabular-nums" }}
    >
      {label}
    </span>
  );
}
