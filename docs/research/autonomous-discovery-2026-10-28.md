# Autonomous strategy discovery: design (2026-10-28)

Written by the research director on 2026-10-28, base `d1a754bb`, for the owner's directive of that day: "The system
should be able to learn and devise novel new strategies that will just work and profit." Decision record: ADR 0058.
Backlog: item 140 (phase 1). The CTO is consulted on the platform questions (section 5); nothing here was decided by
asking the owner.

**Scope limits.** This design runs no trial, spends no budget slot, reads no data and does not touch the holdout
(2025-10-01 .. 2026-09-30). It does not touch `icm-live`, RiskGate, the 1% per-trade cap, the daily loss limit, stops or
the owner's one-click confirmation of real-money entries. The 2026-10-30 live pilot (ADR 0055) is unaffected. Every
number about hit rates in section 6 is a judgement prior, labelled as one. None of it is a measurement.

---

## 0. The decision in one paragraph

We build a **discovery funnel**. Several generators propose strategies in one typed language (a *StrategySpec*). Every
proposed spec is written to an append-only **search ledger** *before* it is evaluated, so each one counts in K. Search
and selection use only the **explore window (2010-2017)**, which the code physically truncates. A spec reaches the
**confirm window (2018-01 .. 2025-09)** only as a pre-registered registry trial with a fixed reading rule, charged to
the quarter's budget. It reaches the **holdout** once, through the existing `holdout_scored` path. It reaches a
**demo arm** as a data row run by one reviewed interpreter, with its own pre-registered forward reading rule. It
becomes **live-eligible** only through the existing promotion gates. The machinery is designed to say "nothing
survived" most of the time. That outcome is a correct answer, not a failure.

What "will just work" can honestly mean: no process can guarantee a strategy that profits. This one can guarantee that
anything it promotes has beaten (a) the best that the *same search* finds on data with no edge, (b) costs at the
conservative table and at 1.5x that table, (c) a window it never saw while it was being selected, and (d) live demo
fills read once by a rule fixed in advance. That is the most "just works" that statistics allows.

## 1. Why the current machinery is not enough, and what we keep

| Exists today | Keep | Gap this design closes |
|---|---|---|
| `research/generator.py`: a frozen one-dimension-at-a-time grid. DESIGN "Bounded research loop": "bounded feature and parameter search, not discovery of novel strategies" | its ordering and refusal logic | It cannot combine primitives or propose a new entry. ADR 0058 lifts the DESIGN boundary, under the controls below |
| `research/discovery.py`: fold-internal stability selection of *features* for a fixed rule | as the meta-labelling engine (G4) | It selects features. It does not select entries |
| `research/screen.py`: primary-signal screen (gross t >= 2, >= 1,000 events) | as a stage-1 floor | Its threshold is per-test. A search over 10^4 specs needs a search-level null |
| `research/cpcv.py` `pbo`; `research/metrics.py` `deflated_sharpe` | both | No White reality check / Hansen SPA and no effective-K (clustered) estimate |
| `research/registry.py`: K, budget, `preregister`, `holdout_scored`, `passed_gates` | all of it, unchanged in meaning | No way to count 10^4 cheap looks without 10^4 JSON rows. Solved by the ledger (SQLite) plus one registry row per batch carrying `k_contrib` |
| `research/arms.py` `arm_hash`, frozen `config/exploration_arms.yaml`; ADR 0045 weekly tier (at most 4 reviewed variants a week) | all of it. Discovered arms use the same weekly tier | Every new arm today needs new rule code. G-spec arms need one reviewed interpreter, after which a new arm is a data row |
| `research/promotion.py`, `population.py` (DSR_PROMOTE with the population as K), ADR 0038 rule models | all of it | none: live eligibility goes through these unchanged |
| `research/owner_history.py` (private, outside the repo) | its privacy rules, verbatim | It turns seeds into prose. Here they become specs, with a holdout-contamination flag (section 2, G5) |

## 2. Generators (several, in parallel, one language)

**The language (StrategySpec).** A spec is a small typed tree:

