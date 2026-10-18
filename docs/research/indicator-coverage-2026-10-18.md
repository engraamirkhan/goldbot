# Indicator coverage map (2026-10-18)

What each of the owner's three chart indicators can show, what the owner has set, and what our replica does with it. Names are
ours: **box indicator** = support/resistance channels (`sr_*`, candle colours, MAs, trend table); **structure
indicator** = market structure (`smc`: BOS/CHoCH, swings, zones, order blocks, EQH/EQL, FVG, daily/weekly levels,
pivots, golden zone, MAs); **money flow** (`mfi`). Source: the pulled study metadata (every input, default, owner value,
tooltip, plot, alert) and the owner's notes 1-4. The vendor-named copy is local only (`~/tmp/tv/coverage_local.md`).

Status words: **verified** = reproduces the pulled plot values (replica parity, `goldbot.research.replica_parity`, 15m / 1h /
4h, 11,080 / 10,614 / 10,509 bars); **rule-only** = implemented from the stated rule, not checkable against pulled
plots (drawings and tables are not in the plot stream); **display** = colour, label size, line style or position, no
information to replicate; **not implemented** = said so, with the reason.

## Totals (100 inputs over the 3 indicators)

| | inputs | verified | rule-only | display / layout (nothing to replicate) | not implemented |
|---|---|---|---|---|---|
| box indicator | 25 | 8 | 1 | 16 | 0 |
| structure indicator | 72 | 15 | 16 | 41 | 0 |
| money flow | 3 | 1 | 0 | 2 | 0 |
| **all** | **100** | **24** | **17** | **59** | **0** |

Implemented = verified + rule-only = 41 of the 41 inputs that carry information. Elements that are not reproduced are
listed under "Not reproduced" below (all are visual or low value).

## Box indicator (support/resistance channels)

| Input / element | Meaning | Owner value | Our feature | Status |
|---|---|---|---|---|
| Max width % | max box width as % of the high-low range of the last 300 bars (1 ~ a line) | 1 | `ReplicaParams.sr_width_pct`, window `sr_range_window` | verified (99.86% of 9,800 break events, 14 differ; sweep of 7 loopback/window pairs: (300,300) best) |
| Max boxes | boxes kept | 15 | `sr_max_channels` | verified |
| Loopback | bars of pivots/touches used to build the boxes | 300 (note says default 400; the study default is 300) | `sr_lookback` | verified at 300 |
| Volume candles + 4 colours | high bull / high bear / low volume / engulfing candle colours | on | `candle_class` | verified exact (volume EMA20, not "last 10 bars" as the docs say; the fitted rule reproduces every bar) |
| MA traverse | candle crosses MA10 and MA20 | on (this study) | `candle_class` code 5 (`candle_traverse`) | verified exact |
| MA display / EMA 5 / MA cross 10&20 | EMA5/10/20/50/200 lines, blue triangle on EMA10/EMA20 cross | EMA, on, on | `ma_ribbon("ema")`, `cross_over/under` | verified exact |
| Trend table + position | chart tf, H1, H4, D rated by arrows | on, bottom right | `trend_15m/1h/4h/1d` (`smc_levels.trend_table`) | rule-only, see below |
| Break triangles | green up / red down on a box break | on | `sr_break_up`, `sr_break_dn` | verified (1 of 1,801 down breaks differs on 15m) |
| Colours (13), table position, `fgcolor`, fast calc | | | | display |

Owner notes 2 and 4 (box strategy) are implemented in `goldbot/features/smc_boxes.py`, rule-only because the box
strategy is a trading rule, not a plot:

- `sr_break_depth` (signed, close through the box as a fraction of its height), `sr_break` (+1/-1 when the depth is at
  least `min_depth`, default 0.2, research range 0.2-0.3), `sr_rip` (reaction in price: the wick reached the box and the
  close stayed outside the `min_depth` band; -1 under a box, +1 over a box), `mfi_regime` attached.
- `next_box_up_atr` / `next_box_down_atr` (take-profit candidates), `broken_box_far_edge_atr` (stop candidate),
  `in_golden_zone` (no-trade filter, 50-61.8% of the 50-bar range).
- The existing verified events `sr_break_up` / `sr_break_dn` are unchanged. 30m: all replica functions are
  timeframe-agnostic and run on any bars; the pulled history has no 30m series, so 30m is untested.
- The box logic (loopback / width window) is owned by another lane; this lane only added the second parameter
  `sr_range_window` (same default) and an optional per-bar `trace` of the boxes in force (commit ff2e257).

## Structure indicator

