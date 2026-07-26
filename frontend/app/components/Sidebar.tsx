"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

// Inline SVG paths (Lucide outlines) — emoji icons read as a hobby project.
const ICONS: Record<string, string> = {
  chart: "M3 3v18h18 M7 13l3-3 4 4 5-6",
  search: "M21 21l-4.3-4.3 M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0z",
  layers: "M12 2 2 7l10 5 10-5-10-5z M2 17l10 5 10-5 M2 12l10 5 10-5",
  calendar: "M8 2v4 M16 2v4 M3 8h18 M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
  list: "M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01",
  ask: "M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z",
};

const navItems = [
  { href: "/", label: "Track Record", icon: "chart" },
  { href: "/ask", label: "Ask the Model", icon: "ask" },
  { href: "/fixtures", label: "Fixtures", icon: "calendar" },
  { href: "/analyze", label: "Analyze Match", icon: "search" },
  { href: "/multi", label: "Multi Builder", icon: "layers" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="fixed left-0 top-0 h-screen w-64 flex flex-col z-50"
      style={{ background: "var(--bg-secondary)", borderRight: "1px solid var(--border)" }}
    >
      {/* Logo */}
      <div className="p-6 flex items-center gap-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center text-lg font-bold"
          style={{ background: "var(--cyan-glow)", color: "var(--cyan)" }}
        >
          SP
        </div>
        <div>
          <h1 className="text-base font-bold" style={{ color: "var(--cyan)" }}>
            Sports Predictor
          </h1>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            AI-Powered Analysis
          </p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className="flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200"
              style={{
                background: isActive ? "var(--cyan-glow)" : "transparent",
                color: isActive ? "var(--cyan)" : "var(--text-secondary)",
                borderLeft: isActive ? "3px solid var(--cyan)" : "3px solid transparent",
              }}
            >
              <svg viewBox="0 0 24 24" className="w-5 h-5 shrink-0" fill="none"
                stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
                aria-hidden="true">
                {ICONS[item.icon].split(" M").map((d, i) => (
                  <path key={i} d={(i ? "M" : "") + d} />
                ))}
              </svg>
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* Status */}
      <div className="p-4" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
          <div className="w-2 h-2 rounded-full" style={{ background: "var(--green)" }} />
          AI Engine Online
        </div>
      </div>
    </aside>
  );
}
