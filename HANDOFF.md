# goldbot — status and next steps

Canonical design: `docs/DESIGN.md` (exported from the original Claude design doc; edit it here from now on).
Standing instructions for Claude sessions: `CLAUDE.md`.

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
  It can only be dispatched once research.yml is on `main` (GitHub rule), i.e. after PR 36 is merged.
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

## Next steps (no owner input needed unless marked)
1. Re-pull the bars: `data-dukascopy.yml` with `full_refresh` (hours). The Dukascopy pull truncated fractional XAUUSD
   volumes to 0 with an int cast, so every tick-volume feature was constant on the published history and breakout
   (which needs a volume surge) never fired; the research report now prints the share of zero-volume 1m bars. Then
   re-run research.yml for all four families. Results so far (2026-10-06, issues "research: <family>"): no family has
   an edge at its default settings — session_open too few candidates per fold; trend (trial #4, one position at a
   time) model takes 2 trades in 13 years; mean_reversion (trial #5) 20 trades in 15 years (PF 8.8) far below the
   trade-count gates; breakout 0 candidates (the volume bug). Until one passes the gates the engines propose nothing.
   Leakage: the shifted-label "shuffle AUC" was dropped (it reads ~0.7 on any non-overlapping trade sequence without
   any lookahead); reports now carry a lookahead check (features rebuilt on history cut at 70%, offending columns
   listed) and the model's out-of-fold AUC.
2. On the VPS, check `state/news_feeds.json` after the first hour: the feed URLs in `settings.yaml: news` could not be
   verified from a Claude sandbox. Fix any that fail; the collector skips broken feeds.
3. VPS: provision Windows VPS, run `goldbot/ops/vps_bootstrap.ps1` — OWNER: log in to the two MT5 demo terminals
   once and answer the credential prompts; create the Cloudflare tunnel and enter its token on the VPS; store a
   GitHub token with `python -m goldbot.ops.accounts set github-token` (bar sync + shared trial registry), an
   Anthropic API key with `python -m goldbot.ops.accounts set anthropic-api-key` (staff agents and headline scoring;
   off without it) and the Telegram bot token with `python -m goldbot.ops.accounts set telegram-bot-token` (from
   @BotFather), and put your Telegram user id in `settings.yaml: telegram.allowed_user_ids`.
4. Dashboard first run — OWNER: create the owner account with the setup code the API prints; invite others.
5. Session-open has too few candidates per 15m test fold (about 90 a year against the 60-per-fold minimum); a
   rationale-backed variant (looser Asia-range filter or both sessions) is the next research trial for it.

## Environment facts
- Claude sandboxes (cloud container and the Mac's Cowork VM) cannot reach market-data hosts or download Actions
  logs; GitHub API works. Data runs in GitHub Actions. Reports come back as GitHub issues.
- MetaTrader5 Python package is Windows-only; execution runs on the VPS.
- On the personal Claude account, cloud sessions push to GitHub directly (Claude GitHub App is installed on this
  repo); no device dependency.
