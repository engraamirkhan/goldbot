// Key levels on the price chart (owner 2026-11-02: "identify key levels, support and resistance on all time frames"):
// goldbot's own bars with each account's nearest key zones as horizontal bands, the trade chart's idiom
// (trades/TradeChart.tsx: inline SVG, no chart library). A stronger zone is a thicker, more solid band; every band has
// a label in words (side, price, timeframes, strength), so nothing depends on colour. Display only: the zones never
// reach an order. Refreshed every 60 s; the engine rebuilds them at every 15-minute close. Multi-instrument (UI spec
// 2026-11-02 §5): `symbol` picks the instrument (InstrumentPicker; the URL keeps it), labels in its digits, gold's round
// zones are not served off gold, and an instrument without levels says so in words.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type KeyLevelTf, type KeyLevelZone } from "../lib/api";
import { GOLD, labelOf } from "../lib/instruments";
import { bandOpacity, bandStroke, historyWords, zoneLabel } from "../lib/keyLevels";
import { spreadLabels } from "../lib/tradeChart";
import { InstrumentPicker } from "./InstrumentPicker";
import "./trades/trades.css";

const W = 960;
const H = 420;
const PAD_R = 300;    // room for the band labels
const PAD_T = 10;
const PAD_B = 18;
const MATURE_DAYS = 540;    // goldbot/features/key_levels.py MATURE_DAYS: a 1w swing's life

type Band = { z: KeyLevelZone; above: boolean };

export function KeyLevelsChart({ tf, symbol = GOLD, onSymbol }: { tf: KeyLevelTf; symbol?: string; onSymbol?: (s: string) => void }) {
  const picker = onSymbol ? <InstrumentPicker value={symbol} onChange={onSymbol} /> : null;
  return <>{picker}<KeyLevelsBody tf={tf} symbol={symbol} /></>;
}

function KeyLevelsBody({ tf, symbol }: { tf: KeyLevelTf; symbol: string }) {
  const [account, setAccount] = useState<string | undefined>(undefined);
  const q = useQuery({ queryKey: ["key-levels", tf, account, symbol], queryFn: () => api.keyLevels(tf, account, symbol), refetchInterval: 60000 });
  const view = q.data;
  if (q.isLoading) return <div className="chart-box skeleton-row" style={{ height: H }} aria-busy="true" aria-label="Loading the key levels" />;
  if (q.isError || !view) return <p className="muted" role="status">Key levels unavailable right now. Trading is not affected.</p>;
  const acc = view.accounts.find((a) => a.account_id === view.account_id) ?? view.accounts[0];
  if (!acc) {
    return <p className="muted">{symbol === GOLD ? "No key levels yet: the engine publishes them at its next 15-minute close."
      : `No levels yet for ${labelOf(symbol)} (${symbol}): the engine publishes them once it holds enough of its bars.`}</p>;
  }

  const bands: Band[] = [...acc.above.map((z) => ({ z, above: true })), ...acc.below.map((z) => ({ z, above: false }))];
  const bars = view.bars;
  const prices = [...bars.flatMap((b) => [b.h, b.l]), ...bands.flatMap((b) => [b.z.hi, b.z.lo]), ...(acc.price != null ? [acc.price] : [])];
  const lo = Math.min(...prices);
  const hi = Math.max(...prices);
  const span = hi - lo || 1;
  const y = (v: number) => PAD_T + (hi - v) / span * (H - PAD_T - PAD_B);
  const plotW = W - PAD_R;
  const step = plotW / Math.max(bars.length, 1);
  const x = (i: number) => i * step + step / 2;
  const cw = Math.max(1, Math.min(10, step * 0.6));
  const labelY = spreadLabels(bands.map((b) => y(b.z.price)), 14, 10, H - 4);
  const name = `Key levels on ${view.timeframe} bars${symbol === GOLD ? "" : ` of ${labelOf(symbol)}`}: ${bands.map((b) => zoneLabel(b.z, b.above, symbol)).join("; ")}`;

  return (
    <div className="key-levels">
      {view.accounts.length > 1 && (
        <label className="small">Account{" "}
          <select aria-label="Account" value={acc.account_id} onChange={(e) => setAccount(e.target.value)}>
            {view.accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.account_id}</option>)}
          </select>
        </label>
      )}
      <div className="chart-box" style={{ height: H }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={name} preserveAspectRatio="none">
          {bands.map((b) => {
            const top = y(b.z.hi);
            const h = Math.max(2, y(b.z.lo) - top);
            return (
              <g key={`${b.above}-${b.z.price}`} className={`zone ${b.above ? "res" : "sup"}`} data-price={b.z.price}>
                <rect x={0} y={top} width={plotW} height={h} strokeWidth={bandStroke(b.z.strength_pct)}
                  fillOpacity={bandOpacity(b.z.strength_pct)} strokeDasharray={b.above ? undefined : "6 3"} />
              </g>
            );
          })}
          {bars.map((b, i) => {
            const up = b.c >= b.o;
            const top = y(Math.max(b.o, b.c));
            return (
              <g key={b.t} className="candle">
                <line x1={x(i)} x2={x(i)} y1={y(b.h)} y2={y(b.l)} />
                <rect x={x(i) - cw / 2} y={top} width={cw} height={Math.max(1, Math.abs(y(b.o) - y(b.c)))} className={up ? "up" : "down"} />
              </g>
            );
          })}
          {acc.price != null && <line className="price-now" x1={0} x2={plotW} y1={y(acc.price)} y2={y(acc.price)} strokeDasharray="2 3" />}
          {bands.map((b, i) => (
            <text key={`l-${b.above}-${b.z.price}`} className="zone-label" x={plotW + 6} y={labelY[i] + 4}>{zoneLabel(b.z, b.above, symbol)}</text>
          ))}
        </svg>
      </div>
      <p className="muted small">
        Solid bands are resistance above the price, dashed bands support below; a thicker band is a stronger zone.
        {acc.zones_total != null && <> Showing the {bands.length} strongest nearby of {acc.zones_total} zones.</>}
        {acc.history_days != null && <> Built on {historyWords(acc.history_days)} of history.</>}
        {acc.history_days != null && acc.history_days < MATURE_DAYS &&
          <> That is short of {MATURE_DAYS} days, so weekly swings and the strength ranking see less than research does.</>}
        {view.bars.length === 0 && <> Bars for this timeframe aren't stored yet.</>}
        {acc.stale && <> <span className="badge" role="status">STALE: last built {new Date(acc.as_of).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}</span></>}
      </p>
    </div>
  );
}
