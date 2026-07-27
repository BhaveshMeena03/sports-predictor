/**
 * Personal bet log — stored in the visitor's own browser, never sent anywhere.
 *
 * The previous tracker kept one shared ledger on the server, which meant the
 * owner's real stakes and P/L were readable by anyone who found the API. On a
 * public site the only version of this feature that isn't a privacy problem is
 * one where the data never leaves the device: no account, no server, nothing
 * to leak.
 *
 * Trade-off worth being honest about in the UI: the log is per-browser, so it
 * doesn't follow you to another device, and clearing site data erases it.
 */

export type Bet = {
  id: string;
  match: string;
  pick: string;
  odds: number;
  stake: number;
  /** ISO date the bet was added. */
  placed: string;
  result: "pending" | "won" | "lost";
};

const KEY = "sp.betlog.v1";

function canStore(): boolean {
  // Static export runs this during prerender where localStorage doesn't exist,
  // and private-mode browsers can throw on access rather than return null.
  try {
    return typeof window !== "undefined" && !!window.localStorage;
  } catch {
    return false;
  }
}

export function loadBets(): Bet[] {
  if (!canStore()) return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // Corrupt or foreign data in the key: start clean rather than crash the page.
    return [];
  }
}

export function saveBets(bets: Bet[]): void {
  if (!canStore()) return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(bets));
  } catch {
    // Quota exceeded or storage disabled — the UI stays usable, just unsaved.
  }
}

export function addBet(b: Omit<Bet, "id" | "placed" | "result">): Bet[] {
  const bets = loadBets();
  const bet: Bet = {
    ...b,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    placed: new Date().toISOString().slice(0, 10),
    result: "pending",
  };
  const next = [bet, ...bets];
  saveBets(next);
  return next;
}

export function settleBet(id: string, result: "won" | "lost"): Bet[] {
  const next = loadBets().map((b) => (b.id === id ? { ...b, result } : b));
  saveBets(next);
  return next;
}

export function removeBet(id: string): Bet[] {
  const next = loadBets().filter((b) => b.id !== id);
  saveBets(next);
  return next;
}

export type Summary = {
  total: number;
  settled: number;
  won: number;
  lost: number;
  pending: number;
  staked: number;
  returned: number;
  profit: number;
  roi: number | null;
  winRate: number | null;
};

export function summarise(bets: Bet[]): Summary {
  const settled = bets.filter((b) => b.result !== "pending");
  const won = settled.filter((b) => b.result === "won");
  // ROI is measured on SETTLED stakes only — counting pending bets as losses
  // would make every open position look like a loss until it lands.
  const staked = settled.reduce((s, b) => s + b.stake, 0);
  const returned = won.reduce((s, b) => s + b.stake * b.odds, 0);
  const profit = returned - staked;
  return {
    total: bets.length,
    settled: settled.length,
    won: won.length,
    lost: settled.length - won.length,
    pending: bets.length - settled.length,
    staked,
    returned,
    profit,
    roi: staked > 0 ? (profit / staked) * 100 : null,
    winRate: settled.length > 0 ? (won.length / settled.length) * 100 : null,
  };
}