```
spec := timeframe × session_window × side_rule × entry_predicate × filter_predicate? × exit_template
entry_predicate / filter_predicate := AND/OR (depth <= 3, <= 4 leaves) of typed leaves:
  level event      (cross/reject/retest of pdh, pdl, pdc, asia/london/ny hi-lo, pwh/pwl, pmh/pml, round numbers, Fibonacci
                    retracements of the prior day/week swing)
  band event       (touch/close-outside/re-enter of BB, KC, Donchian)
  oscillator state (RSI, MFI, Stoch, CCI quantile buckets; divergence flags)
  volume state     (tick-volume quantile, VWAP distance in ATR, session VWAP side)
  structure        (SMC: BOS, CHoCH, order block / FVG touch, liquidity sweep; box break and close; NR4/NR7; inside bar)
  regime           (realised-vol quantile, Hurst band, trend strength, wavelet energy band, day-type score, opportunity score)
  time             (minutes since session open, weekday, month-turn, pre/post tier-1 release)
  macro (4h/1d)    (DXY/real-yield change sign and quantile, COT positioning bucket), point in time via asof_join
side_rule := long | short | with(primitive direction) | against(primitive direction)
exit_template := one of a FIXED menu of 6: {stop 1.0 ATR / 1.5 ATR / structural} × {target 1R / 2R / time exit at N bars}
```

Numeric parameters come from a **quantised grid** (for example, RSI thresholds only in {20, 30, 70, 80}), so the search
space is finite and countable. Every leaf wraps an existing point-in-time feature from `goldbot/features` (survey,
session, smc_*, structure, mtf, macro, tod_periodicity, day_type). No new feature math is invented inside the
generator. **Canonical hash:** commutative operators are sorted, duplicate leaves removed, and parameters written in
canonical form, so two spellings of one rule share one hash and one K.

| Gen | What it does | Prior (why it might find something real) | Share of search compute (start; re-weighted by survivors, section 7) |
|---|---|---|---|
| **G1 Combine** | An enumerated grid of the 34 existing families × at most one filter leaf (regime, time, opportunity score, macro) × the exit menu. Finite, countable, cheap | Highest. It conditions mechanisms we already run (tsmom 4h has the only gross edge, +0.06 R) on the regime where they should work. The demo leaders (session_vwap_fade, nr4_break; n = 25 and 23, noise at K about 358) are allowed as *seeds*, never as evidence | 35% |
| **G2 Grammar / GP** | Strongly-typed genetic programming over the language. NSGA-II objectives: min-fold net t across purged CV folds inside 2010-2017; tree complexity; turnover. Structural primitives (sessions, levels, time, events) are weighted 2:1 over pure oscillators | Low per spec, broad. The literature on GP rules is mostly negative out of sample (section 6). Kept because it is the only generator that finds what nobody wrote down | 35% |
| **G3 LLM hypotheses** | A Claude researcher (strategy-researcher-staff) reads `knowledge-base/`, `state-of-the-art.md`, the playbook and its own previous specs' *explore-window* results, and writes at most 20 specs a week as YAML. Each spec must state a mechanism. It never sees confirm or holdout results | Medium: literature priors. **Leak risk:** the model's training knowledge covers 2018-2025 market history, so a G3 spec may encode hindsight about the confirm window. Mitigation: G3 specs carry `prior_informed: true`, they need the forward demo read even after a holdout pass, and their per-generator hit rate is tracked separately | 10% (compute is trivial; the cap is on count) |
| **G4 Meta-label** | The existing `discovery.py` / H-62 path on top of a family whose rule-only gross edge already passes `screen.py` | Correct by construction (it cannot create an edge, only filter one). Useless until a family passes the screen; then the best use of a slot | runs on demand |
| **G5 Owner trades** | `owner_history.py` seeds become specs automatically when an MT5 export is present in the private folder. The owner is never asked for one | High prior: the owner reports £30-50 a day on £50 by hand. **Contamination:** his trades fall in 2025-2026, inside the holdout. A G5 spec is therefore flagged `holdout_contaminated`, its holdout read is void, and its clean test is the forward demo read under the stricter rule (section 4.5) | runs when data appears |
| **G0 Null** | The same G1/G2 search run on null data: the timing kept, the side drawn by `hash_side`, the outcomes taken from that random side. It is the calibration for everything else, not a generator of candidates | n/a | 20% (fixed, never re-weighted down) |

