# Measured ATR(14), XAUUSD (BACKLOG 22), 2026-10-18

**Verdict.** Neither doc is wrong on units. Both are in $/oz on mid prices. The $1.80 is a long-history average that is
stale; the ~$7.3 is a derived estimate that is about 9% low for 15m today. The current 15m ATR(14) is about **$8**
(last 90 days, median), and the 1d ATR is about $88, not $70.

## Method
- Source: release `data-v1`, XAUUSD 1m Parquet 2020-01-01 to 2026-10-05 (2,298,224 bars; the release was last updated
  2026-10-01, so the last five days of September to 5 October are the newest data). Downloaded with `gh release
  download`; nothing is committed.
- Same code as the engine and research: `goldbot.data.resample.resample_bars` -> `mid()` (bid+ask)/2 ->
  `goldbot.features.technical.atr` (Wilder, n=14, `ewm(alpha=1/14)`). Daily bars use the feature-day (NY 13:30 anchor).
- Units: price dollars per oz (1 oz = 0.01 lot of 100 oz). Per-bar ATR value, every bar after warm-up.
- Sessions by UTC bar-open hour: Asia 21-07, London 07-13, NY 13-21 (approximate buckets, no DST shift).
- Last 90 days = 90 days before the last bar in the data.

## ATR(14) median, with p25 / p75 / p90 ($/oz)
| Year | 15m | 1h | 4h | 1d |
| --- | --- | --- | --- | --- |
| 2020 | 2.59 (1.92/3.63/4.87) | 5.40 (4.28/7.13/9.16) | 10.47 (8.55/13.84/17.52) | 25.28 (21.35/30.67/38.82) |
| 2021 | 1.94 (1.54/2.60/3.32) | 4.15 (3.59/5.01/6.06) | 8.08 (7.28/9.64/11.31) | 19.91 (18.85/22.42/25.33) |
| 2022 | 2.15 (1.67/2.78/3.58) | 4.55 (3.84/5.42/6.40) | 8.95 (7.73/10.29/11.46) | 21.33 (19.38/23.97/26.66) |
| 2023 | 1.89 (1.47/2.49/3.20) | 4.13 (3.42/4.90/5.85) | 8.19 (6.85/9.73/11.10) | 20.17 (17.47/23.27/26.35) |
| 2024 | 2.74 (2.14/3.59/4.52) | 5.91 (4.89/6.99/8.17) | 11.65 (9.91/13.53/15.74) | 28.50 (25.61/32.56/34.31) |
| 2025 | 4.97 (3.56/7.06/9.63) | 10.25 (7.59/14.20/19.59) | 19.72 (14.76/27.86/36.76) | 46.97 (35.02/64.33/78.94) |
| 2026 (to 5 Oct) | 9.63 (7.80/13.04/18.43) | 20.29 (17.33/26.01/36.44) | 40.90 (36.18/50.41/74.82) | 94.87 (86.79/132.42/162.67) |
| **Last 90 d** | **8.01 (6.95/9.52/11.54)** | **17.73 (15.98/19.70/21.36)** | **36.61 (34.49/38.99/41.19)** | **87.63 (82.45/91.84/94.67)** |

## 15m ATR(14) by session, median (p25 / p75 / p90)
| Window | Asia | London | NY |
| --- | --- | --- | --- |
| 2020-2026 | 2.09 (1.54/3.46/7.52) | 2.42 (1.83/3.65/7.33) | 3.34 (2.49/4.78/8.77) |
| 2026 | 9.29 (7.50/12.89/18.67) | 8.73 (7.48/11.24/15.43) | 10.88 (8.74/14.18/20.05) |
| **Last 90 d** | **7.72** (6.70/9.00/10.88) | **7.56** (6.70/8.43/9.69) | **9.05** (7.81/10.91/12.84) |

## Which doc is wrong, and why
- **$1.80** (`docs/research/candidates/scalping-2026-10-17.md` line 26, quoting the "normal-day baseline" of
  `docs/research/holiday-list-measurement-2026-10.md` line 32). It is a mean over the whole multi-year history (n=3,904
  weekdays). Gold's volatility has risen about 4x since 2023: the 2021-2023 yearly medians are $1.9-2.2, so $1.80 is
  right for that regime and wrong for today ($8). Cause: a regime/window mismatch, not units. The same stale baseline
  also understates the spread-to-ATR ratio's meaning: spread/ATR 0.19 is for an ATR of $1.80; at $8 it is about
  0.04. (The cost-per-R figures in that doc, "0.27 R per round trip", are therefore overstated for today's volatility.)
  I could not find `docs/research/candidates/next-2026-10-18.md` in this checkout or on origin/main; the $1.80 figure
  is in the scalping candidate file and the holiday-list note.
- **~$7.3** (ADR 0004 assumption table). It is the ADR's own estimate: $70 (1d, from ~22% annualised vol) / 9.6 =
  $7.29. Units and period are consistent (ATR(14), $/oz). It is an estimate and the ADR says so. Against measurement:
  15m $7.3 vs $8.0 measured (-9%), 1h $14.6 vs $17.7 (-18%), 4h $29 vs $36.6 (-21%), 1d $70 vs $87.6 (-20%). The
  daily vol was understated (measured daily ATR/price is about 2.1% at $4,150, so realised vol is higher than 22%),
  and the sqrt-time scaling is slightly off (measured 1d/15m = 10.9, not 9.6). Correction needed in ADR 0004: replace
  the estimate row with the measured last-90-day medians above (15m 8.0, 1h 17.7, 4h 36.6, 1d 87.6) and re-run the
  equity thresholds; they scale linearly with ATR, so every threshold rises 9-21%.
- **No unit error** (points vs $/oz): XAUUSD price is quoted to 0.01 and the bars hold dollars per oz.

## Sizing consequence
0.01 lot = 1 oz, so a 1.5 x ATR15 stop at the measured $8.0 is $12.0, about **GBP 9.0** at GBPUSD 1.33 (0.9% of
GBP 1,000, before spread). At the stale $1.80 it would be $2.70 (GBP 2.0, 0.2%), at ADR 0004's $7.3 it is $10.95 (GBP 8.2).
So the minimum lot already risks roughly the whole 0.5-1% budget on a 15m stop, and the 4x error would have hidden
that. An ATR in the p90 of the last 90 days ($11.5) gives a GBP 13 stop (1.3%).

Raw tool: scratch script only (not committed); rerun with `resample_bars` -> `mid` -> `atr` over the release files.
