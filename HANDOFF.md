# goldbot — status and next steps

Canonical design: `docs/DESIGN.md` (exported from the original Claude design doc; edit it here from now on).
Standing instructions for Claude sessions: `CLAUDE.md`.

## Where things are (as of 2026-10-01, evening)
- GitHub `engraamirkhan/goldbot`. Work from 2026-10-01 is on branch `claude/gifted-goldberg-lcb7pl` (not yet merged to
  `main`). CI (`ci.yml`) runs pre-commit, backend lint/mypy/unit/integration, frontend eslint/tsc/vitest, an API
  contract check and Playwright e2e as separate jobs, and opens one issue labelled `ci` with each failed job's output. Data workflow (`data-dukascopy.yml`) builds yearly
  1m Parquet files to release `data-v1` and keeps a "data coverage <year>" issue updated.
- Built and tested: point-in-time store, calendar/UTC, resampler, loaders, quality checks, macro loaders; feature
  registry (volatility, MA families + ribbon, trend, mean-reversion, MFI, breakout, microstructure, S/R, swings,
  gaps, candles, session/calendar, macro); triple-barrier labels; specialist interface with agent lineage;
  session-open specialist; purged walk-forward + LightGBM meta-labeller + deflated Sharpe + trial registry;
  RiskGate + supervisor; broker protocol, paper broker, MT5 adapter, account classifier; TradingView webhook;
  account registry + keyring credential prompts (demo-first, live locked by gate); rule-table allocator;
  Telegram approval centre + bot adapter; trading engine loop; FastAPI backend with multi-user auth
  (owner/approver/viewer, password + authenticator, invites, lockout, audit); React+TS dashboard
  (Overview, Approvals, Agents, Feeds, Users); VPS bootstrap (NSSM services, Cloudflare Tunnel) and service entry points.
- Typed stack: records are pydantic (`goldbot/base.py`), settings are a validated `Settings` model, every API endpoint has
  request/response models, mypy is clean; web types are generated from the backend OpenAPI schema.
- Data pull fixes (2026-10-01): dukascopy-node `-to` is exclusive (the last day of every month was missing) and one bad
  day aborted a whole month (the MISSING months in 2010-2018). Fixed with week chunks + retries + salvage pass; the
  workflow now refetches only gaps (`full_refresh` refetches all, keeping published bars as fallback). A full refresh of
  2010-2026 was dispatched from the branch: run https://github.com/engraamirkhan/goldbot/actions/runs/36885085622 ;
  results land in the "data coverage <year>" issues.
- pandas 3 keeps s/ms/us timestamp units: always use `timeutil.epoch_ns`; the old `.asi8` code made the gap check and
  some time features wrong by 1000x (fixed, with regression tests).

## Next steps (no owner input needed unless marked)
1. Read the "data coverage <year>" issues from run 36885085622. Years with MISSING months: re-dispatch
   `data-dukascopy.yml` (no full_refresh) for just those years; it only refetches gaps. Then merge the branch to `main`
   (OWNER: approve the merge, or say "merge it").
2. Research pass: `scripts/fetch_data_release.py` then session-open walk-forward on real bars; record in registry.
   Sandboxes cannot download release assets reliably, so run it as a workflow that posts results to an issue.
3. Scheduler: nightly cost tables + classifier, Saturday retrain + shadow, monthly bounded research loop.
4. Journal + agent layer (data steward, research analyst, risk officer, journal coach, improvement agent).
5. Population tournament mechanics (fitness, cloning, retirement) on top of agent lineage.
6. VPS: provision Windows VPS, run `goldbot/ops/vps_bootstrap.ps1` — OWNER: log in to the two MT5 demo terminals
   once and answer the credential prompts; create the Cloudflare tunnel and enter its token on the VPS.
7. Dashboard first run — OWNER: create the owner account with the setup code the API prints; invite others.

## Environment facts
- Claude sandboxes (cloud container and the Mac's Cowork VM) cannot reach market-data hosts or download Actions
  logs; GitHub API works. Data runs in GitHub Actions. Reports come back as GitHub issues.
- MetaTrader5 Python package is Windows-only; execution runs on the VPS.
- On the personal Claude account, cloud sessions push to GitHub directly (Claude GitHub App is installed on this
  repo); no device dependency.
