# 0066: Retire unused feeds and dashboard screens

Status: accepted (owner directive 2026-11-02, scope amended by the owner the same day; PD + PO + CTO, 2026-11-02)

## Context
The owner's directive of 2026-11-02 is to focus on the main goal: double capital weekly with strategies that are
net-profitable after costs. Code that feeds no trading decision still costs a lot. It has to be deployed, watched by
health checks, kept on heartbeat lists and covered by tests, and the dashboard spends space on it. The CTO audit
(2026-11-02) measured each feed and job:

- **TradingView chart data** (webhook alerts, the direct pull with `tv_reconcile`, the CSV import with its repaint
  check, the plot catalogue, the `tradingview` and `tv_plots` features, the `tv_event` specialist and its frozen
  `TV_GRID_V1`, and the engine's `tv_context` shadow watch; ADR 0030): it is a second copy of prices and indicator
  plots. The engine already trades on the broker's own real-time prices, which is what it fills against. No live
  strategy reads a TradingView plot. Every `requires_tv` agent is shadow-only with share 0, and none has met its
  promotion rule. The data path brings ToS risk (the session cookie), a repaint risk that has to be checked, and a
  cookie that expires. It brings no measured edge.
- **TradingView author ideas** (`tv-ideas`, ADR 0059): 296 of 299 polls returned 0 new ideas. This is expected,
  because the three authors publish a few ideas a week. It is the only TradingView input that is not price data:
  it records analysts' calls, which research scores net of costs. **It is kept** (owner, 2026-11-02).
- **News scoring**: 4,764 polls in 30 days, and every one was stored `scored: 0` because no Anthropic key is set.
  So the news shock blackout (D16, `risk.shock_blackout_min`) has never fired. The scheduled tier-1 and tier-2
  calendar blackouts do not depend on scoring and still work.
- **`agents_daily` / `agents_presession` / `agents_weekly`**: skipped on every run.
- **Dashboard**: the Research section (Today and trials, Road to live, Strategies, Agents) and the Feeds section
  (Feeds, News, TradingView card) answer no question the owner acts on. Health is the only part in daily use.

## Decision
Remove the following. The code goes in one cleanup release made of three lanes: backend, web and ops.

1. **The whole TradingView chart-data path:**
   - Pull: `data/tv_pull.py`, `tv_wire.py`, `tv_alias.py` and `ops/tv_pull_health.py`, `goldbot-tv-pull.service` and
     the `tv-pull` CLI.
   - Cross-feed check: `tv_reconcile.py` and its job.
   - Import: `data/tv_csv.py` with the `tv-import` CLI, and `research/tv_repaint.py` with `tv-repaint-check`.
   - Catalogue: `data/tv_catalog.py`.
   - Features: `features/tradingview.py` and `features/tv_plots.py`.
   - Specialist: `specialists/tv_event.py`, `research/tv_grid.py`, `requires_tv`, and `engine/tv_context.py` with
     its runner wiring.
   - Webhook: `goldbot/webhook/`, `goldbot-webhook.service` and the `webhook:` settings.
   - Dashboard: `api/tv_status.py`.
   - Settings: `tv_pull` and `tv`.
2. **Kept: the TradingView author-ideas feed.** That is `data/tv_ideas.py`, `goldbot-tv-ideas.service`, the
   `tv-ideas` CLI and `check_tv_ideas`, following KnightsofGold, Goldviewfx and ArmanShabanTrading. It reads TradingView's public pages only and needs no cookie. Its data stays point in time on `available_utc`.
3. Scheduler jobs `agents_daily`, `agents_presession` and `agents_weekly`. `goldbot/agents/` stays.
4. The `fxstreet` news feed in config. The news collector stays, because it is a RiskGate input.
5. Dashboard: the Research section (Research, ResearchToday, Progress, Agents, `lib/progress.ts`; routes
   `/api/research`, `/api/gates`, `/api/attribution`, `/api/trades/summary`, `/api/agents`, `/api/agent-runs`) and the
   Feeds section (Feeds, News, TradingViewCard; routes `/api/jobs`, `/api/news`, `/api/calendar`, `/api/tradingview`).
   `Activity.tsx` and `/api/activity` are unused and go too. The `api/activity.py` readers stay for `trades_overview`.
   `/api/feeds`, `/api/health` and the Health screen stay. Health moves to its own section, `#/health`, and
   `#/feeds/*` aliases to it. Nav: Home, Trades, Limits, Health, Settings.
6. Heartbeat and health lists drop `webhook` and `tv-pull`. `tv_ideas` stays.

Not touched: RiskGate, exits, reconcile, backups and secrets. No server data is deleted (`state/tv.db`,
`state/tv_auth.db`, `data/tv_indicators`, `state/tv_ideas.db`, news): it stays on disk and in the backups, so a
reversal loses nothing. The TradingView keyring entries can be deleted by hand once the release is deployed.

The population loop and the news shock blackout are phase 2 (BACKLOG 142). That phase touches the engine and
research paths and needs trading-safety and quant review first. This ADR does not decide it.

## Consequences
- Fewer services and health rows. A dead TradingView session can no longer raise a WARN that hides a real one.
  The cookie's ToS exposure (ADR 0030) ends.
- Research loses TradingView plots as features and signals. Indicators the owner uses are already rebuilt in Python
  from broker bars (`features/smc_replica.py`, `box_course.py`, `mfi_signals.py`), and those stay.
- The RUNBOOK loses sections 2.7b, 2.7c and 2.7d, and the TradingView cookie and webhook-secret rows in 2.3. Section
  2.7f (author ideas) stays. `docs/ux/dashboard-v2.md` records the five-item nav.
- TRACEABILITY F11, F12, F25, R51, R52, R53, R54, R55, R57 and R58 move to `deviates (retired, ADR 0066)`.
  R67 (replica shadow engine) loses `tv_context` from its code list in the cleanup release. Its replica columns
  come from broker bars and stay.
- The research views leave the dashboard. Research results are still in `docs/research/` and on issue #34.
- **Reversal triggers:**
  - TradingView chart data or scored news comes back only if an arm or strategy that needs it passes its history
    qualification (explore + confirm, net of costs) on the stored data. A new ADR then restores the feed from the
    repository history.
  - The author-ideas feed is reviewed if it brings no idea that research can score within 90 days.
