# Pre-registration 2026-10-25: high-volume learning (H-60, H-61, H-62)

Author: research-director (staff draft), 2026-10-25. Program design: `docs/research/high-volume-learning-2026-10-25.md`
(sections 1-4; this file freezes them). Written **before any exploration fill of any arm is read** and before any
H-60 output is read. No trial is dispatched, no budget spent, the holdout (2025-10-01..2026-09-30) is never read.
Arm rules are frozen in `docs/research/arms-2026-10-25.md` (`ARM-SPEC`); each arm below references its entry there by
arm id (section number given), and the arm's config hash (E2) is recorded before the first fill or run of that arm.
The ARM-SPEC arm ids and timeframes govern where this file's wording differs (S1 runs on 5m; ids are upper case).
Only W3 (macro 1d) stays open: it is blocked until its rule is committed to ARM-SPEC by amendment, and blocks that arm only.

## PD decision reflected here (supersedes program doc section 1 hosts)
- **No new demo accounts.** The owner said use the two existing ones. `icm-scalp` and `icm-swing` are NOT opened.
- **All arms (scalp, intraday, swing) run on `icm-probe`** (about £11k demo, £220/day under ADR 0042, probe budget
  £198 = 0.9 x £220). **`icm-demo` is untouched** (strategies, shadow agents, no exploration; ADR 0036).
- Consequence: one daily allowance and one open-position cap are shared by three styles. Isolation is by tighten-only
  sub-budgets (below), not by host. A swing drawdown can still consume the shared allowance, so swing is capped hard.
- **Open-position cap on `icm-probe`: host max open 18 (ADR 0043; notional cap about 19)**, from notional/margin (live figure read from the engine at launch,
  never assumed). Sub-caps: scalp <= 10 open, intraday <= 7, swing <= 1 (ADR 0043), all inside the host max open of 18. Program-doc concurrency figures
  (scalp 15, intraday 12, swing 4) are superseded.

## Sub-budgets on `icm-probe` (H-61; ADR 0043 governs: CFO decision 2026-10-25, supersedes this section's earlier draft)
Tighten-only layers before the RiskGate; RiskGate unchanged. Each pot counts today's realised loss plus open risk.
| Pot | Daily £ | Max open | Per-trade loss cap |
|---|---|---|---|
| Scheduled cost probes | 15 | 1 | $3 stop (unchanged) |
| Mirror probes | 45 | (in host cap) | <= £45 (`max_loss`) |
| Scalp (S arms) | 50 | (in host cap) | <= £6.50 at 0.01 lot (native stop; arm spec F1) |
| Intraday (I arms) | 48 | (in host cap) | <= £45 |
| Swing (W arms) | 40 | 1 | <= £40 |
- Pots sum to the host's £198 (0.9 × £220); host max open 18 (notional cap ≈19). Random-control arms get fixed
  shares within their pot. Exits are never gated.
- Swing arms whose native stop exceeds £40 at 0.01 lot (W1B tsmom 1d ≈ £99) are skipped (`native_stop_above_cap`)
  and wait for a larger demo balance; they are counted in K regardless.
- Drawdown: −6% of icm-probe warns; −8% pauses every arm except scheduled cost probes until the rotation in ADR 0043.
- Per-arm £/day caps from program doc section 1 stay as arm caps inside the style ceiling; swing arm caps = £40 each (ADR 0043)
  and share the swing ceiling.
- Swing contributes about 2-3 trades/day at best (honest; unchanged). Total estimate unchanged at ~290 (200-350)
  across ~24 arms, but scalp volume is now bounded by the £50 scalp pot (ADR 0043) and the 10-open sub-cap, so expect the low end
  of the range until measured. Volume is a capacity, not a quota; no threshold is loosened.

## H-60: historical arm priors (0 trials; K_eff + 24, once; K ~116 -> ~140; reruns +1 to K each)
**Data:** data-v1 Dukascopy 1m, 2015-01-01..2025-09-30. Holdout never read. Run as a GitHub Actions matrix, arm x
2-year block (E10), same rule code as live, reusing `goldbot/research/walkforward.py` and `cpcv.py`.

