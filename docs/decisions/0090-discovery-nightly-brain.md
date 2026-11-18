# 0090: Discovery nightly on the brain: power-first, budgeted, niced

Status: proposed (implementer, 2026-11-18; research director's build 4 of the owner directive of 2026-11-18, "the
system finds new strategies itself"). Needs the quant-reviewer's GO (research path) and the sre's GO (a new brain
job) before it is enabled on any host. Amends ADR 0058 section 5 ("the brain runs only the interpreter and the
forward read") for this job only; respects ADR 0083 and ADR 0085.

## Context
- ADR 0083 retired `autodisc_nightly` from the brain: it only ever skipped there (`nightly_enabled` false, no bars) and
  nothing read what it wrote. The search kept running on the owner's Mac by launchd. The owner now wants discovery to
  run unattended, from our own evidence, every night.
- ADR 0085 rules that no K is spent on a test that cannot pass: "Start only if every power_g >= 0.5, simulated joint
  power >= 0.25, and n_eff >= n_eff_floor (200) at each fresh-data gate".
- The why-fail audit found no gross edge in single rules at 1m-15m; 1h, 4h and 1d come first.
- The brain is a 2-vCPU ARM brain (one core for the child, one for the engines); the scheduler is single-threaded.

## Decision
1. **A new brain job `discovery_nightly`** (daily 01:00 UTC, `max_late_hours` 1.5), off by default
   (`research.discovery.enabled`); a host turns it on together with `bars_path` in settings.local.yaml. Enabled
   without bars **fails** the job (health shows it): it never skips silently, the failure that retired its
   predecessor.
2. **Bounded for the brain** (`goldbot/ops/bounded.py`). The night runs in one `spawn` child at `nice` 15 (10..19,
   never level with the engines), oom_score_adj 500 on Linux (the kernel kills it before an engine), RLIMIT_DATA
   `memory_mb` 4096 (Linux; RLIMIT_AS is not used: pyarrow and numpy
   reserve virtual space far beyond what they touch), one BLAS/OpenMP thread. No timeframe starts once `budget_s`
   (5400 s, at most 7200) is spent, or when the last one took longer than what is left; the parent kills the child at
   budget_s + `kill_grace_s` (300 s, at most 600). The window 01:00-03:10 is clear of every other daily job (a test
   holds it). One run at a time: a non-blocking flock on state/autodisc/discovery.lock (the job and a foreground run
   share it; a held lock refuses). The job result carries the child's elapsed seconds and peak RSS (ru_maxrss); its
   health line warns past 80 % of `budget_s`. Every child that does not finish (killed, an error, MemoryError, the
   OOM killer) writes a `discovery_runs` row (`killed` / `failed`). Measured on the Mac: the 2010-2017 bars load in
   under 1 s, 2.8 M rows, 1.5 GB RSS; the brain dry run records its own peak RSS in the deploy note.
3. **Inputs** (`goldbot/research/autodisc/discovery.py`).
   - The G2 grammar and the honesty set of `nightly.search_and_judge`, unchanged, at the cost of record v3 (ADR 0084)
     per (session, exit) cell, in bps of the gold price where it was measured (`cost_price_usd` 4,000, below the
     4,068.7 median of its window: never fewer bps), so a 2010-2017 trade pays the same fraction of price (0.90 $/oz
     on a ~1,300 $/oz price would be three times the cost). v3 measures fills against the mid and has no spread
     split, so the labels read the mid and each label pays its full cell (session of its decision bar, exit it took):
     the spread is charged once. A table that states `spread_usd` per cell keeps bid/ask labels and charges only the
     rest. No cell is charged below its own commission. Swap is the settings prior, not measured swap, and the cost line says so. (Quant review B2: the first
     build charged the dearest cell in $/oz on top of the bar spread, the spread twice.)
   - Grammar-node priors (`GPConfig.leaf_priors`): demo labels (aux.db `exploration_obs`, gold, non-control,
     `label_sim_r`, void evidence excluded, as the learner reads them; only rows decided on or after HOLDOUT_END,
     2026-10-01; a frame without `decision_utc` gives none, so uniform priors) by family node and decision session; each
     registered discovered arm's labels (its forward result) on every node of its spec. Weight = exp(2 x sum R /
     (n + 50)) clipped to [0.5, 2.0]. Priors only change what is sampled; every evaluated spec counts in K, and the G0
     null reruns the whole search with the same priors, so the reality check prices the steering in.
   - Catalogue cards (config/research/candidates/): the ADR 0085 verdict only, taking each signal as its own day (the
     most favourable reading). A card is free text with no interpreter: a started card waits for its own
     pre-registration and costs nothing here.
