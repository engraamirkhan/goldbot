# goldbot — status and next steps

Canonical design: `docs/DESIGN.md` (exported from the original Claude design doc; edit it here from now on).
Standing instructions for Claude sessions: `CLAUDE.md`. Owner's VPS guide: `docs/RUNBOOK.md`.

## Where things are (as of 2026-10-05)
- GitHub `engraamirkhan/goldbot`. PR 36 (2026-10-01..04 work) is merged into `main`; follow-up work goes on branch
  `claude/gifted-goldberg-lcb7pl` restarted from `main` (Claude sessions do not push to `main` directly). CI (`ci.yml`) runs pre-commit, backend lint/mypy/unit/integration, frontend eslint/tsc/vitest, an API
  contract check and Playwright e2e as separate jobs, and opens one issue labelled `ci` with each failed job's output.
- Built and tested: point-in-time store, calendar/UTC, resampler, loaders, quality checks, macro loaders; feature
  registry (volatility, MA families + ribbon, trend, mean-reversion, MFI, breakout, microstructure, S/R, swings,
  gaps, candles, session/calendar, macro); triple-barrier labels; specialist interface with agent lineage;
  four specialist families per the design table: session-open (15m), mean-reversion (15m), trend (1h, 4h EMA-50
  slope + pullback) and breakout (1h, tight-range break on volume); purged walk-forward + LightGBM meta-labeller + deflated Sharpe + trial registry;
  RiskGate + supervisor; broker protocol, paper broker, MT5 adapter, account classifier; TradingView webhook;
  account registry + keyring credential prompts (demo-first, live locked by gate); rule-table allocator;
  Telegram approval centre + bot adapter; trading engine loop; FastAPI backend with multi-user auth
  (owner/approver/viewer, password + authenticator, invites, lockout, audit); React+TS dashboard
  (Overview, Approvals, Agents, Feeds incl. scheduled jobs, Users); VPS bootstrap (NSSM services, Cloudflare Tunnel).
- Typed stack: records are pydantic (`goldbot/base.py`; persisted timestamps use `UtcTimestamp`), settings are a
  validated `Settings` model, every API endpoint has request/response models, mypy is clean; web types are generated
  from the backend OpenAPI schema.
- Data: every month 2010-01 .. 2026-10 is complete on release `data-v1` (no partial or imputed months after the
  gap-fill run 37012050707). The weekly scheduled data run refreshes the current year (completed days only).
- Research pass: `scripts/research_pass.py` + `.github/workflows/research.yml` (walk-forward on the release bars,
  leakage check, per-year table; registry kept on release `research-v1`; report to issue "research: <specialist>").
  It is on `main` (PR 36 merged), so it can be dispatched from the Actions tab.
- Scheduler (VPS service `goldbot-scheduler`, `goldbot/ops/scheduler.py` + `goldbot/ops/jobs.py`, times in
  `settings.yaml: scheduler`): nightly cost tables per account from the engine's own logged ticks and fills
  (`execution/costs.py`, slippage prior until 50 fills) + Friday classifier; Saturday retrain into a challenger
  (`research/model_registry.py`, checksummed artefacts) with automatic promotion through the design's gates
  (`research/promotion.py`) once a shadow record exists; monthly bounded label-grid research loop. The engine logs
  ticks/fills, prices candidates with the nightly cost table, acts on the classifier's account class (X14: Unknown or
  not yet classified = no entries on a broker account, health warns; Standard = 15m families off except session_open
  and gross edge > 1.5x the measured round trip; the paper broker is never restricted), and trades
  only with registry champions (hot-reloaded on promotion).
- Shadow book (`engine/shadow.py`): the engine on the canonical-cost broker (IC Markets) paper-trades the champion and
  every challenger on live bars with label-identical mechanics (exact parity with `triple_barrier` is tested) and
  writes `state/shadow_<version>.json`; the Saturday job promotes/retires from it, and the daily `model_watch` job runs
  a CUSUM on a new champion's first two weeks and restores the previous champion on an alarm.
- Population tournament (`research/population.py`, weekly `tournament` job): agents = specialist family + config with
  lineage; founders seeded per family; fitness only from shadow trades (per-trade Sharpe x calibration x drawdown
  haircut x diversity penalty); ranked after 60 trades; retired on a negative 80% lower bound after 100 trades or two
  bottom-quartile months (then 6 months more in shadow); shadow -> live via DSR with population size as trials;
  winners cloned into 2-3 mutated shadow children; caps 12 live / 24 shadow; capital = family weight x fitness share.
  Models are keyed by agent (`ModelRegistry`); the engine trades only live members and shadow-trades all of them;
  `state/agents.json` feeds the dashboard's Agents league table.
- Journal + staff agents: the engine journals every decision to the store's `decisions` table. `goldbot/agents/`:
  data steward and risk officer (weekday nights), journal coach and improvement agent (Saturdays) run on Claude
  Opus 5.5 through a budgeted tool-use loop over read-only tools (`agents/tools.py`; the only write is filing a
  hypothesis to `state/hypotheses.jsonl`), with refusal fallbacks enabled, a per-run cap and a monthly cap
  (`settings.yaml: agents`), every run logged to `state/agent_runs.jsonl`; reports appear on the Agents screen.
  The research analyst (Saturdays, after the improvement agent) turns filed hypotheses into at most two bounded
  walk-forward trials per run (`run_trial`: numeric overrides within +-50% of the defaults, recorded in the trial
  registry) and records a verdict on each hypothesis; it promotes nothing. The execution auditor (weekday nights)
  explains `execution/audit.py`'s numbers: slippage per session/order type against the nightly cost table with drift
  flags, widened spreads, failed orders.
