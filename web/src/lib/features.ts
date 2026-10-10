// Plain-word labels for model feature names, so the approval card can say why the model likes a trade without the
// owner knowing the column names. The value shown next to a label is the feature's current value (the engine sends
// the model's three most important features and their values), not a contribution to the score.

const RULES: [RegExp, string][] = [
  [/^adx_?(\d+)_bucket$/, "Trend strength band (ADX $1)"],
  [/^adx_?(\d+)/, "Trend strength (ADX $1)"],
  [/^atr_?(\d+)_pct$/, "Volatility as % of price (ATR $1)"],
  [/^atr_ratio_(\d+)_(\d+)$/, "Volatility now vs usual (ATR $1/$2)"],
  [/^atr_?(\d+)/, "Volatility (ATR $1)"],
  [/^rsi_?(\d+)_extreme$/, "Overbought/oversold (RSI $1)"],
  [/^rsi_?(\d+)/, "Momentum (RSI $1)"],
  [/^mfi_slope$/, "Money-flow slope"],
  [/^mfi_zone$/, "Money-flow zone"],
  [/^bars_since_mfi_zone_cross$/, "Bars since money-flow zone change"],
  [/^mfi_?(\d+)/, "Money flow (MFI $1)"],
  [/^rv_ratio$/, "Volatility short vs long"],
  [/^rv_(\d+)$/, "Realised volatility ($1 bars)"],
  [/^parkinson_(\d+)$/, "High-low volatility ($1 bars)"],
  [/^vol_tercile$/, "Volatility regime"],
  [/^compression_\d+_\d+$/, "Range compression"],
  [/^range_width_(\d+)_atr$/, "$1-bar range width (ATR)"],
  [/^ema_slope/, "Trend slope (EMA)"],
  [/^sma50_ema50_cross$/, "50-bar averages crossed"],
  [/^dist_hma(\d+)_atr$/, "Distance from $1-bar trend line (ATR)"],
  [/^ribbon_state$/, "Moving-average ribbon direction"],
  [/^ribbon_width_atr$/, "Moving-average ribbon width (ATR)"],
  [/^bars_since_ribbon_flip$/, "Bars since ribbon flipped"],
  [/^bb_pctb_(\d+)$/, "Position in Bollinger band ($1)"],
  [/^bb_z_(\d+)$/, "Distance from Bollinger middle ($1, σ)"],
  [/^donchian_pos_(\d+)$/, "Position in $1-bar high-low range"],
  [/^dist_res_atr$/, "Distance to resistance (ATR)"],
  [/^dist_sup_atr$/, "Distance to support (ATR)"],
  [/^res_touches$/, "Resistance touches"],
  [/^sup_touches$/, "Support touches"],
  [/^levels_within_1atr$/, "Price levels within 1 ATR"],
  [/^dist_vwap(\d+)_atr$/, "Distance from $1-bar VWAP (ATR)"],
  [/^tsmom_score$/, "Momentum across timeframes"],
  [/^tsmom_agree$/, "Timeframes agree on momentum"],
  [/^structure_state$/, "Market structure (higher highs/lows)"],
  [/^gap_atr$/, "Gap size (ATR)"],
  [/^body_pct$/, "Candle body size"],
  [/^pin_bar$/, "Pin-bar candle"],
  [/^engulfing$/, "Engulfing candle"],
  [/^spread_atr$/, "Spread vs volatility"],
  [/^spread_rel_median_\d+$/, "Spread vs usual"],
  [/^tick_count_z_\d+$/, "Activity vs usual (ticks)"],
  [/^tick_vol_ratio_\d+$/, "Tick volume vs usual"],
  [/^london$/, "London session"],
  [/^newyork$/, "New York session"],
  [/^asia$/, "Asia session"],
  [/^side$/, "Trade direction"],
];

const TF_PREFIX = /^((?:m|h|d|w)\d*|\d+[mhdw])_(.+)$/;

/** "adx14" -> "Trend strength (ADX 14)"; "h1_rsi14" -> "Momentum (RSI 14), 1h chart"; unknown names are humanised. */
export function plainFeature(name: string): string {
  const tf = TF_PREFIX.exec(name);
  if (tf && tf[2]) {
    const inner = labelFor(tf[2]);
    if (inner) return `${inner}, ${tfLabel(tf[1] ?? "")} chart`;
  }
  return labelFor(name) ?? humanise(name);
}

function labelFor(name: string): string | null {
  for (const [re, label] of RULES) {
    const m = re.exec(name);
    if (m) return label.replace(/\$(\d)/g, (_, i: string) => m[Number(i)] ?? "");
  }
  return null;
}

function tfLabel(t: string): string {
  const m = /^([mhdw])(\d*)$/.exec(t);
  return m ? `${m[2] || "1"}${m[1]}` : t;
}

function humanise(name: string): string {
  const s = name.replace(/_/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : name;
}

/** Compact value: 31.2, 0.40, -0.20, 1234. */
export function featureValue(v: number): string {
  const a = Math.abs(v);
  return v.toFixed(a >= 100 ? 0 : a >= 10 ? 1 : 2);
}
