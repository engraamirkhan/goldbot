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
