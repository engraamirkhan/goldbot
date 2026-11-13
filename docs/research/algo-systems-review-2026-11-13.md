# Algo trading systems review (13 Nov 2026)

Three parallel surveys with cited sources; the full texts are in the `algo-systems-review-2026-11-13/` folder beside this file.
This page is the synthesis and what goldbot does with it.

## 1. What the evidence says is achievable

| Source | Realistic result |
|---|---|
| Best fund ever (Medallion) | ~39 %/yr net |
| AQR managed futures since 2010 | ~4 %/yr, Sharpe 0.2 |
| Practitioner benchmark (Carver) | Sharpe ~0.4 per instrument system, ~1.0 for a portfolio |
| Verified retail gold/FX systems | 1-2 %/month at ~10 % drawdown; 2-4 % at 20-25 %; 5-10 %/month comes with 35-50 % drawdown and short lives |
| Retail CFD accounts (ESMA/broker disclosures) | 74-89 % lose money |
| Prop challenges (Topstep 2025) | 16.8 % pass |
| Published anomalies | 65-85 % fail replication |
| Quantopian, 888 algorithms | backtest Sharpe explained 1-2 % of live Sharpe |

20-30 %/week compounds to ~13,000x a year. No verified system anywhere is close. On £10k under the £50/day cap,
20 %/week needs ~£170 at risk per trade at our best plausible edge, which one stop would breach. Short weeks at that
rate happen with high leverage on small accounts, with a high chance of ruin.

## 2. What survives, across all three surveys

- **Holding period:** hours to days on 15m-4h. Targets at least 3-5x round-trip cost. Sub-30-minute scalps fail far
  more often, which matches our break-even 1m/5m book (93 % of gold fills).
- **Risk:** stop on every trade, fixed 0.5-1 % risk, never increasing lots after losses. Every grid or martingale system
  dies on a 2-6 % straight move (gold fell 9.8 % in one day on 30 Jan 2026).
- **Cost budget:** cost at most a third of the gross edge (Carver's "speed limit"). Our 1m/5m arms fail it.
- **Fewer, pooled signals:** one combined forecast beats many separate arms. Real edges are 0.03-0.10 R per trade.
- **News:** flat through tier-1 releases, or trade the continuation *after* the release, not the spike.
- **Regime test:** a gold system must also make money in 2019-21 or on the short side. Long-only records since 2022
  mostly reflect the gold bull market.

## 3. What goldbot already tested (don't repeat)

- Daily cross-asset trend with vol targeting (POS-1 A1, 6 Nov): CFD financing took 70 % of gross; net Sharpe 0.09; closed.
- Directional session-clock ideas (London open, overlap, Asia-range break, fixes): read on 2010-17, best t 2.78; no new cards.
- Exit geometry (H-82) and meta-labelling on raw signals (R-20): retired.

## 4. Changes to goldbot's evaluation

Our bar (t ≥ 3.6 at 300 fills) only detects edges ≥ ~0.28 R, so a real 0.10 R edge passes ~2 % of the time.
Lowering the bar would only admit more break-even arms. Fix: fewer, slower, pooled hypotheses; reads at fixed
checkpoints (300 / 1,000 / 2,000 fills); clustered K and BHY false-discovery control.

## 5. Plan

| # | Line | Status |
|---|---|---|
| 1 | Gold-only demo; no new 1m arms; slow arms not retired for trading too rarely; MFE/MAE logged per fill | lane F, building |
| 2 | Cost budget gate: no arm runs with cost > 1/3 of its gross edge (audit live arms first) | next |
| 3 | 15m-4h gold holds with targets ≥ 3-5x cost; regime test 2019-21 and short side | next (pre-register) |
| 4 | Post-release continuation on FOMC/CPI/NFP (enter 1-5 min after, hold 15-120 min), pooled | needs consensus-forecast data; data-engineer to find a free source |
| 5 | Pooled test of signal arms vs their random controls (~t 3.8 across accounts, found after the data was seen) | pre-register, forward only |
| 6 | Survivor reads Z10D/Z9D/Z5P at 300 fills; H-90 London on 17 Dec | running |
| 7 | Gold overnight drift and first-hour→last-hour momentum | check against prior session reads before any card |

Sizing beyond the demo caps and any live money stay the owner's decisions under ADR 0004; nothing here waits on the owner.