## 3. Honesty machinery

### 3.1 Counting (K)
- **Ledger first.** `state/discovery.db` (SQLite WAL; robust-storage rule: no new JSON state). Row = spec hash,
  canonical spec, generator, generator version, parent hashes, written_utc, and hash-chain digest. It is written
  *before* the evaluator runs, and the evaluator refuses an unledgered hash.
- **K_search** = distinct spec hashes ever evaluated, across all generators and generations (re-evaluating a hash
  adds nothing; null-run specs are counted separately as K_null and never mixed in).
- **K_eff** = the number of clusters of candidate daily-return series (ONC clustering, López de Prado 2019). It is
  used *inside* the DSR alongside the cross-trial variance of Sharpe. The raw K_search is always reported next to it.
- **Registry link.** One registry row per discovery batch: kind `discovery_batch`, `k_contrib = new distinct hashes`.
  `TrialRegistry` K = trials + Σ k_contrib. A batch row takes no budget slot (it never touches the confirm window). The
  DSR of any discovered candidate is read at the full registry K. Human pre-registered hypotheses written
  independently keep the registry's K. A human hypothesis *inspired by* discovery output inherits the batch's K.

### 3.2 Explore stage (2010-01 .. 2017-12; selection happens only here)
1. **Window guard.** `autodisc.windows.ExploreFrame` truncates at 2017-12-31 23:59 UTC at load time. Any later bar
   raises. The test asserts that the evaluator's input digest equals the truncated digest.
2. **Outcome tensor.** For every bar × side × exit template, the triple-barrier outcome comes from the 1m bid/ask path
   (`labels/triple_barrier.py`), net of costs from `execution/costs.CostTable` at the conservative value (the max of
   the refit cell and the table, ADR 0045). Costs are in R at that day's spread and ATR. A spec evaluation is then a
   boolean mask and a sum, about 1 ms.
3. **Floors (no return read):** events >= 300 (15m/1h), >= 150 (4h), >= 100 (1d, retire-only, as `screen.py`); time in
   market <= 40%.
4. **Fitness (G2 objective and stage-1 score):** min over 4 purged, embargoed CV folds (walkforward purge/embargo
   days) of the net mean-R t-statistic, clustered by session day.
5. **Side and drift control:** long-only and short-only net R are reported separately. A spec must beat the gold
   drift: excess net R over a same-timing, same-side, random-entry baseline. Gold rose about 8%/yr in 2018-2025, so a
   long-biased rule otherwise looks clever.
6. **Plateau test:** at least 60% of the spec's grid neighbours (each parameter moved ±1 step) have a positive net
   fitness. This rejects knife-edge optima.
7. **Search null:** the explore fitness must exceed the **95th percentile of the best-of-search fitness on G0's null
   replications** (20 replications per generator version, rerun whenever the generator or primitive set changes).
   This is the reality check for an *adaptive* search, which a bootstrap over only the evaluated set understates.
8. **White RC / Hansen SPA** (stationary bootstrap, 1,000 draws, mean block 5 days) over the batch's daily-return
   matrix against the random-side benchmark: the batch must reject at 10% before any member advances.
9. **PBO (CSCV, `cpcv.pbo`)** on the batch's top 50: PBO <= 0.30, otherwise nothing from the batch advances.
10. **DSR** at K_eff >= 0.90 on explore (informational at this stage; binding at confirm).
11. **Cost stress:** net mean R > 0 at 1.5x the conservative cost.
12. **Novelty** (section 3.4).

Passing 1-12 makes a spec an **explore survivor**. That is a recorded fact, never evidence of edge.