- Research director (`research/director.py`, scheduler job `research_director` Saturdays 12:30 UTC, before
  `agents_weekly`): a deterministic score per family from the trial registry (median OOF AUC z-score with
  se = 1/sqrt(3 n_oof), best DSR on 200+ model-filtered trades, lookahead flags) and the shadow book/population
  (shadow t-stat), then splits what is left of the quarter's trial budget (`research.trial_budget_quarter`, 20 when
  unset, counted from the registry): an exploration floor (`research.director_floor`) per family, the rest in
  proportion to evidence, 0 for a lookahead-dirty family; the label grid may use at most half of a family's share.
  Trials with status `holdout` are never evidence and `monthly_research` never walks into the held-out year
  (`research.holdout_from`, default 2025-10-01). Output `state/research_plan.json`; `monthly_research` follows it (flat
  budget without a plan under 21 days old) and stops when the quarter's budget is spent. The `research_director` staff
  agent explains the plan (`read_research_plan`) and may file two hypotheses; the analyst prefers the plan's focus
  families. Boundary: it decides what to research, never what is promoted or traded; promotion stays with the gates.
- Approvals across processes (`telegram/bus.py`): engines, the API and the Telegram service are separate services,
  so engines publish proposals to `state/approvals/pending/`, the dashboard or Telegram writes a decision file
  (created exclusively: first decision wins), and the engine applies it on its next tick, re-running the RiskGate,
  then archives it in `approvals/done/` with the outcome. Owner halt: `state/control.json`, set by Telegram `/halt`
  or the dashboard (any approver); re-arm only on the dashboard by the owner with an authenticator code. Engines in
  production (`halt_checks`) also block entries when the supervisor's heartbeat is missing or stale. Exits are
  never gated.
- Clone mutations (`population.mutate_agent`): perturbed barriers/thresholds, a different feature subset
  (`feature_seed`: seeded 40 of the eligible features) or a different timeframe for breakout and mean_reversion
  (15m <-> 1h, holding horizon kept).
- Self-improvement loop (learns from past and present, changes only through gates): Saturday 03:17 UTC the data
  workflow appends the latest week of Dukascopy bars; 06:00 every population member is retrained on a rolling window
  ending at the latest bar, charged the canonical broker's measured slippage and commission from the nightly cost
  table (`jobs.live_extra_cost_usd`), and enters shadow as a challenger; it replaces the champion only when its live
  shadow record passes the promotion gates, and the daily CUSUM rolls a new champion back if live results drift.
  The tournament scores agents only on live shadow trades (expectancy, calibration of their own p, drawdown,
  correlation), clones winners into mutated children and retires losers; the research analyst tests hypotheses the
  improvement agent files from live scorecards. Every trial counts in the deflated Sharpe.
- One position per agent at a time (`labels.one_at_a_time`): research keeps a candidate only after the previous one
  exited, the shadow book refuses entries while the agent's trade is open, the engine skips an agent with an open
  position or pending proposal. Without it trend's clustered candidates overlapped and inflated every statistic.
- News blackout: `calendar_archive` (daily 06:10 UTC) stores the Forex Factory week in `calendar_events` with tiers
  (1 = US CPI/NFP/FOMC/PCE, 2 = other high-impact USD); production engines block entries from 15 min before to 30
  min after each tier-1 event, and for 30 min after a high-relevance unscheduled news shock. Headlines: service
  `goldbot-news` (`run.py news`) polls the RSS feeds in `settings.yaml: news` every 5 min, scores gold-relevant items
  in batches with structured output (budget `news.daily_cap_usd`, counted in the agents' monthly cap; unscored items
  are kept), stores them in `news`. The macro/news analyst writes the pre-session briefing (weekdays 06:30 UTC) from
  the calendar and headlines.
- Telegram service (`python -m goldbot.ops.run telegram`, NSSM `goldbot-telegram`): sends proposals with
  Approve/Reject buttons, posts each outcome, delivers every staff-agent report, answers /status and /halt
  (`telegram/outbox.py` keeps progress across restarts).
- One trial registry (`research/registry_sync.py`): the VPS monthly loop and the research workflow both union their
  registry with the `research-v1` release copy before and after writing, so the deflated Sharpe counts every trial once.
- Multi-timeframe: research, retrains, the dry run and the engine all build context with `features.mtf.context_tfs`
  (every one of 1h/4h/1d longer than the decision bar, prefixed h1_/h4_/d1_), so a model sees the columns it was
  trained on. The engine runs on its 15m clock and evaluates each agent only when a bar of the agent's own timeframe
  completes; context features are cached until a new context bar completes; open positions' time barriers and
  shadow trades count bars of the agent's timeframe.
- pandas 3 keeps s/ms/us timestamp units: always use `timeutil.epoch_ns`, never `.asi8`.
- Research discipline (proposal P1-P3, `docs/proposals/2026-10-design-improvements.md`): selection is cross-fitted
  (fold k calibrated only on earlier folds' OOF predictions; threshold from signal-time information); the cost hurdle
  is slippage + commission (`live_extra_cost_usd`, the settings prior before a cost table exists), not the spread
  again; `research/gates.py` checks 1,500 candidates / 60 per test fold / three positive years incl. 2021-22, and
  live promotion in the tournament needs a passed trial of the exact config; DSR is null below 200 trades; reports
  carry rule-only gross and net expectancy. Quarterly budget of 20 pre-registered trials
  (`research.trial_budget_quarter`), holdout 2025-10-01..2026-09-30 (`research_pass.py --score-holdout`, once per
  config), monthly label grid paused (`research.label_grid_paused`). Meta-models get `side` and side-aligned signed
  features (`features.registry.side_align`) from each specialist's declared `model_features` (h1_/h4_/d1_ context
  included). Fold-internal feature selection is deferred.
  Evaluator fixes (2026-10-08): research stops at the holdout start (no stub folds from later bars; the 60-per-fold
  gate counts complete folds only); gates include DSR >= 0.95 on 200+ model-filtered trades; `--score-holdout` needs a
  passed trial, is charged to the budget and is judged by its own rule (mean R > 0, t >= 1.65); one cap and one count
  for the quarterly budget (`registry.quarter_trials`, every row stamped in the quarter; the director plans from the
  same number); check-run-record under a portable registry lock; tournament, analyst trials and the paused monthly
  loop sync the release registry first; engine threshold and size multiplier use cost ex spread (RiskGate keeps the
  full round trip); signed-feature patterns are part of the feature version.
  Rule: a clone (new config hash) needs its OWN passed research trial before shadow -> live; nothing is inherited
  from its parent (the design counts real trials). Held-back agents show as `awaiting_research` in the tournament
  job's output.
  Deferred (L4): ~~a `preregistered` status written before a trial runs~~ (done 2026-10-10, discovery), an `eval_version` field on registry rows,
  bootstrap confidence intervals on expectancy, and a seeded no-signal test (100 seeds, DSR < 0.5 in >= 95).
