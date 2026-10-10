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
  Two more inputs (2026-10-10, revised after the quant review): (1) the daily attribution report
  (`state/attribution.json`): the director rebuilds each family's cell from the report's per-trade rows, counting only
  trades entered on or after their model version's promotion (model registry; an unpromoted version counts nothing),
  from a report dated at or before the plan and at most 14 days old; under `attribution.min_trades` is noise. The
  net-R t-stat is shrunk by n/(n+100), clipped to +-1 at t = 3, and tilts the evidence share of the pool by at most
  +-25%, only for a family with no shadow t-stat (the same shadow trades are never counted twice; the
  `director_floor` is never tilted). (2) Retired families are governance in settings, `research.retired_families`
  (family, hypothesis id, retired date, reason, registry trials); `docs/research/hypotheses.md` section B mirrors it,
  a test checks they agree, and the plan stores the doc's sha256 and any drift (`hypotheses_drift`) but the doc never
  moves a trial. The retired families SHARE one exploration trial a quarter (none once any of them had a trial this
  quarter; after the live families' floors), given to the clean retired family with the best positive
  post-retirement attribution, else by a deterministic rotation over quarters (`retired_explore` in the plan); each
  is reinstated only by attribution trades after its retirement date with shrunk t >= 2
  Bonferroni-corrected for retired families x sources (5 families: 2.61). (3) The pre-registered queue is reserved
  first: `research.reserved_trials_quarter: 13` (docs/research/preregistration-2027Q1.md) minus trials run against a
  `preregistered` registry row, at least the queued preregistered rows; the director and `monthly_research` spend
  only budget - used - reserved, and the plan's grid share is 0 while `research.label_grid_paused` (it is). With
  today's settings in Q1 2027: 20 - 13 = 7 planned trials, tsmom 6 and 1 shared by the five retired families. The
  plan records `evidence_budget`, `moves`, `quarter_reserved`, `reservation`, `retired_floor`, `retired_explore` and
  `reinstate_t`.
- Director re-verify fixes: the research analyst's trial runner (`jobs.make_trial_runner`) now spends only budget -
  used - reserved like the director and the grid; only a trial of a configuration with a pending `preregistered` row
  of the quarter may use the reservation, and its registry row is linked to that pre-registration
  (`director.pending_preregistration`). `monthly_research` reads the reservation of the slot's quarter, not the wall
  clock's.
- Reservation on the manual paths (quant review MEDIUM, 2026-10-10): `scripts/research_pass.py` (ad-hoc variants,
  screens, pooled runs, holdout scorings) and `--discover` now check through `TrialRegistry.check_budget_reserved`
  (same `reserved_trials`/`pending_preregistration` as the director): a run matching a pending queued
  `preregistered` row (family + config hash) may use the reservation and is linked to that row; anything else must fit
  in budget - used - reserved. An ad-hoc discovery's own just-in-time pre-registration is written with `queue: false`
  and no longer counts as a queue trial run. A run's link to its pre-registration now also matches the row's
  timestamp (two rows can share a trial number). Holdout scorings come out of the unreserved remainder unless
  pre-registered (preregistration-2027Q1.md). CPCV stays evidence, uncharged (ADR 0003). To spend the reserved H-02
  slot, pre-register the discovery's exact config (incl. bars range) before running it.
- CUSUM re-verify fixes (M24/M25): h now sits halfway between the chosen reachable value of the statistic and the
  next higher one (`cusum.decision_interval`: same rate, but unrounded live sums can no longer turn a tie into an
  alarm); the drift watch calibrates on the agent's actual taken p values resampled per trade (`ps`), not their mean.
  Live false-alarm rates (production `residual_cusum` arithmetic, 200k paths, p spread per trade): 4.84-4.96% at p
  0.35-0.55 and 0.30-0.70 (the mean-p h gave 5.7-6.3% at 0.35-0.55; champion watch 2.6-2.9%). The new-champion watch runs two weeks or the
  first 12 trades, whichever is later, capped at 8 weeks, with h calibrated for that count (`cusum.watch_trades`);
  when the count cannot reach h it reports `cannot_alarm` (job output, `state/model_watch.json`, health warning
  `model_watch`) instead of a silent "ok": such slow agents rely on drift_watch and the drawdown halt.
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
- Performance attribution (2026-10-10, BACKLOG 12, TRACEABILITY G12): the daily `attribution` job (23:50 UTC,
  `goldbot/research/attribution.py`) writes `state/attribution.json` and `state/attribution.md` from the shadow book
  (every candidate, taken or not), engine fills, pending_orders and the canonical cost table: expectancy in R gross
  and net (count, t, 95% interval, hit rate, profit factor) by timeframe, family, agent, session, side, volatility
  tercile, decision and exit (stop/target/time/policy); per-trade cost in R (spread, slippage, commission, swap) and
  the trades costs flipped to losers; calibration of p on taken and untaken candidates; live fill slippage vs the
  table cell. Cells under `attribution.min_trades` (30) are "noise". Champion-path trades only in the breakdowns
  (challengers apart). The improvement agent and research analyst read it first via `read_attribution`; hypotheses
  still go only through `file_hypothesis`; gap_watch caps unchanged. Reporting only: nothing is traded or changed.
  Open: live realised R per position (needs the engine trade record, item 8) and a dashboard view (item 12's other
  criteria). The research director now reads it as a capped, noise-aware input (see the director bullet).
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
- Exit-policy safety review fixes (2026-10-10, `engine/runner.py`, TRACEABILITY M3/M6/M7/R21): a raising blackout
  re-score or scale-out (model error, MT5 `symbol_info` failing) no longer escapes the tick/bar (ops/run.py catches
  only AssertionError): it is logged (`blackout_rescore_failed`, `scale_out_failed`), the position is kept and the
  others are still managed; `scaled` is set only after a successful partial close, with `scale_out_retries` (3)
  attempts in all. New per-tick `_stop_check`: price at or through the engine's stop while the broker's stop is
  looser (rejected `modify`) closes at market (`engine_stop_close`); reconciliation re-sends a rejected stop with a
  30 s doubling backoff up to `modify_backoff_max_s` (900 s). The Friday weekend rule keeps the engine's stop when
  it is tighter than the half-profit stop (never loosens). Fills recovered on restart (`_load_orders`; the ATR is now
  recorded in the pending_orders row before sending, older rows use the ATR implied by the initial stop) and orphans
  whose magic maps to exactly one loaded agent get that agent's exit policy back. Gaps: labels and the shadow book
  fill a stop at the stop even when a bar gaps through it (documented in `labels/exit_policy.py`; unchanged), the
  broker fills at the market, so a gap costs the live trade the gap; parity tests now cover shorts and a gap.
  **Known limitation (quant finding 4, not changed):** for policy families (trend, breakout, session-open) the entry
  threshold (`breakeven_prob`) and Kelly sizing (`size_multiplier`) still assume the binary target_atr/stop_atr
  payoff, while a policy's exits pay a distribution (trail, flat, scaled). Recommended fix: an EV hurdle from the
  policy's realised payoff distribution in the walk-forward (mean win and mean loss in R per family), or train on
  sign(ret) and size from the empirical payoffs; a quant-reviewer decision before any policy model trades.
- Closed-trade record (2026-10-10, `engine/runner.py`, `ops/gates_phase.py`, TRACEABILITY P6/P7): the engine now
  appends one `ClosedTrade` per fully closed position to `state/closed_trades.jsonl` (single O_APPEND write + fsync)
  for every exit: broker target/stop/stop-out (found at the bar close by `_sweep_closed`), time exit, hard flat,
  trail close, engine stop close, blackout close, kill switch, weekend loser, and trades that closed while the engine
  was down (restart). Fields: account, mode (demo for paper/demo engines), agent, side, lots at entry, entry/exit
  time and price, exit reason, net P&L, R against the initial stop, commission and swap (from the broker's deals;
  None when only the engine's fills are known), client order id, position id. **Scale-outs are folded into the final
  record** (`partial_lots`, lots-weighted exit price, summed P&L): one record = one labelled trade, so the gates'
  trade counts compare with the backtest's; `load_closed_trades` also counts a repeated (account, position) once.
  Idempotent across restarts (recorded position ids are read back from the file). Records are queued and written
  after the tick's exits and before the open-trade table is saved; a failure is logged and journalled
  (`closed_trade_record_failed`) and never blocks or raises from an exit. A trade the broker stops listing without an
  exit deal (MT5 `positions_get` returning None) is kept and re-checked for `close_confirm_checks` (8) bar closes
  instead of being forgotten. Same change, safety-review follow-ups: `_stop_check` backs off a refused engine-stop
  close (30 s doubling to 900 s, reset on success; the broker's stop stays meanwhile) instead of a close per tick, and
  a raising `positions()` in `_stop_check`, `_scale_out`, `_reconcile`, `_manage_open` or `_refresh_account` is logged
  and skipped (the account refresh then blocks entries via `dq_error`). Not done: concurrent appends from two engines
  on Windows are not locked (one line per write, rare); the paper broker's deals now also carry MT5-style
  `profit`/`commission`/`swap` columns. (The `_kill_switch`/`_weekend_rule` gap is closed by the review fix below.)
- Closed-trade review fixes (2026-10-10, trading-safety findings, `engine/runner.py`, `execution/mt5_adapter.py`,
  `ops/gates_phase.py`, `ops/health.py`, tests/test_closed_trade_review.py; TRACEABILITY X6, X8, P6, P7):
  **no fake closes on a terminal fault**: MT5 `positions_get`/`history_deals_get` returning None now raise with
  `mt5.last_error()` (and `close`/`modify` raise instead of reporting "no such position"; `order_send` None is a
  refused result); in the engine an unreadable deal history never confirms a close and an empty one only after
  `close_confirm_checks`, and nothing is recorded or forgotten from an unreadable book. **Unreadable-book check**:
  consecutive failed reads (counted once per tick) set `positions_unreadable` in the engine state's `dq_checks`
  (entries blocked, exits keep trying); health warns from the first failure and FAILs, so Telegram alerts, after
  `risk.positions_unreadable_alert` (5) in a row. **Guarded kill switch and weekend rule**: each close in its own try;
  a failed close is re-sent every tick (`_retry_closes`) and the kill switch re-runs every tick until this engine's
  magic range reads flat; `weekend_done` is set only once every position was handled. **Close-confirmed
  bookkeeping**: every exit path (kill switch, weekend, time exit, hard flat, trail, blackout, engine stop) drops a
  trade only on `res.ok`; reconciliation re-adopting a trade this engine sent keeps the pending_orders row's agent,
  initial stop and lots (R) and signal ATR, never the trailed stop. **Backoff only when the market is closed**:
  10017/10018 back off (30 s doubling to 900 s); requotes, price changes and raising calls retry every tick on every
  path. **Record retry**: a failed write stays queued (persisted in `orders_<account>.json` as `closing`), retried
  every `reconcile_every_s` up to `closed_record_retries` (10), then reported lost (`closed_records_lost` in the
  engine state, health FAIL, the record logged in full). **Torn writes**: the append completes short writes and starts
  a new line after a torn tail; the loader skips bad lines, keeps the rest (so `_recorded` is complete) and the
  engine shows a `closed_trades_unreadable` dq warning. Behaviour change the owner can see: the engine health line
  now names an unreadable terminal and a lost record. Not verified: the real MT5 terminal (Windows only).
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
- Account security review (2026-10-10, security-reviewer findings; `api/auth.py`, aux.db migration 4
  `auth_hardening`; tests/test_auth_security.py): forgot-password for the OWNER now needs a recovery code (spent)
  as well as the TOTP (`ForgotPasswordRequest.recovery_code`), so the phone or seed alone cannot take over the
  admin; every successful forgot-password queues a Telegram notice to the owner (a row with role
  `account_security` in state/agent_runs.jsonl, relayed by the Telegram outbox; email masked). Each TOTP step is
  accepted once per user (`totp_steps`; login, password change, forgot, /rearm via `AuthStore.verify_totp`, /mode
  via `verify_owner_totp`, shared counter). Login and recovery-login run scrypt against a dummy hash for unknown
  emails. Owner-only actions (`_require("owner")`, the API's `need("owner")`, recovery codes, /mode) need
  `email == auth.owner_email` and fail closed when it is unset; the users.json importer demotes a non-matching owner
  to viewer (audit `import_owner_demoted`, warning; kept when owner_email is unset, since every owner action is then
  refused anyway). Password change, forgot and disable delete the account's open reset links. Invite/reset links use
  the URL fragment (`/#invite=`, `/#reset=`, read once and cleared, `web/src/lib/links.ts`) and every response sends
  `Referrer-Policy: no-referrer`. Lockout counters live in aux.db (`auth_failures`, pruned to the 15-min window,
  survive restarts): 5 per email, plus 20 per client IP across emails (CF-Connecting-IP trusted only from the
  loopback tunnel peer). Known trade-off: anyone who knows an email can keep it locked out. DB files are chmod 0600
  on every open. e2e codes come from `freshTotp` (waits for an unused step, so the suite takes ~3.5 min).
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
- Discovery quant-review fixes (2026-10-10): K_eff now follows the pre-registered survivor unit
  (`DiscoveryConfig.survivor_unit`, default `feature`): feature survivors pay for every feature screened (~300), not
  the groups (~10); the discovery row records `n_features_screened`, `n_groups_screened`, `survivor_unit` and `k_eff`,
  and `registry.n_trials_effective` adds `k_eff` (older rows: features screened unless the unit was `group`). Group
  stability ranks groups by summed member importance in each subsample and counts the top `group_top_k` groups
  (default: top_k's share of groups), so correlated near-copies no longer deflate their group. `column_groups` maps
  columns to features on the 2,000 bars before the holdout start (no holdout bar read). `registry_sync.merge_rows`
  rewrites the result row's `preregistration.trial` when it renumbers. The inner permutation split is `inner_split`,
  with a test where labels outlive the purge gap. Charging K_eff to every later trial stays the conservative default,
  recorded as owner-acknowledged in `docs/research/preregistration-2027Q1.md`, with the extra rule that a discovery
  survivor must also pass the holdout rule before promotion.
- Slow TSMOM (2026-10-10, H-01, preset `slow`): `--variants '["slow"]'` runs tsmom with the signal on the feature-day
  bars (`signal_tf: "1d"`, 20/60/120-day vol-scaled returns, 60-day vol) traded on 4h bars, read on the first 4h bar
  whose close sees the settlement; barriers 3.0 / 1.5 x ATR(1d) frozen at entry (`atr_tf: "1d"`), time barrier 124
  4h bars (20 trading days), long and short, swap per rollover, one position at a time; walk-forward purge raised to
  31 days for the 28-day hold (M14; `TimeSeriesMomentumSpecialist.hold_calendar_days`). New hooks
  `Specialist.candidates_in_context` / `barrier_atr` (used by research `prepare`), `optional_config` and `presets`
  (accepted by `research_pass.parse_variants`); tsmom's default config, agent ids and labels are byte-identical
  (digest test). Research-only: without the d1 bars (the engine calls `candidates`) the slow option proposes nothing.
  Event count on data-v1 (no labels, no outcomes): 2,631 daily signals in the research window; one at a time with
  every trade held to the time barrier, 165. The true count lies between, so owner ruling A (event floor) is needed
  before the trial. Note for quant review: feature-day bars include a Friday-evening stub bar (settlement to the
  Friday close, visible Saturday), so "20 daily bars" is about 3.3 weeks, as for the existing 1d option. Run inputs:
  `docs/research/preregistration-2027Q1.md` H-01. No research trial was run.
- CPCV and CUSUM calibration (2026-10-10, rows M16/M25): `goldbot/research/cpcv.py` runs combinatorial purged CV
  (6 equal-duration groups of the research window, holdout excluded; 15 purged/embargoed splits of 2 test groups; 5
  rebuilt backtest paths) with the walk-forward's own cross-fitted calibration and threshold per path, reports the
  path Sharpe / mean R distribution and, across several configurations, PBO (CSCV). It is evidence on an existing
  trial (`<registry>.evidence.jsonl`), not a trial: no budget slot, no deflated-Sharpe count. Run it with
  `scripts/research_pass.py --cpcv <trial#> [...]`; the scheduler job `cpcv_quarterly` (first Sunday of each quarter,
  14:00 UTC, new `months` filter on schedules) does every gate-passing trial against its family's other trials ->
  `state/cpcv_<quarter>.md`. The evidence sidecar is not yet unioned by `registry_sync` with the release copy, and
  research.yml does not upload it. CUSUM: `goldbot/research/cusum.py` picks h by simulation so in-control residuals
  alarm within a quarter's expected trades with 5% probability (settings `drift.cusum_k`, `drift.cusum_false_alarm`;
  `cusum_h` 4.0 only without a backtest trade rate); `drift_watch` and `model_watch` pass the champion's
  `trades_per_week`. At 1 trade a week h is 3.46 (more sensitive than the old 4.0), at 5 a week 5.21 (fewer false
  halts). `goldbot/api/explain.py` still draws the CUSUM trace with the fixed `cusum_h` (API lane: should read the
  `cusum_h` now stored per agent in drift.json).
- CUSUM/CPCV review fixes (2026-10-10, rows M16/M25, quant review): CUSUM h is now simulated on two-point trade
  residuals (win +sqrt((1-p)/p), loss -sqrt(p/(1-p))) at the mean taken p in 0.01 buckets, choosing the most
  sensitive attained h whose false-alarm rate is <= 5% at 95% confidence (outcomes are discrete). Realised quarterly
  rates at p 0.4/0.5/0.6 and 2/5/15 trades a week: 3.4-4.8% (normal-based h gave 0.3-0.5% at p 0.4); a drop from
  p 0.4 to 0.3 is caught within a quarter 18-33% of the time against 4-6% before. The drift watch uses the shadow
  trades' mean p; the new-champion watch (`model_watch`) now calibrates for its own window (trades/week x 2) at the
  backtest hit rate, so it can actually fire inside two weeks (at ~2 or fewer trades a week it cannot alarm at 5%).
  ADR docs/decisions/0002: CPCV/PBO can only veto (fragile when PBO > 0.5 or most paths negative, recorded as
  `verdict` in evidence and report, not yet wired into `passed_gates`: owner decision, recommended after the first
  quarterly run); `research_pass --cpcv` refuses gate-failed trials unless `--diagnostic` ("diagnostic, not
  evidence"). PBO drops the never-traded group 0 (5 groups, 2 vs 3 both ways) and is labelled a lower bound of the
  selection set (every registered family trial on the timeframe, screened included). `cpcv_quarterly` skips trials
  whose feature version differs from what the bars build now. The evidence sidecar stays local-only (documented, not
  synced). Not done: `goldbot/api/explain.py` still draws the CUSUM trace with the fixed `cusum_h`.
- GitHub Actions supply chain hardened (2026-10-10, security): every action in `.github/workflows/*.yml` is pinned
  to a full commit SHA with the version as a comment (checkout v5.1.0, setup-python v6.3.0, setup-node v5.0.0, cache
  v4.3.0, upload-artifact v4.6.2, download-artifact v4.3.0; resolved via the GitHub API, not guessed). Token scopes:
  `ci.yml` is `contents: read` with `issues: write` only on `report-failure`; the data and research workflows are
  `permissions: {}` with `contents: write` + `issues: write` granted only to the job that publishes. Workflow inputs,
  `github.event_name` and matrix values reach scripts only through `env:` (data-dukascopy, research). Every checkout
  sets `persist-credentials: false` (no job pushes with git; releases and issues use `GH_TOKEN`). New concurrency
  groups `data-dukascopy` and `data-macro` (queue, never cancel). `.github/dependabot.yml` opens weekly grouped PRs
  for actions, pip and npm (`web/`). `actionlint` 1.7.12 (with shellcheck) is clean. Not exercised on GitHub yet:
  the first CI run on the PR is the check.
- H-01 review fixes (2026-10-10, before any H-01 outcome was seen): the screen's event floor is configurable,
  `research.screen_min_events` (1,000) with `research.screen_min_events_daily` (null = the same) for rules whose
  signal is daily, passed by `research_pass.py`; behaviour unchanged until the owner sets it (ruling A, recommended
  150). A screen that fails only on the event floor is now `inconclusive (event floor)`: still a recorded, charged
  `screened` trial with no model, but the report says it cannot retire the hypothesis. The slow preset's daily
  inputs (signal and ATR) drop the Friday stub bar (daily bars spanning under 12 trading hours), so lookbacks are
  5-a-week trading days; `schedule_h` is left out of configurations with `signal_tf` (`Specialist.unused_config`),
  so the slow agent id is now `tsmom-g0-c12c9afb24` (default tsmom ids unchanged, digest test green). Added an
  unfiltered-context truncation test. Pre-registration H-01 now freezes the stub, Wilder ATR on mids, the score
  formula, min_score 0.5, the time-barrier wording, costs and swap source, the short-swap bias (report long-only),
  holdout-crossing labels dropped, 10 trades per positive year, the t-stat and DSR count, and the inconclusive
  branch; it states H-01 can retire but not promote. Not done: the research report does not yet print the
  long-only net R split the pre-registration asks for (a follow-up before the run); the 2,631 signal count predates
  the stub fix (recount at the freeze). No research trial was run.

- H-01 pre-freeze (2026-10-11): any primary-signal screen below the event floor is "inconclusive (event floor)",
  whatever its sign; "screen passes but net t < 1.65 or < 3 positive years (incl. 2021/22)" is inconclusive too.
  Reports carry long-only and short-only net R. Recount after the Friday-stub fix (data-v1, events only): 2,154 daily
  signals (1,466 long / 688 short), one-at-a-time lower bound 154 events, first signal 2010-06-18. Owner ruling A
  (recommended floor 150 for daily signals, can retire not promote) must be committed as
  `research.screen_min_events_daily` before the run.

- Dependencies (2026-10-11): Dependabot groups minor/patch updates per ecosystem; majors come as separate PRs to be
  hand-tested (the 15-package web group #65 was closed: TypeScript 7 broke `npm ci` through openapi-typescript, and
  React 19 / lightweight-charts 5 / Vite 8 / Vitest 5 need code changes). TypeScript majors are ignored until
  openapi-typescript supports them. Actions now run on node24 (PR #64): a self-hosted runner (`vars.CI_RUNNER`) must
  be Actions Runner >= 2.327.1.
- CI install failures reported (2026-10-10, closes the loop on issue #66, `npm ci` ERESOLVE shown as "no runner was
  assigned"): the backend and frontend install steps tee into `ci-out/<job>-install.txt` and add `<job>-install` to
  the failed list, so `report-failure` posts the install output. "No runner was assigned" is now said only when no
  artifact exists at all. `actionlint` + shellcheck clean; the first failing run on GitHub is the real check.
- Daily owner digest (2026-10-10, `goldbot/telegram/digest.py`, TRACEABILITY A12): the Telegram service sends one
  message a day at `telegram.digest_at` (default 06:45 UTC): health status (failing/warning checks named), yesterday's
  proposals by outcome, closed trades (net $ and R), open positions, drawdown stage and cap use, halts, the next
  pre-registered trial or quarter budget, the attribution best/worst non-noise cell, and what waits for the owner
  (approvals, ROADMAP decision count, deploy on offer). Units and UTC on every number, no identifiers, under 15 lines;
  each section degrades on its own. Sent once per slot (`state/telegram_digest.json`, recorded after delivery); a
  slot missed while the service was down goes out on start if under 6 h late. Reporting only.

- Web stack upgrade (2026-10-10, four `chore(web)` commits replacing #65): React 19.3.0, react-dom 19.3.0,
  @types/react(-dom) 19.3.0, lightweight-charts 5.2.1, Vite 8.3.1, @vitejs/plugin-react 6.1.1, Vitest 5.0.2,
  @vitest/coverage-v8 5.0.2, jsdom 30.1.1, TanStack Query 5.104.1, Playwright 1.64.0, ESLint 10.12.0,
  typescript-eslint 8.71.1; TypeScript stays 5.9.3 (openapi-typescript 7.13 needs TS 5). Vite/plugin-react/Vitest/
  jsdom are the newest releases at least two weeks old (8.3.4, 6.1.2, 5.0.3, 30.1.2 were days old; no GitHub
  advisory affects the chosen versions). Nothing imports lightweight-charts yet (Health charts are inline SVG), so
  there was no series code to port; new price/equity charts must use the v5 `chart.addSeries(LineSeries, ...)` API.
  Vitest 5 and jsdom 30 need Node 22 (`engines` already says so; Node 20 fails to start the workers). The lockfile
  was regenerated (npm 10 hit ERESOLVE/an arborist crash moving off the Vite 5 tree); `npm ci` on npm 10 is clean.
  `npm audit` (prod and dev) reports 0 vulnerabilities, down from 7 dev ones. Production JS bundle 245 -> 318 kB
  (75 -> 96 kB gzip), mostly React 19. Deferred: TypeScript 7 and @types/node 26 (Node 22 is the runtime).
- Trading sessions from the broker (2026-10-10, TRACEABILITY D13, still partial): read-only broker method
  `trading_sessions(symbol)` (paper: static hours; RemoteBroker via the bridge allow-list) returns 7 server days of
  sessions resolved to UTC per date (Athens DST tested); `SessionTable.from_broker` makes the runtime table (reported
  days: only reported sessions open, so early closes and holidays block entries; an unreadable list falls back to the
  static defaults with a logged reason) and `BrokerSessions` refreshes it per server day. Finding: the MetaTrader5
  Python package has no session schedule (`session_deals` is a deal count), so MT5 reports the documented hours; real
  holiday hours need an MQL5 `SymbolInfoSessionTrade` helper. Engine hook for the runner lane (not wired):
  `self._sessions = BrokerSessions(broker, symbol, fallback=SessionTable(server_tz=...))` at start, then
  `why = self._sessions.entry_block(now)` as an entry-blocking reason. Exits are unaffected.

## Next steps (no owner input needed unless marked)
- XAUUSD trader playbook (2026-10-10, owner request): `docs/research/xauusd-trader-playbook.md` lists ~70 things a
  professional gold trader considers (drivers, CFD microstructure, technicals, risk management, process), each with
  sources, evidence grade and goldbot status, then a ranked gap list (risk gaps G-1..G-10 first: swap in live EV,
  holiday/reopen rule, tier-2 releases, total open-risk cap, drawdown Monte Carlo, loss-streak throttle, min-lot
  feasibility, weekend gap risk, MAE/MFE, edge-linked sizing). BACKLOG items 16-24 and H-15 come from it. Open
  ops check from the D13 review: confirm whether the IC Markets server clock follows Europe/Athens or US DST dates.
- COT positioning and GLD holdings, point in time (2026-10-10, indicator survey #67/#68, hypotheses H-09/H-12, row
  D26): `goldbot/data/positioning.py` + `scripts/positioning_data.py` + `.github/workflows/data-positioning.yml`
  (Saturdays 05:23 UTC) publish `positioning.parquet` on release `positioning-v1`: CFTC disaggregated futures-only
  COMEX gold (088691) managed-money long/short/net, open interest, commercials net (producer/merchant + swap dealers),
  and GLD tonnes from the issuer archive (`GLD_US_archive_EN.csv`). COT is stamped Friday 15:30 ET (zoneinfo: 20:30
  UTC, 19:30 in US DST), the next business day after the Friday in a federal-holiday week, never before the shutdown
  catch-up floors (2013, 2018-19 conservative and UNVERIFIED against CFTC notices; 2025 from CFTC 9147-25: reports to
  23 Dec 2025 not before 3 Jan 2026); unscheduled closures (mourning days 2018-12-05, 2025-01-09) count as holidays;
  GLD the next US business day 14:00 UTC. Publishing never loses history: only a confirmed-missing release/asset is a
  first run, and the script refuses (exit 2, nothing published) a frame with fewer rows per source or any published
  row missing. The 2006+ backfill carries current corrected COT values with first-release stamps (rare, small
  revisions: accepted look-ahead, documented in `cot_frame`). Revisions add rows (first release kept); a row missing from the previous successful download is
  stamped at the run that first saw it (catches future delays). GLD blocked/reformatted -> "source unavailable",
  published rows kept, run still green (reported in the step summary), no scraping. Opt-in features
  (`goldbot/features/positioning.py`, `research_pass.py --positioning DIR`, not with `--discover`): MM net % OI, its
  52-report z-score and 4-report change, GLD 5/20-day % change; registered only by `enable()`, so the default feature
  version is unchanged. `fetch_data_release.py --positioning` loads the store table `positioning`. The old unused
  `macro.fetch_cot_gold` / `gld_holdings_from_csv` (no holiday rule, GLD stamped 06:30 ET) are removed. Not yet: the
  workflow has never run (Actions -> data-positioning -> Run workflow); `research.yml` has no positioning input; the
  cleaner opt-in home is `goldbot/research/pipeline.py` OPT_IN_FEATURES (outside this lane).
- Minor traceability fixes (2026-10-10, gap item 20): `walkforward.splits_for` / `window_for` take an optional
  `settings` (its `walkforward` months and `labels` purge/embargo replace `WINDOWS`; 1d keeps the constant), row M15
  stays partial until the research callers (`pipeline.run_specialist`/`run_pool`, `discovery.discover`) pass it
  (values equal today, so folds do not change); the tournament promotes shadow -> live at DSR >= 0.95 (row G6).
- Encrypted off-host backups (2026-10-10, state-store step 2, row P8, `goldbot/ops/backup.py`): scheduler job
  `backup` (daily 22:15 UTC) snapshots every `state/*.db` with the online backup API (integrity-checked before upload),
  the state JSON, trial registry, models and the brain's own Parquet (not release sources, not features/labels, not
  `.secrets.json`) with a SHA-256 manifest, saves them with restic to Oracle Object Storage (secrets only in restic's
  environment, masked in errors) and writes `state/backup_last.json`. The brain only appends: retention
  (`forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune`) runs monthly from the owner's Mac with a second
  key (`scripts/backup_retention.sh`, refuses on a server; uploads a marker the brain records, health `backup_prune`
  warns after 45 days). Staging and restore dirs are 0700 under umask 077; JSONL copies end at the last newline; a
  month partition caught mid-dedupe is retried, then listed in `skipped_partitions`; `verify_restore` refuses symlinks
  and paths outside the restore dir. Decision record docs/decisions/0002-backup-scope-and-thresholds.md (renumbered
  from 0001, which is the auth ADR); `restore_drill` (Sunday 10:00) restores to a temp dir and re-verifies everything
  (`state/restore_drill_last.json`). Health `backup_age` warns at 26 h (Telegram once), fails at 72 h; `restore_drill`
  fails on a failed drill. `run.py backup [--init] | restore --latest --to DIR | restore-drill`; brain_bootstrap.sh
  installs restic. OWNER: create the bucket with Object Versioning and a 30-day previous-version lifecycle rule, a
  no-delete writer key for the brain and a retention key for the Mac, store the four `restic-*` keys on the brain and
  the four Keychain items on the Mac, and run the retention script monthly (RUNBOOK 0.6).
  Not verified against a real restic or Oracle endpoint from here (restic is not installed in the sandbox; the
  integration test runs where it is).
- Broker margin in the gate and tick flags (2026-10-10, ROADMAP W2-2, rows R8/D11): brokers gain a read-only
  `margin_required(symbol, side, lots, price)` (MT5 `order_calc_margin`, paper 1:20, bridge allow-list retry-safe);
  `RiskGate.check(..., margin_required=...)` uses the larger of the broker figure and notional / 20 and keeps the
  300% rule; a broker that cannot answer falls back to 1:20 with the reason in `GateDecision.margin_note` (never a
  smaller margin). `Tick.flags` (default 0) and `tick_key` make the live dedup (time_msc, bid, ask, flags). The engine
  passes `margin_required=broker_margin(self.broker, self.cfg.symbol)` to both `self.gate.check` calls
  (proposal and approval re-check; tests/test_engine_margin.py), so R8 is implemented.
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
- FYI for the OWNER (no decision needed, 2026-10-10): `research.reserved_trials_quarter: 13` holds the 13 trials
  of the Q1 2027 pre-registration (`docs/research/preregistration-2027Q1.md`) before the research director and the
  label grid can spend any; the budget itself (20) is unchanged. Revisit the number with each quarter's
  pre-registration. Residual: the research analyst's `run_trial` tool still checks the full quarter budget, not the
  reservation (left out of this change's scope).
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
