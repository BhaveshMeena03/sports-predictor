"use client";
import { useEffect, useState } from "react";
import Card from "../components/Card";
import {
  Bet,
  addBet,
  loadBets,
  removeBet,
  settleBet,
  summarise,
} from "../utils/betlog";

/**
 * Your own bet log. Everything here lives in this browser's localStorage and
 * is never sent to the server — see app/utils/betlog.ts for why.
 */
export default function BetLogPage() {
  const [bets, setBets] = useState<Bet[]>([]);
  const [ready, setReady] = useState(false);
  const [match, setMatch] = useState("");
  const [pick, setPick] = useState("");
  const [odds, setOdds] = useState("");
  const [stake, setStake] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Load after mount: localStorage doesn't exist during the static prerender.
  useEffect(() => {
    setBets(loadBets());
    setReady(true);
  }, []);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const o = parseFloat(odds);
    const s = parseFloat(stake);
    if (!match.trim() || !pick.trim()) return setError("Match and pick are required.");
    if (!Number.isFinite(o) || o <= 1) return setError("Odds must be a number above 1.0.");
    if (!Number.isFinite(s) || s <= 0) return setError("Stake must be a positive number.");
    setError(null);
    setBets(addBet({ match: match.trim(), pick: pick.trim(), odds: o, stake: s }));
    setMatch(""); setPick(""); setOdds(""); setStake("");
  }

  const sum = summarise(bets);
  const money = (n: number) => `${n < 0 ? "−" : ""}${Math.abs(n).toFixed(2)}`;

  return (
    <div className="space-y-6 animate-fade-in max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: "var(--cyan)" }}>My Bet Log</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Track what you actually staked and see your real return. Stored in this browser
          only — never uploaded, no account, nobody else can see it.
        </p>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Settled bets" value={ready ? String(sum.settled) : "—"}
          sub={sum.pending ? `${sum.pending} still open` : "none open"} />
        <Stat label="Win rate" value={sum.winRate == null ? "—" : `${sum.winRate.toFixed(0)}%`}
          sub={`${sum.won}W · ${sum.lost}L`} />
        <Stat label="Profit / loss" value={ready ? money(sum.profit) : "—"}
          sub={`${money(sum.staked)} staked`}
          color={sum.profit > 0 ? "var(--green)" : sum.profit < 0 ? "var(--red)" : undefined} />
        <Stat label="ROI" value={sum.roi == null ? "—" : `${sum.roi.toFixed(1)}%`}
          sub="on settled stakes"
          color={sum.roi == null ? undefined : sum.roi > 0 ? "var(--green)" : sum.roi < 0 ? "var(--red)" : undefined} />
      </div>

      {/* Add */}
      <Card>
        <h2 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>
          Add a bet
        </h2>
        <form onSubmit={submit} className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <Field label="Match" value={match} onChange={setMatch}
            placeholder="Arsenal v Chelsea" className="md:col-span-2" />
          <Field label="Pick" value={pick} onChange={setPick} placeholder="Arsenal" />
          <Field label="Odds" value={odds} onChange={setOdds} placeholder="1.85" inputMode="decimal" />
          <div className="flex gap-2 items-end">
            <Field label="Stake" value={stake} onChange={setStake} placeholder="10" inputMode="decimal" />
            <button
              type="submit"
              className="px-4 py-2.5 rounded-lg text-sm font-bold cursor-pointer transition-colors duration-200 shrink-0"
              style={{ background: "var(--cyan)", color: "var(--bg-primary)" }}
            >
              Add
            </button>
          </div>
        </form>
        {error && <p className="text-xs mt-2" style={{ color: "var(--red)" }}>{error}</p>}
      </Card>

      {/* List */}
      <Card>
        <h2 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>
          Your bets
        </h2>
        {!ready ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>loading…</p>
        ) : bets.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Nothing logged yet. Add a bet above and settle it once the match finishes —
            the summary then shows your real ROI rather than a guess.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs" style={{ fontVariantNumeric: "tabular-nums" }}>
              <thead>
                <tr className="text-left" style={{ color: "var(--text-muted)" }}>
                  <th className="py-2 pr-3 font-medium">date</th>
                  <th className="py-2 pr-3 font-medium">match</th>
                  <th className="py-2 pr-3 font-medium">pick</th>
                  <th className="py-2 pr-3 font-medium text-right">odds</th>
                  <th className="py-2 pr-3 font-medium text-right">stake</th>
                  <th className="py-2 pr-3 font-medium text-right">return</th>
                  <th className="py-2 pr-0 font-medium text-right">result</th>
                </tr>
              </thead>
              <tbody style={{ color: "var(--text-secondary)" }}>
                {bets.map((b) => {
                  const ret = b.result === "won" ? b.stake * b.odds - b.stake
                    : b.result === "lost" ? -b.stake : null;
                  return (
                    <tr key={b.id} style={{ borderTop: "1px solid var(--border)" }}>
                      <td className="py-2 pr-3 whitespace-nowrap" style={{ color: "var(--text-muted)" }}>{b.placed}</td>
                      <td className="py-2 pr-3">{b.match}</td>
                      <td className="py-2 pr-3">{b.pick}</td>
                      <td className="py-2 pr-3 text-right">{b.odds.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-right">{b.stake.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-right"
                        style={{ color: ret == null ? "var(--text-muted)" : ret > 0 ? "var(--green)" : "var(--red)" }}>
                        {ret == null ? "—" : money(ret)}
                      </td>
                      <td className="py-2 pr-0 text-right whitespace-nowrap">
                        {b.result === "pending" ? (
                          <span className="inline-flex gap-1">
                            <SmallBtn onClick={() => setBets(settleBet(b.id, "won"))}
                              color="var(--green)" label="Won" />
                            <SmallBtn onClick={() => setBets(settleBet(b.id, "lost"))}
                              color="var(--red)" label="Lost" />
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-2">
                            <span style={{ color: b.result === "won" ? "var(--green)" : "var(--red)" }}>
                              {b.result}
                            </span>
                            <SmallBtn onClick={() => setBets(removeBet(b.id))}
                              color="var(--text-muted)" label="✕" title="Remove" />
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
        This log is saved in your browser&apos;s local storage. It won&apos;t follow you to
        another device, and clearing your browser data will erase it. Informational
        only — nothing here is betting advice.
      </p>
    </div>
  );
}

function Stat({ label, value, sub, color }: {
  label: string; value: string; sub: string; color?: string;
}) {
  return (
    <div className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}>
      <p className="text-[11px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="text-2xl font-bold mt-1.5"
        style={{ color: color ?? "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
        {value}
      </p>
      <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>{sub}</p>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, className = "", inputMode }: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder: string; className?: string; inputMode?: "decimal";
}) {
  return (
    <div className={className}>
      <label className="block text-[11px] uppercase tracking-wider mb-1"
        style={{ color: "var(--text-muted)" }}>
        {label}
      </label>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        inputMode={inputMode}
        className="w-full px-3 py-2.5 rounded-lg text-sm outline-none"
        style={{ background: "var(--bg-secondary)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
      />
    </div>
  );
}

function SmallBtn({ onClick, color, label, title }: {
  onClick: () => void; color: string; label: string; title?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="px-2 py-1 rounded cursor-pointer transition-colors duration-200"
      style={{ border: `1px solid ${color}`, color }}
    >
      {label}
    </button>
  );
}
