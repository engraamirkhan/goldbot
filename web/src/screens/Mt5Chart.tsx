import { useEffect, useState } from "react";
import { api } from "../lib/api";

// MT5 (IC Markets) terminal picture beside the TradingView chart. Refetched every 60 s and on timeframe change; the
// server answers 404 when its capture is older than 5 minutes. The blob URL is revoked on every refresh and on unmount.

type Shot = { url: string; capturedAt: string | null } | "none" | "error" | null;

function when(iso: string | null): string {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "";
  return ` · ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} your time (${d.toISOString().slice(11, 16)} UTC)`;
}

export function Mt5Chart({ tf }: { tf: string }) {
  const [shot, setShot] = useState<Shot>(null);

  useEffect(() => {
    let live = true;
    let current: string | null = null;
    const drop = () => { if (current) { URL.revokeObjectURL(current); current = null; } };
    const load = async () => {
      try {
        const r = await api.mt5Chart(tf);
        if (!live) { if (r) URL.revokeObjectURL(r.url); return; }
        drop();
        current = r?.url ?? null;
        setShot(r ?? "none");
      } catch {
        if (live) { drop(); setShot("error"); }
      }
    };
    void load();
    const t = setInterval(() => void load(), 60000);
    return () => { live = false; clearInterval(t); drop(); };
  }, [tf]);

  const box = { height: 420, border: "1px solid var(--border, var(--line))", borderRadius: "var(--r-lg, 8px)", overflow: "hidden", background: "var(--surface-2, var(--surface))", display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center" as const, padding: "var(--s3, 12px)" };
  return (
    <div data-testid="mt5-chart">
      <div style={box} aria-live="polite">
        {shot === null && <p className="muted" role="status">Loading the MT5 chart...</p>}
        {shot === "none" && <p className="muted">The MT5 chart has not updated for over 5 minutes, so it is hidden. The TradingView chart still works.</p>}
        {shot === "error" && <p className="muted" role="status">MT5 chart unavailable; the TradingView chart still works.</p>}
        {shot && typeof shot === "object" && <img src={shot.url} alt={`MT5 gold chart, ${tf} timeframe`} style={{ width: "100%", height: "100%", objectFit: "contain" }} />}
      </div>
      {shot && typeof shot === "object" && <p className="muted" style={{ fontSize: "var(--fs-small, 13px)" }}>MT5 chart, IC Markets{when(shot.capturedAt)}</p>}
    </div>
  );
}
