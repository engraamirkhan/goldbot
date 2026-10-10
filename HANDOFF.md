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
  ticks/fills, prices candidates with the nightly cost table, reports the classifier's account class, and trades
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
  Deferred (L4): a `preregistered` status written before a trial runs, an `eval_version` field on registry rows,
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
  in retraining. `run.py export-costs --out FILE` writes the canonical broker's table; `research_pass.py --cost-table
  FILE` uses it (research.yml passes `config/costs_measured.json` when committed). Health warns while a table has no
  measured swap. None of this is verified against a real terminal yet (the conversions are tested on a fake mt5).
- Live data into learning (P9, 2026-10-09): the shadow book records EVERY candidate a champion/challenger scores
  with its p, raw score, the threshold at the time and `taken` (p > threshold); one position per agent applies over
  all candidates (as research thins before the model filter), and promotion stats, the CUSUM, re-arm and the
  population count taken trades only. Books written before this load unchanged (trades taken, no threshold) and are
  never calibration data. Weekly `recalibrate` job (Saturday 11:30, after the retrain, before the tournament) refits
  only the probability map of each champion/challenger on `ShadowBook.outcomes` of the last 182 days: a Platt layer
  on logit(p) with pseudo-outcomes equal to the current p worth 200 trades (`research.recal_prior_trades`), no
  update below 50 outcomes, p moves at most 0.05 per run; stored as a minor version (new checksummed artefact, same
  version/status, `recalibrations` row with before/after ECE; also `state/recalibration.jsonl`). It promotes and
  retires nothing. The ECE after is in-sample. Not done from P9: the monthly refit / drift-triggered refit, recency
  weighting and the replay trial; owner vetoes are not in the shadow book (it is the model's own decision).
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

## Next steps (no owner input needed unless marked)
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
   `python -m goldbot.ops.run export-costs --out config\costs_measured.json` and commit the file (docs/RUNBOOK.md
   section 4); research.yml then charges the measured swap, commission and slippage instead of the priors (swap
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
