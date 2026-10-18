# Small-account playbook: trading XAUUSD at £1,000 without more capital or more risk per trade

Research director, 2026-10-18. Owner brief: no deposit, no rise in `risk_per_trade` (0.5% = £5), keep profits and
trade count. Nothing here changes risk config, spends a trial or reads the holdout. The volatility figures are
volatility only: no returns, no signals. They come from Dukascopy 1m mid bars over the last 90 days to 2026-10-05,
the same window and code as `atr-measured-2026-10-18.md` (scratch script, not committed). **No strategy has passed
the gates yet** (best gross +0.061 R, R-06), so every £ figure below is arithmetic on stated assumptions, not a forecast.

## 1. The constraint, exactly
- Contract: 100 oz a lot, minimum and step 0.01. So 0.01 lot = 1 oz, and **$1/oz = £0.752** at GBPUSD 1.33. The
  gate reads `volume_min`, `volume_step` and `trade_contract_size` from `symbol_info` (`mt5_adapter.py:231`) and
  sizes in `sizing.size_lots` (`gate.py:640-657`). The IC Markets spec sheet gives 100 oz, a 1 oz minimum and a stops
  level of 0. I found no micro gold symbol.
- At £5 risk the gate accepts **0.01 lot for a stop up to $7.98** (its 1.2x tolerance, £6.00), **0.02 lot up to
  $3.32** and **0.03 lot up to $2.21**. The min-lot exception (`min_lot_risk_cap`, up to 1%) would raise the risk per
  trade, so this playbook excludes it.
- Round-trip cost is $0.71/oz. In R that is 0.06 R at a $12 stop, 0.09 R at $8, 0.21 R at $3.3 and 0.32 R at $2.2.
  A tighter stop is never free.

**Measured fit today** (the share of 15m bars where the stop is at most $7.98):

| Stop | Median $ | Fits 0.01 lot | Who uses it |
|---|---|---|---|
| 1.5 x ATR15 | 12.0 | **2%** | H-17 V0/V1, R-01 |
| 1.0 x ATR15 | 8.0 | 49% (10-11 UTC 74-76%; 14-16 UTC 6-17%) | R-02 session_open, breakout on 15m |
| 0.75 x ATR15 | 6.0 | 85% (07-12 UTC 92-98%; 14-16 UTC 55-62%) | none yet |
| 1h / 4h / 1d at 1.0-1.5 ATR | 18-131 | **0%** | H-01, H-04, tsmom, trend |
| Signal-bar extreme (15m) + $0.34 | 3.37 (p75 5.88) | 86% (49% fit 0.02 lot) | proposed |
| Last 5 x 1m swing + $0.34 | 2.05 (p75 3.52) | 97% (73% fit 0.02 lot, 53% fit 0.03) | proposed |
| Last 6 x 5m swing + $0.34 | 4.82 (p75 8.36) | 73% | proposed |

1m ATR14 is $1.82, 5m $4.38, 15m $8.01. Median daily range is $75.7 (p75 $102, p90 $125), and 20% of days are at
least 1.5x the median.

**Finding.** At their pre-registered stops, the gate would refuse almost every trade from every live hypothesis in
the Q1 2027 queue (H-01, H-04, H-17, tsmom) on this account. Research is measuring edges the account cannot take.

## 2. Every idea assessed
Columns: (a) stop / risk per 0.01 lot; (b) trades/day; (c) expected R and £ net; (d) code and honesty.

