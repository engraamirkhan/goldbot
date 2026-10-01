import { useEffect, useState } from "react";

/** Wall-clock milliseconds, re-rendering every `intervalMs` so countdowns tick on their own. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function secondsLeft(expiresAtIso: string, now: number): number {
  return Math.max(0, Math.round((new Date(expiresAtIso).getTime() - now) / 1000));
}
