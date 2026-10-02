# goldbot — status and next steps

Canonical design: `docs/DESIGN.md` (exported from the original Claude design doc; edit it here from now on).
Standing instructions for Claude sessions: `CLAUDE.md`.

## Where things are (as of 2026-10-02)
- GitHub `engraamirkhan/goldbot`. Work from 2026-10-01/02 is on branch `claude/gifted-goldberg-lcb7pl`, open as
  PR https://github.com/engraamirkhan/goldbot/pull/36 (OWNER: merge it; Claude sessions are not allowed to push to
  `main`). CI (`ci.yml`) runs pre-commit, backend lint/mypy/unit/integration, frontend eslint/tsc/vitest, an API
  contract check and Playwright e2e as separate jobs, and opens one issue labelled `ci` with each failed job's output.
- Built and tested: point-in-time store, calendar/UTC, resampler, loaders, quality checks, macro loaders; feature
  registry (volatility, MA families + ribbon, trend, mean-reversion, MFI, breakout, microstructure, S/R, swings,
  gaps, candles, session/calendar, macro); triple-barrier labels; specialist interface with agent lineage;
  session-open specialist; purged walk-forward + LightGBM meta-labeller + deflated Sharpe + trial registry;
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
- pandas 3 keeps s/ms/us timestamp units: always use `timeutil.epoch_ns`, never `.asi8`.

## Next steps (no owner input needed unless marked)
1. OWNER: merge PR 36. Then dispatch `research.yml` (session_open, 2010-2026) and read the "research: session_open"
   issue; until a model passes there is no champion, so the engine proposes nothing (by design).
2. Shadow book in the engine: run every challenger (and retired agents) on the same bars without orders, record
   trades to `state/shadow_<version>.json` as `PerfStats` (the Saturday job already reads it to promote/retire).
   Also feed live champion PerfStats for the turnover gate and a CUSUM alarm that calls `restore_previous`.
3. Journal + agent layer (data steward, research analyst, risk officer, journal coach, improvement agent).
4. Population tournament mechanics (fitness, cloning, retirement) on top of agent lineage and the shadow book.
5. One trial registry: the VPS monthly loop writes `state/research_registry.jsonl`, the research workflow writes the
   `research-v1` release copy; sync them (VPS pushes/pulls the release asset) so N is counted once.
6. VPS: provision Windows VPS, run `goldbot/ops/vps_bootstrap.ps1` — OWNER: log in to the two MT5 demo terminals
   once and answer the credential prompts; create the Cloudflare tunnel and enter its token on the VPS.
7. Dashboard first run — OWNER: create the owner account with the setup code the API prints; invite others.

## Environment facts
- Claude sandboxes (cloud container and the Mac's Cowork VM) cannot reach market-data hosts or download Actions
  logs; GitHub API works. Data runs in GitHub Actions. Reports come back as GitHub issues.
- MetaTrader5 Python package is Windows-only; execution runs on the VPS.
- On the personal Claude account, cloud sessions push to GitHub directly (Claude GitHub App is installed on this
  repo); no device dependency.