- Primary-signal screen and new families (proposal P4, 2026-10-09): `research/screen.py` measures the rule alone
  (every candidate, one position at a time, mid prices) over the research window (holdout excluded) and passes it
  only with gross mean R > 0, t >= 2.0 and >= 1,000 events; net R is reported, not a criterion. `research_pass.py`
  screens every configuration first and fits no model for one that fails (registry status `screened`) unless
  `--skip-screen` (recorded as `screen_skipped`). **A screen is a trial**: it selects rules on data, so each screened
  configuration is one registry row, charged to the quarter's budget and counted by the deflated Sharpe whether it
  passes or not; when it passes, the walk-forward that follows is the same trial (one row). New families, both
  registered and founders in the population: `tsmom` (1h, 4h option; vol-scaled trailing return over 24/120/480 h,
  on a fixed 4-hour schedule, barriers 3.0/1.5 ATR, 48 bars) and `intraday_momentum` (15m; at mid-session on the
  London or New York local clock, DST by zoneinfo, enter in the direction of the move since the session open, exit at
  the session close, stop 2.0 / target 3.0 ATR). Their features (`tsmom`, `intraday_session`, family `momentum`) are
  versioned registry features and pass the lookahead check; adding them changes `feature_version` for every family
  (no champion exists, so nothing live is affected; retrains pick it up). Swap (2026-10-09, horizon study): net research
  labels pay overnight financing for every broker server-day rollover held through (`triple_barrier(swap=...)`, x3 on
  `costs.swap_triple_weekday`), from `costs.swap_long/short_usd_per_lot` (prior -60 / 0 USD per lot per night;
  replace with the broker cost table, `CostTable.swap_*`, which `live_swap` prefers when present); the gross screen
  carries no cost. tsmom also runs on `timeframe: "1d"` (feature-day bars, no context timeframe; daily defaults in
  `timeframe_defaults`: 20/60/120-day vol-scaled returns with 60-day vol, every settlement, 3.0/1.5 ATR, 10 bars;
  walk-forward `WINDOWS["1d"]` train 60 / test 12 / step 12 months expanding). Event count on data-v1 (counted only,
  outcomes not looked at): 454 events 2010-05..2025-09 (~29 a year, mean hold 6.9 bars, 8 swap nights), so the
  screen's 1,000-event floor cannot be met: one position at a time caps a 10-day daily rule at roughly
  trading days / (mean hold + 1), ~500 in 15.75 years. The per-fold gate fails too (12-month folds hold 23-38; the
  first fold starts 2018-05 because the 200-row training minimum takes ~7 years to fill). The 1d trial's verdict is the
  event floor; its gross R is still reported. tsmom 4h with swap: 2,289 events, 79% held overnight, 1.78 nights per
  trade. Every walk-forward report also shows the design's gates applied to the rule alone (`rule_only_gates`: every
  candidate net of all costs, same folds and trial count), labelled "rule-only (informational; promotion still
  requires the model path)"; `passed_gates` reads only the model path's `gates`. 4h is
  research-only: `saturday_retrain` skips timeframes without a settings walk-forward window.
- Measured broker costs (2026-10-09): the engine reads swap (symbol_info swap_long/short/mode/rollover3days,
  converted to USD per lot per night: points via tick value, base/margin/deposit money via price when the currency is
  XAU, interest % of the current price x 100 oz / 360; reopen modes and non-USD deposits are not converted and fall
  back to the `costs.swap_*` prior with a logged reason) and commission (per lot round trip, commission + fees over
  the last 90 days' closed positions) from the MT5 terminal every 6 hours (`mt5_adapter.broker_terms` ->
  `state/broker_terms_<account>.json`); `nightly_costs` puts them into the cost table (`CostTable.swap_*`,
  `commission_measured`; readings older than 7 days are ignored), so `live_swap` / `live_extra_cost_usd` charge them
  in retraining. With `costs.publish_release` on and the github-token in the keyring, `nightly_costs` then publishes
  the canonical broker's table to release `costs-v1` as `costs_measured.json` (`jobs.publish_costs`, via
  `release.upload_asset`; by hand `run.py publish-costs [--out FILE]`). The published format
  (`costs.PublishedCostTable`) holds costs only: spread, slippage, commission and swap per lot, `measured_at`, and per
  field measured/prior with its tick, fill or lot count. No account id, login, balance, equity or free-text notes
  (tested). Publishing is refused while swap is unmeasured or there are fewer than 50 fills. research.yml downloads
  the asset when present and passes `--cost-table`; without it the report's cost source says "PRIORS ONLY". Nothing
  is committed to the repo. Health warns while a table has no measured swap and, with publishing on, when the
  published copy is missing or older than 8 days (`costs:published`). None of this is verified against a real
  terminal or a real upload yet (the conversions are tested on a fake mt5, the upload on a fake uploader).
