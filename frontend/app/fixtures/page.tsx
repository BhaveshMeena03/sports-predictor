"use client";
import { useState, useEffect } from "react";
import Card from "../components/Card";
import { TeamLogo, LeagueLogo } from "../components/TeamLogo";
import { useRouter } from "next/navigation";

const API = "http://localhost:8000/api";

const SPORT_FILTERS = [
  { key: "all", label: "All Sports" },
  { key: "premier_league", label: "Premier League" },
  { key: "la_liga", label: "La Liga" },
  { key: "bundesliga", label: "Bundesliga" },
  { key: "serie_a", label: "Serie A" },
  { key: "ligue_1", label: "Ligue 1" },
  { key: "champions_league", label: "Champions League" },
  { key: "mls", label: "MLS" },
  { key: "nba", label: "NBA" },
  { key: "nhl", label: "NHL" },
  { key: "ipl", label: "IPL" },
];

interface Match {
  id: string;
  sport: string;
  sport_label: string;
  home_team: string;
  away_team: string;
  commence_time: string;
  home_odds: number;
  away_odds: number;
  draw_odds: number | null;
}

interface MultiLeg {
  match: string;
  pick: string;
  odds: number;
  sport: string;
}

export default function FixturesPage() {
  const router = useRouter();
  const [matches, setMatches] = useState<Match[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("all");
  const [multiLegs, setMultiLegs] = useState<MultiLeg[]>([]);
  const [showMultiPanel, setShowMultiPanel] = useState(false);

  async function loadMatches(sportFilter?: string) {
    setLoading(true);
    try {
      const sportParam = sportFilter && sportFilter !== "all" ? `?sports=${sportFilter}` : "?sports=premier_league,la_liga,bundesliga,nba";
      const resp = await fetch(`${API}/upcoming-with-odds${sportParam}`);
      const data = await resp.json();
      // Merge with existing matches (don't replace - accumulate)
      setMatches(prev => {
        const newMatches = data.matches || [];
        const existingIds = new Set(prev.map((m: Match) => m.id));
        const merged = [...prev];
        for (const m of newMatches) {
          if (!existingIds.has(m.id)) merged.push(m);
        }
        return merged;
      });
    } catch {
      // Keep existing matches on error
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadMatches(); }, []);

  function handleFilterClick(key: string) {
    setFilter(key);
    if (key !== "all") {
      // Fetch this sport if we don't have matches for it yet
      const hasMatches = matches.some(m => m.sport === key);
      if (!hasMatches) loadMatches(key);
    }
  }

  const filtered = filter === "all" ? matches : matches.filter(m => m.sport === filter);

  function addToMulti(match: Match, pick: "home" | "away" | "draw") {
    const pickName = pick === "home" ? match.home_team + " Win" : pick === "away" ? match.away_team + " Win" : "Draw";
    const odds = pick === "home" ? match.home_odds : pick === "away" ? match.away_odds : match.draw_odds || 0;
    const matchName = `${match.home_team} vs ${match.away_team}`;

    // Don't add duplicates
    if (multiLegs.some(l => l.match === matchName)) {
      setMultiLegs(multiLegs.filter(l => l.match !== matchName));
      return;
    }

    setMultiLegs([...multiLegs, { match: matchName, pick: pickName, odds, sport: match.sport }]);
    setShowMultiPanel(true);
  }

  function removeFromMulti(match: string) {
    setMultiLegs(multiLegs.filter(l => l.match !== match));
  }

  function isInMulti(match: Match) {
    return multiLegs.some(l => l.match === `${match.home_team} vs ${match.away_team}`);
  }

  const totalOdds = multiLegs.reduce((acc, l) => acc * l.odds, 1);

  function goToMultiBuilder() {
    localStorage.setItem("multiLegs", JSON.stringify(multiLegs));
    router.push("/multi");
  }

  function formatDate(iso: string) {
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }

  function formatTime(iso: string) {
    const d = new Date(iso);
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  }

  // Group by date
  const grouped: Record<string, Match[]> = {};
  filtered.forEach(m => {
    const dateKey = formatDate(m.commence_time);
    if (!grouped[dateKey]) grouped[dateKey] = [];
    grouped[dateKey].push(m);
  });

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: "var(--cyan)" }}>Upcoming Matches</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>Next 7 days — click odds to add to multi</p>
        </div>
        <button
          onClick={loadMatches}
          disabled={loading}
          className="px-4 py-2 rounded-lg text-sm font-bold"
          style={{ background: "var(--cyan)", color: "var(--bg-primary)" }}
        >
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {/* Sport Filters */}
      <div className="flex gap-2 flex-wrap">
        {SPORT_FILTERS.map(f => (
          <button
            key={f.key}
            onClick={() => handleFilterClick(f.key)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200"
            style={{
              background: filter === f.key ? "var(--cyan-glow)" : "var(--bg-card)",
              color: filter === f.key ? "var(--cyan)" : "var(--text-muted)",
              border: `1px solid ${filter === f.key ? "var(--cyan)" : "var(--border)"}`,
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Multi Panel (sticky bottom) */}
      {multiLegs.length > 0 && (
        <div
          className="fixed bottom-0 left-64 right-0 z-40 p-4"
          style={{ background: "var(--bg-secondary)", borderTop: "2px solid var(--cyan)" }}
        >
          <div className="max-w-5xl mx-auto flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div>
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>Multi Slip</span>
                <span className="ml-2 text-sm font-bold" style={{ color: "var(--cyan)" }}>{multiLegs.length} legs</span>
              </div>
              <div className="flex gap-2 flex-wrap">
                {multiLegs.map((leg, i) => (
                  <span
                    key={i}
                    className="flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg cursor-pointer"
                    style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                    onClick={() => removeFromMulti(leg.match)}
                  >
                    {leg.pick} @ {leg.odds.toFixed(2)}
                    <span style={{ color: "var(--red)" }}>x</span>
                  </span>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>Total Odds</p>
                <p className="text-lg font-bold" style={{ color: "var(--cyan)" }}>{totalOdds.toFixed(2)}x</p>
              </div>
              <button
                onClick={goToMultiBuilder}
                className="px-5 py-2.5 rounded-lg text-sm font-bold transition-all duration-200 hover:scale-105"
                style={{ background: "var(--cyan)", color: "var(--bg-primary)" }}
              >
                Analyze Multi
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Matches grouped by date */}
      {loading && (
        <Card>
          <p className="text-sm text-center" style={{ color: "var(--text-muted)" }}>Loading matches from 10+ leagues...</p>
        </Card>
      )}

      {Object.entries(grouped).map(([dateKey, dayMatches]) => (
        <div key={dateKey} className="space-y-2">
          <h2 className="text-sm font-bold uppercase tracking-wider px-1" style={{ color: "var(--text-muted)" }}>
            {dateKey}
          </h2>
          {dayMatches.map((match, i) => {
            const inMulti = isInMulti(match);
            return (
              <div
                key={i}
                className="rounded-xl p-4 transition-all duration-200"
                style={{
                  background: inMulti ? "var(--bg-card-hover)" : "var(--bg-card)",
                  border: `1px solid ${inMulti ? "var(--cyan)" : "var(--border)"}`,
                }}
              >
                <div className="flex items-center gap-4">
                  {/* Sport badge with league logo */}
                  <div
                    className="flex items-center gap-1.5 text-xs font-bold px-2 py-1 rounded shrink-0"
                    style={{ background: "var(--bg-secondary)", color: "var(--text-muted)" }}
                  >
                    <LeagueLogo sport={match.sport} size={16} />
                    <span className="w-12 text-center">{match.sport_label}</span>
                  </div>

                  {/* Time */}
                  <div className="text-xs shrink-0 w-14 text-center" style={{ color: "var(--text-muted)" }}>
                    {formatTime(match.commence_time)}
                  </div>

                  {/* Teams with logos */}
                  <div className="flex-1 flex items-center gap-3">
                    <div className="flex items-center gap-2 flex-1 justify-end">
                      <span className="text-sm font-medium text-right" style={{ color: "var(--text-primary)" }}>
                        {match.home_team}
                      </span>
                      <TeamLogo team={match.home_team} sport={match.sport} size={28} />
                    </div>
                    <span className="text-xs px-1" style={{ color: "var(--text-muted)" }}>vs</span>
                    <div className="flex items-center gap-2 flex-1">
                      <TeamLogo team={match.away_team} sport={match.sport} size={28} />
                      <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                        {match.away_team}
                      </span>
                    </div>
                  </div>

                  {/* Odds buttons */}
                  <div className="flex gap-2 shrink-0">
                    <OddsButton
                      label="1"
                      odds={match.home_odds}
                      onClick={() => addToMulti(match, "home")}
                      active={multiLegs.some(l => l.match === `${match.home_team} vs ${match.away_team}` && l.pick.includes(match.home_team))}
                    />
                    {match.draw_odds && (
                      <OddsButton
                        label="X"
                        odds={match.draw_odds}
                        onClick={() => addToMulti(match, "draw")}
                        active={multiLegs.some(l => l.match === `${match.home_team} vs ${match.away_team}` && l.pick === "Draw")}
                      />
                    )}
                    <OddsButton
                      label="2"
                      odds={match.away_odds}
                      onClick={() => addToMulti(match, "away")}
                      active={multiLegs.some(l => l.match === `${match.home_team} vs ${match.away_team}` && l.pick.includes(match.away_team))}
                    />
                  </div>

                  {/* Analyze button */}
                  <a
                    href={`/analyze?home=${encodeURIComponent(match.home_team)}&away=${encodeURIComponent(match.away_team)}&sport=${match.sport}&odds=Home ${match.home_odds} | Draw ${match.draw_odds || '-'} | Away ${match.away_odds}`}
                    className="text-xs px-2.5 py-1.5 rounded font-medium shrink-0 transition-all duration-200"
                    style={{
                      background: "var(--cyan-glow)",
                      border: "1px solid var(--cyan)",
                      color: "var(--cyan)",
                    }}
                  >
                    AI
                  </a>
                </div>
              </div>
            );
          })}
        </div>
      ))}

      {!loading && filtered.length === 0 && (
        <Card>
          <p className="text-sm text-center" style={{ color: "var(--text-muted)" }}>
            No upcoming matches found. Try a different filter or click Refresh.
          </p>
        </Card>
      )}

      {/* Spacer for multi panel */}
      {multiLegs.length > 0 && <div className="h-20" />}
    </div>
  );
}

function OddsButton({ label, odds, onClick, active }: { label: string; odds: number; onClick: () => void; active: boolean }) {
  if (!odds || odds === 0) return null;
  return (
    <button
      onClick={onClick}
      className="flex flex-col items-center px-3 py-2 rounded-lg text-xs transition-all duration-200 min-w-[52px]"
      style={{
        background: active ? "var(--cyan-glow)" : "var(--bg-secondary)",
        border: `1px solid ${active ? "var(--cyan)" : "var(--border)"}`,
        color: active ? "var(--cyan)" : "var(--text-primary)",
      }}
    >
      <span className="text-[10px] font-medium" style={{ color: active ? "var(--cyan)" : "var(--text-muted)" }}>{label}</span>
      <span className="font-bold">{odds.toFixed(2)}</span>
    </button>
  );
}