4. **Power before K, per spec.** `gen_gp.evaluate_logged` reads an outcome-blind `admit` rule before it ledgers a spec:
   the search, its null and the plateau neighbours. The card of a G2 spec: XAUUSD alone, learn = its explore signals
   and distinct signal days, one fresh gate, TEST-OLD (3.92 years), at Holm rank 1 of 3 configs at the ledger year's
   alpha_j (`promotion_confirm.batch_alpha`). A refused spec reads no outcome, is never ledgered and gets fitness -inf
   ("NOT STARTED (power)"). The search redraws a refused draw at sampling time (within `_TRIES`), so its population
   is made of admitted specs; outcome-blind, and the G0 null runs the same loop. A refused plateau neighbour reads as
   not positive (conservative), and each judged candidate reports how many of its neighbours were admitted. A
   timeframe on which even an every-bar spec is refused is not searched at all.
   - TEST-OLD is read once per family, and every G2 spec is family `autodisc:G2`. Once EVAL-2's reads ledger or a
     promotion read in the registry holds a TEST-OLD read of that family, no spec is admitted and nothing is searched.
   - A survivor is offered as a demo learning arm only if its kill rule can read it by its `until` (quant ruling (a),
     2026-11-18): at the explore rate over 42 days, at least 50 replayed candidates (MIN_N) and 10 signal days
     (MIN_DAYS); otherwise the hand-off refuses it (`kill_unreadable`). The ADR 0085 verdict of the forward window
     over the learning kill rule's horizon (45 days or 300 fills, fresh, one-sided 5 %) is kept on the candidate row
     as `power_forward`, labelled "reported, not evidence": it never gates, because over that horizon no spec can
     reach the floor (at most ~32 signal days, so n_eff <= 32 x 4 = 128 at rho_day 0.25 against 200; power 0.5 for
     0.10 R at sd 1.2 needs n_eff ~390).
5. **Trial budget: two checks on two scales** (quant review B1: the first build compared spec K with registry rows).
   - Registry rows: the night writes at most one discovery batch row per timeframe, so `assert_budget(len(timeframes),
     ...)` against the quarter's trial budget, its used rows and the pre-registered reservation.
   - K: the quarter's summed discovery `k_contrib` (registry batches stamped in the quarter; the one-off prior-search
     seed excluded), plus the real ledger's K no batch counts yet (a night killed or failed before its sync), plus the night's planned K at its bound (timeframes x (population x (generations + 1) +
     max_candidates x 24 neighbours)) must stay within that quarter's entry of `research.discovery.k_cap_quarter`, a
     validated per-quarter map. The research director's decision (docs/research/discovery-k-cap-2026-11-18.md):
     2026Q4 15,000, 2027Q1 0 (moved to 30,000 only on a written Q4 read citing that doc's continuation rule); a
     quarter not listed refuses every night, so a value never carries into the next quarter by default.
   - Power-gate refusals are counted and reported (`n_refused_power` in the run record and the report), never booked
     as K.
   Over budget: nothing is searched; the run and its report are recorded.
6. **Holdout sealed.** The 1m bars come only through `cost_of_record.load_research_bars` (per file of a per-year
   directory, only the explore years opened); a window past 2018-01-01 is refused before any file is opened.
7. **Screen and stages.** Survivors are `search_and_judge`'s. The stage journal is read, never opened: a spec whose
   confirm or holdout read failed or was voided is never offered again. **This job opens no confirm read**: ADR 0085
   gives 4 confirm batches a ledger year, and a nightly job must not spend them; the confirm stays a deliberate batch.
8. **Out.** Survivors that can run live (`arm_rules_spec` through `Arm`, prereg to come) become rows of
   `candidate_arms` in the discovery ledger's database (`goldbot/research/autodisc/handoff.py`: insert-only,
   `pending()` = offered and not yet in `arm_registrations`). Registration stays `ops/autodisc_register.py`
   (`--stage explore`, demo accounts only, RiskGate unchanged); a hot-load path reads `pending()`. The run record goes
   to aux.db `discovery_runs` (migration 38) and to the ledger's `nightly_runs`; the report to
   state/autodisc/discovery-<night>.md (explore numbers only). A dry run plans and writes nothing.

## Consequences
- The brain gets one more job that can fail; it fails loudly, at low priority, inside a fixed window. A killed night's
  K stays in the ledger and the next `sync_discovery_k` counts it.
- Outcome-blind reading on the real bars (300 random specs each): the power gate admits about 8 % of G2 specs at 1h and
  5 % at 4h; 1d is just reachable. No catalogue card with a stated count passes (31 refused, 0 started). Discovery on
  gold alone can only find frequent rules; a pooled multi-instrument card (k > 1) is the route to slower ones.
- Not changed: the trial budget, the holdout, the phase gate, live unlock, RiskGate, any arm or config file, the
  red-box arms.
- The hand-off is gated on whether the kill rule can read the arm, not on forward power, which no spec can reach
  over the learning horizon (quant ruling (a)). A learning arm's forward read stays learning evidence, never
  qualification.
- What would change this: a quant ruling that the explore stage needs its own power gate, or that priors from 2026
  demo labels contaminate TEST-OLD freshness; an sre finding that the window or the memory cap does not fit the brain.