### 3.3 Confirm stage (2018-01 .. 2025-09; one look, pre-registered)
- At most **8 specs a month**. Each is an ad-hoc `registry.preregister` row (`rule_ref = autodisc:<hash>`) with the
  reading rule text generated from a fixed template and committed before the run. Each is one budget trial
  (`check_budget_reserved`: they never take reserved slots).
- It runs through the **existing pipeline** (`SpecSpecialist` adapter → `research_pass.py`: walk-forward, cross-fitted
  calibration, `gates.py`), so a passer has a calibrated model, as ADR 0038 requires for live.
- **Reading rule (frozen here):** pass ⇔ `gates.py` passes (positive years incl. 2021-22, DSR >= DSR_BAR at the full
  registry K) AND rule-only net mean R > 0 with clustered one-sided t >= the Holm-adjusted threshold for the month's
  batch (at 5%) AND net mean R > 0 at 1.5x cost AND the confirm-window mean R lies inside the explore-window 90%
  prediction interval (consistency, which catches regime-luck passes).
- An evaluator verdict row is recorded before any follow-up. A failed spec's descendants may be searched again in
  explore, but the failed hash can never be re-confirmed.

### 3.4 Novelty check
A spec is **novel** only if, on the explore window, (a) the Jaccard overlap of its entry bars with every existing
family, arm and earlier survivor is < 0.30, and (b) the |correlation| of its daily net-R series with each of them is
< 0.50. Otherwise it is a **variant** of the nearest one. It is still counted in K, it is labelled with that parent,
and to advance it must beat the parent incrementally: positive alpha of its returns regressed on the parent's, with
t >= 2. Novelty is a label and a hurdle, not a filter that hides failures.

### 3.5 Holdout (2025-10 .. 2026-09)
Only confirm passers, scored once per configuration through the existing `holdout_scored` / `gates.holdout_verdict`
path (positive mean R, one-sided t >= HOLDOUT_MIN_T). G5 and other `holdout_contaminated` specs skip it, and their
forward read is the stricter one in 4.5.

## 4. History → demo → live

1. **Arm row (automatic draft, reviewed merge).** A holdout passer gets an arm row:
   `{id: D<yymm>-<n>, rule: spec_rule, timeframe, params: {spec: <canonical>, spec_hash, prereg: <path#section>}, max_open: 1, status: run}`,
   `config_hash = arms.arm_hash(...)`. The arm runs only on demo accounts (`icm-probe`, `vantage-demo`, `icm-demo`).
   `load_arms` refuses `spec_rule` on any live-class account (a test enforces it). Caps, pots, `max_stop_usd`,
   `min_stop_usd` and RiskGate are inherited unchanged.
2. **One interpreter.** `research/arm_rules_spec.py` executes any spec through the same primitives as the explore
   evaluator. Quant-reviewer and trading-safety-reviewer review it once. After that a discovered arm is a data change.
   Each still goes through the ADR 0045 weekly tier (at most 4 reviewed variants a week across all sources): an
   auto-generated PR with the arm row and the pre-registration, the quant-reviewer checklist, and merge on green.
3. **Parity before the first fill.** The interpreter replayed on the last 8 weeks of broker bars matches the explore
   evaluator's decisions on >= 99% of bars, with every mismatch explained.
