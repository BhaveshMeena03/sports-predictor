"use client";
import { ReactNode } from "react";

export default function Card({
  children,
  className = "",
  glow = false,
}: {
  children: ReactNode;
  className?: string;
  glow?: boolean;
}) {
  return (
    <div
      className={`rounded-xl p-5 transition-all duration-200 ${glow ? "animate-pulse-cyan" : ""} ${className}`}
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
      }}
    >
      {children}
    </div>
  );
}
