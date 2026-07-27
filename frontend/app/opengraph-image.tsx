import { ImageResponse } from "next/og";

/**
 * The card that renders when the site is linked on Farcaster, X, or Discord.
 *
 * Without this, link previews fall back to an empty grey rectangle, which is
 * what a reader sees before they see anything else. Static export renders this
 * to a PNG at build time, so it costs nothing at runtime.
 *
 * The numbers are deliberately baked in rather than fetched: an OG image is
 * generated once per build and cached hard by every platform that scrapes it,
 * so a "live" number here would be stale and misleading rather than current.
 * These are the final World Cup figures and they don't move.
 */

// Required under output: "export" — without it the build treats this as a
// dynamic route and refuses, since there is no server to render it on demand.
export const dynamic = "force-static";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt =
  "Sports Predictor — 104 matches scored in public, 64.4% accuracy";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#0a0a0f",
          padding: "72px 80px",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div
            style={{
              display: "flex",
              fontSize: 26,
              letterSpacing: 4,
              textTransform: "uppercase",
              color: "#00e5ff",
            }}
          >
            Sports Predictor
          </div>
          <div
            style={{
              display: "flex",
              fontSize: 62,
              lineHeight: 1.2,
              color: "#e8e8f0",
              marginTop: 24,
              maxWidth: 820,
            }}
          >
            A prediction model that publishes its own scoreboard.
          </div>
        </div>

        <div style={{ display: "flex", gap: 64 }}>
          <Stat value="104" label="matches scored" />
          <Stat value="64.4%" label="called right" />
          <Stat value="0.170" label="Brier score" accent="#3987e5" />
          <Stat value="$0.02" label="per call · x402 on Base" accent="#00e676" />
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderTop: "1px solid #2a2a3a",
            paddingTop: 28,
            fontSize: 24,
            color: "#55556a",
          }}
        >
          <div style={{ display: "flex" }}>
            Every prediction logged before kickoff. The misses too.
          </div>
          <div style={{ display: "flex", color: "#00b8d4" }}>
            sports-predictor-7nf6.onrender.com
          </div>
        </div>
      </div>
    ),
    size,
  );
}

function Stat({
  value,
  label,
  accent = "#e8e8f0",
}: {
  value: string;
  label: string;
  accent?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", fontSize: 56, color: accent }}>{value}</div>
      <div style={{ display: "flex", fontSize: 22, color: "#55556a", marginTop: 6 }}>
        {label}
      </div>
    </div>
  );
}
