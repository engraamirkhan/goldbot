# goldbot — status and next steps

Canonical design: `docs/DESIGN.md` (exported from the original Claude design doc; edit it here from now on).
Standing instructions for Claude sessions: `CLAUDE.md`.

## Where things are (as of 2026-10-01)
- GitHub `engraamirkhan/goldbot`, `main` is the only branch. CI (`ci.yml`) lints, tests and dry-runs on every push and
  opens an issue labelled `ci` with the full report on failure. Data workflow (`data-dukascopy.yml`) builds yearly
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

## Next steps (no owner input needed unless marked)
1. Confirm the 2025 data run gives full monthly coverage (issue "data coverage 2025"); then dispatch 2010-2026.
2. Research pass: `scripts/fetch_data_release.py` then session-open walk-forward on real bars; record in registry.
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
