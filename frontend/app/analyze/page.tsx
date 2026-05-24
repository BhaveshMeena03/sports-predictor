"use client";
import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Card from "../components/Card";
import ConfidenceBadge from "../components/ConfidenceBadge";

const API = "http://localhost:8000/api";

const SPORTS = ["football", "nba", "nhl", "cricket"];
const LEAGUES: Record<string, string[]> = {
  football: ["premier_league", "la_liga", "bundesliga", "serie_a", "ligue_1", "champions_league", "mls", "fa_cup"],
  nba: ["nba"],
  nhl: ["nhl"],
  cricket: ["ipl"],
};

export default function AnalyzePage() {
  return (
    <Suspense fallback={<div style={{ color: "var(--text-muted)" }}>Loading...</div>}>
      <AnalyzeContent />
    </Suspense>
  );
}

function AnalyzeContent() {
  const searchParams = useSearchParams();
  const [sport, setSport] = useState("football");
  const [league, setLeague] = useState("premier_league");
  const [homeTeam, setHomeTeam] = useState("");
  const [awayTeam, setAwayTeam] = useState("");
  const [context, setContext] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [autoTriggered, setAutoTriggered] = useState(false);

  // Read URL params and auto-analyze
  useEffect(() => {
    const home = searchParams.get("home");
    const away = searchParams.get("away");
    const sportParam = searchParams.get("sport");
    const leagueParam = searchParams.get("league");
    const oddsParam = searchParams.get("odds");

    if (home && away) {
      setHomeTeam(home);
      setAwayTeam(away);

      // Map sport key to category
      const sportMap: Record<string, string> = {
        premier_league: "football", la_liga: "football", bundesliga: "football",
        serie_a: "football", ligue_1: "football", champions_league: "football",
        mls: "football", fa_cup: "football",
        nba: "nba", nhl: "nhl", ipl: "cricket",
      };

      if (sportParam) {
        const mappedSport = sportMap[sportParam] || sportParam;
        setSport(mappedSport);
        if (sportMap[sportParam]) setLeague(sportParam);
        else if (leagueParam) setLeague(leagueParam);
      }

      if (oddsParam) {
        setContext(`Odds: ${oddsParam}`);
      }

      setAutoTriggered(true);
    }
  }, [searchParams]);

  // Auto-analyze when params are loaded
  useEffect(() => {
    if (autoTriggered && homeTeam && awayTeam && !result && !loading) {
      handleAnalyze();
      setAutoTriggered(false);
    }
  }, [autoTriggered, homeTeam, awayTeam]);

  async function handleAnalyze() {
    if (!homeTeam || !awayTeam) return;
    setLoading(true);
    setResult(null);

    try {
      const resp = await fetch(`${API}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sport,
          league,
          home_team: homeTeam,
          away_team: awayTeam,
          extra_context: context,
        }),
      });
      const data = await resp.json();
      setResult(data.analysis);
    } catch (err) {
      setResult({ error: "Failed to analyze. Is the backend running?" });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6 animate-fade-in max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--cyan)" }}>Analyze Match</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>Get AI-powered prediction for any match</p>
      </div>

      <Card>
        <div className="space-y-4">
          {/* Sport & League */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs uppercase tracking-wider block mb-2" style={{ color: "var(--text-muted)" }}>Sport</label>
              <select
                value={sport}
                onChange={e => { setSport(e.target.value); setLeague(LEAGUES[e.target.value]?.[0] || ""); }}
                className="w-full p-3 rounded-lg text-sm outline-none"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                {SPORTS.map(s => <option key={s} value={s}>{s.toUpperCase()}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider block mb-2" style={{ color: "var(--text-muted)" }}>League</label>
              <select
                value={league}
                onChange={e => setLeague(e.target.value)}
                className="w-full p-3 rounded-lg text-sm outline-none"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                {(LEAGUES[sport] || []).map(l => <option key={l} value={l}>{l.replace(/_/g, " ").toUpperCase()}</option>)}
              </select>
            </div>
          </div>

          {/* Teams */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs uppercase tracking-wider block mb-2" style={{ color: "var(--text-muted)" }}>Home Team</label>
              <input
                value={homeTeam}
                onChange={e => setHomeTeam(e.target.value)}
                placeholder="e.g. Arsenal"
                className="w-full p-3 rounded-lg text-sm outline-none"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider block mb-2" style={{ color: "var(--text-muted)" }}>Away Team</label>
              <input
                value={awayTeam}
                onChange={e => setAwayTeam(e.target.value)}
                placeholder="e.g. Bournemouth"
                className="w-full p-3 rounded-lg text-sm outline-none"
                style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
          </div>

          {/* Extra Context */}
          <div>
            <label className="text-xs uppercase tracking-wider block mb-2" style={{ color: "var(--text-muted)" }}>
              Extra Context (injuries, form, etc.)
            </label>
            <textarea
              value={context}
              onChange={e => setContext(e.target.value)}
              placeholder="e.g. Saka doubtful with knee inflammation. Arsenal won last 4 PL games. Bournemouth drawn 5 in a row."
              rows={3}
              className="w-full p-3 rounded-lg text-sm outline-none resize-none"
              style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          </div>

          <button
            onClick={handleAnalyze}
            disabled={loading || !homeTeam || !awayTeam}
            className="w-full py-3 rounded-lg text-sm font-bold uppercase tracking-wider transition-all duration-200 disabled:opacity-40"
            style={{
              background: loading ? "var(--border)" : "var(--cyan)",
              color: "var(--bg-primary)",
            }}
          >
            {loading ? "Analyzing..." : "Analyze Match"}
          </button>
        </div>
      </Card>

      {/* Results */}
      {result && !result.error && (
        <Card glow>
          <div className="space-y-5">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-lg font-bold" style={{ color: "var(--text-primary)" }}>{result.match}</h2>
                <p className="text-sm mt-1" style={{ color: "var(--cyan)" }}>
                  Recommendation: <span className="font-bold">{result.recommendation}</span>
                </p>
              </div>
              <ConfidenceBadge value={result.confidence || 0} />
            </div>

            {/* Predicted Score */}
            {result.predicted_score && (
              <div className="text-center py-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>Predicted Score</p>
                <p className="text-2xl font-bold mt-1" style={{ color: "var(--cyan)" }}>{result.predicted_score}</p>
              </div>
            )}

            {/* Key Factors */}
            {result.key_factors?.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--green)" }}>Key Factors</h3>
                <ul className="space-y-1">
                  {result.key_factors.map((f: string, i: number) => (
                    <li key={i} className="flex items-start gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                      <span style={{ color: "var(--green)" }}>+</span> {f}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Red Flags */}
            {result.red_flags?.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--red)" }}>Red Flags</h3>
                <ul className="space-y-1">
                  {result.red_flags.map((f: string, i: number) => (
                    <li key={i} className="flex items-start gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                      <span style={{ color: "var(--red)" }}>!</span> {f}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Multi Safety */}
            <div className="flex items-center gap-2 pt-3" style={{ borderTop: "1px solid var(--border)" }}>
              <span className="text-sm" style={{ color: "var(--text-muted)" }}>Safe for Multi:</span>
              <span
                className="text-sm font-bold px-2 py-1 rounded"
                style={{
                  background: result.safe_for_multi ? "rgba(0,230,118,0.15)" : "rgba(255,23,68,0.15)",
                  color: result.safe_for_multi ? "var(--green)" : "var(--red)",
                }}
              >
                {result.safe_for_multi ? "YES" : "NO"}
              </span>
              <span className="text-sm ml-2" style={{ color: "var(--text-muted)" }}>
                Risk: <span style={{ color: result.risk_level === "low" ? "var(--green)" : result.risk_level === "medium" ? "var(--yellow)" : "var(--red)" }}>{result.risk_level?.toUpperCase()}</span>
              </span>
            </div>

            {/* Reasoning */}
            {result.reasoning && (
              <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{result.reasoning}</p>
            )}
          </div>
        </Card>
      )}

      {result?.error && (
        <Card>
          <p className="text-sm" style={{ color: "var(--red)" }}>{result.error}</p>
        </Card>
      )}
    </div>
  );
}
