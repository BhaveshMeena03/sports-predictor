"use client";
import { useState, useEffect } from "react";
import Card from "./components/Card";

const API = "http://localhost:8000/api";

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null);
  const [recentBets, setRecentBets] = useState<any[]>([]);

  useEffect(() => {
    fetch(`${API}/bets/summary`).then(r => r.json()).then(setStats).catch(() => {});
    fetch(`${API}/bets`).then(r => r.json()).then(d => setRecentBets(d.bets?.slice(-5) || [])).catch(() => {});
  }, []);

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--cyan)" }}>Dashboard</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>Your betting overview</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Total Bets" value={stats?.total_bets ?? 0} icon="🎲" />
        <StatCard label="Win Rate" value={`${stats?.win_rate ?? 0}%`} icon="🏆" color="var(--green)" />
        <StatCard label="Profit/Loss" value={`$${stats?.total_profit ?? 0}`} icon="💵" color={(stats?.total_profit ?? 0) >= 0 ? "var(--green)" : "var(--red)"} />
        <StatCard label="ROI" value={`${stats?.roi ?? 0}%`} icon="📈" color={(stats?.roi ?? 0) >= 0 ? "var(--cyan)" : "var(--red)"} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <QuickAction href="/analyze" icon="🔍" title="Analyze Match" desc="AI-powered match analysis" />
        <QuickAction href="/multi" icon="🎯" title="Build Multi" desc="Create & analyze multi-bets" />
        <QuickAction href="/fixtures" icon="📅" title="Today's Fixtures" desc="Browse upcoming matches" />
      </div>

      <Card>
        <h2 className="text-lg font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Recent Bets</h2>
        {recentBets.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>No bets recorded yet. Start by analyzing a match!</p>
        ) : (
          <div className="space-y-2">
            {recentBets.map((bet: any, i: number) => (
              <div key={i} className="flex items-center justify-between p-3 rounded-lg" style={{ background: "var(--bg-secondary)" }}>
                <div>
                  <p className="text-sm font-medium">{bet.match}</p>
                  <p className="text-xs" style={{ color: "var(--text-muted)" }}>{bet.pick} @ {bet.odds}</p>
                </div>
                <span
                  className="text-xs font-bold px-2 py-1 rounded"
                  style={{
                    background: bet.result === "won" ? "rgba(0,230,118,0.15)" : bet.result === "lost" ? "rgba(255,23,68,0.15)" : "rgba(0,229,255,0.15)",
                    color: bet.result === "won" ? "var(--green)" : bet.result === "lost" ? "var(--red)" : "var(--cyan)",
                  }}
                >
                  {bet.result.toUpperCase()}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function StatCard({ label, value, icon, color }: { label: string; value: string | number; icon: string; color?: string }) {
  return (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</p>
          <p className="text-2xl font-bold mt-1" style={{ color: color || "var(--text-primary)" }}>{value}</p>
        </div>
        <span className="text-3xl">{icon}</span>
      </div>
    </Card>
  );
}

function QuickAction({ href, icon, title, desc }: { href: string; icon: string; title: string; desc: string }) {
  return (
    <a
      href={href}
      className="block p-5 rounded-xl transition-all duration-200 hover:scale-[1.02]"
      style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = "var(--cyan)")}
      onMouseLeave={e => (e.currentTarget.style.borderColor = "var(--border)")}
    >
      <span className="text-3xl">{icon}</span>
      <h3 className="text-base font-semibold mt-3" style={{ color: "var(--text-primary)" }}>{title}</h3>
      <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{desc}</p>
    </a>
  );
}