| Idea | (a) | (b) | (c) | (d) | Verdict |
|---|---|---|---|---|---|
| **Precision entry**: signal on 15m/1h, entry on a 1m/5m retest, stop beyond 1m/5m structure | $2-6: 0.01-0.03 lot at at most £5 | Takeable share rises from 2-49% to about 90% of signals; fill rate perhaps 50-70% | Same £5 per R. Cost 0.15-0.25 R, so it needs about 0.3 R gross. A runner pays more R per $ moved | Entry layer in labels, shadow and engine; real broker SL; size on stop + spread + slippage | **Top 1** |
| **Runner + risk-free pyramid** on opportunity days | First unit £5; the add has its own £5 only after the first stop is at breakeven | At most 1 a day, top-score days only | The only route to a 10 R (£50) day; arithmetic below | Pyramid labels; gate allows the add only with `project_open_risk` and heat on (stricter) | **Top 2** |
| **Lot-fit routing and research overlay**: use the ATR stop if it fits, else precision, else skip; the low-ATR windows 07-12 and 20-23 UTC fit best | Per §1 | Takes the 49% of 1.0-ATR signals that fit now | Prevents a false £ claim. Low-ATR windows mean smaller moves, which works against opportunity days | Report columns plus a routing rule; 0 trials | **Top 3** |
| Session or low-vol selection alone | Fits up to 98% at 0.75 ATR in 07-12 UTC | Fewer, quieter trades | Lower $ per trade. Asia spread is wider ($0.76 vs $0.57 Dukascopy) | Filter only | Inside Top 3, not alone |
| VWAP-band or liquidity-level stops | Unmeasured; probably like 5m swings | Same as precision entry | Same as precision entry | One stop rule per trial, to keep K down | One variant of Top 1 later |
| Time stop before the price stop | No change: the gate sizes to the real stop | Same | May cut the average loss; does not let a trade fit | `max_bars` variant | Low value |
| Selecting trades by E£ under the daily cap | Same | The supervisor's 1.5% (£15) allows 3 losses; rarely binds at 1-3 trades | About £0 now | Allocator ranking | Later |
| Clustering correlated signals into one position | Same | Fewer, duplicate bets merged | About £0 with 1-2 families | Heat cap (BACKLOG 19) already counts same-side positions | Safety item, not £ |
| Smaller contract (micro symbol) | 0.001 lot would let 1h/1d stops fit | Many more | Large if it exists | One `mt5.symbols_get("*XAU*")` listing on the VPS (SRE, 5 min). No code: `symbol_info` already drives sizing | Check; unlikely at IC Markets |
| Risk measured to the real stop incl. slippage | Adds $0.15-0.30 at $3 (5-10% more real risk) | Same | Honest £ | `sizing.stop_distance` floors at stops level + spread, but has no slippage term. Add it for precision entries (stricter) | **Required by Top 1** |

## 3. Top 3 ranked by (expected £/day x feasibility)
Assumptions shared by all three: £5 a trade, no edge yet measured. "If net E = x" is a condition, not a claim.

**1. Precision-entry execution layer (H-25).** Keep the signal; change only the entry and the stop. When a
15m signal fires, place a limit at the retest of the signal-bar breakout level, valid for 15 one-minute bars. The
stop goes beyond the last 5 one-minute bars' extreme, plus spread and a $0.15 slippage buffer. It is floored at
0.25 x ATR15 and capped at min(0.75 x ATR15, the 0.01-lot fit of $7.98); a wider structure means skip. Size by the
gate. Effect: H-17 goes from 2% to about 90% takeable, with 0.02 lot on about half of fills. At 1.5-2.5 trades/day,
**£0.75-1.25/day if net E = +0.10 R, £1.5-2.5/day if +0.20 R** (£16-52 a month). Risk: on today's ATR the tight stop
triples cost in R, and a limit fill selects adversely. Code: `entry_mode`, `entry_tf`, `entry_wait_bars`,
`stop_mode`, `swing_bars`, `stop_floor_atr`, `stop_cap_atr` as `optional_config` (absent = off, ids unchanged),
1m-resolution entry and barrier simulation in `labels/triple_barrier.py` / `exit_policy.py`, the same rule in
`engine/shadow.py` and `engine/runner.py`, the slippage term in sizing. Reviews: quant-reviewer and
trading-safety-reviewer.

**2. Runner + risk-free pyramid on opportunity days (H-26).** Only on H-16 top-quintile days (about 4 a month).
Take the first unit with H-25. At +1 R, move its stop to breakeven, then add one unit on a 5m retest with its own
swing stop and trail both at 1.0 ATR15. Worked day: two 0.02 lot legs capturing $25 and $15/oz = $80 - $2.84 cost =
**£58**. That is under one third of a p75 day's range. Bad days: -£5 (first stop) or 0 / -£5 (breakeven then the
add stopped), so at most -£10 including gap risk. Break-even condition: p x £55 > (1 - p) x £7, so it needs **p > 11%
of top days**. If p = 20%, EV is about £6.4 per top day, about £1.2/day averaged, and 1 day a month at £50 or more.
Code: pyramid state in the labels and the engine, gate support for an add (`max_positions_per_account` 2 already).
It runs only with `project_open_risk` and `heat` enabled, so it is stricter than today. Depends on H-25 and on H-16
continuing.

