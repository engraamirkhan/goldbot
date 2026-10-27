// One trade on its bars (docs/design/trades-ux-2026-10-27.md §3.3, backlog 133): inline SVG, no chart library. Candles
// are told apart by shape (hollow up, filled down), levels by line style and a label with the word and the price, so
// nothing depends on colour. Fixed height: loading and a missing-bars state take the same box (no layout shift).
import { barIndexAt, chartLevels, spreadLabels } from "../../lib/tradeChart";
import { type Bar, price, type TradeRow } from "../../lib/trades";

type Level = { key: string; y: number; word: string; dash?: string; strong?: boolean };

const PAD_R = 118;    // room for the level labels
const PAD_T = 10;
const PAD_B = 18;

export function TradeChart({ trade, bars, loading, height, width = 360 }: {
  trade: TradeRow; bars: Bar[] | null | undefined; loading: boolean; height: number; width?: number;
}) {
  const W = width;
  if (loading) return <div className="chart-box skeleton-row" style={{ height }} aria-busy="true" aria-label="Loading the chart" />;
  const levels = chartLevels(trade);
  if (!bars || bars.length === 0) {
    return (
      <div className="chart-box chart-missing" style={{ height }} role="note">
        <strong>Bars for this period aren't stored yet.</strong>
        <ul className="small">{levels.map((l) => <li key={l.key}>{l.word} {price(l.value)} USD/oz</li>)}</ul>
      </div>
    );
  }
  const H = height;
  const lo = Math.min(...bars.map((b) => b.l), ...levels.map((l) => l.value));
  const hi = Math.max(...bars.map((b) => b.h), ...levels.map((l) => l.value));
  const span = hi - lo || 1;
  const y = (v: number) => PAD_T + (hi - v) / span * (H - PAD_T - PAD_B);
  const plotW = W - PAD_R;
  const step = plotW / bars.length;
  const x = (i: number) => i * step + step / 2;
  const cw = Math.max(1, Math.min(10, step * 0.6));
  const entryI = barIndexAt(bars, trade.opened_utc);
  const exitI = barIndexAt(bars, trade.exit_utc);
  const lv: Level[] = levels.map((l) => ({ key: l.key, y: y(l.value), word: `${l.word} ${price(l.value)}`, dash: l.dash, strong: l.strong }));
  const labelY = spreadLabels(lv.map((l) => l.y), 13, 10, H - 4);
  const name = `Chart of ${trade.timeframe ?? ""} bars: ${levels.map((l) => `${l.word} ${price(l.value)}`).join(", ")} USD per ounce`;
  return (
    <div className="chart-box" style={{ height }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label={name}>
        {bars.map((b, i) => {
          const up = b.c >= b.o;
          const top = y(Math.max(b.o, b.c));
          const h = Math.max(1, Math.abs(y(b.o) - y(b.c)));
          return (
            <g key={b.t} className="candle">
              <line x1={x(i)} x2={x(i)} y1={y(b.h)} y2={y(b.l)} />
              <rect x={x(i) - cw / 2} y={top} width={cw} height={h} className={up ? "up" : "down"} />
            </g>
          );
        })}
        {lv.map((l, i) => (
          <g key={l.key} className={`level ${l.key}`}>
            <line x1={0} x2={plotW} y1={l.y} y2={l.y} strokeDasharray={l.dash} strokeWidth={l.strong ? 1.6 : 1} />
            <text x={plotW + 6} y={labelY[i] + 4}>{l.word}</text>
          </g>
        ))}
        {entryI >= 0 && trade.entry != null && (
          <path className="marker entry" d={markerPath(x(entryI), y(trade.entry), trade.side === "long")} aria-hidden="true" />
        )}
        {exitI >= 0 && trade.exit != null && <circle className="marker exit" cx={x(exitI)} cy={y(trade.exit)} r={5} aria-hidden="true" />}
      </svg>
    </div>
  );
}

/** An arrow under a long entry pointing up, over a short entry pointing down. */
function markerPath(cx: number, cy: number, up: boolean): string {
  const d = up ? 1 : -1;
  const by = cy + d * 14;
  return `M${cx} ${cy + d * 3} L${cx - 6} ${by} L${cx + 6} ${by} Z`;
}