**Arm list (exact rule, constants, stops, targets, hold: the cited ARM-SPEC entry).** Fixed 0.01 lot, each arm's own native
stop and hold (H-59 principle):
| Arm id | Style / TF | Rule, constants, costs |
|---|---|---|
| S0 | scalp 1m, random bracket control | arms §1 S0 (every 10 min 07-20 UTC, random side by the frozen hash, stop $3.00, target 1R, 30 bars; S0R resting-stop twin is a cost arm, arms §4) |
| S1A, S1B | scalp 5m, session-VWAP 2-sigma fade | arms §1 S1A, S1B |
| S2A, S2B | scalp 5m, opening-range break London 07:00 / NY 13:30 | arms §1 S2A, S2B |
| S3A, S3B | scalp 5m, sweep-and-reclaim | arms §1 S3A, S3B |
| S4 | scalp 5m, owner box break+close+MFI | arms §1 S4 |
| S5A, S5B | scalp 1m, EMA pullback in 15m trend | arms §1 S5A, S5B |
| S6 | scalp meta-labelled stream (ML arm, month 2+) | arms §1 S6 |
| P0 | cost probe 1m, 30 s hold (cost only, no R read) | exists; no history run |
| I0 | intraday 15m random bracket control | arms §2 I0 |
| M (MB15, MB30, MB1H, MSA15, others by registry) | mirrors of the shadow agents at own stop | roster rules as in ADR 0037 am. 4/5 and H-59; history = existing agent code; arms §2 |
| I1 | intraday 1h tsmom, p in [0.30, 0.36) filled | arms §2 I1 (band per H-57) |
| I2A, I2B | intraday 15m box without MFI / stop 1.0 ATR | arms §2 I2A, I2B |
| I3 | intraday meta-labelled stream (ML arm, month 2+) | arms §2 I3 |
| W0 | swing 4h random bracket control | arms §3 W0 (1 a day, stop 1.5 ATR4h, target 2R, 24 bars) |
| W1A, W1B | swing tsmom 4h / 1d incl. p band | arms §3 W1A, W1B |
| W2 | swing box 4h roster rule | arms §3 W2 |
| W3 | swing macro 1d | arms §3 W3 (macro-researcher's rule; blocked until committed, arms §3) |
The list is closed. Adding or editing an arm needs an amendment written before its first fill or history run.

**Costs per era (frozen):** Dukascopy bid/ask at the bar, plus measured slippage and commission by hour of day from
probe fills (with the K9 fx field); slippage never favourable; swap charged per rollover. Era split: 2015-2018,
2019-2025-09 (current cost era; the prior uses this only). Era spread/slippage constants: `cost-model-1m-2026-10-25.md` (current era: central $0.144/oz,
conservative $0.23/oz round trip; the 2015-2018 era constants are pasted from the same study before the first H-60 job runs).

**Validation:** rule arms: per-year OOS statistics (nothing fitted). ML arms: anchored walk-forward, train >= 3 y,
test 6 months, purge = max hold, embargo 1 day, refit every step.
**Outputs per arm:** fire rate per day, gross and net mean R, SD, day-clustered t, per-year table, hold-time and
concurrency distributions, share of stops that fit the £ caps at 0.01 lot (swing: share <= £40, ADR 0043), live-label parity
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
  exploration outcomes (`h62-meta-labeller-spec-2026-10-25.md` section 1, committed before the first retrain). Fill-time snapshot,
  as-of joined on `available_utc`, stored with the fill (no recompute skew).
- **Labels:** net R on the arm's own barriers; S0/I0/W0 random brackets are the unbiased core.
- **Retrain:** weekly (Sunday) once >= **1,500 new admissible labels** in the style and >= 3,000 total. Swing will
  not reach this by exploration alone; its model is trained on H-60 history and scored forward only, with no live
  retrain until the threshold is met. 5-fold cross-fit blocked by session day, purge = max hold, embargo 1 day (5
  swing), isotonic calibration on out-of-fold scores.
- **Strict forward:** a trade is scored only by a model whose training ended before its decision time.
- **Champion/challenger:** the challenger replaces the champion only if its log-loss on the **next 2 unseen weeks**
  beats the champion's by the margin frozen in `h62-meta-labeller-spec-2026-10-25.md` (0.001 nats per row, lower bound > 0, ECE <= 0.03) before the first retrain. Never chosen
  in-sample. A model reaches `icm-demo` only through promotion.

## Graduation and promotion (program doc section 4, amended by quant-review 2026-10-25)
- **Graduation: one read, fixed sample.** Each arm is read **once, at its 300th admissible trade**, provided that
  trade falls on >= 20 session days (otherwise the read waits for the 20th day and still uses the first 300 trades
  only; later trades are never added). Graduate iff P(net mean > 0 | first 300) > **1 - 0.05 / m** and live-backtest
  parity holds, where m = the number of graduation-eligible arms live on the read date (m <= 19: rule arms S1A, S1B,
  S2A, S2B, S3A, S3B, S4, S5A, S5B, I1, I2A, I2B, MB15, MB30, MB1H, MSA15, W1A, W1B, W2; at m = 19 the bar is
  P > 0.9974). No interim reads of the graduation statistic, no second read.
- **Random controls never graduate:** S0, S0R, I0, W0 and P0 are nulls and cost arms; they are excluded from m.
  S6 and I3 cannot graduate either (H-62 section 0).
- **Stop rule at n = 300:** an arm not graduated at its read retires from exploration (its sub-budget share returns to
  the style pot; re-entry only by a new pre-registration). The cumulative-R stops of H-61 remain earlier exits.
  Swing arms (about 0.3-1 fills a day) are expected not to reach 300 this quarter and run only under their own stops.
- **Retired-family neighbours: graduation void by default.** Arms S3A/S3B (R-14), S4, I2A, I2B, W2 (R-15), I1, W1A,
  W1B (R-06/R-10) and S6, I3 (R-07) may explore, but a graduation read on them is **void** unless the evaluator records,
  **before the read**, that the retirement row's re-entry condition is met (new mechanism, or measured costs below
  half the prior for R-15). Without that record the 300-trade read is only descriptive and the arm retires.
- **Promotion:** (1) new pre-registration of the exact config before any further fill is read; (2) research trial on
  2015-2025-09 at full K incl. H-60 k_eff, gate-1 DSR t about 4.1+; (3) holdout scored once; (4) **forward n >= 60,
  fixed sample, read once**, at native stop on fills after the pre-registration date, admissible by H-52, and taken
  **only from fills not allocated by the bandit or the 0.8/0.2 taking rule**: the roster shadow agent on `icm-demo` (no
  exploration order there); exploration fills are never promotion evidence; (5) evaluator verdict; (6) capital weight
  only through the tournament, one promotion per family per quarter, sizing under the RiskGate.
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

## Amendment 1 — 2026-10-26, before any live exploration fill is read
Written before any live exploration fill is read and before any job below runs. Nothing above is loosened.

**(a) H-60 prior scenario and wiring.** The prior uses the stop-overshoot **$0.70** scenario (conservative), not the
central one, for every arm. S0 null
audit passed (e5ea02e1, docs/research/s0-null-audit.md): gross -0.0026R, se 0.0019. Priors are wired as relative
allocation weights only. The open items in that note are tracked as follow-ups.

**(b) Box break+close feature-first screen (new, counted in K).** Data and holdout rules as H-60 (data-v1, 2015-01-01..
2025-09-30, holdout never read, current cost era, $0.70 overshoot, fixed 0.01 lot). Rule: owner box break+close, no MFI
filter, fixed 1R target-free read. The 24-bar time exit is the ONLY primary read per cell; the 1R/2R brackets are
descriptive, reported separately, and never "of interest".
Grid: timeframe {15m, 30m, 1h, 4h} x box stop size / ATR(14) of the same timeframe in buckets {<0.5, 0.5-1.0, 1.0-1.5,
>1.5} = 16 cells, defined now and not edited. Output per cell: n, gross mean R, day-clustered t, per-year table.
**Reading rule (fixed now):** a cell is "of interest" only if gross mean R > 0, day-clustered t >= 3.41 (frozen; K up to
~156), positive in >= 70% of years, and n >= 300. Per-year positive counts use only years with n >= 20 (other years
are excluded from the 70%). Overlapping trades are collapsed per day before the day-clustered t. Of-interest cells are hypotheses for a separate pre-registered
net test, never promoted from this screen. Zero cells passing is reported as such. K: +16 trials, clustered via `k_eff`,
stated here (K ~140 -> up to ~156).

**(c) Exits check for 1h and 4h box trades (new, counted in K).** Same trades as (b) for 1h and 4h only, all stop
buckets pooled and per bucket. Output: MFE distribution in R (quantiles 10/25/50/75/90, share reaching 1R, 2R, 3R), and
total R for **trailing** (chandelier = highest high since entry - 3 x ATR(14), active once +1R is reached) versus **fixed 2R** target, same stop and time cap.
**Reading rule (fixed now):** trailing is preferred only if its mean R exceeds fixed 2R by > 0 with a paired
day-clustered t >= 2.0 in both 1h and 4h and in >= 70% of years; otherwise fixed 2R stays. A MFE median < 1R is reported
as evidence against any box exit change. 2 timeframes x 2 exit rules = 4 comparisons, K +4 (clustered), K up to ~160.
Only pooled per-timeframe results decide; per-bucket results are descriptive. Run (c) only if (b) has at least one
of-interest 1h or 4h cell; otherwise skip it and do not charge K +4.
No live parameter changes from this check without a further amendment.

**(d) PD recommendation (recorded, not a decision of the research director).** Box break+close stays **demo-only for
learning**, with **no real-money weight**, whatever (b) and (c) show, until a separate pre-registered net test and the
usual graduation gates pass.

**(e) Pending proposal, not in force.** The research director proposes amending **ADR 0043** to allow **one** W1B
(tsmom 1d, stop ~ £99) trade up to **£110** funded from the scalp pot. Status: **pending CFO and trading-safety-reviewer
decisions**; until both are recorded, the ADR 0043 caps (swing per-trade cap, sub-budgets) apply unchanged and no such
trade is placed.
