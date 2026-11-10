# Amendment 2026-11-10: execution and cost calibration controls (VEX15, VEX1H, IEX15, IEX1H)

Status: **drafted 2026-11-10; in force at the deploy that carries it, after the quant signature below.** Amends
prereg-amendment-2026-11-06-control-thinning.md (the control registrations it thinned and retired). Sources: research
director's arm list of 2026-11-09 (sections 2 to 4), PD volume ruling 2026-11-09 item 2 (the "~15 15m-1h
control/execution arms" bucket), EVAL-2 v6 (docs/research/eval2-method-2026-11-09.md) section 5 ("random-control arms
serve as the fill-vs-replay calibration stream").

## 1. Why

EVAL-2 needs, per broker, the cost of record by leg and session and S5 (fill against replay) at 15m and 1h. The
11-06 thinning retired I0, VI0, C1H and VC1H because they *paired nothing*. That leaves no 15m-1h random stream on
either demo broker, and icm-demo's S5 audit has n 70 at 15m and n 15 above it. Gold learning arms cannot run on
vantage (N-1a `learning_icm_only`), so vantage's useful demo work until its exit deal price is recorded is to measure
its own execution and costs.

This is a new card, not a re-run: the purpose, the stop and the ids are new. No retired id is reopened (A2), and
`thin_control_fills` / `thin_control_share` are unchanged.

## 2. The arms

| Id | Account | Rule, timeframe | Geometry | max_open | config_hash | Start |
|---|---|---|---|---|---|---|
| VEX15 | vantage-demo | `random_bracket` 15m, XAUUSD | minutes 0/15/30/45, 07:00-20:30 UTC; stop 1.5 x ATR14 in [$3, $22.6]; target 2R; 16 bars (4 h) | 2 | c05155207165e963 | deploy |
| VEX1H | vantage-demo | `random_bracket` 1h, XAUUSD | minute 0, 08:00 London to 16:00 New York; stop 1.5 x ATR14 in [$3, $39.8]; target 2R; 8 bars | 1 | c0514a7202094915 | deploy |
| IEX15 | icm-demo | as VEX15 (own id, own fills) | as VEX15 | 2 | 0d096cf24fbe20dd | deploy |
| IEX1H | icm-demo | as VEX1H (own id, own fills) | as VEX1H | 1 | 05c6633e6297a37d | deploy |

The hashes are `research.arms.arm_hash` of the rows in config/exploration_arms.yaml (tested,
tests/test_execution_controls.py).

Per-trade caps (accounts.yaml): 15m £17, 1h £30. Every day, hour and session budget,
`max_per_day` (icm-demo 70, icm-probe 85, vantage-demo 40), every RiskGate cap and the daily loss limits are
unchanged.

### 2.1 Considered and dropped: VCAL5

VCAL5 (vantage-demo, `random_bracket` 5m, minute 0, 07:00-20:30 UTC, stop $7, 1.5R, 6 bars) was proposed to measure
vantage's exit leg once the exit deal price was recorded. **Dropped (quant ruling 2026-11-09): no spec row, no stop
rule, no account row, K +0.**
- The exit deal price is already recorded (exit deal VWAP, R110, 3e72bf9a), and vantage-demo fills every stop and
  target at the bracket level (294 of 294), so its exit leg measures nothing on demo.
- Its entry leg duplicates VEX15/VEX1H: spread and entry fill-vs-replay belong to the instrument, broker and session,
  not to the 5m bar; cost per R at a $7 stop follows from the measured spread.
- What remains is about 10 fills a day of 5m volume, scalp padding that PD ruling 2026-11-09 point 3 forbids.
- Reopened only with a new card and a new id, if vantage becomes a candidate real-money venue with a planned
  real-money calibration sample, or if vantage-demo starts showing non-zero stop slip.

Vantage-demo volume therefore stays at about 20-28 fills a day, inside the PD ruling's floor.

## 3. Purpose and class

- **Execution and cost measurement only.** Each is read for cost per R, cost of record per broker x leg x session,
  and fill against replay. **Never read for edge, never paired with any arm, never a qualification, never pooled
  across brokers.**
- Class `control` (`role: control`, `control_share` set): no pairing (`learning:` block, `params.control`, EXTRA_PAIRS,
  COST_CONTROLS and the levels CONTROLS name none of them), so the daily budget admits each with propensity 1.0,
  bounded by the day, hour and session budgets only. Gold, so not `thin`: they never spend the thin-control share.
- Outside N-1a and the 02:00-07:00 window: `refusals_novel.in_scope` covers gold learning arms and the fixed
  LC*/ZCD set only, and every window here starts at 07:00 UTC or later.
- On vantage-demo the exit legs fill at the bracket level (demo-server artefact, quant ruling 2026-11-09), so VEX15/
  VEX1H are read on the entry leg (fill against replay, spread) and cost only; their exit-slip term is not read and is
  never a cost prior for real money.
- When EVAL-2 5B is enabled on an account, its execution controls shrink by share to at most 10 % of that account's
  day; they are never removed early by it.

## 4. Kill rules (pre-registered)

1. **Cost stop (Z 4 shape, config/arm_stops.yaml `cost_stop`).** At >= 30 fills, the one-sided 95 % upper bound of
   cost per R (signal-time `cost` / `stop_loss`, SE clustered by UTC day): above 0.10 halves the arm, above 0.20
   suspends it until a dated amendment.
