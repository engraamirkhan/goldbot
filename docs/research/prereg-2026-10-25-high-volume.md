# Pre-registration 2026-10-25: high-volume learning (H-60, H-61, H-62)

Author: research-director (staff draft), 2026-10-25. Program design: `docs/research/high-volume-learning-2026-10-25.md`
(sections 1-4; this file freezes them). Written **before any exploration fill of any arm is read** and before any
H-60 output is read. No trial is dispatched, no budget spent, the holdout (2025-10-01..2026-09-30) is never read.
Arm rule details live in the strategy-researcher's arm spec (`ARM-SPEC`, being written in parallel); each placeholder
`[ARM-SPEC:<arm id>]` below is replaced by a reference to its frozen entry **before the first fill or run of that arm**,
and the arm's config hash (E2) is recorded in the same commit. A placeholder still open blocks that arm only.

## PD decision reflected here (supersedes program doc section 1 hosts)
- **No new demo accounts.** The owner said use the two existing ones. `icm-scalp` and `icm-swing` are NOT opened.
- **All arms (scalp, intraday, swing) run on `icm-probe`** (about £11k demo, £220/day under ADR 0042, probe budget
  £198 = 0.9 x £220). **`icm-demo` is untouched** (strategies, shadow agents, no exploration; ADR 0036).
- Consequence: one daily allowance and one open-position cap are shared by three styles. Isolation is by tighten-only
  sub-budgets (below), not by host. A swing drawdown can still consume the shared allowance, so swing is capped hard.
- **Open-position cap on `icm-probe` is about 19**, from notional/margin (live figure read from the engine at launch,
  never assumed). Sub-caps: scalp <= 10 open, intraday <= 7, swing <= 2 (sum 19). Program-doc concurrency figures
  (scalp 15, intraday 12, swing 4) are superseded.

## Sub-budgets on `icm-probe` (H-61, tighten-only layers before the RiskGate; RiskGate unchanged)
| Style | Daily £ ceiling (sub-budget) | Max open | Per-trade loss cap |
|---|---|---|---|
| Scalp (S arms, P0) | 70 | 10 | <= £3 at 0.01 lot (native stop) |
| Intraday (I arms, M mirrors) | 60 | 7 | <= £45 (existing `max_loss`) |
| Swing (W arms) | 110 | 2 | arm's native stop £38-110, see below |
- Ceilings are limits, not targets; their sum (240) exceeds the host's £198, so **the host's £198 binds** and the
  RiskGate's £220 daily limit, weekly 6%, streak, SIZE_DOWN and HALTED are untouched. Exits are never gated.
- **Swing reserve:** a swing entry is taken only if remaining host allowance >= its stop in £ + £30 (so one full
  swing stop never exhausts the day for scalp/intraday risk accounting). Combined open stop risk of both swing slots
  <= £150. Swing P&L counts against the host allowance like any other.
- **Swing per-trade cap:** stops £38-110 at 0.01 lot breach the current £45 `max_loss`. The swing arms' per-trade cap
  is raised **only to what their native stop needs and never above £110 (the RiskGate 1% per-trade hard limit)**.
  Scalp and intraday per-trade caps stay as above. The change is a config on `icm-probe` for W arms only; the
  architect and trading-safety-reviewer sign it off before the first W fill. A W candidate whose stop at 0.01 lot
  exceeds £110 is skipped and logged (reason `stop_gt_gate`), never resized or widened.
- Per-arm £/day caps from program doc section 1 stay as arm caps inside the style ceiling; swing arm caps = £110 each
  and share the swing ceiling.
- Swing contributes about 2-3 trades/day at best (honest; unchanged). Total estimate unchanged at ~290 (200-350)
  across ~24 arms, but scalp volume is now bounded by the £70 scalp ceiling and the 10-open cap, so expect the low end
  of the range until measured. Volume is a capacity, not a quota; no threshold is loosened.

## H-60: historical arm priors (0 trials; K_eff + 24, once; K ~116 -> ~140)
**Data:** data-v1 Dukascopy 1m, 2015-01-01..2025-09-30. Holdout never read. Run as a GitHub Actions matrix, arm x
2-year block (E10), same rule code as live, reusing `goldbot/research/walkforward.py` and `cpcv.py`.

