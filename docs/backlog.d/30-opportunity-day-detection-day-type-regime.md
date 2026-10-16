---
number: 30
title: 'Opportunity-day detection: day-type / regime classifier (ADR 0004 north star)'
stage: Market preparation, selectivity · FIND
size: M
status: '30a done (PR #73, main d28a482; 71e9092); 30b and the trials parked: Q2 2027 budget, enable only after H-16'
---
- **30. Opportunity-day detection (hypothesis; quant review).** Value: the north star depends on trading more on
  days the market offers moves and standing aside otherwise (FIND). Acceptance:
  - The strategy-researcher registers a pre-registered hypothesis in `docs/research/hypotheses.md` and the trial
    registry (id assigned there) for a point-in-time day-type classifier: tier-1/tier-2 news day, volatility
    expansion (realised or HAR-forecast range vs ATR(1d)), London–New York overlap. The reading rule, written in
    advance, is net R per trade and the share of ≥ 1 ATR(1d) days captured, flagged vs unflagged.
  - A read-only descriptive report from `data-v1` (no trial): frequency of days with range ≥ 1 and ≥ 1.5 × ATR(1d)
    by weekday and session, so ADR 0004's "a few such days a month" is measured. Truncation test on every feature.
  - No trial runs until a quarter's budget is available; the Q1 queue (item 5) is not displaced without the
    owner's say. Depends on: 24 (DST-aware sessions), 18 (tier-2 events).
  - **Registered (2026-10-10):** the hypotheses are H-16 (range forecast), H-17 (trend-day continuation), H-18
    (post-blackout events), H-19 (stand-aside overlay) and H-20..H-23 (features), in `hypotheses.md` A2. Evidence
    and design: `docs/research/opportunity-days.md`. Proposed for the Q2 2027 pre-registration: 3–4 trials.
  - **30a. `day_type` feature family, opt-in (quant + data-engineer review).** **Status (2026-10-11): built; the
    quant and data review said "merge after fixes", and the fixes are applied** (goldbot/features/day_type.py,
    `research_pass --day-type`, tests/test_day_type_features.py, TRACEABILITY F24).
    - The HAR column is H-16's session-window model, with the draft's refit, GVZ staleness and tier-1 counting rules.
    - The tier-1 columns are wired to the merged US calendar (`--us-calendar` fills ctx; `--day-type` requires it).
    - Pinned versions: `f-1eada57050` (default + day_type) and `f-93fbe9386d` (+ macro).
    - Registered like `positioning`: computed only with an explicit flag (`--day-type`), so the default feature
      set, the frozen Q1 H-02 universe and its K_eff are unchanged (test: the default column list is identical
      before and after).
    - Columns, per feature day (22:00 UTC rollover), joined to decision bars as-of:
      - `opp_har_range_hat`: HAR on stub-free daily ranges (1/5/22), coefficients fitted only on rows before the
        fold's end (a fold-fit hook, not a precomputed constant);
      - `opp_gvz_rv`: `macro_gvz` ÷ annualised YZ20, via `asof_join` on `available_utc`;
      - `opp_tier1_today`, `opp_min_to_first_tier1`: from the scheduled calendar;
      - `opp_prev_range_atr`, `opp_nr4`, `opp_nr7`, `opp_inside_day`;
      - `opp_gap_atr`: weekend/daily reopen gap ÷ ATR(1d);
      - `opp_noise_ratio`: range since the London/NY local open ÷ its 14-day mean at the same local time, DST-aware
        (`calendar.LOCAL_SESSIONS`).
    - A truncation test for every column. The lookahead check reports 0 columns. A test that the GVZ value at a
      Monday 07:00 UTC bar is the previous Thursday's close. No column uses `side` or a later return.
  - **30b. Day-score allocator input (trading-safety + quant review).**
    - `allocator.day_type: {enabled: false, low_cut: 0.33, high_cut: 0.80, factors: [0.0, 1.0, 1.5]}` in settings.
      With `enabled: false`, decisions are identical to today on a recorded replay (test).
    - `Regime.day_score` is filled by `runner._regime`. `RuleAllocator.weights` multiplies every family weight by
      the factor. Validation refuses a factor > `multiplier_bounds[1]` or < 0.
    - Tests:
      - factor 0 gives `below_threshold` and no gate call;
      - factor 1.5 never yields risk above 0.75% at 0.5% risk, and the 1% clamp holds at any risk rate;
      - `SIZE_DOWN` still caps the multiplier at 0.5;
      - the exit path never reads `day_score`;
      - the £1,000 min-lot interplay test in item 25.
    - `day_score` and the factor are in the decisions log and on the approval card's risk line (ui-ux review).
    - **Enable only after H-16 passes** its pre-registered reading rule (evaluator verdict on #34).
  Sweep 2026-10-16 (product owner): 30a done (PR #73, main d28a482; 71e9092); 30b and the trials parked: Q2 2027 budget, enable only after H-16.
