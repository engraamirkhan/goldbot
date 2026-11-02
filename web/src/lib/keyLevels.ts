// Pure helpers of the key-level chart (screens/KeyLevelsChart.tsx): the API timeframe of a chart interval, how thick a
// zone's band is drawn (thicker = stronger) and its label in words (side, price, timeframes, strength), so nothing
// depends on colour alone.
import type { KeyLevelTf, KeyLevelZone } from "./api";

/** The Chart tab's interval buttons (TradingView values) -> the API's timeframe. */
export const TF_OF_INTERVAL: Record<string, KeyLevelTf> = { "15": "15m", "60": "1h", "240": "4h", D: "1d" };

const TF_WORD: Record<string, string> = { round: "$ round" };

/** "2.2 years" from a year on, else "62 days". */
export function historyWords(days: number): string {
  return days >= 365 ? `${(days / 365).toFixed(1)} years` : `${Math.round(days)} days`;
}

/** Border width of a zone's band in px: 1 for the weakest to 4 for the strongest (unknown strength: 1). */
export function bandStroke(pct: number | null | undefined): number {
  if (pct == null || !Number.isFinite(pct)) return 1;
  return 1 + 3 * Math.min(Math.max(pct, 0), 100) / 100;
}

/** Fill opacity of a zone's band: stronger zones are more solid. */
export function bandOpacity(pct: number | null | undefined): number {
  if (pct == null || !Number.isFinite(pct)) return 0.12;
  return 0.12 + 0.28 * Math.min(Math.max(pct, 0), 100) / 100;
}

/** "Resistance 2412.50 · 1h 1d · strength 88%" (support below the price; "was support" when its role flipped). */
export function zoneLabel(z: KeyLevelZone, above: boolean): string {
  const word = above ? "Resistance" : "Support";
  const tfs = z.tfs.map((t) => TF_WORD[t] ?? t).join(" ");
  const pct = z.strength_pct == null ? "strength warming up" : `strength ${Math.round(z.strength_pct)}%`;
  const flip = z.flipped ? ` · was ${above ? "support" : "resistance"}` : "";
  return `${word} ${z.price.toFixed(2)} · ${tfs} · ${pct}${flip}`;
}
