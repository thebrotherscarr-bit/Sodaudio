import { useCallback, useEffect, useState } from "react";

export const fmtTime = (s: number | null | undefined) => {
  if (s === null || s === undefined || !isFinite(s)) return "–";
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
};

export const fmtDuration = (s: number | null | undefined) => {
  if (s === null || s === undefined || !isFinite(s)) return "–";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
};

// Loads data from a function, with reload; errors are kept for display.
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    load().then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e.message || e)));
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, reload, setData };
}

export function cx(...names: (string | false | null | undefined)[]) {
  return names.filter(Boolean).join(" ");
}