**Arm list (exact rule, constants, stops, targets, hold: `[ARM-SPEC:<id>]`).** Fixed 0.01 lot, each arm's own native
stop and hold (H-59 principle):
| Arm id | Style / TF | Rule, constants, costs |
|---|---|---|
| S0 | scalp 1m, random bracket control | [ARM-SPEC:S0] (program doc: every 10 min 07-20 UTC, random side, stop 1 ATR1m, target 1R, 30 bars; RNG seed frozen in ARM-SPEC) |
| S1a, S1b | scalp 1m, session-VWAP 2-sigma fade | [ARM-SPEC:S1a], [ARM-SPEC:S1b] |
| S2a, S2b | scalp 5m, opening-range break London 07:00 / NY 13:30 | [ARM-SPEC:S2a], [ARM-SPEC:S2b] |
| S3a, S3b | scalp 5m, sweep-and-reclaim | [ARM-SPEC:S3a], [ARM-SPEC:S3b] |
| S4 | scalp 5m, owner box break+close+MFI | [ARM-SPEC:S4] |
| S5a, S5b | scalp 1m, EMA pullback in 15m trend | [ARM-SPEC:S5a], [ARM-SPEC:S5b] |
| S6 | scalp meta-labelled stream (ML arm, month 2+) | [ARM-SPEC:S6] |
| P0 | cost probe 1m, 30 s hold (cost only, no R read) | exists; no history run |
| I0 | intraday 15m random bracket control | [ARM-SPEC:I0] |
| M | mirrors of the 13 shadow agents at own stop | roster rules as in ADR 0037 am. 4/5 and H-59; history = existing agent code |
| I1 | intraday 1h tsmom, p in [0.30, 0.36) filled | [ARM-SPEC:I1] (band per H-57) |
| I2a, I2b | intraday 15m box without MFI / stop 1.0 ATR | [ARM-SPEC:I2a], [ARM-SPEC:I2b] |
| I3 | intraday meta-labelled stream (ML arm, month 2+) | [ARM-SPEC:I3] |
| W0 | swing 4h random bracket control | [ARM-SPEC:W0] (1 a day, stop 1.5 ATR4h, target 2R, 24 bars) |
| W1 | swing tsmom 4h and 1d incl. p band | [ARM-SPEC:W1] |
| W2 | swing box 4h roster rule | [ARM-SPEC:W2] |
| W3 | swing macro 1d | [ARM-SPEC:W3] (macro-researcher's frozen rule) |
The list is closed. Adding or editing an arm needs an amendment written before its first fill or history run.

**Costs per era (frozen):** Dukascopy bid/ask at the bar, plus measured slippage and commission by hour of day from
probe fills (with the K9 fx field); slippage never favourable; swap charged per rollover. Era split: 2015-2018,
2019-2025-09 (current cost era; the prior uses this only). Era spread/slippage constants: `[ARM-SPEC:costs-per-era]`
(to be pasted from the cost study before the first H-60 job runs).

**Validation:** rule arms: per-year OOS statistics (nothing fitted). ML arms: anchored walk-forward, train >= 3 y,
test 6 months, purge = max hold, embargo 1 day, refit every step.
**Outputs per arm:** fire rate per day, gross and net mean R, SD, day-clustered t, per-year table, hold-time and
concurrency distributions, share of stops that fit the £ caps at 0.01 lot (swing: share <= £110), live-label parity
on 2026-10 bars.
**Prior:** Normal on net mean R from 2019-2025-09 OOS years, mean x 0.5, effective prior n = min(50, n_hist / 100).
Per arm only, never pooled; live rows update the arm's own posterior with daily arm sums; backtest rows never added
to live n. **Parity alarm:** after 200 live trades, |live - backtest mean| > 3 SE is investigated before either is
believed.
**K:** 0-trial look, no verdict, no promotion, no retirement. Adds ~24 arms once, clustered via `k_eff`; K ~116 ->
~140, one-sided Bonferroni t 3.33 -> ~3.38. Stated, not hidden.

## H-61: exploration program and Thompson allocation (0 trials; K 0, tagged, no reading)
- **Host:** `icm-probe` only. Exploration status is refused on any non-`probe_only` account (E2). Sub-budgets, caps and
  swing rules as above. `icm-demo` receives no exploration order.
- **Per-arm stop (permanent; re-entry only by new pre-registration):** running net R in exit order reaches **-30 R
  within first 300 trades** (scalp), **-15 R within 100** (intraday), **-10 R within 40** (swing).
- **Posterior:** Normal-Inverse-Gamma on net R per trade per arm, updated nightly 21:30 UTC from daily sums, never
  intraday.
- **Allocation:** share of its style's £ ceiling and open slots proportional to P(arm best | data); **floor 5%** per
  live arm, **cap 30%**; always inside the sub-budgets and the host's £198. Fail closed to equal shares inside caps if
  the table is missing or unsigned (E5).
- **Taking rule:** each candidate taken with probability **0.8 by meta-p rank, 0.2 uniformly at random**. Every
  candidate logs its propensity (taken or not, probability, meta and bandit versions, feature snapshot, list
  version) (E6). Off-policy estimates by IPW / doubly robust.
- **Frozen arms:** the bandit never edits an arm.
- **Rotation at -8%:** pending its own ADR (trading-safety + CFO); not used before it exists. The 12% drawdown kill
  switch is a rail, not a variable. With one account the whole exploration program stops together at the rail.
- **Exploration is not promotion:** rows carry `exploration`, are excluded from `population.py` evidence, never
  pooled into H-57/H-58 or any trial. H-59 stays as written for the mirror probes.

## H-62: online meta-labeller (0 trials; K 0 until a model is proposed for promotion)
- **One model per style** (scalp, intraday, swing); arm id as a feature; scored and evaluated per arm.
- **Features:** <= **40**, list v1 frozen per quarter, chosen by the ml-researcher on history only, never on
  exploration outcomes (`[ARM-SPEC:feature-list-v1]`, to be committed before the first retrain). Fill-time snapshot,
  as-of joined on `available_utc`, stored with the fill (no recompute skew).
- **Labels:** net R on the arm's own barriers; S0/I0/W0 random brackets are the unbiased core.
- **Retrain:** weekly (Sunday) once >= **1,500 new admissible labels** in the style and >= 3,000 total. Swing will
  not reach this by exploration alone; its model is trained on H-60 history and scored forward only, with no live
  retrain until the threshold is met. 5-fold cross-fit blocked by session day, purge = max hold, embargo 1 day (5
  swing), isotonic calibration on out-of-fold scores.
- **Strict forward:** a trade is scored only by a model whose training ended before its decision time.
- **Champion/challenger:** the challenger replaces the champion only if its log-loss on the **next 2 unseen weeks**
  beats the champion's by a margin frozen at `[ARM-SPEC:meta-margin]` before the first retrain. Never chosen
  in-sample. A model reaches `icm-demo` only through promotion.

## Graduation and promotion (program doc section 4, unchanged)
- **Graduate:** >= 300 admissible trades over >= 20 session days, P(net mean > 0) > 0.95, live-backtest parity holds.
- **Promotion:** (1) new pre-registration of the exact config before any further fill is read; (2) research trial on
  2015-2025-09 at full K incl. H-60 k_eff, gate-1 DSR t about 4.1+; (3) holdout scored once; (4) forward n >= 60 at
  native stop on fills after the pre-registration date, admissible by H-52; (5) evaluator verdict; (6) roster shadow
  agent on `icm-demo`, then capital weight only through the tournament, one promotion per family per quarter,
  sizing under the RiskGate.
- **Budget:** 2027Q1 open 14; H-57, H-58 take 2 and H-53 takes 1, leaving 11; **4 reserved for graduates** (H-63+);
  the 6 reserved slots untouched. Scalping cost bar of H-14/O-4 (0.35 R withdrawal) applies.

## Engine prerequisites (architect) and status
| Item | Status |
|---|---|
| **E0** scheduled-probe fall (85 -> 6/day) | **DONE**: fixed and deployed 2026-10-25 (probe-only static limits, PR #178). Verify the daily count recovers over the next 3 days; skips are to be logged with reasons. |
| E1 per-account `decision_tf` (1m base clock, probe-only only; trading-safety review) | open |
| E2 arm registry, per-arm magic, config hash | open |
| E3 tighten-only layers: per-arm £ cap, max_open, cumulative-R stop, sub-budgets, swing reserve, host open risk <= 0.5 x remaining allowance | open (now also the style sub-budgets) |
| E4 N probe-only hosts | **not needed** (single host `icm-probe`); one MT5 terminal, check broker request-rate limits |
| E5-E10 allocation table, candidate journal, fill cost record, model slot, dashboards, history runner | open |
- **If E1 slips:** only S0 and the existing 1m probe path run on the scalp side; no 1m/5m strategy arm runs before E1.
  Intraday and swing arms start on the 15m clock.

## Flags
- Single host couples styles through one allowance and the ~19 open cap; the scalp null-edge drift (~-£30/day) now
  competes with intraday and swing for the same £198. Re-check the sub-budgets after week 1 from measured rates
  (amendment before use).
- Swing per-trade cap raise on `icm-probe` needs the trading-safety-reviewer before the first W fill.