4. **Demo evidence (forward read, pre-registered in the arm's prereg).** One read at the later of >= 40 session-day
   clusters and >= 100 fills, or at 16 weeks, whichever comes first (a 16-week read with fewer clusters is
   "inconclusive", never a pass). Pass ⇔ cluster-robust one-sided t >= 2.0 on net R (broker costs as filled) AND the
   hit rate within 1 binomial SE of the backtest AND net R per trade >= 50% of the confirm-window estimate. The H-61
   style stop retires it early on the pre-registered loss bound. Fills only count after the arm's prereg date.
5. **Contaminated or prior-informed specs (G3, G5):** forward t >= 2.5, and the read happens at >= 60 clusters.
6. **Live eligibility** = the demo read passes AND `promotion.py` gates (4 weeks, 40 clusters, Sharpe > 0.8, DD ratio
   <= 1.5) AND `population` DSR_PROMOTE at the population K AND `registry.passed_gates` for the exact config. It then
   joins the live roster only through the program-director's release with trading-safety review. **Real-money entries
   stay owner-confirmed. Exits are never gated. Sizing and the live unlock stay owner-only.** Nothing in this loop
   writes to `config/accounts.yaml` live accounts, RiskGate or limits.

## 5. Compute and budget (CTO consulted on platform; the decision is recorded in ADR 0058)

| Where | Runs | Why |
|---|---|---|
| **Owner's Mac** (12 cores, 16 GB; `raw/` 1m history is local-only, data-local-only rule) | Outcome tensor build; G1/G2/G0 search; RC/SPA/PBO/DSR; confirm runs (`research_pass`); holdout scoring | The data lives here; the brain is a 2 OCPU / 12 GB production box |
| **Brain** (OCI A1, production) | The `spec_rule` interpreter inside the engine; nightly labelling/bandit (unchanged); the forward demo read job; receiving the ledger backup | No search: it must never compete with the engine for CPU |
| **GitHub Actions** | CI only | No market data may be published (ADR 0048) |
| **Claude** | G3 via dev-session staff runs (the owner's plan). At most 20 specs a week. The in-app agents' $40/month cap is not used for G3 | No new paid service |

Sizing estimate (to be measured in phase 1): 15m 2010-2017 is about 190k bars; the tensor is about 190k × 2 sides × 6
exits ≈ 2.3M outcomes, built once (< 1 h). With 4-fold fitness, a spec costs about 5 ms, so 100k specs ≈ 10 min on 10
workers. A G0 calibration (20 null replications) ≈ 3-4 h, run overnight. The Mac sleeps: jobs run under `launchd`
with `caffeinate` while it is plugged in. Missed nights are reported in the weekly scorecard, not hidden.
**Incremental spend: £0.** If Mac availability becomes the bottleneck (more than 2 missed nights a week for 2
weeks), the CTO decides between a capped brain worker (1 core, nice 19, 2 GB) and an extra Always Free shape. Paid
compute stays an owner decision.

**Trial budget.** Explore costs no budget slots (it costs K). Confirm costs one slot per spec. 2026Q4 (145) is held
almost entirely by the arm ids, so no confirm trial runs in Q4 unless the program-director extends it under the
2026-10-18 "backtest as much as needed" precedent (recommended: +8 for one December batch, reported on #34). 2027Q1:
**6 of the 14 open slots** go to discovery confirms. The 6 reserved slots (H-02, C4H, the 4 contingent ones) are not
touched.

## 6. What to expect (honest)

Evidence from the literature: data-mined technical rules mostly fail out of sample once the mining is accounted for:
Sullivan, Timmermann and White 1999 (7,846 rules, the best rule fails after 1986); Bajgrowicz and Scaillet 2012 (FDR
plus costs: no outperformance); Allen and Karjalainen 1999 (GP rules beat buy-and-hold only before costs); Neely and
Weller (GP FX rules decayed after the mid-1990s); Harvey, Liu and Zhu 2016 (t > 3 for new factors). Our own record:
20 trials this quarter, 0 passes; K about 358 by the arm-id rule.

Judgement priors (not measurements; the phase-6 scorecard replaces them with the counts):

| Stage | Expected pass rate | Per month at 8 confirms |
|---|---|---|
| Spec → explore survivor (all 12 checks) | 0.05-0.3% | 10-60 survivors out of ~20k specs |
| Explore survivor → confirm pass | 3-10% | 0.25-0.8 |
| Confirm pass → holdout pass | 40-60% | |
| Holdout pass → demo read pass | 40-60% | |
| **Spec → live-eligible** | **about 1 in 10^5** | **about 0.05-0.2 a month** |

- **First explore survivors:** phase 1, mid-November 2026. Expect many from G1, and expect most to die at confirm.
- **First confirm pass:** median expectation late January 2027. P(at least one by 2027-03-31) ≈ 40%.
- **First live-eligible strategy:** no earlier than about April 2027 (a demo read needs 40+ session-day clusters,
  about 2-4 months at 15m). P(at least one by 2027-06-30) ≈ 20-30%.
- **The most likely survivors** are conditional versions of mechanisms with a structural reason (session flows, the
  times around releases, slow trend on 4h/1d gated by regime), not exotic GP trees. Allocation follows that prior
  (section 2) until survivors say otherwise.
- **Effect on the north-star:** a survivor at +0.05-0.10 R net per trade is the realistic size. At the £1,000 account
  that is pennies a day. Its value is the base the £50-day runner exits (priority 2) can build on. This program does
  not by itself move the account to £50 days, and no one should report that it will.

## 7. Evolution and stopping
- Survivors at each stage enter the population as founders (`population.spawn_founder` from a passed trial), and their
  specs seed G2's next generation. Every child is a new hash and counts in K.
- **Generator scorecard** (monthly, in `scorecard-YYYY-MM.md`): per generator, specs, explore survivors, confirm
  passes, and the null-relative fitness. Search compute is re-weighted by Thompson sampling on confirm passes, with a
  floor of 10% per generator and G0 fixed at 20%.
- **Stop rules (pre-registered):** a generator with 0 confirm passes in 24 confirms is cut to its 10% floor. If the
  whole funnel has 0 confirm passes after 48 confirms (about 6 months), it goes to the quarterly SoA refresh: the
  primitive set is re-examined, and the budget moves back to hand-written hypotheses and exits research. That outcome
  will be reported plainly.

## 8. Phased build plan (ranked)

Phases 1-3 are sequential. Phase 5 starts after phase 1, in parallel with phase 2.

### Phase 1, this week (starts 2026-10-29, target 2026-11-06): the funnel's first stage with one generator
Builds: the spec language, the ledger, the window guard, the outcome tensor, the vectorised explore evaluator with the
cost model, the novelty check, the G0 random-side null, and generator G1 (Combine). No confirm, no budget, no arms.
- **New:** `goldbot/research/autodisc/__init__.py`, `spec.py` (pydantic StrategySpec, canonicalise, `spec_hash`),
  `primitives.py` (leaf → point-in-time boolean over `goldbot/features`), `windows.py` (ExploreFrame guard),
  `outcomes.py` (bar × side × exit tensor from `labels/triple_barrier.py` + `execution/costs.py`), `evaluate.py`
  (floors, CV-fold fitness, side split, drift baseline, plateau, cost stress), `ledger.py` (SQLite WAL, hash chain,
  K_search), `novelty.py`, `nulls.py` (G0), `gen_combine.py` (G1); `scripts/autodisc.py` (CLI: `build-tensor`,
  `run --generator combine`, `null`, `report`); `tests/research/autodisc/` (synthetic fixtures only).
- **Edited:** `goldbot/config.py` + `config/settings.yaml` (a `research.autodisc` block: windows reuse the existing
  explore/confirm/holdout dates, floors, caps); `.gitignore` (the `state/discovery.db` path).
- **Not touched:** `goldbot/risk/`, `goldbot/engine/`, `goldbot/execution/` (read-only import of costs),
  `config/accounts.yaml`, `config/exploration_arms.yaml`, the registry's budget logic.
- **Acceptance:**
  1. Parity: for 3 existing families (tsmom 4h, box_break_close 15m, session_open 15m) re-expressed as specs, explore
     event counts are within 1% and net mean R within 0.01 R of `screen.py` / `research_pass` on the same window.
  2. Guard: any read at or after 2018-01-01 raises. The test feeds a 2018 bar and asserts the refusal. The evaluator
     input digest equals the truncated digest.
  3. Ledger: every evaluated spec has a ledger row written before its result. The hash chain verifies. Re-evaluating
     a hash does not change K_search. Two spellings of one rule share one hash.
  4. Null: G0 produces 20 replications and stores the best-of-search distribution. On a synthetic series with an
     injected +0.15 R edge the evaluator flags it, and on pure noise <= 5% of runs produce a survivor (size test).
  5. Novelty: an existing arm re-expressed as a spec is labelled a variant of that arm.
  6. Throughput: 10k specs in <= 30 min on the Mac at 10 workers (measured and reported).
  7. Report: `scripts/autodisc.py report` prints K_search, survivors per check and the null percentile, with no
     confirm-window number anywhere. The test greps the output for 2018+ dates.
  8. Gates: ruff, mypy and the touched tests pass. The quant-reviewer has signed off on the evaluator and the null.

### Phase 2 (2026-11-09 .. 11-27): G2 grammar/GP and the full honesty set
`autodisc/gen_gp.py` (typed GP, NSGA-II, quantised parameters), `autodisc/stats.py` (stationary bootstrap White
RC/Hansen SPA, ONC K_eff, DSR via `metrics.deflated_sharpe`, batch PBO via `cpcv.pbo`), G0 extended to GP.
Acceptance: the injected-edge recovery and size tests for GP; PBO and SPA on every batch report; K_eff <= K_search
always; quant review.

### Phase 3 (2026-11-23 .. 12-11): confirm and holdout
`autodisc/specialist.py` (SpecSpecialist adapter for `pipeline`/`research_pass.py`), `autodisc/prereg.py` (template
pre-registration + `registry.preregister`), the registry `discovery_batch` row with `k_contrib` (`registry.py`,
`director.py` K count). Acceptance: a confirm run without its preregistered row is refused; budget checked under the
lock; the Holm threshold is computed from the frozen batch; holdout read once (existing test extended); contaminated
specs cannot be holdout-scored; the evaluator verdict row exists before any follow-up. Quant review.

### Phase 4 (2026-12-07 .. 12-23): demo arms
`research/arm_rules_spec.py` (the interpreter), `research/arms.py` (the `spec_rule` row; refused on live-class
accounts), an auto PR/prereg generator in `scripts/autodisc.py promote`, a forward-read job in `ops/jobs.py` (weekly
cadence, blinded otherwise per ADR 0045). Acceptance: >= 99% decision parity on the 8-week replay; a test that a
`spec_rule` arm cannot load on a live account; caps inherited (test); trading-safety and quant review of the
interpreter.

### Phase 5 (from 2026-11-09, parallel to phase 2): G3 LLM and G5 owner trades, G4 hook
`autodisc/gen_llm.py` (YAML intake, schema validation, the `prior_informed` flag, the 20-a-week cap),
`autodisc/gen_owner.py` (reads `owner_history` seeds from the private folder only, sets the
`holdout_contaminated` flag, writes nothing into a git tree), a G4 trigger when `screen.py` passes a family.
Acceptance: an LLM spec without a mechanism is refused; the cap is enforced; the owner miner refuses any path in a git
work tree (it reuses `owner_history` guards); the flags survive into the prereg.

### Phase 6 (from 2026-12, monthly): scorecard and re-weighting
The generator scorecard in the monthly research scorecard; Thompson re-weighting of compute; the stop rules in
section 7; the quarterly SoA refresh reads the funnel counts.

## 9. Risks the design accepts, and the controls

| Risk | Control |
|---|---|
| The confirm window is not virgin: humans ran about 110 trials on 2018+ | The Holm and DSR thresholds use the full K; a G1 "variant" must beat its parent incrementally; the holdout and forward reads are the clean tests |
| An LLM's hindsight about 2018-2025 | `prior_informed` flag, stricter forward read, separate hit rate |
| Owner trades overlap the holdout | `holdout_contaminated`: the holdout is void for those specs, and the forward read is the test |
| Regime: 2010-2017 gold differs from 2025-26 (price × 3, spread in R) | Everything in ATR/R units; the 1.5x cost stress; the consistency check at confirm; the forward read |
| "Many survivors" headlines | Survivors are reported only with K_search, the null percentile and the PBO beside them; no survivor is called an edge before confirm |
| Engine risk from a generic interpreter | One reviewed module, decision parity, demo-only load guard, RiskGate unchanged |
