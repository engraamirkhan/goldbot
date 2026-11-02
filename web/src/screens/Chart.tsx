import { useState } from "react";
import { TF_OF_INTERVAL } from "../lib/keyLevels";
import { KeyLevelsChart } from "./KeyLevelsChart";
import { Mt5Chart } from "./Mt5Chart";

// Chart tab: the broker's own MT5 chart and goldbot's key levels (ADR 0068). The TradingView widget and the saved
// TradingView layouts were retired with the TradingView chart path (ADR 0066). The tab is not in the nav (owner
// 2026-10-25: focus on the trading system; lib/route.ts); the screen is kept for later.

const INTERVALS = [
  { label: "15m", value: "15" }, { label: "1h", value: "60" }, { label: "4h", value: "240" }, { label: "1D", value: "D" },
] as const;

export function Chart() {
  const [interval, setIntervalValue] = useState<string>("15");
  return (
    <section className="chart-tab" aria-labelledby="chart-h">
      <h2 id="chart-h" className="sr-only">Gold chart, live</h2>
      <div className="intervals" role="group" aria-label="Chart interval, both charts">
        {INTERVALS.map((i) => (
          <button key={i.value} aria-pressed={interval === i.value} onClick={() => setIntervalValue(i.value)}>{i.label}</button>
        ))}
      </div>
      <div className="chart-panel"><h3>MT5 · IC Markets (the broker goldbot trades on)</h3>
        <Mt5Chart key={interval} tf={interval === "D" ? "1D" : interval} /></div>
      <div className="chart-panel key-levels-panel"><h3>Key levels · support and resistance on every timeframe (goldbot)</h3>
        <KeyLevelsChart tf={TF_OF_INTERVAL[interval] ?? "1h"} /></div>
      <p className="muted">Times on the chart are in your local time zone.</p>
    </section>
  );
}
