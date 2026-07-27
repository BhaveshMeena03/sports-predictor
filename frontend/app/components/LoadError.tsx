"use client";

/**
 * Shown when data can't be loaded.
 *
 * Exists because of a real incident: the landing page swallowed every fetch
 * failure with `.catch(() => {})`, so when a bad deploy broke the page it
 * rendered blank placeholders forever. Silence is the worst failure mode —
 * it looks identical to "still loading", so nobody investigates.
 */
export default function LoadError({
  message = "Couldn't load this data.",
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      className="rounded-lg px-4 py-3 text-sm flex items-center justify-between gap-4"
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        color: "var(--text-secondary)",
      }}
      role="status"
    >
      <span>
        <span style={{ color: "var(--red)" }}>●</span> {message}
      </span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-3 py-1.5 rounded-md text-xs cursor-pointer transition-colors duration-200 shrink-0"
          style={{ border: "1px solid var(--cyan-dim)", color: "var(--cyan)" }}
        >
          Retry
        </button>
      )}
    </div>
  );
}
