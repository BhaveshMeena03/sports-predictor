"use client";
import { getTeamLogo, getLeagueLogo } from "../utils/logos";
import { useState } from "react";

export function TeamLogo({ team, sport, size = 28 }: { team: string; sport: string; size?: number }) {
  const [error, setError] = useState(false);
  const logo = getTeamLogo(team, sport);

  if (!logo || error) {
    return (
      <div
        className="rounded-full flex items-center justify-center font-bold text-xs shrink-0"
        style={{
          width: size,
          height: size,
          background: "var(--bg-secondary)",
          color: "var(--text-muted)",
          border: "1px solid var(--border)",
        }}
      >
        {team.charAt(0)}
      </div>
    );
  }

  return (
    <img
      src={logo}
      alt={team}
      width={size}
      height={size}
      className="rounded-full object-contain shrink-0"
      style={{ background: "transparent" }}
      onError={() => setError(true)}
    />
  );
}

export function LeagueLogo({ sport, size = 20 }: { sport: string; size?: number }) {
  const [error, setError] = useState(false);
  const logo = getLeagueLogo(sport);

  if (!logo || error) {
    return null;
  }

  return (
    <img
      src={logo}
      alt={sport}
      width={size}
      height={size}
      className="object-contain shrink-0"
      onError={() => setError(true)}
    />
  );
}
