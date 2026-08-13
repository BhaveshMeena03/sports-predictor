/**
 * Regenerate public/og.png — the card X, Discord and Slack show for the site.
 *
 * This exists because the previous card was a committed PNG with no source,
 * so nothing kept it honest. It drifted twice over: it claimed "every
 * prediction logged before kickoff" long after that stopped being true of the
 * World Cup rows, and it displayed the old onrender.com hostname months after
 * the site moved to predictor.lexthedev.com. Both were invisible in code
 * review because the claim lived in pixels.
 *
 * The numbers are fetched live at build time rather than typed in, so the card
 * cannot quietly disagree with the API it describes.
 *
 * Usage: node scripts/make-og.mjs
 */
import { chromium } from "playwright";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const API = "https://sports-predictor-api.fly.dev/api";
const SITE = "predictor.lexthedev.com";
const here = dirname(fileURLToPath(import.meta.url));

const track = await fetch(`${API}/trackrecord`).then((r) => r.json());
const wc = track.world_cup.summary;
const clubs = track.clubs;

const accuracy = ((wc.picked_correct / wc.n) * 100).toFixed(1);
const brier = wc.avg_brier.toFixed(3);

// Say what the log actually is. "Logged before kickoff" was the old line and
// it was not true of these rows; the standing club predictions are, so lead
// with whichever the data supports.
const footer = clubs.pending
  ? `${wc.n} scored in public · ${clubs.pending} more logged before kickoff`
  : `${wc.n} scored in public — including the ones it got wrong`;

const html = `<!doctype html><meta charset="utf-8"><style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{width:1200px;height:630px;background:#0a0e14;color:#e8edf3;
       font:400 16px/1.4 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif;
       padding:56px 64px;display:flex;flex-direction:column;
       background-image:radial-gradient(720px 340px at 88% -12%,rgba(34,211,238,.10),transparent 62%)}
  .eyebrow{color:#22d3ee;font-size:19px;font-weight:600;letter-spacing:4.5px;margin-bottom:26px}
  h1{font-size:64px;line-height:1.08;font-weight:700;letter-spacing:-1.6px;max-width:15ch}
  .stats{display:flex;gap:64px;margin-top:auto;align-items:flex-end}
  .v{font-size:52px;font-weight:700;letter-spacing:-1.4px;line-height:1}
  .l{font-size:17px;color:#7d8896;margin-top:9px}
  .cy{color:#22d3ee}.gr{color:#34d399}
  .rule{border-top:1px solid #1e2733;margin:34px 0 20px}
  .foot{display:flex;justify-content:space-between;font-size:18px;color:#7d8896}
  .foot b{color:#22d3ee;font-weight:500}
</style>
<div class="eyebrow">SPORTS PREDICTOR</div>
<h1>A prediction model that publishes its own scoreboard.</h1>
<div class="stats">
  <div><div class="v">${wc.n}</div><div class="l">matches scored</div></div>
  <div><div class="v">${accuracy}%</div><div class="l">called right</div></div>
  <div><div class="v cy">${brier}</div><div class="l">Brier score</div></div>
  <div><div class="v gr">$0.02</div><div class="l">per call · x402 on Base</div></div>
</div>
<div class="rule"></div>
<div class="foot"><span>${footer}</span><b>${SITE}</b></div>`;

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1200, height: 630 },
  deviceScaleFactor: 1,
});
await page.setContent(html, { waitUntil: "load" });
const buf = await page.screenshot({ type: "png" });
browser.close();

for (const out of ["public/og.png", "out/og.png"]) {
  try {
    writeFileSync(join(here, "..", out), buf);
    console.log(`  wrote ${out}  (${(buf.length / 1024).toFixed(0)} KB)`);
  } catch (e) {
    console.log(`  skipped ${out}: ${e.code}`);
  }
}
console.log(`  footer: ${footer}`);
