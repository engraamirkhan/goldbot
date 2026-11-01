# Pre-registration: retail gold setups not yet screened (strategy researcher, 2026-11-01)

Frozen by commit before any 2010-2017 outcome of these rules is computed (no event count, no R). Owner request
2026-11-01: "find strategies, setups, signals to add more"; brief: the setups gold retail traders use that the repo
has not screened, defined mechanically, at most 6 rules, matched random control, one look each on 2010-2017, pass bar
per `growth-sprint-2026-11-01.md` section 2. Results go to `retail-setups-screen-2026-11.md`. Code:
`docs/research/candidates/retail/retail_screen.py` (committed after this file, before the run).

## 0. Coverage check first: 7 of the 9 requested setups were already screened

Checked against `hypotheses.md` (A15 "retired by this programme" list, R-01, R-09, R-14, R-15, H-80) and the
time-structure slices. They are **not re-run** (no new mechanism, no new data):

| Requested setup | Already screened as | Evidence (2010-2017 unless stated) | Status |
|---|---|---|---|
| FVG fills (magnet / target) | slice E, E-2 (`time-structure/smc-orderblocks.md` 5.3) | midpoint retrace lift +2 pts vs random level (< 5), no range lift beyond the displacement bar | closed |
| Order-block retest | slice E, E-1 (15m, `smc-orderblocks.md` 5.2) | gross +0.112 R (t 2.84) but over two random controls only +0.04..+0.08 R, CI includes 0; net -0.31 R | closed (kill rule b) |
| Asia high/low sweep into London | H-48 / trial #88 (R-14); E-3; H-76 (A-1 failed Asia-range fade) | #88 gross -0.028 R, t -1.17, n 3,210; key-levels slice: sweep-and-reverse equal to control | retired |
| Round-number magnet | slice G, G-7 and 3.1 (`time-structure/key-levels.md`) | first-touch probability of $10/$50/$100 levels within 1.5 pts of random levels: no magnet; $10 1h break diff +0.059 [-0.006, +0.129] | closed (feature only) |
| VWAP-band reversion | R-01 (#3, #5, #10, #12); `time-structure/oscillators-trend-volume.md` s.5 | day/London/NY VWAP stretch fades gross -0.05..-0.53 R, 0-2 of 8 years | retired |
| RSI / MFI divergence at session extremes | `oscillators-trend-volume.md` (RSI14 15m -0.23 t -2.6, 1h +0.64 t 2.1, 4h -1.27: sign flips); H-40 `mfi_signals` OOF AUC 0.503 | closed as rules; "at session extremes" is a location filter on a closed signal, and the key-levels slice found session extremes no better than random levels | closed |
| Owner's box break + close + MFI | H-50 / trial #89 (R-15), H-80 R2 | gross +0.030 R t 1.61 n 5,684, net -0.297 R; exits R2 failed | retired |

Untested, and screened here: the **ICT silver bullet** (a time window plus sweep plus FVG combination; its parts were
tested alone, the combination was not), **supply/demand zones** (dropped in slice E as "duplicates", never measured),
and, added by me, the **inverse FVG** (a 2024-26 retail/ICT staple, in no slice). Three rules, not six: the other candidates are
neighbours of closed items and would only add K.

**Prior.** No peer-reviewed evidence for any of the three (grade D: Huddleston / ICT video material, retail
supply-demand courses). The only economic story is Osler (2003, *J. Finance* 58(5); 2005, *J. Int. Money Finance* 24;
FX dealer order books, gross): resting orders cluster at visible levels, so a level made by a fast move may hold
resting liquidity. Every level-based test in this programme so far read "no better than a random level". Stated prior
of a PASS: about 3% per rule.

## 1. Common (all three rules)

| Item | Frozen value |
|---|---|
| Window | 2010-01-01..2017-12-31, `raw/xauusd_1m_dukascopy_2010..2017.parquet` (local); the loader asserts no 2018+ bar. 2018-01..2025-09 and the holdout 2025-10..2026-09 are not read |
| Prices | 1m mid = (bid + ask) / 2. 5m / 15m / 1h bars resampled from 1m mid (left-labelled, UTC-aligned); a bar is known at its close. ATR_tf = ATR(14), simple mean of true range of that timeframe's bars, last bar completed at or before the decision |
| Clock | New York local times via `America/New_York` per date (DST-correct) |
| Entry | a **limit** order at level L, live from the first 1m bar after the setup bar closes until its expiry. Fills in the first minute whose low <= L (long; high >= L short), always at L (no price improvement) |
| Exit | stop and target from the fill minute: in the fill minute only the stop counts; a minute holding both counts as the stop; a stop gapped through exits at the minute's open (worse); a target exits at the target; else the close of the time-exit minute |
| R | net USD/oz over the stop distance \|L - stop\| |
| Cost | `config/cost_of_record.yaml` **cor-2026-10-31.2**, ICM cell of the fill session (asia < 07:00, london 07:00-12:30, newyork >= 12:30 UTC): **1.5 x round_trip_usd**, plus **1.5 x overshoot_usd** on a stop exit; long swap $0.60/oz per 22:00 UTC weekday rollover held (Wednesday x3), shorts 0 |
| Cost gate | a setup is skipped when 1.5 x round_trip_usd of its setup-bar session > 0.20 x its stop distance (the expected-move-vs-cost rule for short stops). Twins inherit the real setup's stop distance, so the gate is identical |
| Positions | one real position per rule at a time: a setup created while a real trade is open is skipped (and counted) |
| Random twin | 4 per real setup, seeded from sha256(rule, setup time, i): same stop distance, same limit offset (distance from the setup-bar close to L, placed on the twin's pull-back side), same target multiple, same expiry and time-exit lengths, fair-coin side, setup time a uniform random bar close of the same timeframe in the window stated per rule. A twin that never fills scores 0, as does a real setup that never fills |
| Statistics | day-block bootstrap, 10,000 resamples, seed 20261101 |
| Reported | setups, gated, skipped (busy), fills, n, mean gross R, mean net R, sd, 90% LB, twin mean, real minus twin, one-sided p, years positive, long/short split, stop / target / time shares |

## 2. Rules

### RS1 ICT silver bullet, New York AM (5m)

- Reference range: 5m bars opening 00:00-09:25 ET (high refH, low refL). Weekdays only.
- Candidate FVG: three 5m bars j-2, j-1, j with bar j opening 10:00..10:55 ET. Bullish: low_j > high_{j-2}, gap >=
  0.1 x ATR15. Bearish mirrored.
- Liquidity condition (the "sweep"): bullish only if the lowest low of the 5m bars opening 09:30 ET .. bar j is below
  refL; bearish only if the highest high in that span is above refH.
- First qualifying FVG of the day only. Limit at the gap's near edge (bull: low_j; bear: high_j), live until 11:00 ET.
  Stop: bull min(low_{j-2}, low_{j-1}) - 0.1 x ATR15; bear mirrored. Target 2 R. Time exit 12:00 ET.
- Twin window: a random 5m close in 10:00..10:55 ET bars of the same day; its limit expiry is 11:00 ET and its time
  exit 12:00 ET, as the real rule.

### RS2 Supply/demand zone, first retest (1h)

- Base bar: |close - open| <= 0.5 x (high - low) and high - low <= 1.0 x ATR1h. Departure bar d (demand): close - open
  >= 1.5 x ATR1h (ATR of the bars before d) and close_d > the highest high of the base. Base = the longest run (1-3)
  of consecutive base bars ending at d-1; at least 1.
- Zone: proximal = highest max(open, close) of the base bars; distal = lowest low of the base. Created at the close of
  d. Skipped if the stop distance exceeds 3 x ATR1h.
- Limit buy at proximal, live 120 1h bars; cancelled by any 1h close below distal before the fill; consumed at the
  first fill. Stop distal - 0.1 x ATR1h. Target 2 R. Time exit 48 hours after the fill. Supply mirrored.
- Twin window: a random 1h close on the same 22:00-22:00 UTC trading day. The twin limit has no invalidation close
  (no zone), only the 120-bar expiry.

### RS3 Inverse FVG (15m), London and New York

- FVG at bar j as in RS1 on 15m bars, gap >= 0.25 x ATR15 (slice E's floor). Bullish zone [high_{j-2}, low_j].
- Inversion: the first 15m bar m in j+1..j+32 whose close is beyond the far edge (bull gap: close_m < high_{j-2})
  creates a bearish IFVG at the close of m, if m closes 07:00..20:00 UTC. Bear gaps mirrored into bullish IFVGs.
- Sell limit at high_{j-2} (the gap edge now overhead), live 16 bars after m. Stop: highest high of bars j..m + 0.1 x
  ATR15. Target 2 R. Time exit 32 bars (8 h) after the fill.
- Twin window: a random 15m close in 07:00..20:00 UTC of the same day.

## 3. Reading rule (written before data)

**PASS (per rule, all must hold):** (a) n >= 200 fills; (b) net mean R day-bootstrap 90% LB > 0; (c) real minus
mean-twin R per setup day, one-sided bootstrap p x 3 (Bonferroni over this lane's 3 rules) < 0.10; (d) >= 5 of 8
calendar years with net mean R > 0. Anything else is FAIL. n < 200 is reported as FAIL (underpowered), not as a lead.

- **FAIL:** retire the rule as specified (new R row in `hypotheses.md`); do not re-test on XAUUSD without new
  evidence (a mechanism or data source not used here). No neighbour variants (other windows, other R multiples, other
  gap floors) are run from this lane.
- **PASS:** earns only a confirm on 2018-01..2025-09 at the program-wide deflated bar (t about 4 at registry K, DSR >=
  0.95, >= 200 trades). That confirm is a registry trial, and the slot is a PD + PO budget decision; it is flagged,
  not taken here. No demo arm before a confirm (owner rule 10-30, "qualify before demo").
- Gross R and the twin difference are reported beside the verdict so a FAIL by cost is distinguishable from a FAIL
  for no edge; they do not change the verdict.

## 4. Multiple testing and budget

K +3 (one look per rule). Program K recorded on main 2026-11-01: 3,014 (hypotheses.md, after C1-C4) -> **3,017**;
parallel lanes add their own. Screen only: **0 registry trials, 0 budget slots**; `trial_budget_overrides` unchanged.
The 7 already-screened setups in section 0 add nothing (not re-run). Holdout sealed.
