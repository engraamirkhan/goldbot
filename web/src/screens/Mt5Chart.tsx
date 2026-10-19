import { useEffect, useState } from "react";
import { api, type Mt5Shot } from "../lib/api";

// MT5 (IC Markets) terminal picture beside the TradingView chart. Refetched every 60 s and on timeframe change; the
// server answers 404 when its capture is older than 5 minutes. The blob URL is revoked on every refresh and on unmount.
// The server crops the capture to the chart pane (plot, price scale, time axis); a known non-gold symbol is noted.
// The image fills the panel width at its own aspect ratio (no fixed height); over 3 minutes old it carries a STALE badge.

const TF_WORDS: Record<string, string> = { "15": "15-minute", "60": "1-hour", "240": "4-hour", "1D": "daily" };
const STALE_MS = 3 * 60 * 1000;

type Shot = Mt5Shot | "none" | "error" | null;

function hhmm(iso: string | null): { local: string; utc: string; ms: number } | null {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return null;
  return { local: d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }), utc: d.toISOString().slice(11, 16), ms: d.getTime() };
}

export function Mt5Chart({ tf }: { tf: string }) {
  const [shot, setShot] = useState<Shot>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 30000); return () => clearInterval(t); }, []);

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

  const img = shot && typeof shot === "object" ? shot : null;
  const t = img ? hhmm(img.capturedAt) : null;
  const stale = t !== null && now - t.ms > STALE_MS;
  return (
    <div data-testid="mt5-chart">
      <div className="mt5-box" aria-live="polite">
        {shot === null && <p className="muted" role="status">Loading the MT5 chart...</p>}
        {shot === "none" && <p className="muted">The MT5 chart has not updated for over 5 minutes, so it is hidden. The TradingView chart still works.</p>}
        {shot === "error" && <p className="muted" role="status">MT5 chart unavailable; the TradingView chart still works.</p>}
        {img && (
          <a href={img.url} target="_blank" rel="noopener" className="mt5-open" aria-label="Open the MT5 chart full size in a new tab">
            <img src={img.url} alt={`MT5 gold chart, ${TF_WORDS[tf] ?? tf}`} />
          </a>
        )}
      </div>
      {img && img.isGold === false && <p className="muted" role="status">MT5 is showing {img.symbol} — gold chart not open</p>}
      {img && (
        <p className="muted mt5-cap">
          MT5 chart, IC Markets{t && <> · <span title={`${t.utc} UTC`}>updated {t.local}</span> your time ({t.utc} UTC)</>}
          {stale && <> <span className="badge" role="status">STALE: over 3 min old</span></>}
        </p>
      )}
    </div>
  );
}