2. **Execution stop.** |fill - replay| above 0.10 R (mean absolute gap of the arm's fills against their bar replay)
   at n >= 50 suspends the arm as a defect, to be investigated before any re-start.
3. **Sample stop.** The arm retires once each session (asia / london / newyork) x leg (entry; stop, target and time
   exit) cell has >= 100 fills, or on **2026-12-09**, whichever comes first.
4. **No R stop.** A control is the null; listed under `none:` in config/arm_stops.yaml.

A retirement or suspension only refuses new entries; open positions keep their stop, target, hold and every exit.

### Enforcement as built

- **Rule 1, suspend band: automatic.** `arm_stops.cost_states` reads the band for every id in `cost_stop.arms`, and
  the daily review (`daily_review.run_daily_review`) retires a control listed there whose state is `suspended`, with
  the rule "execution control cost stop (Z 4 shape) ..." in `exploration_arm_status`. The engine then refuses its new
  entries; open positions keep every exit. Every other control keeps "never retired on its own" (quant ruling
  2026-10-26, "the controls are the null"): ZCD with the same fills is not retired (tested).
- **Rule 1, halve band: no automatic effect.** The engine's halving scales the arm's pot share, which binds only on a
  pot with a £ figure; the intraday pots of both accounts are slots only (`pot_budgets: false`). The halve band is
  reported by the performance-analyst's daily read; it never retires the arm and never raises anything.
- **Rule 2, execution stop: manual.** EVAL-2 defines fill against replay on replay **v2** (measured per-leg slippage,
  no credit, exit overshoot; eval2-method section S5). Replay v2 (Lane 5A) is not merged. The v1 label replay
  (`label_sim_r`) carries the slippage prior (about +0.078 R of the +0.084 R gap measured on icm), so an automatic
  0.10 R stop on v1 would fire on the prior, not on execution. Until replay v2 is merged, the performance-analyst
  reads the gap daily and a hit is switched off the same day by the spec row's `retired_day` / `retired_rule`
  (kill-only, unhashed fields). It becomes automatic in the review once v2 lands, by a dated note to this amendment.
- **Rule 3, sample stop: manual.** The cell counts are read daily, and the 2026-12-09 end is the same `retired_day`
  edit, made on that day at the latest.
- Kill-only throughout: no enforcement path raises a share, reopens an arm or admits an entry.

## 5. K and budget

- **K +1 per arm id** (the arm-id K, the convention of every past control): **+4** (VEX15, VEX1H, IEX15, IEX1H).
  VCAL5 is dropped and adds nothing.
- **0 registry trial rows**: these arms are never read for edge, so the DSR K is unchanged.
- If the quant rules otherwise, the 4 rows come from the PD's reserve of 5 inside the 105 headroom.
- The quarterly trial budget, the holdout, the phase gate and every promotion threshold are unchanged.

## 6. Expected cost (demo)

About £0.34 a fill at 15m on both brokers (~0.033 R at a $14 stop; ~0.017 R at 1h): about -£5 a day on vantage-demo.
On icm-demo about £0: IEX displaces 5m fills of about the same cost inside the unchanged 70. Paid
learning, not income.

## 7. Implementation note: `control_share`

The research list put `control_share: 0.25` on each. That fails the engine's check that a pot's control shares sum
to at most 1 (vantage-demo intraday is at 0.90, icm-demo at 0.65, the retired VI0/VC1H/I0/C1H rows still registered
so their exits are kept; 0.05 each would put vantage-demo at 1.0000000000000004 in float). Each is registered at
**0.04** (vantage-demo intraday 0.98, icm-demo 0.73). The share is not the admission probability (that is the
daily budget's propensity, 1.0 here); on these slots-only pots it only labels the journalled `propensity` and sets the
base a cost-stop halving would scale. Admission, volume and every cap are the same at 0.04 as at 0.25.

## 8. Bias statement and disclosure

Written after the 2026-10-26..11-09 control fills and their P&L were seen. Acceptable because these arms are never
read for edge: they add volume for measurement and carry kill rules only. No threshold, reading rule, window or
matched-control method of any other arm changes.

## 9. Separate kill: I0-XAUJPY formally retired 2026-11-09

- I0-XAUJPY (vantage-demo) has been refused `thin_cell_complete` since 2026-11-06 under the 11-06 amendment rule 4:
  its XAUJPY cost cell on vantage-demo has 36 fills of 30. That count is per account, so the arm would enter again if
  XAUJPY were ever listed on another account.
- The spec row now carries `retired_day: "2026-11-09"` and a `retired_rule` naming rule 4, as S0-XAUJPY's did on
  2026-11-07 (kill-only amendment section 4). The engine then refuses it on every account (`spec_retired`). Only
  unhashed fields changed: hash 4e2532b406cdcf47, id and K row stay. Open positions keep every exit. K unchanged.
- Evidence (performance-analyst, 2026-11-09): measured cost £0.413 a fill (0.038 R, 1.36 bp; SE ±0.07, n 6), about
  VI0's per R. The £1.13 a fill seen earlier is the engine's pre-trade estimate on the `cost_atr = 0.10` fallback (no
  measured XAUJPY cost table on vantage), not a measured cost. Its -£42.8 is direction on 6 fills (noise), not cost.
  The retirement rests on rule 4 (cell complete), not on that loss.

## 10. Rollback

Drop the VEX15/VEX1H/IEX15/IEX1H lines from config/accounts.yaml and restart the engines (any open position keeps its
exits only while its arm stays registered: retire by `retired_day` first, drop the lines once flat). The spec rows
and arm_stops entries stay (ids are never reused).

Quant sign-off:
