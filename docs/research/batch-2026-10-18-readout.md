# Batch 2026-10-18: readout of B01-B04 and rulings (research director)

Written 2026-10-18 after B01-B03 were read by the evaluator and B04 finished. It reads `batch-2026-10-18.md` section 2
as written. **No pre-registered verdict changes here**, and nothing in this file computes a new outcome. The per-year
numbers below come from the runs' own recorded output: the `rule_only_split.years` table that rule 2.5 requires.
Gross R by year was not computed and will not be (section 2).

Artifacts: `~/tmp/research/B01..B03/cell-*` (local copies) and the runs' artifacts: B01 run 39101594107, B02
39102627317, B03 39103285539, B04 39104042350. GitHub keeps run artifacts for 90 days, so they expire around
2027-01-16.

## 1. Verdicts, B01-B03: 12 of 12 FAIL

The published registry numbers trials #21-#32 in dispatch order. Gross is rule-only at mid prices. Net charges spread,
0.37 $/oz and swap. 2025 runs to September, because the holdout is excluded.

| Trial | Run | Config (changed keys) | Events | Gross R (t) | Net R (t) | Net 2010-17 | Net 2024 (n) | Net 2025 (n) | Verdict |
|---|---|---|---:|---|---|---:|---|---|---|
| #21 | B01 v1 | target 3, max_bars 32 | 1,498 | +0.027 (0.63) | -0.421 (-10.4) | -0.52 | -0.28 (94) | -0.19 (80) | FAIL |
| #22 | B01 v2 | confirm_bars 1 | 4,722 | +0.015 (0.74) | -0.443 (-22.9) | -0.46 | -0.30 (298) | -0.00 (196) | FAIL |
| #23 | B01 v3 | asia_range_max 1.5 | 1,715 | +0.029 (0.84) | -0.399 (-12.3) | -0.49 | -0.22 (107) | -0.06 (93) | FAIL |
| #24 | B01 v4 | stop 0.75 | 1,498 | +0.054 (1.27) | -0.553 (-14.3) | -0.66 | -0.35 (94) | -0.35 (80) | FAIL |
| #25 | B01 v5 | NY open 09:30 | 1,389 | +0.069 (1.80) | -0.373 (-10.4) | -0.42 | -0.23 (87) | -0.18 (86) | FAIL |
| #26 | B02 v1 | NY, move 1.5, target 3 | 2,427 | -0.052 (-1.98) | -0.336 (-13.2) | -0.37 | -0.40 (145) | -0.19 (122) | FAIL |
| #27 | B02 v2 | London, move 1.5, target 3 | 2,308 | -0.066 (-1.88) | -0.491 (-15.1) | -0.56 | -0.42 (145) | -0.32 (90) | FAIL |
| #28 | B02 v3 | NY, formation 0.25, target 2 | 1,973 | +0.037 (1.21) | -0.269 (-9.0) | -0.32 | -0.38 (127) | -0.04 (97) | FAIL |
| #29 | B03 v1 | 15m, range 8 | 3,874 | +0.008 (0.48) | -0.524 (-31.7) | -0.60 | -0.36 (276) | -0.23 (184) | FAIL |
| #30 | B03 v2 | range 16, target 3 | **358** | +0.008 (0.14) | -0.630 (-11.7) | -0.63 | -0.36 (26) | -0.63 (6) | FAIL (ruling 1.1) |
| #31 | B03 v3 | range 16, tick ratio 1.0 | **519** | +0.001 (0.02) | -0.631 (-14.6) | -0.63 | -0.48 (37) | -0.80 (13) | FAIL (ruling 1.1) |
| #32 | B03 v4 | range 32, target 3 | **155** | -0.066 (-0.77) | -0.626 (-7.8) | -0.76 | -0.27 (9) | -0.18 (7) | FAIL (ruling 1.1) |

No trial clears rule 2.1 (the best gross t is 1.80, #25). Every trial has rule-only net mean R <= 0. Every trial is
also net negative in **both** 2024 and 2025, so rule 2.5 fails as well. Long and short lose about equally in every
trial. R-02 (session_open) and R-05 (intraday_momentum) stay retired; the retirement is reconfirmed on 2026-10-18 with
trials #21-#28. Breakout on 15m is new and is retired for the 2h, 4h and 8h range designs (#29-#32).

### 1.1 Ruling on the three B03 trials below 1,000 events

The cell reports call #30-#32 "INCONCLUSIVE, cannot retire". The batch rule says otherwise, and the batch rule
governs. Its FAIL clause has two independent parts: "the screen fails at or above its event floor, **or** rule-only
net mean R <= 0". The second part has no event floor. INCONCLUSIVE ("fewer events than the floor") protects an edge
that more data might confirm. Here more data cannot plausibly help: net t is -7.8 to -14.6 and gross is about 0. The
same section already lets a sample below its pass floor retire a row (daily rows, 150-999 events). **Verdict: FAIL**,
with the reason stating that the screen part was below the floor. No re-run is allowed.

The code disagrees: `director.preregistered_decisions` gives only "inconclusive" to a screened row below its floor.
Defect D-2 (section 3) fixes that. Lesson for later pre-registrations: state the expected event count for each variant
before dispatch. #32 (155 events) could have been seen to be underpowered in advance.

## 2. Ruling on the cost paradox (task: does any family show edge in the high-volatility regime?)

**The premise was only partly tested.** The batch argued that cost in R is about 4x lower now. The pooled net-gross
gap is 0.43-0.64 R for B01/B03 and 0.28-0.43 R for B02. That matches the low ATR15 of most of 2010-2025, not today's
$8, where a 1.0 x ATR15 stop costs about 0.09 R. Most of the volatility rise falls in late 2025 to 2026. That is the
holdout (2025-10-01..2026-09-30) plus the forward data, and neither is in this sample.

**What the pre-registered per-year table already says:**
- Net losses shrink as volatility rises. 2010-17 averages -0.32 to -0.76 R. 2025 runs from -0.00 to -0.80 R, and 10 of
  12 trials improve (one is flat, one is worse). That is what a cost falling in R predicts.
- No trial is net positive in 2024 or in 2025. The closest is #22 in 2025: -0.002 R on 196 events, t -0.02.
- Pooled gross is indistinguishable from zero in every trial (|t| <= 1.98). Even at zero cost there is no edge to
  keep.
- So no family shows a gross or net edge in the 2024-2025 regime that the sample contains. These are statements about
  one rule-required table. They are not a new test.

**Decision: no regime-conditional retest in sample. 0 new trials, K unchanged.**
1. Selection is contaminated. The per-year nets of all 12 trials have been seen. Any configuration chosen now for a
   high-volatility retest is chosen on them.
2. A retest cannot pass. In sample, the high-volatility window (2024-01..2025-09) holds about 16-500 events per
   configuration. At K = 53 the gate needs t >= 3.94. A +0.10 to +0.15 R net edge with SD about 1.3 R needs about
   1,200-2,600 events. A trial that cannot pass by construction only adds to K.
3. The regime the premise is about is the holdout and forward data. The holdout gets one look, reserved for survivors
   after the G2 repair (due 2026-11-06), and there is no survivor. Forward data is the honest test.
4. The question therefore moves to mechanisms that are conditional on regime by design. These are S-1 and H-25 in
   section 5, pre-registered with their own rules before any of their data is computed.

Gross R by year or by ATR regime for B01-B04 is not to be computed. It would be an outcome read with no rule attached.

## 3. Recording the verdicts (registry path and two defects)

**Path.** No workflow writes verdicts. The sanctioned path is `TrialRegistry.record_verdict` on a fresh copy of the
published registry, then a compare-and-swap save with `scripts/registry_release.sh save`. That is the same protocol
the research workflow uses; it retries when another writer lands. The decision vocabulary is `continue` / `stop` /
`inconclusive`, and a FAIL is recorded as `stop`. The first verdict is final. I tested this on a local copy of the
registry: for #29 (screened), the checked path refuses `continue` and `inconclusive` and accepts `stop` with
`rule_checked: true`. Nothing was uploaded.

**Defect D-1 (found here; blocker for evidence integrity).** For 11 of the 12 trials the published registry holds a
`failed` row with no results, and B04 has one more (#35). Only #29, #33 and #34 carry results. The cause is in
`research.yml`. GitHub's `fromJSON`/`toJSON` turns `3.0` into `3` in the matrix cell, so the cell's agent id (from
`target_atr: 3`) differs from the id the plan computed (from `3.0`). `cells._read_cell_rows` then refuses the cell's
row and `record` writes a `failed` row in its place. A local simulation of plan, matrix round trip and `_planned`
reproduces the mismatch for the variants as pre-registered and shows a match when integral values are written as
integers. K is still honest, because the failed rows count as trials. The evidence, though, is missing from the
registry.

The fix goes to the implementer, reviewed by the quant-reviewer, **due 2026-10-25**:
- (a) `cells.plan` canonicalises integral floats the way the matrix will before resolving the agent id, and a test
  covers the round trip.
- (b) A one-off, reviewed repair attaches each cell artifact's results to its `failed` row: #21-#28 and #30-#32 from
  the local and run artifacts, #35 from run 39104042350. It keeps the trial number, ts and config_hash, so K does not
  change and the trial numbers are not re-ordered.
- (c) **D-2:** `preregistered_decisions` applies the batch rule's "net mean R <= 0 -> stop" when the cited rule is
  `batch-2026-10-18.md#2-reading-rule`, including below the floor.

**Commands for the evaluator.** These are run from the repo on the Mac with gh authenticated. Run them now only for
#29. Run them for the rest after D-1(b) and D-2 are merged.

```bash
scripts/registry_release.sh fetch ~/tmp/reg/registry.jsonl
uv run python - <<'EOF'
from pathlib import Path
from goldbot.research.registry import TrialRegistry
reg = TrialRegistry(Path.home() / "tmp/reg/registry.jsonl")
RULE = "batch-2026-10-18.md#2-reading-rule"
# (ts, config_hash, reason): one line per trial, taken from the published row
for ts, h, why in [
    ("2026-10-18T06:17:02.822263+00:00", "672a77cc8c2f",
     "FAIL by batch-2026-10-18.md s2: gross t 0.48 < 2.0 on 3,874 events; rule-only net -0.524 R; net < 0 in 2024 and 2025"),
]:
    print(reg.record_verdict({"ts": ts, "config_hash": h}, "stop", why, rule=RULE)["verdict_of"])
EOF
scripts/registry_release.sh save ~/tmp/reg/registry.jsonl
```

If D-1(b) is not merged by 2026-10-25, the evaluator records `stop` on the 11 `failed` rows on 2026-11-01. The path is
the same, but it is not checked, because a failed row has no results. Each reason cites the run id and this file, so
no verdict waits past a month. B04 (#33-#35) gets its own evaluator verdict by the same rule before anything else
uses it.

## 4. B05-B13: continue, same order, integers written as integers, `lot_fit=true`

- **Continue, as pre-registered and in the order of section 1.** K is already priced at 53 for every reading
  (rule 2.3), so skipping trials saves nothing in the deflation bar. B08 (H-04) and B10 (H-01) discharge two Q1 queue
  items. The 1h, 4h and 1d evidence holds for a larger account or a micro contract even though no survivor can place a
  0.01 lot at £1,000 (shadow only, `sizability-2026-10-18.md`). Reordering would buy nothing and would depart from
  the written order.
- **Transcription amendment, made before B05 is dispatched.** Integral floats in the variants are written as
  integers, for example `"target_atr":4` instead of `4.0`. The resolved configuration is identical; it is exactly what
  the cell would run anyway. Only the planned agent id now matches it. I simulated this for all of B05-B13: every cell
  matches, and B08 and B10 keep their pre-registered ids (`asia_drift-g0-98dc13a518`, `tsmom-g0-c12c9afb24`). If
  D-1(a) merges first, the original text works as well.
- **`lot_fit=true` (F3).** It is reporting only. `cells.argv` adds `--lot-fit`, and `research_pass` passes a
  `LotFitSpec` to the report. It is not part of `spec.config`, the agent id or `config_hash`; the simulation shows the
  same ids with it on. It is not used with `--discover`, `--cpcv` or `--h16`, and none of those apply here.
- Dispatch one run at a time (blocker 0.2). Each run gets its verdict before its result is used.

```bash
gh workflow run research.yml --ref main -f specialist=tsmom -f from_year=2010 -f to_year=2025 -f lot_fit=true -f variants='[{"max_bars":96,"target_atr":4},{"schedule_h":24,"max_bars":96,"target_atr":4},{"min_score":1}]' -f rule_ref='batch-2026-10-18.md#2-reading-rule' -f rationale='BATCH-2026-10-18 B05 R-06 regime retest: 1h tsmom had gross +0.061 R (t 2.63) and failed on costs; larger target, longer hold, stronger signal'
gh workflow run research.yml --ref main -f specialist=trend -f from_year=2010 -f to_year=2025 -f lot_fit=true -f variants='[{"adx_min":25,"target_atr":4,"max_bars":96},{"stop_atr":1,"target_atr":3},{"adx_min":30,"pullback_atr":1,"target_atr":4,"stop_atr":1.5,"max_bars":96}]' -f rule_ref='batch-2026-10-18.md#2-reading-rule' -f rationale='BATCH-2026-10-18 B06 R-03 retest on 1h: trend-day runner targets (3-4 ATR) under the 2024-26 regime'
gh workflow run research.yml --ref main -f specialist=breakout -f from_year=2010 -f to_year=2025 -f lot_fit=true -f variants='[{"range_bars":24,"target_atr":3,"max_bars":48},{"max_range_atr":3,"min_tick_ratio":1.2}]' -f rule_ref='batch-2026-10-18.md#2-reading-rule' -f rationale='BATCH-2026-10-18 B07 R-04 retest on 1h: one-day range and looser compression filter'
gh workflow run research.yml --ref main -f specialist=asia_drift -f from_year=2010 -f to_year=2025 -f lot_fit=true -f variants='[{}]' -f rule_ref='preregistration-2027Q1.md#H-04' -f rationale='BATCH-2026-10-18 B08 H-04 Asia-session drift exactly as pre-registered, moved forward from Q1 2027 by owner directive 2026-10-18'
gh workflow run research.yml --ref main -f specialist=tsmom -f from_year=2010 -f to_year=2025 -f lot_fit=true -f variants='[{"timeframe":"4h","max_bars":24,"target_atr":4},{"timeframe":"4h","max_bars":12,"min_score":1},{"timeframe":"4h","max_bars":30,"target_atr":5,"stop_atr":2}]' -f rule_ref='batch-2026-10-18.md#2-reading-rule' -f rationale='BATCH-2026-10-18 B09 tsmom 4h: 4-5 day holds and a stronger signal (C4H itself is trial 17 and stays in Q1)'
gh workflow run research.yml --ref main -f specialist=tsmom -f from_year=2010 -f to_year=2025 -f lot_fit=true -f variants='["slow"]' -f rule_ref='preregistration-2027Q1.md#H-01' -f rationale='BATCH-2026-10-18 B10 H-01 slow TSMOM exactly as pre-registered (retire-only), moved forward from Q1 2027 by owner directive 2026-10-18'
gh workflow run research.yml --ref main -f specialist=tsmom -f from_year=2010 -f to_year=2025 -f lot_fit=true -f variants='[{"timeframe":"4h","signal_tf":"1d","atr_tf":"1d","lb_fast_h":480,"lb_mid_h":1440,"lb_slow_h":2880,"vol_window_h":1440,"min_score":0.5,"target_atr":2,"stop_atr":1,"max_bars":62},{"timeframe":"1d"},{"timeframe":"1d","max_bars":20,"target_atr":4}]' -f rule_ref='batch-2026-10-18.md#2-reading-rule' -f rationale='BATCH-2026-10-18 B11 slow trend: H-01 signal with a 10-day hold, and the 1d tsmom horizon (daily rows are retire-only at 150 events)'
gh workflow run research.yml --ref main -f pooled=15m -f from_year=2010 -f to_year=2025 -f lot_fit=true -f rule_ref='batch-2026-10-18.md#2-reading-rule' -f rationale='BATCH-2026-10-18 B12 pooled 15m meta-model (never trialled)'
gh workflow run research.yml --ref main -f pooled=1h -f from_year=2010 -f to_year=2025 -f lot_fit=true -f rule_ref='batch-2026-10-18.md#2-reading-rule' -f rationale='BATCH-2026-10-18 B13 pooled 1h meta-model, rerun of trial 20 config on the current feature version'
```

Expectation, stated before the results: 0 PASS is the most likely outcome. R-07 already says a meta-model cannot
create an edge, and B12 and B13 fit nothing unless a member passes P4.

## 5. Research priorities after B01-B04 (ranked against the north-star)

The finding that drives the order: at £1,000 the binding constraint is the **stop in dollars**, not the signal
family. High volatility makes cost cheap in R (good) but pushes ATR stops past the 0.01-lot fit of $7.98 (bad). Only a
structure-based stop gets both at once.

| Rank | Item | Why now | Pre-registration / trial cost | Owner, date |
|---|---|---|---|---|
| 1 | **D-1/D-2 registry repair** | Without it, results do not reach the registry, verdicts cannot be checked, and gap_watch cannot see a pass | none (engineering) | implementer + quant-reviewer, 2026-10-25 |
| 2 | **H-25 precision entry with an S-1 regime gate**: 15m signal, 1m/5m structure stop of $2-6, traded only when ATR15 is above a pre-stated floor (S-1: skip low-ATR bars, where cost in R is high) | The one design where the high-volatility premise and lot fit agree. It is the honest successor to the cost paradox | New pre-registration (rule, ATR floor fixed from the cost arithmetic, not from returns; event-count estimate per variant; forward-shadow reading plan). Counted in K when run | strategy-researcher drafts by 2026-10-25; research director rules |
| 3 | **S-2 hour features + S-1 ATR-regime features as feature groups in O-4/H-02 discovery** | Feature-first: a group costs K_eff about 1 each, not a trial. Hour-of-day overlaps N1 `tod_periodicity`, so they become one group, not two | Amendment under O-4 before the group map is pinned (due 2026-12-11) | strategy-researcher, 2026-11-06 |
| 4 | **TradingView SMC/MFI replica** | Lets the owner's own method (owner-method-study) be backtested over 2010-2025 | **As bulk features only** (R-09 retires SMC as a standalone rule; MFI is already `money_flow`). Before any screen it must pass two checks: (i) a parity test against the exported TradingView plots; (ii) a **no-repaint / point-in-time check**. SMC pivots, BOS and order blocks confirm N bars late, so `available_utc` = confirmation bar close and `lookahead_check` shows 0 columns. A standalone-rule trial needs new evidence first | implementer (replica), quant-reviewer (PIT), then group amendment; research director rules on admission |
| 5 | **Adaptive demo limits** | They change what can be placed on the demo, so lot-fit reports must state which limits applied | **Not a research variable** (rails: RiskGate only path, 1% cap, daily loss limit). Research never tunes a limit on backtest outcomes. Any change is owner-only, through an ADR | program-director / CFO; research reports only |
| 6 | B05-B13 readouts | Evidence for larger accounts; H-01/H-04 discharge | already pre-registered | evaluator, as each run finishes |

**Not funded:** any neighbour or variant of B01-B04 in Q4 2026 (batch rule). Any in-sample regime retest (section 2).
Shadow of FAIL configurations: a FAIL is retired, and only a LEAD may shadow.

**Honest scorecard line:** 0 of 32 trials have passed to date (20 Q4 + 12 batch read). K for reading is 53. No profit
is forecast. The north-star (£50 on opportunity days) has no strategy behind it yet. The nearest route is rank 2,
read on forward data.

## 6. Batch close-out, B04-B12 (research director, 2026-10-18 about 07:40 UTC)

All 32 batch trials (#21-#52) have run and each has a recorded verdict. B13 was withdrawn before it ran, to fund H-02
(`preregistration-2026Q4-pullforward.md`). **Batch result: 0 PASS, 0 LEAD, 31 stop, 1 inconclusive.** K is 52 now
and 53 once H-02 runs.

Verdicts #44-#52 were recorded on 2026-10-18 at 07:30 UTC through the sanctioned path:
- the published registry was fetched, `TrialRegistry.record_verdict` was applied (code 91dc9cf, with D-2 and
  `BELOW_FLOOR_STOP_RULE`), and the compare-and-swap was saved with `goldbot.research.registry_sync save`;
- the race guard was checked twice: `gh run list --workflow research.yml` and `preregister-quarter.yml` showed
  nothing in progress or queued before the fetch and again before the save;
- the registry was then re-fetched: 84 rows, 52 trials;
- a dry run on a copy came first. The files are under `~/tmp/research/verdicts2/` (`record3.py`, `dry/`, `live/`,
  `verify/`).

Verdicts #33-#43 were recorded earlier the same day by the same path. Gross is rule-only at mid prices. Net charges
the bar spread, 0.37 $/oz and the swap prior. "Evidence" is the registry's `family_evidence`: a below-floor stop is
not counted against its family.

| Trial | Batch row, config | Events | Gross R (t) | Net R (t) | Verdict (rule) | Evidence |
|---|---|---:|---|---|---|---|
| #33 | B04 mean_reversion 1h, band 2.5, terc <= 2 | 889 | -0.039 (-1.47) | -0.185 (-6.9) | stop (batch, below floor) | no |
| #34 | B04 mean_reversion 1h defaults | 699 | +0.009 (0.30) | -0.156 (-5.1) | stop (batch, below floor) | no |
| #35 | B04 mean_reversion 15m, band 2.5, stop 1.0 | 3,268 | -0.004 (-0.20) | -0.384 (-19.2) | stop | yes |
| #36 | B05 tsmom 1h, min_score 1 | 1,691 | +0.084 (2.43) | -0.104 (-3.1) | stop (screen pass, net <= 0) | yes |
| #37 | B05 tsmom 1h, schedule 24 h, target 4, 96 bars | 1,740 | +0.031 (0.80) | -0.118 (-3.1) | stop | yes |
| #38 | B05 tsmom 1h, target 4, 96 bars | 3,055 | +0.053 (1.77) | -0.133 (-4.6) | stop | yes |
| #39 | B06 trend 1h, stop 1, target 3 | 1,784 | +0.049 (1.44) | -0.186 (-5.7) | stop | yes |
| #40 | B06 trend 1h, ADX 30, pullback 1, target 4 | 573 | +0.013 (0.26) | -0.146 (-3.0) | stop (below floor) | no |
| #41 | B06 trend 1h, ADX 25, target 4 | 784 | +0.067 (1.38) | -0.128 (-2.7) | stop (below floor) | no |
| #42 | B07 breakout 1h, one-day range | 0 | — | — | stop (degenerate) | no |
| #43 | B07 breakout 1h, looser compression | 4,783 | +0.010 (0.63) | -0.215 (-14.3) | stop | yes |
| #44 | **B08 H-04** asia_drift as pre-registered | 4,025 | **+0.043 (3.09)** | -0.093 (-6.8) | **stop** (H-04 rule: screen pass, net <= 0) | yes |
| #45 | B09 tsmom 4h, min_score 1, 12 bars | 1,192 | +0.053 (1.45) | -0.077 (-2.1) | stop | yes |
| #46 | B09 tsmom 4h, target 5, stop 2, 30 bars | 1,247 | +0.080 (1.97) | -0.048 (-1.2) | stop | yes |
| #47 | B09 tsmom 4h, target 4, 24 bars (model fitted) | 1,655 | +0.090 (2.37) | -0.065 (-1.8) | stop (net <= 0; design gates fail: per_fold, positive_years, DSR n/a on 85 model trades) | yes |
| #48 | **B10 H-01** slow tsmom as pre-registered | 294 | +0.166 (2.04) | +0.002 (0.03) | **inconclusive, held** (H-01 rule: below floor, net > 0) | — |
| #49 | B11 tsmom 1d, 10 bars | 457 | +0.123 (2.12) | -0.024 (-0.4) | stop (daily retire-only band 150-999, net <= 0) | no |
| #50 | B11 tsmom 1d, target 4, 20 bars | 309 | +0.179 (2.02) | -0.023 (-0.3) | stop (daily band, net <= 0) | no |
| #51 | B11 tsmom 4h on the H-01 1d signal, 10-day hold (62 bars) | 509 | +0.124 (2.03) | -0.030 (-0.5) | stop (ruling 6.1) | no |
| #52 | B12 pooled 15m (intraday_momentum, mean_reversion, session_open) | 9,116 | -0.004 (-0.48) | -0.250 (-27.1) | stop, not rule-checked (row is `failed`; numbers from run 39107483173's artifact) | yes |

### 6.1 Ruling on #51 (retire-only)

#51 executes the H-01 daily signal on 4h bars. That is the same construction as B10, which rule 2.1 places in the
daily-signal band. The rule text lists only "the two 1d rows of B11", so #51's band is ambiguous. **The ambiguity
does not change the verdict.**
- Read as a daily-signal row: 509 events fall in the 150-999 band, which can retire a row but never pass it. Net mean
  R is -0.030 <= 0, so the row is retired.
- Read as a 4h row: 509 events are below the 1,000 floor. The FAIL clause (net <= 0) has no floor (ruling 1.1), so
  the row stops.

**Verdict: stop.** The configuration is retired. Because it is below the 1,000 floor, the registry records
`family_evidence: false`. It is **not** evidence against H-01 or slow trend as a family. Its gross t of 2.03 cannot
pass below 1,000 events under either reading, and no re-run is allowed. For later pre-registrations, any row on a 1d
signal is in the daily band, whatever bars it executes on.

**Erratum (the verdict rows' reason text, not the decisions).** The reason strings written for #49-#51 swap the
variant labels:
- #49 is the 1d, 10-bar row (not "4h on 1d signal");
- #50 is the 1d, 20-bar, target-4 row;
- #51 is the 4h-on-1d-signal row.

The registry configs above are authoritative. Each decision is `stop` under every reading (net <= 0 in all three).
The first verdict is final, so the rows stay as written, and this erratum is the record.

### 6.2 What the batch says (no new test; statements about rule-required tables)
1. **15m: no gross edge.** 0 of 13 15m trials and the pooled 15m model have gross t >= 2. The best is #25 at 1.80.
   The pooled screen is gross -0.004 R on 9,116 events. At 15m the problem is signal, not cost.
2. **1h, 4h and 1d momentum: a small gross edge that costs remove.** Gross ranges from +0.04 to +0.18 R, with t about
   2.0-2.4 in 7 trials (#36, #47, #48-#51, plus H-04 at 3.09). Every one is rule-only net <= 0 under the cost priors.
   The evaluator tables show the edge mostly on the long side. The short side carries a zero-swap prior, so long and short are not symmetric
   evidence.
3. **H-04 is the strongest gross signal so far:** t 3.09 on 4,025 events. That is still below 3.94 at K = 53 even at
   zero cost (`costs-binding-2026-10-18.md` section 3), and it is net -0.093 R on priors. It is stopped by its own
   rule. No H-04 re-test is funded, and any revisit needs measured costs and a new pre-registration. O-7, O-8 and O-9
   lapse.
4. **No known signal reaches t about 3.94 at K = 53, even at zero cost.** Trials spent on cost tweaks of these signals
   can at best produce a LEAD. The research order below follows from that.

### 6.3 Defects found at close-out (accountable, date)
- **#52 is a `failed` row with no results.** It is the same one-cell artifact defect, fixed by `bb81934` on the branch
  only. A reviewed repair attaches run 39107483173's `cell-0/rows.jsonl` to #52, keeping the trial number, ts and
  config_hash, as in D-1(b). Implementer and quant-reviewer, **2026-10-25**.
- **`bb81934` must reach `main` before any one-cell run.** H-02 and CB-T1 are both one-cell runs. Release-manager,
  **2026-10-21**, the same date as H-02's P2.

## 7. Next research order (ranked against the north-star; written before any of their outcomes)

| Rank | Item | Trials | Why this order | Status / gate | Accountable, date |
|---|---|---:|---|---|---|
| 1 | **H-02** 4h feature discovery (one trial, ~120 features in 5 clusters plus families, `f-51ac5ebabb`, no `smc_replica`) | 1 (#53, the last 2026Q4 slot) | Highest information per trial: it screens H-03/05/07/08/10 together and may find a gross signal stronger than the momentum family's t of about 2. Only a signal with gross t of about 4.2 or more can pass | Ready. Open: P2 (quant sign), and preferably `bb81934` on `main` (pull-forward 2.4a). Command in pull-forward 2.5, unchanged | research director dispatches; quant-reviewer P2 2026-10-21 |
| 2 | **CB-0: `costs-v1` with the measured broker spread** (K1 GBP swap, K2 GBP commission, K3 publish with slippage = prior; hourly spread profile after 20 days) | 0 | Every net figure so far charges priors (about 1.07 $/oz against perhaps 0.3-0.5 on Raw). Measuring costs cannot pass a trial, but it decides which LEADs are worth a holdout look and whether 15m stops become affordable | K1-K3 by 2026-10-30. First publication about 2026-11-06 if the MT5 login lands by 2026-10-22. Profile about 2026-11-20 | implementer + quant-reviewer (K1-K3); sre/owner (publish token); data-engineer (profile) |
| 3 | **CB-T1** tsmom 1h, cost-amortised (stop 2.0, target 4.0, 96 bars, min_score 1.0; `preregistration-costs-2026-10-18.md`) | 1 | It closes 1h tsmom exits for good if it fails, and it is the only cost-aware market-order trial pre-registered. The stated prior is PASS <= 2% and LEAD about 15-20%, and a 2 x ATR1h stop is unsizable at £1,000, so any LEAD is shadow only | **No 2026Q4 slot** (53 = 52 + H-02). Either (a) Q1 free reserve, dispatch from 2027-01-04, or (b) the owner raises `trial_budget_overrides."2026Q4"` 53 -> 54 (K at its verdict >= 54, gate-1 t still about 3.95). It is also a one-cell run, so it needs `bb81934` on `main` | owner decision via the program-director (24 h); research director dispatches |

**Owner decision (via the program-director, with a recommendation).** CB-T1's slot:
- **(b) Raise 2026Q4 to 54 and run CB-T1 the week after H-02 is dispatched.** This fits the owner's 2026-10-18
  directive (no waiting), and one more trial moves the gate-1 bar by less than 0.01 in t.
- **(a) Wait for Q1.** This keeps the quarter at 53, and the batch shows little is lost by waiting.

**Research recommendation: (b), but only after H-02 is dispatched, so that H-02 keeps #53.** Expectation is stated
plainly: the most likely CB-T1 reading is FAIL or LEAD, not PASS.

**Not funded, and why:**
- any neighbour of a batch trial (batch rule);
- an H-04 or H-01 re-run (stopped / inconclusive with no pre-registered power gain);
- 15m cost variants (no gross edge to keep);
- TVR-1/TVR-2 (withdrawn before running: TVR-1 has 441 events, below the floor, and TVR-2 is below its gate-1
  minimum; 0 trials). The SMC replica lives on only as an O-4 feature group.

H-25 (precision stop with the S-1 regime gate) and O-4 stay as ranked in section 5 and the pull-forward file. Neither
gets a slot in 2026Q4.

**Honest scorecard line (updated):** 0 of 52 trials have passed (20 Q4 + 32 batch). K = 52 now and 53 with H-02. No
strategy exists yet behind the north-star (£50 on opportunity days). No profit is forecast. The next information is
H-02's verdict and the first measured broker spread.