- Live data into learning (P9, 2026-10-09): the shadow book records EVERY candidate a champion/challenger scores
  with its p, raw score, the threshold at the time and `taken` (p > threshold); one position per agent applies over
  all candidates (as research thins before the model filter), and promotion stats, the CUSUM, re-arm and the
  population count taken trades only. Books written before this load unchanged (trades taken, no threshold) and are
  never calibration data. Weekly `recalibrate` job (Saturday 11:30, after the retrain, before the tournament) refits
  only the probability map of each champion/challenger on `ShadowBook.outcomes` of the last 182 days: a Platt layer
  on logit(p) with pseudo-outcomes equal to the validated p worth 200 trades (`research.recal_prior_trades`), no
  update below 50 outcomes, p at most 0.05 from the validated calibrator. Each run REPLACES the layer, refitted from
  the validated map (stacking layers on overlapping 182-day windows compounded: an independent review measured a
  -0.166 total move against a 0.05 cap after 8 weeks); rows without a raw score are not used; stored as a minor version (new checksummed artefact, same
  version/status, `recalibrations` row with before/after ECE; also `state/recalibration.jsonl`). It promotes and
  retires nothing. The ECE after is in-sample. Not done from P9: the monthly refit / drift-triggered refit, recency
  weighting and the replay trial; owner vetoes are not in the shadow book (it is the model's own decision).
- Deploys (2026-10-10, owner chose one click + manual): never automatic. The Telegram service (GOLDBOT_DEPLOY=1 on
  Linux) offers a new main commit that passed CI (backend + frontend check-runs) with [Deploy]/[Skip]
  (`goldbot/ops/deploy.py`); the tap writes `state/deploy/approved.json` for that exact commit; the root-owned
  `/usr/local/sbin/goldbot-deploy` (copy of `goldbot/ops/linux/goldbot-deploy.sh`, timer every minute) re-checks main,
  fast-forward and CI, waits while an approval is pending, restarts (supervisor first), verifies every enabled service
  after 60 s and rolls back otherwise; `sudo goldbot-deploy latest|<sha>` does the same by hand. Results in
  `/var/lib/goldbot-deploy/deploys.jsonl` (root only), published read-only to `state/deploys.jsonl` by rename (Telegram
  report, health `deploy`). The MT5 box is updated by hand only. Hardened after the security review: root never writes
  or follows anything in a service-user-writable directory (symlink-to-root attack), approval file opened O_NOFOLLOW
  and owner-checked, any failure after checkout rolls back, stability = no systemd restart for 90 s plus a
  supervisor heartbeat written after the restart (engines write only on ticks), copy in state/ written as the service
  user, approval opened O_NONBLOCK, CI read from the latest GitHub Actions runs only, no CLI approve.
- Drift and health (M26/M27, 2026-10-10): every fitted model stores its training distribution per input
  (`feature_ref`); the daily `drift_watch` job (23:40) rebuilds the last 30 days of candidates as in training and
  computes PSI on the top-10 inputs by gain (0.1 warns, 0.25 sizes the agent to 50%), ECE/Brier on the trailing 100
  taken shadow trades (ECE > 0.08 sizes down), a residual CUSUM (halts the agent) and each agent's 30-day drawdown;
  two halted agents or a drawdown > 1.5x backtest halt the system. Output `state/drift.json`; halts are sticky per
  champion version until the owner runs `python -m goldbot.ops.run drift-review --clear "<note>"`. The engine reads
  it (missing = no restriction, unreadable = halt), the gate reason is `drift_system_halt`, health check `drift`.
  Entries only; exits unaffected. Models trained before this have no reference: PSI is skipped for them.
- Macro data pipeline (2026-10-10, TRADER_LIFECYCLE gap 2): `.github/workflows/data-macro.yml` (Tuesdays 04:41 UTC
  and by hand) pulls DFII10, T10YIE, DTWEXBGS, GVZCLS and DGS2 from FRED's public fredgraph CSV (no key) via
  `scripts/fred_macro.py` and publishes `macro_fred.parquet` on release `macro-v1`. Rows carry value_date, vintage
  (retrieval date) and a conservative available_utc (`data/macro.py: fred_available_utc`): next US business day 23:00
  UTC for daily H.15/GVZ values, the business day after the following Monday for the weekly H.10 dollar. Published
  history is never rewritten; a revision is a new row dated from the day it was seen. Feature `macro_drivers`
  (`features/macro.py`): 20-obs real-yield change, 1-year real-yield z-score, 20-obs dollar change, GVZ level and
  20-obs change, from first releases only, joined via `asof_join` on available_utc. It is opt-in
  (`pipeline.OPT_IN_FEATURES`): the default feature version and every declared `model_features` are unchanged.
  `research_pass.py --macro DIR` (workflow input `macro`) adds it and reports "macro features: on/off (reason)"; a
  missing release runs without it. The VPS Saturday retrain and `fetch_data_release.py --macro` load the release
  into the store's `macro` table (`release.sync_release_macro`; 0 rows until the workflow has run). Not done: the
  live engine does not build `macro_drivers`, so a model trained with `--macro` gets a different feature version
  and is refused live (`feature_version_mismatch`) until the engine passes the store's macro rows; COT and GLD are
  not pulled. The workflow has not run yet (first run: Actions -> data-macro -> Run workflow).
- Free hosting without Windows (2026-10-10, owner: MT5 + $0 hosting, Mac only): two Oracle Cloud Always Free VMs.
  The MT5 terminal runs under Wine on an x86 E2.1.Micro with the bridge (`goldbot/execution/bridge.py`,
  `run.py bridge <account>`); everything else runs on an Ampere A1 (4 OCPU / 24 GB) under systemd
  (`goldbot/ops/linux/`: `brain_bootstrap.sh`, `mt5_bootstrap.sh`, units). The engine uses `RemoteBroker` when the
  keyring holds `mt5-bridge-url-<account>` + `mt5-bridge-token-<account>`. Bridge safety (from the trading-safety and
  security review): both ends verify the terminal's login, server and demo/real against the registry
  (`accounts.verify_terminal_account`; MT5 logins live in the keyring as `mt5-login-<account>`); listens on 127.0.0.1,
  reached through an SSH tunnel whose key may only forward to that port; constant-time token; allow-listed methods;
  5 s socket timeout; order calls never retried and refused when older than 10 s at the terminal; `GuardedBroker`
  refuses orders outside the account's magic range or above 3 lots and stop changes on foreign positions (closes
  allowed: the kill switch closes everything); a lost reply ends the engine, systemd restarts it
  (`StartLimitIntervalSec=0`) and restart reconciliation settles the order. Measured: engine ~200 MB, API ~110 MB, a
  6-month dry run 1.7 GB, so a 1 GB Windows VM (Azure free year) was rejected. Not verified yet: MT5 + the
  MetaTrader5 package under Wine on the real VM (owner setup: docs/RUNBOOK.md section 0). Wine is for the demo phase;
  re-decide before real money.
