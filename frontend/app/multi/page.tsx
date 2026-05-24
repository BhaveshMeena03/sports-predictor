"use client";
import { useState, useEffect } from "react";
import Card from "../components/Card";

const API = "http://localhost:8000/api";

interface Leg {
  match: string;
  pick: string;
  odds: number;
  sport: string;
}

export default function MultiPage() {
  const [legs, setLegs] = useState<Leg[]>([{ match: "", pick: "", odds: 0, sport: "football" }]);
  const [stake, setStake] = useState(100);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  // Load legs from fixtures page if available
  useEffect(() => {
    const saved = localStorage.getItem("multiLegs");
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          setLegs(parsed);
          localStorage.removeItem("multiLegs");
        }
      } catch {}
    }
  }, []);

  function addLeg() {
    setLegs([...legs, { match: "", pick: "", odds: 0, sport: "football" }]);
  }

  function removeLeg(i: number) {
    setLegs(legs.filter((_, idx) => idx !== i));
  }

  function updateLeg(i: number, field: keyof Leg, value: any) {
    const updated = [...legs];
    (updated[i] as any)[field] = field === "odds" ? parseFloat(value) || 0 : value;
    setLegs(updated);
  }

  const totalOdds = legs.reduce((acc, l) => acc * (l.odds || 1), 1);
  const payout = stake * totalOdds;

  async function handleAnalyze() {
    setLoading(true);
    setResult(null);
    try {
      const resp = await fetch(`${API}/analyze-multi`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ legs, stake }),
      });
      const data = await resp.json();
      setResult(data);
    } catch {
      setResult({ error: "Failed to analyze" });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6 animate-fade-in max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--cyan)" }}>Multi-Bet Builder</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>Build and analyze your multi-bet slip</p>
      </div>

      <Card>
        <div className="space-y-4">
          {legs.map((leg, i) => (
            <div key={i} className="p-4 rounded-lg space-y-3" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)" }}>
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold uppercase" style={{ color: "var(--cyan)" }}>Leg {i + 1}</span>
                {legs.length > 1 && (
                  <button onClick={() => removeLeg(i)} className="text-xs px-2 py-1 rounded" style={{ color: "var(--red)" }}>
                    Remove
                  </button>
                )}
              </div>
              <div className="grid grid-cols-4 gap-3">
                <input
                  value={leg.match}
                  onChange={e => updateLeg(i, "match", e.target.value)}
                  placeholder="Match (e.g. Arsenal vs Bournemouth)"
                  className="col-span-2 p-2.5 rounded-lg text-sm outline-none"
                  style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                />
                <input
                  value={leg.pick}
                  onChange={e => updateLeg(i, "pick", e.target.value)}
                  placeholder="Pick (e.g. Arsenal Win)"
                  className="p-2.5 rounded-lg text-sm outline-none"
                  style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                />
                <input
                  type="number"
                  step="0.01"
                  value={leg.odds || ""}
                  onChange={e => updateLeg(i, "odds", e.target.value)}
                  placeholder="Odds"
                  className="p-2.5 rounded-lg text-sm outline-none"
                  style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--cyan)" }}
                />
              </div>
            </div>
          ))}

          <button
            onClick={addLeg}
            className="w-full py-2.5 rounded-lg text-sm font-medium transition-colors"
            style={{ border: "1px dashed var(--border)", color: "var(--text-muted)" }}
          >
            + Add Leg
          </button>

          {/* Stake & Summary */}
          <div className="flex items-center gap-4 p-4 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
            <div className="flex-1">
              <label className="text-xs block mb-1" style={{ color: "var(--text-muted)" }}>Stake</label>
              <input
                type="number"
                value={stake}
                onChange={e => setStake(parseFloat(e.target.value) || 0)}
                className="w-full p-2.5 rounded-lg text-sm outline-none"
                style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
            </div>
            <div className="text-center">
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>Total Odds</p>
              <p className="text-xl font-bold" style={{ color: "var(--cyan)" }}>{totalOdds.toFixed(2)}x</p>
            </div>
            <div className="text-center">
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>Payout</p>
              <p className="text-xl font-bold" style={{ color: "var(--green)" }}>${payout.toFixed(2)}</p>
            </div>
          </div>

          <button
            onClick={handleAnalyze}
            disabled={loading || legs.some(l => !l.match || !l.pick || !l.odds)}
            className="w-full py-3 rounded-lg text-sm font-bold uppercase tracking-wider transition-all duration-200 disabled:opacity-40"
            style={{ background: loading ? "var(--border)" : "var(--cyan)", color: "var(--bg-primary)" }}
          >
            {loading ? "Analyzing..." : "Analyze Multi-Bet"}
          </button>
        </div>
      </Card>

      {/* Results */}
      {result?.analysis && !result.error && (
        <Card glow>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold" style={{ color: "var(--text-primary)" }}>AI Analysis</h2>
              <span
                className="text-sm font-bold px-3 py-1.5 rounded-lg"
                style={{
                  background: result.analysis.overall_recommendation === "PLACE" ? "rgba(0,230,118,0.15)" : result.analysis.overall_recommendation === "RISKY" ? "rgba(255,234,0,0.15)" : "rgba(255,23,68,0.15)",
                  color: result.analysis.overall_recommendation === "PLACE" ? "var(--green)" : result.analysis.overall_recommendation === "RISKY" ? "var(--yellow)" : "var(--red)",
                }}
              >
                {result.analysis.overall_recommendation}
              </span>
            </div>

            {/* Combined Stats */}
            <div className="grid grid-cols-3 gap-4">
              <div className="text-center p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>Combined Probability</p>
                <p className="text-xl font-bold mt-1" style={{ color: "var(--cyan)" }}>{result.analysis.combined_probability?.toFixed(1)}%</p>
              </div>
              <div className="text-center p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>Weakest Link</p>
                <p className="text-sm font-bold mt-1" style={{ color: "var(--red)" }}>{result.analysis.weakest_link}</p>
              </div>
              <div className="text-center p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>Expected Value</p>
                <p className="text-xl font-bold mt-1" style={{ color: (result.analysis.expected_value ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                  {result.analysis.expected_value?.toFixed(2)}
                </p>
              </div>
            </div>

            {/* Per-Leg Analysis */}
            {result.analysis.legs_analysis?.map((leg: any, i: number) => (
              <div key={i} className="flex items-center justify-between p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
                <div className="flex-1">
                  <p className="text-sm font-medium">{leg.match}</p>
                  <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>{leg.pick} @ {leg.odds}</p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-bold" style={{ color: leg.confidence >= 75 ? "var(--green)" : leg.confidence >= 60 ? "var(--yellow)" : "var(--red)" }}>
                    {leg.confidence}%
                  </span>
                  <span className="text-xs px-2 py-1 rounded" style={{
                    background: leg.risk_level === "LOW" ? "rgba(0,230,118,0.15)" : leg.risk_level === "MEDIUM" ? "rgba(255,234,0,0.15)" : "rgba(255,23,68,0.15)",
                    color: leg.risk_level === "LOW" ? "var(--green)" : leg.risk_level === "MEDIUM" ? "var(--yellow)" : "var(--red)",
                  }}>
                    {leg.risk_level}
                  </span>
                </div>
              </div>
            ))}

            {/* Suggestions */}
            {result.analysis.suggestions?.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--cyan)" }}>Suggestions</h3>
                <ul className="space-y-1">
                  {result.analysis.suggestions.map((s: string, i: number) => (
                    <li key={i} className="flex items-start gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                      <span style={{ color: "var(--cyan)" }}>→</span> {s}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