**3. Lot-fit routing and research overlay (H-27, 0 trials).** Every research report adds: share of trades fitting
£5 at today's stop/price ratio (stop/price at most 0.19% = $7.98 at $4,150), lot tier, and net R on the fitting
subset. The engine routing becomes: the ATR stop if it fits, else H-25, else skip (reason recorded). Effect: no new
edge, but no £ figure will again be claimed for trades the account cannot take, and today's fitting 49% of 1.0-ATR
15m signals stay in. Code: `research_pass.py` report columns, a `min_lot.py` reuse, a routing branch before
`RiskGate.check`.

## 4. Pre-registered tests (proposed; freeze with Q1 before 2027-01-01)
Q4 2026 is spent (20/20). Q1 2027 has 3 free reserve slots in the open budget. Nothing can run until the code
merges to `main` and the rows are frozen in `preregistration-2027Q1.md`.

**H-25 (1 trial, Q1 reserve slot; row O-11).** Run on the H-17 family (BACKLOG 31) as **V2**: V1's signals, exits
and window, plus the precision entry.
```
gh workflow run research.yml --ref main -f specialist=<H-17 family> -f from_year=2010 -f to_year=2025 \
 -f rule_ref=preregistration-2027Q1.md#O-11 -f rationale="H-25 precision entry on H-17 V1 signals" \
 -f variants='[{"entry_mode":"retest","entry_tf":"1m","entry_wait_bars":15,"stop_mode":"swing","swing_bars":5,
   "stop_buffer_usd":0.49,"stop_floor_atr":0.25,"stop_cap_atr":0.75,"fill_rule":"trade_through_1tick"}]'
```
**Reading rule.** Continue only if all hold:
- gross t ≥ 2.0 and net mean R > 0 at **today's cost-to-ATR ratio** (0.09 ATR15 round trip). Real historical $
  costs are reported but are not in the rule: 2010-24 ATRs were $2-5, which makes tight stops look worse than they
  are today.
- fill rate ≥ 50%;
- V2 net R per signal (misses count 0) ≥ V1's;
- ≥ 3 positive net years;
- DSR reported at K = registry count.

Fail: precision entry is retired for H-17, with the reason. Holdout scoring (real costs, the current regime) once,
only after a pass.

**H-26 (1 trial, Q2 2027; only if H-25 and H-16 continue).** Variant: H-25's config plus
`{"pyramid":{"max_adds":1,"add_after_r":1.0,"first_stop_to":"breakeven","add_entry":"retest"},"trail_atr":1.0,
"trail_after_atr":1.0,"target_atr":10.0,"opp_quintile_min":5}`.

**Reading rule.** Continue only if all hold:
- net mean £ per top day > 0, with t ≥ 2 on ≥ 600 top days;
- share of top days ≥ +10 R is at least 10%;
- no simulated day below -2.2 R.

**H-27 (0 trials).** Overlay, as H-19. It needs code before any Q1 report is read.

**Owner and PD asks:**
- SRE: run the VPS symbol listing for any smaller XAU contract.
- Strategy-researcher: write rows O-11 and H-25/26/27 into `hypotheses.md`, with code owners, by 2026-11-15.
- PD: schedule the H-25/H-27 code lane before the Q1 freeze.

## 5. Honest verdict
**Without more capital or more risk, £50 a day is not reachable as an average.** £50 is 10 R at £5. The ideas above
make the account able to trade at all in today's volatility (from about 0-2% takeable to about 90%). They also turn
rare trend days into 10 R days through a runner and a risk-free add. If an edge of 0.1-0.2 R net exists, which is
not yet shown, a realistic outcome is **£1-3 a day on average and roughly 1-2 days a month at £30-60**: about 2-6% a
month. That is the ceiling of what these levers can honestly buy. Capital (CFO option 2) remains the only lever that
moves the average.

Sources: [IC Markets commodity specification sheet](https://cdn.icmarkets.com/uploads/Commodity+Specification+Sheet.pdf)
(100 oz contract, 1 oz minimum, stops level 0; an MT4-era sheet, so confirm in the terminal);
`docs/research/atr-measured-2026-10-18.md`; `docs/finance/north-star-feasibility-2026-10-18.md`.