- Pooled meta-model (P5): `research_pass.py --pooled 15m|1h` fits ONE model over the union of every family whose
  default timeframe it is (15m: intraday_momentum, mean_reversion, session_open; 1h: breakout, trend, tsmom), with
  one indicator column per family, `side` and a declared pooled list (`pipeline.POOLED_FEATURES`, <= 40 inputs in
  all), the same cross-fitted calibration, per-candidate thresholds (each family's own barriers) and gates, uniqueness
  weights across the pool; the screen is applied to the union (members shown alone too). Recorded as family
  `pooled_<tf>` (one trial). The report adds a per-family table with P5's deciding comparison: pooled vs the
  family's own model, OOF log-loss on the same rows of the same folds. Research only: the engine and model registry
  do not serve a pooled model yet (needed only if one passes). session_open now trains on an expanding window with
  6-month test folds (`Specialist.walkforward`); at ~95 candidates a year a 6-month fold holds ~47, so its per-fold
  gate still needs denser filters or 9-month folds.
- Trader-toolkit features (2026-10-10, `goldbot/features/trader.py`, TRACEABILITY F13-F16): four registry families
  for research to screen; no specialist declares them (its `model_features` are unchanged), so they reach a model only
  through the P4 screen and a pre-registered trial. `session_zones` (session high/low/open now and for the previous
  session, each zone's last high/low, previous feature-day and Sunday-week high/low, all (close - level)/ATR14;
  sessions are the `DEFAULT_SESSIONS.sessions_utc` windows, 21:00-23:00 UTC belongs to none); `market_structure`
  (liquidity sweep = traded beyond the last confirmed swing, previous session or previous day high/low known at t-1
  and closed back inside; break of structure = first close beyond the last confirmed unbroken swing); `fair_value_gaps`
  (bullish if high[i-2] < low[i], known when bar i closes; partial fills shrink it, filled once a bar trades through
  its far edge; >= 0.1 ATR); `order_blocks` (last opposite-colour candle within 5 bars before a displacement with body
  > 1.0 ATR closing beyond the last confirmed swing; invalidated by a close through it). Warm-up: ATR 13 bars, the
  first completed session/day/week; nearest gap/block columns are NaN while none is active (counts 0). Every value at
  bar t uses only bars closed by t (truncated-history test + pipeline lookahead check). They are in
  `DEFAULT_FEATURE_NAMES`, so the default `feature_version` changes (expected: no champions exist, nothing is
  invalidated) and feature-seeded clones now draw from a larger column pool.
- Survey features (2026-10-10, `goldbot/features/survey.py`, TRACEABILITY F17-F22; indicator survey section 4a, top-15
  ranks 3, 4, 6 and 10): five registry families for research to screen, no specialist declares them. `vol_estimators`
  (Garman-Klass, Rogers-Satchell, Yang-Zhang over 20/60 bars, Parkinson 60, YZ20/YZ60 term structure, vol-of-vol =
  CV of YZ20 over 60); `round_numbers` ((close - nearest $5/$10/$25/$50 level) / ATR14 and how many of the last 50
  bars traded through it); `regime_stats` (variance ratio q = 2/4/8 over 120 bars, R/S Hurst over 128 returns with
  chunks 8-64, Kaufman efficiency ratio 10/30); `jumps` (return / bipower sigma of the 60 returns before the bar, flag
  at |z| > 4, sign, bars since the last jump capped at 500); `expected_move` (close x YZ20 x sqrt(h) for h = 4/16/48
  bars, and ATR14, over a round-trip cost = 96-bar median spread + 0.37 USD/oz slippage and commission priors; a feature
  only, the cost-to-move filter is a later trial). Warm-up up to 128 bars (documented per family in the module).
  Like the trader families they are in `DEFAULT_FEATURE_NAMES`: the default `feature_version` changes f-6932724812 ->
  f-1b8f723a67 (no champions exist, nothing is invalidated). Equity stress (opt-in, survey rank 6): `macro_drivers`
  gains `macro_vix`, `macro_vix_chg5`, `macro_spx_dd20` from FRED VIXCLS and SP500 (version 1 -> 2, so the
  default+macro version changes too; NaN until the release carries them); `scripts/fred_macro.py` now downloads both
  (`RELEASE_SERIES`, same next-business-day 23:00 UTC availability as GVZCLS; FRED's SP500 covers about ten years).
  The next data-macro run publishes them to `macro-v1`.
- Exit policies (2026-10-10, `goldbot/labels/exit_policy.py`, TRACEABILITY M3/M6/M7/R21): trend trails 1.5 ATR once 1.25 ATR in
  profit; breakout closes half at 1.0 ATR and trails the rest 1.0 ATR (2.0 ATR target kept as the cap); session-open
  goes flat 1 h before the next session open. One `policy_step` drives the labels (`triple_barrier(policy=)`, used
  by research `prepare`) and the shadow book; the engine runs the same rules with `modify`/`close` (trail and flat at
  the agent's bar close, scale-out on the tick), only tightening or reducing, never gated. Parity is tested label vs
  shadow and label vs the live engine on the paper broker. Families without a policy (tsmom, mean-reversion,
  intraday momentum) keep byte-identical labels (digest test). R21: in a tier-1 blackout open trades re-scored at
  p < 0.5 are closed. Consequences: labels of trend/breakout/session-open changed, so their earlier research
  verdicts were on plain barriers and any model of theirs must be retrained before it trades; the entry threshold
  and sizing still assume the binary target/stop payoff.
- Feature discovery (2026-10-10, survey 4b, TRACEABILITY M37/M38; hypothesis H-02 tooling ready, not run):
  `research/discovery.py` + `research_pass.py --discover [--families [feature|family]] [--discover-config JSON]`
  screens every eligible column (may exceed 40) on one specialist's candidates as ONE trial. Inside each purged
  walk-forward fold, on its training rows only: stability selection (50 weekly-block half-samples, shallow LightGBM
  gain or L1-logistic, top-k frequency), optional permutation confirmation on an inner purged time split; each fold's
  model uses its own selection (<= 39 + `side`), then the usual cross-fitted calibration, thresholds and gates
  (`_walk_forward(fold_cols=...)`). Report: group and feature frequency per fold, stability (share of folds at
  frequency >= 0.6), survivors (stability >= 0.7, capped at 39), the gates and the pre-registered reading rule
  (continue: AUC >= 0.53, bootstrap lower bound > 0.50, >= 1,000 scored, net mean R of model-filtered trades > 0;
  stop: AUC < 0.52). Registry: a `preregistered` row (config, reading rule, plan) is written before the run (closes
  L4's first item); it is not a trial (`registry.is_trial`: no budget slot, no DSR count; the release merge numbers
  trials only). The result row has status `discovery`, family `discovery_<specialist>` (never promotable, not director
  evidence) and `n_groups_screened`. `registry.n_trials_effective` = trials + every discovery's groups screened;
  research_pass now uses it for every trial's deflated Sharpe (equal to the old count while no discovery exists).
  Deviations from 4b: the label is the specialist's (no primary-free 4h label yet), groups are registry
  features/families instead of |Spearman| > 0.7 medoid clusters, and the trade rule is the existing break-even +
  margin threshold, not the top-tercile cut.
- SQLite state store, steps 0-1 (2026-10-10, `docs/proposals/2026-10-state-store.md`): package `goldbot/db/`
  (stdlib `sqlite3`) opens `state/core.db` (synchronous FULL) and `state/aux.db` (NORMAL) with WAL, busy_timeout
  5000, foreign keys and trusted_schema off; writes take `BEGIN IMMEDIATE`. Forward-only migrations in
  `db/migrations.py`, one transaction each, recorded in `schema_version` (+ `PRAGMA user_version`); a newer file
  refuses to open. `RecordRepo` maps a pydantic Record to key/filter columns + a JSON `body`. Import skeleton:
  `python -m goldbot.db migrate|import-state [--dry-run]` (idempotent by file SHA-256, files left in place).
  Dashboard auth (`api/auth.py`) now keeps users, invites, sessions (token hashes) and an append-only audit table in
  aux.db: logins survive API restarts and deploys, and several API processes share sessions. On first start with
  an empty DB it imports users.json and audit.jsonl once and never writes them again (backup). Failed-login
  counters stay in memory. Not done: `run.py migrate`/`import-state` wiring, `state_db` health check (step 0
  leftovers, files owned elsewhere); the API migrates aux.db on open until then. Steps 2-8 not started.
- Dashboard accounts (2026-10-10, owner request): the owner is the sole admin. `auth.owner_email` (Settings
  `AuthSettings`, default None; set only in the server's `config/settings.local.yaml`, never in the public repo)
  is required for bootstrap and is the only address that can hold the owner role. Invites and `set_role` grant
  only approver/viewer (`GrantableRole` in the contract; "owner" gets 422); the owner cannot be demoted or disabled;
  an invite never overwrites an existing account; the only user-creating paths are bootstrap and invites. A stored
  owner that differs from the setting is logged and left alone; `auth.owner_health_note(state_dir, owner_email)`
  is ready for health.py (not wired: health is owned elsewhere). Password policy: 12+ characters, different from the
  current one. Endpoints: `POST /api/auth/setup` now returns `SetupResponse` with 10 one-time recovery codes
  (hashes stored, aux.db migration 3); `/api/auth/password/change` (logged in; password + TOTP; other sessions
  revoked); `/api/auth/password/forgot` (email + TOTP + new password; same 403 for unknown email or wrong code;
  shares the 5-in-15-min lockout; all sessions revoked); `/api/auth/reset-link` (owner; 24 h single-use token,
  hash stored) and `/api/auth/reset` (new password + new TOTP, all sessions revoked); `/api/auth/recovery/login`
  (owner: password + recovery code, re-enrols TOTP, returns a session) and `/api/auth/recovery/codes` (owner,
  regenerate); `/api/users/enable` and `/api/users/revoke-sessions` (owner). All audited. Minimal UI: Forgot
  password / recovery-code / reset-link modes on the login page, recovery codes shown after setup, an Account tab
  (change password), Enable / Sign out everywhere / Reset link on Users. No button yet for regenerating recovery
  codes.
- Bounded spawning (2026-10-10, BACKLOG 13, TRADER_LIFECYCLE section 3, TRACEABILITY G11): the daily `gap_watch`
  job (23:55 UTC, after drift_watch; `goldbot/ops/gap_watch.py`) detects gaps (uncovered family timeframe, all agents
  of a family retired, drift/system halts, a volatility tercile no champion trained on, error dq events on 3+ days in
  7, planned family without a founder, a passed trial without an agent) and writes `state/gaps.json` with the actions
  taken and refused (with reasons). Trading: `Population.spawn_founder` only, zero-capital SHADOW founders of
  registered families (default on another timeframe, or a passed trial's config), at most `gaps.founders_per_month`
  (2), only into 4 reserved slots inside the 24 shadow cap (clones now stop at 20), never during a system halt or a
  drawdown stage, never in a lookahead-blocked family; lineage in `gap_id`/`origin`/`notes`. Live still needs the
  tournament's DSR and a passed trial of the exact config. Staff: on-demand runs of the existing read-only
  `data_steward` / `risk_officer` only, 3 per rolling week, once per gap, refused when the month's agent budget is
  below the role's per-run cap. Regime and dead-family gaps file at most 2 hypotheses per run; a new family, role or
  untrainable timeframe (4h/1d: no walk-forward window in settings, so the retrain cannot train them) is a BACKLOG
  suggestion only. Not done: the 4h founder path in `saturday_retrain`.

- Auto-mode offer (2026-10-10, `goldbot/telegram/automode.py`, TRACEABILITY A10, BACKLOG 15): the Telegram service
  checks every 15 min whether /mode auto may be offered: >= 100 decided proposals since the last mode change, no
  RiskGate breach (an 8%/12% drawdown stage or a kill switch in `risk_*.json`), no re-arm lock or halt, and no
  distinguishable veto: approved vs rejected proposals are matched to the shadow book's counterfactual outcomes
  (same agent, side, signal bar) and compared in R with Welch's t-test (eligible only if p >= 0.10 and the 90% CI
  spans 0; fewer than 10 outcomes a side fails closed). When it holds, ONE message per mode epoch; it never switches.
  `/mode auto <code>` checks the dashboard owner's authenticator (`goldbot.api.auth.totp_verify`) and the evidence,
  then writes `approval_mode` to `state/control.json`; `/mode propose` needs nothing. Both, and refusals, go to
  `state/audit.jsonl`. Engines read the mode each refresh; the 30-day re-arm lock and the 12% kill switch still force
  propose, and the kill switch writes propose back to control.json (so the evidence restarts). Known limits: breaches
  are read from the engines' current risk files (a size-down that recovered before the check is not seen); the
  dashboard has no /mode control yet (goldbot/api untouched).
- Cross-feed checks (2026-10-10, `goldbot/data/crossfeed.py`, TRACEABILITY D12/D20/D23/F10, BACKLOG item 14).
  `feed_reconcile` (scheduler, Mon-Fri 23:20 UTC) rebuilds each account's 1m bars from its stored ticks and compares the
  last day with the broker's own M1 (`get_bars`; bridge `RemoteBroker`, or on Windows the terminal's saved login via
  `run._job_broker`): close off by > 2 points, or a minute on one side only, diverges; > 0.5% of broker minutes warns,
  > 5% (or no engine ticks at all) is an error -> dq_events, state/reconcile_<account>.json, health check
  `reconcile:<account>` (warn/fail; stale after 96 h). The broker's M1 is kept in the new store table `bars_1m_broker`,
  so broker history accumulates. Spikes (D20): `quality.confirm_spikes` drops a check_bars spike when the other feed
  moves the same way by >= half of it within +-1 minute; used by the reconcile run, check_bars alone is unchanged.
  Survival (D23/F10): `scripts/crossfeed_check.py --specialist X --account A` runs the rule-only screen on Dukascopy and
  on `bars_1m_broker` over the common period outside the holdout; survives = Dukascopy mean R > 0 with t >= 2.0 AND
  broker mean R > 0 with t >= 1.0; broker-only significance = feed artefact, dropped; < 90 days or < 100 events per
  feed = insufficient overlap (not a pass, the current state). Not a trial (it can only discard). Not yet a promotion
  gate: wire it in once broker history reaches 90 days. Known limit: ticks sharing a millisecond can come back from
  the store in another order, so a minute's close may differ (absorbed by the 0.5% warning band).

## Next steps (no owner input needed unless marked)
- Roadmap gates and stop rule in code (2026-10-10, BACKLOG item 8, rows P6/P7, `goldbot/ops/gates_phase.py`):
  `python -m goldbot.ops.run gates` prints each roadmap gate as met / not met with its evidence (trial registry DSR,
  positive years and backtest trades; the nightly cost tables; `state/closed_trades.jsonl` for the paper and live
  record; `state/gate_evidence.json` for the leakage audit and chaos drill, recorded with `run.py gate-evidence`) and
  writes `state/gate_report.json`. Health adds `stop_rule` (FAIL on a breach: alerted once, recommends /halt, halts
  nothing) and `phase_gates` (next gate, informational). Nothing records a gate or unlocks live. OWNER: sign off
  the `gates:` values marked PROPOSED in `config/settings.yaml` before the first paper trade (DSR bar 0.95 vs the
  roadmap's 1.0, shuffle AUC tolerance, feed mismatch share, minimum paper/live days, trades per broker). Follow-up
  for the engine owner: append a `gates_phase.ClosedTrade` per closed position (`append_closed_trade`); until then the
  paper/live record is empty, so gates 2-3 read "not met" and the stop rule cannot fire on expectancy.
- Ops alerts (BACKLOG item 7, rows S5/R11/X6): supervisor, scheduler, telegram, news and api write
  `state/heartbeat_<service>.json` every minute (`goldbot.ops.health.Heartbeat` / `start_heartbeat`); health fails a
  service silent for 5 min, warns on a tripped daily/weekly loss cap per account and on each order that failed after
  the retries (orders_<account>.json `rejected`/`unfilled`, retcode from the decisions journal), and fails a bridge
  that does not answer /health through the tunnel. Those warnings are in `NOTIFY_ON_WARN`, so the existing Telegram
  health pass announces them once per incident; other warnings stay silent as before. Follow-up for the bot.py
  owner: HEALTH_EVERY_S 300 -> 60 so a silent service is announced within 6 min (acceptance S5).
- OWNER decision: design improvements after the first clean research pass, ranked, first batch proposed: `docs/proposals/2026-10-design-improvements.md`.
1. Research status (2026-10-09, Q4 2026 trial budget spent: 20/20, research stops until 2027-01-01). Evaluation is
   cross-fitted, spread charged once, design gates and the P4 screen enforced, holdout 2025-10..2026-09 untouched,
   lookahead check clean on every run (reports: issues #37 #39 #40 #41 #51 #52 #53; summaries on #34).
   - No gross edge: mean_reversion, session_open, trend, breakout, intraday_momentum (NY, London). Retired from model
     research.
   - tsmom (vol-scaled time-series momentum) has a small gross edge: +0.06 R/trade, t 2.6 (1h), 2.4 (4h); net of costs
     -0.079 R (1h), -0.014 R (4h, before swap). tsmom 1d cannot reach the screen's 1,000 events (454).
   - Meta-models add no skill: OOF AUC ~0.50 per family and pooled (pooled_1h, #53).
   Q1 2027 plan (first pre-registered trial): `specialist=tsmom`, `variants=[{"timeframe": "4h", "max_bars": 12}]`,
   rationale "horizon study: tsmom 4h with swap" (refused on 2026-10-09 by the budget guard). Before it, OWNER on the VPS:
   once `state\costs_icm-demo.json` carries measured swap (the health check stops warning "no measured swap"), run
   set `costs.publish_release: true` and restart the scheduler (or run `python -m goldbot.ops.run publish-costs`;
   docs/RUNBOOK.md section 4); research.yml then charges the measured swap, commission and slippage instead of the priors (swap
   prior: long -60, short 0 USD/lot/night, x3 Wednesday). If net stays <= 0, the next option (other
   instruments) is an OWNER decision. Raising `research.trial_budget_quarter` is an OWNER decision.
   Until an agent passes the gates the engines propose nothing (by design).
2. On the VPS, check `state/news_feeds.json` after the first hour: the feed URLs in `settings.yaml: news` could not be
   verified from a Claude sandbox. Fix any that fail; the collector skips broken feeds.
3. VPS (step by step in `docs/RUNBOOK.md`): provision Windows VPS, run `goldbot/ops/vps_bootstrap.ps1` — OWNER: log in
   to the two MT5 demo terminals once and store their credentials with `python -m goldbot.ops.accounts add icm-demo`
   / `add vantage-demo` before starting the services (services cannot prompt); create the Cloudflare tunnel and enter its token on the VPS; store a
   GitHub token with `python -m goldbot.ops.accounts set github-token` (bar sync + shared trial registry), an
   Anthropic API key with `python -m goldbot.ops.accounts set anthropic-api-key` (staff agents and headline scoring;
   off without it) and the Telegram bot token with `python -m goldbot.ops.accounts set telegram-bot-token` (from
   @BotFather), and put your Telegram user id in `settings.yaml: telegram.allowed_user_ids` (the file has no
   `telegram:` section yet; add it).
4. Dashboard first run — OWNER: create the owner account with the setup code the API prints; invite others.
5. Session-open has about 90 candidates a year, so most 24-month training windows miss the walk-forward's 200-trade
   training minimum (9 folds in trial #11); it now trains on an expanding window with 6-month test folds (P5), and
   fails the P4 screen anyway (gross t 1.31), so no model is fitted for it unless a later rule change passes.
6. Gaps found while writing `docs/RUNBOOK.md` (code changes, through CI): the bootstrap does not build `web/dist`
   (the API serves nothing without it) and registers services under NSSM's default account, which may not see
   secrets stored in the owner's Windows Credential Manager; `run_engine` passes no `RiskLimits`, so
   `settings.yaml: risk` (incl. `risk_per_trade_tiny_live`) is ignored; the engine never calls `risk.gate.new_day`,
   so daily/weekly caps count from engine start; nothing clears the in-memory `drawdown_halt` except a restart;
   no live terminals or live engine services are installed. (being fixed)

## Environment facts
- GitHub free tier: a private repo gets 2,000 Actions minutes a month (reset each billing cycle). CI is two jobs
  per PR (~15-20 min); research.yml ~30-60 min per run; the weekly data refresh ~5 min. A `full_refresh` of
  data-dukascopy costs hours per year of data and used up the October quota on 2026-10-06: avoid it on hosted
  runners. Setting the repository variable `CI_RUNNER` (e.g. `self-hosted`) moves CI to a self-hosted runner, which
  costs no minutes.
- Claude sandboxes (cloud container and the Mac's Cowork VM) cannot reach market-data hosts or download Actions
  logs; GitHub API works. Data runs in GitHub Actions. Reports come back as GitHub issues.
- MetaTrader5 Python package is Windows-only; execution runs on the VPS.
- On the personal Claude account, cloud sessions push to GitHub directly (Claude GitHub App is installed on this
  repo); no device dependency.
