import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { validLayoutUrl } from "../lib/chart";
import { Mt5Chart } from "./Mt5Chart";
import { api, type ChartLayoutIn, type ChartLayoutsView } from "../lib/api";

// Chart tab: TradingView's free Advanced Chart widget on OANDA:XAUUSD, plus links to the owner's own saved TradingView
// layouts. The widget cannot load saved layouts or invite-only scripts, so those open in TradingView itself. The layout
// URL identifies the owner's TradingView account: it lives server-side (owner-only) and is never in the repo.

const INTERVALS = [
  { label: "15m", value: "15" }, { label: "1h", value: "60" }, { label: "4h", value: "240" }, { label: "1D", value: "D" },
] as const;

const WIDGET_ORIGIN = "https://www.tradingview-widget.com";
const SYMBOL = "OANDA:XAUUSD";

function prefersDark(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function useDark(): boolean {
  const [dark, setDark] = useState(prefersDark);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setDark(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return dark;
}

function TradingViewWidget({ interval, dark }: { interval: string; dark: boolean }) {
  const [loaded, setLoaded] = useState<string | null>(null);
  const [slow, setSlow] = useState<string | null>(null);
  // A cross-origin iframe, never a script in our origin: it cannot read this page's storage (the session token).
  const src = `${WIDGET_ORIGIN}/embed-widget/advanced-chart/?locale=en#` + encodeURIComponent(JSON.stringify({
    autosize: true, symbol: SYMBOL, interval, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Etc/UTC",
    theme: dark ? "dark" : "light", style: "1", allow_symbol_change: false, hide_side_toolbar: true, hide_top_toolbar: true,
    support_host: "https://www.tradingview.com",
  }));
  // an iframe reports no error across origins (blocked or offline just stays empty): no load within 15 s shows the text
  useEffect(() => {
    const t = setTimeout(() => setSlow(src), 15000);
    return () => clearTimeout(t);
  }, [src]);
  const failed = slow === src && loaded !== src;
  return (
    <div className="chart-box">
      <iframe
        key={src} src={src} title="Live XAU/USD chart from TradingView (OANDA)" data-testid="tv-widget" loading="lazy"
        sandbox="allow-scripts allow-same-origin allow-popups" referrerPolicy="no-referrer"
        onLoad={() => setLoaded(src)}
      />
      {failed && (
        <p className="fallback muted" role="alert">
          The live chart could not load. TradingView may be blocked on this network or you may be offline. Your trading is not affected.
        </p>
      )}
    </div>
  );
}

type Row = { name: string; url: string };
const EMPTY: Row = { name: "", url: "" };

function LayoutEditor({ view, onSaved }: { view: ChartLayoutsView; onSaved: (v: ChartLayoutsView) => void }) {
  const initial = (): Row[] => [0, 1, 2].map((i) => view.layouts[i] ? { name: view.layouts[i].name, url: view.layouts[i].url ?? "" } : EMPTY);
  const [rows, setRows] = useState<Row[]>(initial);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const set = (i: number, k: keyof Row, v: string) => { setSaved(false); setRows((r) => r.map((x, j) => (j === i ? { ...x, [k]: v } : x))); };

  async function save() {
    setErr(null);
    const out: ChartLayoutIn[] = [];
    for (const [i, r] of rows.entries()) {
      const name = r.name.trim(), url = r.url.trim();
      if (!name && !url) continue;
      if (!name || !url) { setErr(`Layout ${i + 1}: give it both a name and a link.`); return; }
      if (!validLayoutUrl(url)) { setErr(`Layout ${i + 1}: the link must look like https://www.tradingview.com/chart/AbCd1234/`); return; }
      out.push({ name, url });
    }
    setBusy(true);
    try { onSaved(await api.setChartLayouts(out, code.trim())); setSaved(true); }
    catch (e) { setErr((e as Error).message || "Could not save."); }
    finally { setBusy(false); }
  }

  return (
    <details className="layout-edit-box">
      <summary>{view.layouts.length ? "Edit my saved layouts" : "Add my saved TradingView layout"}</summary>
      <div className="layout-edit">
        <p className="muted">Open your chart in TradingView, copy the address from the browser bar and paste it here. Up to 3. Only you can see these links.</p>
        {rows.map((r, i) => (
          <fieldset key={i}>
            <legend>Layout {i + 1}</legend>
            <label>Name<input value={r.name} maxLength={40} onChange={(e) => set(i, "name", e.target.value)} /></label>
            <label>TradingView link<input value={r.url} maxLength={200} inputMode="url" autoComplete="off" onChange={(e) => set(i, "url", e.target.value)} placeholder="https://www.tradingview.com/chart/..." /></label>
          </fieldset>
        ))}
        <label>Authenticator code (only if you signed in with an emailed code)<input value={code} inputMode="numeric" autoComplete="one-time-code" maxLength={16} onChange={(e) => setCode(e.target.value)} /></label>
        <div aria-live="polite">
          {err && <p className="err" role="alert">{err}</p>}
          {saved && <p>Saved.</p>}
        </div>
        <button onClick={save} disabled={busy}>{busy ? "Saving..." : "Save layouts"}</button>
      </div>
    </details>
  );
}

export function Chart() {
  const qc = useQueryClient();
  const [interval, setIntervalValue] = useState<string>("15");
  const dark = useDark();
  const layouts = useQuery({ queryKey: ["chart-layouts"], queryFn: api.chartLayouts });
  const view = layouts.data;

  return (
    <section className="chart-tab" aria-labelledby="chart-h">
      <h2 id="chart-h" className="sr-only">Gold chart, live</h2>
      <div className="intervals" role="group" aria-label="Chart interval, both charts">
        {INTERVALS.map((i) => (
          <button key={i.value} aria-pressed={interval === i.value} onClick={() => setIntervalValue(i.value)}>{i.label}</button>
        ))}
      </div>
      <div className="chart-panels">
        <div className="chart-panel"><h3>TradingView · OANDA XAU/USD</h3><TradingViewWidget interval={interval} dark={dark} /></div>
        <div className="chart-panel"><h3>MT5 · IC Markets (the broker goldbot trades on)</h3>
          <Mt5Chart key={interval} tf={interval === "D" ? "1D" : interval} /></div>
      </div>
      <p className="muted">Times on the chart are in your local time zone. The chart is TradingView's own and may be delayed or unavailable; goldbot does not use it to trade.</p>

      <div className="layout-links">
        {layouts.isLoading && <p className="muted" role="status">Loading your saved layouts...</p>}
        {layouts.isError && <p className="err" role="alert">Could not load your saved layouts.</p>}
        {view && view.can_edit && view.layouts.length === 0 && <button disabled>Open my TradingView chart</button>}
        {view && view.layouts.map((l) => l.url && (
          <a key={l.name + l.url} href={l.url} target="_blank" rel="noopener noreferrer">
            {view.layouts.length === 1 ? "Open my TradingView chart" : `Open ${l.name}`}
            <span className="sr-only"> (opens TradingView in a new tab)</span>
          </a>
        ))}
      </div>
      {view && view.can_edit && view.layouts.length === 0 && <p className="muted">Add your saved layout below to enable this button.</p>}

      <p className="note">Your invite-only indicators show in TradingView itself; their signals appear here once alerts are connected.</p>

      {view?.can_edit && <LayoutEditor view={view} onSaved={(v) => qc.setQueryData(["chart-layouts"], v)} />}
    </section>
  );
}
