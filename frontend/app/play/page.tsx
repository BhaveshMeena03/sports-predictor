"use client";

/**
 * Beat the Model — pick fixtures, get scored the way the model is.
 *
 * The whole point is the metric. Every prediction game scores picks right or
 * wrong, which rewards confident guessing: call ten favourites at 95% and you
 * look sharp until you don't. Here a pick is a probability, scored on Brier
 * against the same model on the same fixtures, so being correctly unsure beats
 * being loudly right.
 *
 * Two things the UI has to be honest about, because they are what make the
 * leaderboard mean anything:
 *   - a pick locks at kickoff and cannot be edited afterwards, so the confirm
 *     step says so plainly rather than burying it;
 *   - an unsigned pick is just a name anyone could type. Signing with a wallet
 *     makes it attributable, and the table marks the difference rather than
 *     mixing them.
 *
 * Wallet signing goes through window.ethereum directly (EIP-1193 personal_sign)
 * rather than a connector library: one request method, no dependency, works
 * with MetaMask, Rabby and Coinbase Wallet alike.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Card from "../components/Card";
import { API } from "../utils/api";

type Fixture = { league: string; league_label: string; date: string; home: string; away: string };
type Row = {
  player: string; picks: number; avg_brier: number; correct: number;
  accuracy: number | null; verified: boolean; rank?: number; beats_model?: boolean;
};
type Board = {
  model: { avg_brier: number | null; matches: number };
  ranked: Row[]; unranked: number; min_picks_to_rank: number;
};

const LEAGUE_LABELS: Record<string, string> = {
  premier_league: "Premier League", la_liga: "La Liga", serie_a: "Serie A",
  bundesliga: "Bundesliga", ligue_1: "Ligue 1",
};

const OUTCOMES = [
  { key: "home", label: "Home win" },
  { key: "draw", label: "Draw" },
  { key: "away", label: "Away win" },
] as const;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
declare global { interface Window { ethereum?: any } }

export default function Play() {
  const [fixtures, setFixtures] = useState<Fixture[]>([]);
  const [board, setBoard] = useState<Board | null>(null);
  const [player, setPlayer] = useState("");
  const [address, setAddress] = useState<string | null>(null);
  const [sel, setSel] = useState<Record<string, { pick: string; conf: number }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const loadBoard = useCallback(() => {
    fetch(`${API}/leaderboard`).then((r) => r.json()).then(setBoard).catch(() => {});
  }, []);

  useEffect(() => {
    // Fixtures come from the model's own standing predictions, not the odds
    // feed. The picks API only accepts a fixture the model has already
    // pre-logged and that has no result yet — so reading the same list means
    // the UI can never offer something the server will refuse, and every pick
    // is a like-for-like comparison against a prediction made in advance.
    fetch(`${API}/trackrecord`)
      .then((r) => r.json())
      .then((d) => {
        const open = (d?.clubs?.matches ?? [])
          .filter((m: Record<string, unknown>) => m.brier === null)
          .map((m: Record<string, string>) => ({
            league: m.league,
            league_label: LEAGUE_LABELS[m.league] ?? m.league,
            date: m.date, home: m.home, away: m.away,
          }))
          .sort((a: Fixture, b: Fixture) => a.date.localeCompare(b.date));
        setFixtures(open.slice(0, 24));
      })
      .catch(() => {});
    loadBoard();
    setPlayer(localStorage.getItem("btm_player") || "");
  }, [loadBoard]);

  const identity = address || player.trim().toLowerCase();

  async function connect() {
    if (!window.ethereum) {
      setMsg({ kind: "err", text: "No wallet found. Install MetaMask, Rabby or Coinbase Wallet — or just play with a name." });
      return;
    }
    try {
      const [a] = await window.ethereum.request({ method: "eth_requestAccounts" });
      setAddress(a.toLowerCase());
      setMsg(null);
    } catch {
      setMsg({ kind: "err", text: "Wallet connection was rejected." });
    }
  }

  async function submit(f: Fixture) {
    const key = fxKey(f);
    const choice = sel[key];
    if (!choice) return;
    if (!identity) {
      setMsg({ kind: "err", text: "Pick a name or connect a wallet first." });
      return;
    }
    setBusy(key);
    setMsg(null);
    try {
      const base = {
        league: f.league, date: f.date, home: f.home, away: f.away,
        pick: choice.pick, confidence: choice.conf,
      };
      let body: Record<string, unknown> = { ...base, player: identity };

      // Signed picks are attributable; unsigned ones are a name anyone could
      // type. The server verifies by rebuilding the message from these exact
      // fields, so it is served rather than composed here.
      if (address) {
        const m = await fetch(`${API}/picks/message`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(base),
        }).then((r) => r.json());
        const signature = await window.ethereum.request({
          method: "personal_sign", params: [m.message, address],
        });
        body = { ...base, player: address, signature, issued: m.issued };
      }

      const res = await fetch(`${API}/picks`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const out = await res.json();
      if (!res.ok) throw new Error(out.detail || `HTTP ${res.status}`);

      if (!address) localStorage.setItem("btm_player", identity);
      setMsg({ kind: "ok", text: `Locked: ${f.home} v ${f.away} — ${choice.pick} at ${Math.round(choice.conf * 100)}%.` });
      setFixtures((prev) => prev.filter((x) => fxKey(x) !== key));
      loadBoard();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "Could not save that pick." });
    } finally {
      setBusy(null);
    }
  }

  const modelBrier = board?.model?.avg_brier ?? null;

  return (
    <div className="space-y-6 animate-fade-in max-w-4xl">
      <p className="text-xs uppercase tracking-widest mb-2" style={{ color: "var(--text-muted)" }}>
        Free to play · no stake, no payout
      </p>
      <h1 className="text-3xl font-bold mb-2" style={{ color: "var(--text-primary)" }}>
        Beat the model.
      </h1>
      <p className="text-sm mb-6 max-w-2xl" style={{ color: "var(--text-secondary)" }}>
        Pick a fixture and say how sure you are. You&apos;re scored on Brier —
        the same measure the model is scored on, over the same matches — so
        being correctly unsure beats being loudly right. Picks lock at kickoff
        and can&apos;t be changed.
      </p>

      {/* Identity */}
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          {address ? (
            <span className="text-sm px-3 py-2 rounded" style={{ background: "var(--cyan-glow)", color: "var(--cyan)" }}>
              {address.slice(0, 6)}…{address.slice(-4)} · signed picks
            </span>
          ) : (
            <>
              <input
                value={player}
                onChange={(e) => setPlayer(e.target.value)}
                placeholder="a name (2–32 chars)"
                className="px-3 py-2 rounded text-sm outline-none"
                style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>or</span>
              <button
                onClick={connect}
                className="px-3 py-2 rounded text-sm font-medium"
                style={{ background: "var(--cyan-glow)", color: "var(--cyan)" }}
              >
                Connect wallet
              </button>
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                A name is anyone&apos;s to type. A wallet signature proves the pick was yours.
              </span>
            </>
          )}
        </div>
      </Card>

      {msg && (
        <div className="mt-4 px-4 py-3 rounded text-sm"
          style={{
            background: msg.kind === "ok" ? "var(--cyan-glow)" : "rgba(239,68,68,.12)",
            color: msg.kind === "ok" ? "var(--cyan)" : "#fca5a5",
          }}>
          {msg.text}
        </div>
      )}

      {/* Fixtures */}
      <h2 className="text-lg font-semibold mt-8 mb-3" style={{ color: "var(--text-primary)" }}>
        Upcoming fixtures
      </h2>
      {fixtures.length === 0 && (
        <Card><p className="text-sm" style={{ color: "var(--text-muted)" }}>
          No open fixtures right now. New ones appear as the model logs them, up to a week ahead.
        </p></Card>
      )}
      <div className="space-y-3">
        {fixtures.map((f) => {
          const key = fxKey(f);
          const choice = sel[key];
          return (
            <Card key={key}>
              <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                <div>
                  <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                    {f.home} <span style={{ color: "var(--text-muted)" }}>v</span> {f.away}
                  </p>
                  <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                    {f.league_label} · {f.date}
                  </p>
                </div>
                <div className="flex gap-2">
                  {OUTCOMES.map((o) => (
                    <button
                      key={o.key}
                      onClick={() => setSel((s) => ({ ...s, [key]: { pick: o.key, conf: s[key]?.conf ?? 0.5 } }))}
                      className="px-3 py-1.5 rounded text-xs font-medium"
                      style={{
                        background: choice?.pick === o.key ? "var(--cyan-glow)" : "var(--bg-primary)",
                        color: choice?.pick === o.key ? "var(--cyan)" : "var(--text-secondary)",
                        border: "1px solid var(--border)",
                      }}
                    >{o.label}</button>
                  ))}
                </div>
              </div>

              {choice && (
                <div className="flex flex-wrap items-center gap-4">
                  <label className="text-xs" style={{ color: "var(--text-muted)" }}>
                    How sure?
                  </label>
                  <input
                    type="range" min={34} max={95} value={Math.round(choice.conf * 100)}
                    onChange={(e) => setSel((s) => ({ ...s, [key]: { ...choice, conf: Number(e.target.value) / 100 } }))}
                    className="flex-1 min-w-[180px]"
                  />
                  <span className="text-sm font-semibold tabular-nums" style={{ color: "var(--cyan)" }}>
                    {Math.round(choice.conf * 100)}%
                  </span>
                  <button
                    onClick={() => submit(f)}
                    disabled={busy === key}
                    className="px-4 py-2 rounded text-sm font-semibold"
                    style={{ background: "var(--cyan)", color: "#04121a", opacity: busy === key ? 0.6 : 1 }}
                  >
                    {busy === key ? "Locking…" : address ? "Sign & lock" : "Lock it in"}
                  </button>
                </div>
              )}
            </Card>
          );
        })}
      </div>

      {/* Leaderboard */}
      <h2 className="text-lg font-semibold mt-10 mb-1" style={{ color: "var(--text-primary)" }}>
        Leaderboard
      </h2>
      <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
        Lower Brier is better. {modelBrier !== null
          ? `The model scores ${modelBrier} over the same fixtures.`
          : "The model's score appears once picked fixtures have results."}
        {board ? ` Ranked from ${board.min_picks_to_rank} picks.` : ""}
      </p>
      <Card>
        {!board?.ranked?.length ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Nobody ranked yet — results land as fixtures finish.
            {board?.unranked ? ` ${board.unranked} player(s) still building up picks.` : ""}
          </p>
        ) : (
          <table className="w-full text-sm" style={{ fontVariantNumeric: "tabular-nums" }}>
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
                <th className="pb-2">#</th><th className="pb-2">Player</th>
                <th className="pb-2">Picks</th><th className="pb-2">Brier</th>
                <th className="pb-2">Right</th>
              </tr>
            </thead>
            <tbody>
              {board.ranked.map((r) => (
                <tr key={r.player} style={{ borderTop: "1px solid var(--border)" }}>
                  <td className="py-2">{r.rank}</td>
                  <td className="py-2">
                    {r.player.startsWith("0x") ? `${r.player.slice(0, 6)}…${r.player.slice(-4)}` : r.player}
                    {r.verified && <span className="ml-2 text-xs" style={{ color: "var(--cyan)" }} title="every pick signed by this wallet">✓ signed</span>}
                    {r.beats_model && <span className="ml-2 text-xs" style={{ color: "#34d399" }}>beats the model</span>}
                  </td>
                  <td className="py-2">{r.picks}</td>
                  <td className="py-2">{r.avg_brier}</td>
                  <td className="py-2">{r.accuracy !== null ? `${Math.round(r.accuracy * 100)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <p className="text-xs mt-6" style={{ color: "var(--text-muted)" }}>
        No stake and no payout — this is a scoreboard, not a book. Informational only.
      </p>
    </div>
  );
}

function fxKey(f: Fixture) {
  return `${f.league}|${f.date}|${f.home}|${f.away}`;
}
