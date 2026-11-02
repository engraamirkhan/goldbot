# 0066: Retire unused feeds and dashboard screens

Status: accepted (owner directive 2026-11-02; PD + PO + CTO, 2026-11-02)

## Context
The owner's directive of 2026-11-02 is to focus on the main goal: double capital weekly with strategies that are
net-profitable after costs. Code that feeds no trading decision still costs a lot. It has to be deployed, watched by
health checks, kept on heartbeat lists and covered by tests, and the dashboard spends space on it. The CTO audit
(2026-11-02) measured each feed and job:

- **TradingView author ideas** (`tv-ideas`, ADR 0059): 296 of 299 polls returned 0 new ideas. No strategy reads them.
- **TradingView direct pull** (`tv-pull`, `tv_reconcile`, ADR 0030): nothing reads it live. The engine reads no
  pulled plot, and no `requires_tv` agent has met its promotion rule.
- **TradingView webhook** (`goldbot/webhook/`): it feeds only the same unused `tv_indicators` alert rows.
- **News scoring**: 4,764 polls in 30 days, and every one was stored `scored: 0` because no Anthropic key is set.
  So the news shock blackout (D16, `risk.shock_blackout_min`) has never fired. The scheduled tier-1 and tier-2
  calendar blackouts do not depend on scoring and still work.
- **`agents_daily` / `agents_presession` / `agents_weekly`**: skipped on every run.
- **Dashboard**: the Research section (Today and trials, Road to live, Strategies, Agents) and the Feeds section
  (Feeds, News, TradingView card) answer no question the owner acts on. Health is the only part in daily use.

## Decision
Remove the following. The code goes in one cleanup release made of three lanes: backend, web and ops.

1. TradingView ideas: `data/tv_ideas.py`, `goldbot-tv-ideas.service`, the `tv-ideas` CLI and `check_tv_ideas`.
2. TradingView pull: `data/tv_pull.py`, `tv_wire.py`, `tv_alias.py`, `tv_reconcile.py`, `ops/tv_pull_health.py`,
   `goldbot-tv-pull.service`, the `tv_reconcile` job and the `tv-pull` CLI. `tv_csv` and `tv_catalog` stay, because
   `engine/tv_context.py` uses them.
3. TradingView webhook: `goldbot/webhook/`, `goldbot-webhook.service` and the `webhook:` settings.
4. Scheduler jobs `agents_daily`, `agents_presession` and `agents_weekly`. `goldbot/agents/` stays.
5. The `fxstreet` news feed in config. The news collector stays, because it is a RiskGate input.
6. Dashboard: the Research section (Research, ResearchToday, Progress, Agents, `lib/progress.ts`; routes
   `/api/research`, `/api/gates`, `/api/attribution`, `/api/trades/summary`, `/api/agents`, `/api/agent-runs`) and the
   Feeds section (Feeds, News, TradingViewCard; routes `/api/jobs`, `/api/news`, `/api/calendar`, `/api/tradingview`).
   `Activity.tsx` and `/api/activity` are unused and go too. The `api/activity.py` readers stay for `trades_overview`.
   `/api/feeds`, `/api/health` and the Health screen stay. Health moves to its own section, `#/health`, and
   `#/feeds/*` aliases to it. Nav: Home, Trades, Limits, Health, Settings.
7. Heartbeat and health lists drop `webhook` and `tv-pull`.

Not touched: RiskGate, exits, reconcile, backups and secrets. No server data is deleted (`state/tv.db`,
`state/tv_ideas.db`, `data/tv_indicators`, news): it stays on disk and in the backups, so a reversal loses nothing.

The population loop and the news shock blackout are phase 2 (BACKLOG 142). That phase touches the engine and
research paths and needs trading-safety and quant review first. This ADR does not decide it.

## Consequences
- Fewer services and health rows. A dead TradingView session or idea feed can no longer raise a WARN that hides a
  real one.
- The RUNBOOK loses sections 2.7b, 2.7c, 2.7d and 2.7f, and the TradingView cookie and webhook-secret rows in 2.3.
  `docs/ux/dashboard-v2.md` records the five-item nav.
- TRACEABILITY F25, R52 and R53 move to `deviates (retired, ADR 0066)`. R51, R54 and R57 stay (`tv_csv`,
  `tv_catalog` and `tv_context` are kept).
- The research views leave the dashboard. Research results are still in `docs/research/` and on issue #34.
- **Reversal trigger:** an arm or strategy that needs TradingView data or scored news passes its history
  qualification (explore + confirm, net of costs). The feed it needs then comes back from git history under a new
  ADR. The stored data lets that history pass run before the feed is restored.
