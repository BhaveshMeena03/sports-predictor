"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/", label: "Dashboard", icon: "📊" },
  { href: "/analyze", label: "Analyze Match", icon: "🔍" },
  { href: "/multi", label: "Multi Builder", icon: "🎯" },
  { href: "/fixtures", label: "Fixtures", icon: "📅" },
  { href: "/tracker", label: "Bet Tracker", icon: "💰" },
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
              <span className="text-lg">{item.icon}</span>
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
