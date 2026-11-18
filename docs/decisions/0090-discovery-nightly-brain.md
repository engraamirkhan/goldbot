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
- The brain is a 4-core ARM VM shared with the engines; the scheduler is single-threaded.

## Decision
1. **A new brain job `discovery_nightly`** (daily 01:00 UTC, `max_late_hours` 1.5), off by default
   (`research.discovery.enabled`); a host turns it on together with `bars_path` in settings.local.yaml. Enabled
   without bars **fails** the job (health shows it): it never skips silently, the failure that retired its
   predecessor.
2. **Bounded for the brain** (`goldbot/ops/bounded.py`). The night runs in one `spawn` child at `nice` 15 (10..19,
   never level with the engines), RLIMIT_DATA `memory_mb` 4096 (Linux; RLIMIT_AS is not used: pyarrow and numpy
   reserve virtual space far beyond what they touch), one BLAS/OpenMP thread. No timeframe starts once `budget_s`
   (5400 s, at most 7200) is spent, or when the last one took longer than what is left; the parent kills the child at
   budget_s + `kill_grace_s` (300 s, at most 600). The window 01:00-03:10 is clear of every other daily job (a test
   holds it). Measured on the Mac: the 2010-2017 bars load in under 1 s, 2.8 M rows, 1.5 GB RSS.
3. **Inputs** (`goldbot/research/autodisc/discovery.py`).
   - The G2 grammar and the honesty set of `nightly.search_and_judge`, unchanged, at the cost of record v3 (ADR 0084):
     the canonical broker's dearest `charged_usd` cell (icm 0.90 $/oz) on every round trip, on top of the tensor's
     bar spread. Conservative: v3 measures against the mid, so the bar spread is charged twice.
   - Grammar-node priors (`GPConfig.leaf_priors`): demo labels (aux.db `exploration_obs`, gold, non-control,
     `label_sim_r`, void evidence excluded, as the learner reads them) by family node and decision session; each
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
   ("NOT STARTED (power)"); a refused plateau neighbour reads as not positive (conservative). A timeframe on which
   even an every-bar spec is refused is not searched at all.
5. **Trial budget.** Before the search, the night's planned K at its bound (timeframes x (population x (generations +
   1) + max_candidates x 24 neighbours)) must pass `catalogue.run.assert_budget` against the quarter's cap, the
   registry's used trials and the pre-registered reservation. Discovery K is a sidecar and never a budget slot
   (ADR 0058 F3), so this is a tightening. Over budget: nothing is searched; the run is recorded.
6. **Holdout sealed.** The 1m bars come only through `cost_of_record.load_research_bars` (per file of a per-year
   directory, only the explore years opened); a window past 2018-01-01 is refused before any file is opened.
7. **Screen and stages.** Survivors are `search_and_judge`'s. The stage journal is read, never opened: a spec whose
   confirm or holdout read failed or was voided is never offered again. **This job opens no confirm read**: ADR 0085
   gives 4 confirm batches a ledger year, and a nightly job must not spend them; the confirm stays a deliberate batch.
8. **Out.** Survivors that can run live (`arm_rules_spec` through `Arm`, prereg to come) become rows of
   `candidate_arms` in the discovery ledger's database (`goldbot/research/autodisc/handoff.py`: insert-only,
   `pending()` = offered and not yet in `arm_registrations`). Registration stays `ops/autodisc_register.py`
   (`--stage explore`, demo accounts only, RiskGate unchanged); a hot-load path reads `pending()`. The run record goes
   to aux.db `discovery_runs` (migration 37) and to the ledger's `nightly_runs`; the report to
   state/autodisc/discovery-<night>.md (explore numbers only). A dry run plans and writes nothing.

## Consequences
- The brain gets one more job that can fail; it fails loudly, at low priority, inside a fixed window. A killed night's
  K stays in the ledger and the next `sync_discovery_k` counts it.
- Outcome-blind reading on the real bars (300 random specs each): the power gate admits about 8 % of G2 specs at 1h and
  5 % at 4h; 1d is just reachable. No catalogue card with a stated count passes (31 refused, 0 started). Discovery on
  gold alone can only find frequent rules; a pooled multi-instrument card (k > 1) is the route to slower ones.
- Not changed: the trial budget, the holdout, the phase gate, live unlock, RiskGate, any arm or config file, the
  red-box arms.
- What would change this: a quant ruling that the explore stage needs its own power gate, or that priors from 2026
  demo labels contaminate TEST-OLD freshness; an sre finding that the window or the memory cap does not fit the brain.