| Input / element | Meaning | Owner value | Our feature | Status |
|---|---|---|---|---|
| Internal structure + filters + colours + size | BOS/CHoCH on the 5-bar swing | on, all | `bos_*/choch_*_internal` (8 columns with swing) | verified exact (42/121/159/120 events on 15m) |
| Swing structure | BOS/CHoCH on the 50-bar swing | on, all | `bos_*/choch_*_swing` | verified exact |
| Swing length | 50 | 50 | `ReplicaParams.swing_len` | verified |
| Show swing points | HH / HL / LH / LL labels | on | `swing_labels` (`swing_high_label`, `swing_low_label`) | rule-only |
| Strong / weak high-low | the swing extreme that did / did not get broken (strong high while the swing trend is bearish) | on | `swing_zones.strong_high`, `strong_low` | rule-only |
| Internal / swing order blocks, count 5, filter (ATR or cumulative mean range) + 4 colours | last block before each break | off, 5, ATR | `smc_boxes.order_blocks` (`ob_<scale>_bull/bear_top/btm/_n/_break`) | rule-only, computed for research |
| Equal highs / lows, bars 3, threshold 0.1 | | on | `eqh`, `eql` | verified exact (77 / 58 on 15m) |
| Fair value gaps, auto threshold | 3-bar gaps filtered by body | on, on | `fvg_bull`, `fvg_bear` | verified exact (376 / 369) |
| FVG timeframe | gaps of another timeframe on this chart | chart timeframe | `smc_levels.fvg_projected` (lower tf gaps onto higher decision bars, asof on `available_utc`) | rule-only |
| Extend FVG, label sizes, colours | | 1 bar | | display |
| Daily / weekly / monthly highs-lows + line style + colour | levels of the period | daily on, weekly on, monthly off | `smc_levels.period_levels`: running `day_high`..`month_low` and previous `pdh/pdl/pwh/pwl/pmh/pml` | previous-day high verified against the chart label (PDH 4177.455); rest rule-only. The chart labels show PREVIOUS levels (PDH/PWH); the owner's note says current; both are provided |
| Premium / equilibrium / discount | top / middle / bottom bands of the swing range | on | `swing_zones` (`premium_lo`, `equilibrium_lo/hi`, `discount_hi`) | rule-only; equilibrium band matches the image (4134-4140) |
| MA display (SMA10, SMA20, EMA50, SMA200), EMA5, MA cross 10&20, MA50/200 cross | | SMA/EMA, EMA5 off, cross off | `ma_ribbon("sma_ema")`, `smc_levels.ma_crosses` | lines verified exact; crosses rule-only (golden/death of EMA50 over SMA200) |
| Volume candles / MA traverse | as above | on / off | `candle_class` | verified exact |
| Daily pivot | floor pivots PP, R1-3, S1-3 from the previous day H/L/C | on | `smc_levels.period_levels` (`pivot_pp`, `pivot_r1..3`, `pivot_s1..3`), `pivot_features` (distance in ATR, `day_bias`, nearest level) | rule-only; PP 4149.4 and R1 4198.8 match the two short yellow lines of the chart. Day boundary: 17:00 America/New_York (21:00 UTC summer, 22:00 winter) |
| Trend table + position | as in the box indicator (one shared feature) | on | `trend_*` | rule-only |
| Golden zone, length 50, fib 50 / 61.8 | band of the 50-bar range | on | `range_f1/f2_dist_atr` | verified exact (levels match every bar) |
| 16 alert conditions (BOS/CHoCH x4 scales, OB breakouts x4, EQH, EQL, FVG x2) | | | events above; OB breakouts = `ob_*_break` | events verified; OB breakouts rule-only |

## Money flow

| Input / element | Meaning | Owner value | Our feature | Status |
|---|---|---|---|---|
| Length | candles in the money flow | 12 (normal 10-14) | `mfi_len`, `mfi` | verified exact; 10-14 is the research range |
| Bands 20/40/60/80, circles at 20/80 | extremes; bullish above 60, bearish below 40 | | `mfi_os_cross`, `mfi_ob_cross`, `mfi_cross_marker` | verified exact |
| Alerts (oversold, overbought, both) | | | the events above | verified |

Owner note 1 (money flow uses), `goldbot/features/mfi_signals.py`, rule-only: `mfi_regime` (>60 / <40 / between),
`mfi_extreme_circle`, `mfi_double_circle` (re-entry from OB/OS, the second circle), `mfi_cross50`, `mfi_pullback_hold`
(bull pullback holds above 40 and turns up; mirror), `mfi_divergence` (price swing vs line swing, stamped at the pivot's
confirming bar). Entering OB/OS as an exit signal is the same event as `mfi_extreme_circle`.

## Trend table: what is known

The graphics payload carries the table once, as its final state, never per bar: rows chart tf / H1 / H4 / D, each a double
arrow, all four down at the last bar (the study says "based on ADX"; the owner's note: two arrows / one arrow / weak /
ranging). Our rule is the Wilder DMI(14) direction (sign of +DI - -DI) per timeframe on closed bars, joined with
`available_utc`. It reproduces all four down arrows at that bar. A rule that also requires ADX >= 20 does not (ADX is
below 20 on 15m, 1h and 4h there), so no ADX gate is applied; the arrow count scale (two / one / weak / ranging) cannot
be recovered from one snapshot and is rule-only/not implemented. Capture a few snapshots over time to fit it.

## Not reproduced (visible or informational)

Label sizes and colours; the chart watermark; drawing details (FVG box extension, label font); the arrow-count scale of
the trend table (above); a 30m series (none pulled, so 30m boxes are untested). The yellow band around the 50-period
average on the structure chart is the golden zone (verified), the two short yellow lines are the daily pivots PP / R1.

## Volume source

Parity uses the charting site's OANDA volume. Our own broker bars carry tick counts: the volume rules (candle colours,
money flow) therefore run on tick flow, not on traded volume; thresholds are relative (1.5x / 0.5x the EMA20, money
flow ratios) so the shape transfers, the exact bar colours and the money-flow values will differ from the chart.
