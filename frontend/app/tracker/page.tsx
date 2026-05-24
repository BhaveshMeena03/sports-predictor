"use client";
import { useState, useEffect } from "react";
import Card from "../components/Card";

const API = "http://localhost:8000/api";

export default function TrackerPage() {
  const [bets, setBets] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [form, setForm] = useState({ match: "", bet_type: "1x2", pick: "", odds: 0, stake: 0 });

  function loadData() {
    fetch(`${API}/bets`).then(r => r.json()).then(d => setBets(d.bets || [])).catch(() => {});
    fetch(`${API}/bets/summary`).then(r => r.json()).then(setStats).catch(() => {});
  }

  useEffect(loadData, []);

  async function addBet() {
    if (!form.match || !form.pick || !form.odds || !form.stake) return;
    await fetch(`${API}/bets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    setForm({ match: "", bet_type: "1x2", pick: "", odds: 0, stake: 0 });
    loadData();
  }

  async function settleBet(id: number, result: string) {
    await fetch(`${API}/bets/${id}/settle?result=${result}`, { method: "PUT" });
    loadData();
  }

  async function clearHistory() {
    if (!confirm("Are you sure you want to clear all bet history?")) return;
    await fetch(`${API}/bets`, { method: "DELETE" });
    loadData();
  }

  return (
    <div className="space-y-6 animate-fade-in max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--cyan)" }}>Bet Tracker</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>Track your wins, losses and ROI</p>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[
            { label: "Total", value: stats.total_bets, color: "var(--text-primary)" },
            { label: "Won", value: stats.won, color: "var(--green)" },
            { label: "Lost", value: stats.lost, color: "var(--red)" },
            { label: "Win Rate", value: `${stats.win_rate}%`, color: "var(--cyan)" },
            { label: "Profit", value: `$${stats.total_profit}`, color: stats.total_profit >= 0 ? "var(--green)" : "var(--red)" },
          ].map((s, i) => (
            <div key={i} className="p-3 rounded-lg text-center" style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>{s.label}</p>
              <p className="text-lg font-bold mt-1" style={{ color: s.color }}>{s.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Add Bet Form */}
      <Card>
        <h2 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>Record a Bet</h2>
        <div className="grid grid-cols-5 gap-3">
          <input value={form.match} onChange={e => setForm({ ...form, match: e.target.value })} placeholder="Match" className="col-span-2 p-2.5 rounded-lg text-sm outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <input value={form.pick} onChange={e => setForm({ ...form, pick: e.target.value })} placeholder="Pick" className="p-2.5 rounded-lg text-sm outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
          <input type="number" step="0.01" value={form.odds || ""} onChange={e => setForm({ ...form, odds: parseFloat(e.target.value) || 0 })} placeholder="Odds" className="p-2.5 rounded-lg text-sm outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--cyan)" }} />
          <input type="number" value={form.stake || ""} onChange={e => setForm({ ...form, stake: parseFloat(e.target.value) || 0 })} placeholder="Stake" className="p-2.5 rounded-lg text-sm outline-none" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
        </div>
        <button onClick={addBet} className="mt-3 px-6 py-2 rounded-lg text-sm font-bold" style={{ background: "var(--cyan)", color: "var(--bg-primary)" }}>
          Add Bet
        </button>
      </Card>

      {/* Bet History */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>History</h2>
          {bets.length > 0 && (
            <button
              onClick={clearHistory}
              className="text-xs px-3 py-1.5 rounded font-medium transition-all duration-200 hover:opacity-80"
              style={{ background: "rgba(255,23,68,0.15)", color: "var(--red)", border: "1px solid rgba(255,23,68,0.3)" }}
            >
              Clear All
            </button>
          )}
        </div>
        {bets.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>No bets yet</p>
        ) : (
          <div className="space-y-2">
            {bets.slice().reverse().map((bet: any) => (
              <div key={bet.id} className="flex items-center justify-between p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
                <div className="flex-1">
                  <p className="text-sm font-medium">{bet.match}</p>
                  <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                    {bet.pick} @ {bet.odds} | Stake: ${bet.stake} | Payout: ${bet.potential_payout}
                  </p>
                </div>
                {bet.result === "pending" ? (
                  <div className="flex gap-2">
                    <button onClick={() => settleBet(bet.id, "won")} className="text-xs px-3 py-1 rounded font-bold" style={{ background: "rgba(0,230,118,0.15)", color: "var(--green)" }}>Won</button>
                    <button onClick={() => settleBet(bet.id, "lost")} className="text-xs px-3 py-1 rounded font-bold" style={{ background: "rgba(255,23,68,0.15)", color: "var(--red)" }}>Lost</button>
                  </div>
                ) : (
                  <div className="text-right">
                    <span className="text-xs font-bold px-2 py-1 rounded" style={{
                      background: bet.result === "won" ? "rgba(0,230,118,0.15)" : "rgba(255,23,68,0.15)",
                      color: bet.result === "won" ? "var(--green)" : "var(--red)",
                    }}>
                      {bet.result.toUpperCase()}
                    </span>
                    <p className="text-xs mt-1" style={{ color: bet.profit_loss >= 0 ? "var(--green)" : "var(--red)" }}>
                      {bet.profit_loss >= 0 ? "+" : ""}{bet.profit_loss}
                    </p>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
