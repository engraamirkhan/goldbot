# goldbot — evolving XAUUSD trading assistant

Phase 0 codebase for the design in *Gold Trading System — Design*. Research runs on a Mac; live execution
runs on a Windows VPS beside MT5 terminals (IC Markets, Vantage). Aamir confirms entries on Telegram;
everything after entry is automatic under a RiskGate no model can override.

## What is here (Phase 0 definition of done)

| Area | Module | Status |
| --- | --- | --- |
| Point-in-time Parquet store + DuckDB views, `asof_join` that refuses nominal-date joins | `goldbot/data/store.py` | done, tested |
| Three named days (feature-day 13:30 NY, risk-day 00:00 UTC, swap-day from broker), EET→UTC per bar | `goldbot/data/timeutil.py`, `calendar.py` | done, tested (incl. US/EU DST) |
| Ticks → 1m → 15m/1h/4h/1d/1w with `visible_at`, no placeholder bars | `goldbot/data/resample.py` | done, tested |
| Loaders: MT5 Mac CSV export, Dukascopy CSV | `goldbot/data/loaders.py`, `scripts/` | done |
| Quality checks (gaps, spikes, bid>ask, duplicates, stale feed) | `goldbot/data/quality.py` | done, tested |
| FRED / CFTC COT / GLD with `available_utc` stamps | `goldbot/data/macro.py` | done (needs API key to run) |
| Versioned feature registry: volatility, MA families + ribbon, trend, mean-reversion, MFI(12) replica, breakout, microstructure, support/resistance from confirmed swings, swings, gaps, candles, session/calendar, macro | `goldbot/features/` | done, tested |
| Multi-timeframe merge without lookahead | `goldbot/features/mtf.py` | done, tested |
| Spread-adjusted triple-barrier labels + uniqueness weights | `goldbot/labels/` | done, tested |
| Specialist interface with agent identity (parent, generation, clone/mutate) | `goldbot/specialists/base.py` | done, tested |
| Session-open specialist (first to build) | `goldbot/specialists/session_open.py` | done, tested |
| Purged/embargoed walk-forward (15m 24/3/3, 1h 36/6/6), LightGBM meta-labeller, isotonic calibration, deflated Sharpe, trial registry | `goldbot/research/` | done, dry-run green |
| RiskGate (caps, staged drawdown, blackout, spread, stale data, EV floor, FCA 1:20 margin) + Supervisor | `goldbot/risk/` | done, tested |
| Broker protocol, paper broker (pessimistic fills), MT5 adapter (Windows), account classifier | `goldbot/execution/` | done; MT5 adapter untested off-Windows |
| TradingView webhook receiver (IP allow-list, secret, content-hash dedup, intrabar flag, latency) | `goldbot/webhook/app.py` | done, tested |

Built since Phase 0 (see `HANDOFF.md` for status): allocator, Telegram bot, dashboard (`web/`), live engine loop,
scheduler, news collector, staff agents, population tournament. Not yet built: TradingView ideas collector.
Operating the VPS: `docs/RUNBOOK.md`.

## Quick start (Mac)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q -m "not integration"  # unit tests (CI also runs -m integration)
python scripts/dry_run.py 3     # synthetic 3 years end-to-end (~1 min)
```

Real data:

```bash
bash scripts/download_dukascopy.sh 2010-01-01 2026-10-01          # free tick archive (hours)
python scripts/build_bars.py --source dukascopy raw/dukascopy/*.csv
# MT5 Mac app: View → Symbols → XAUUSD → Bars → export M1 CSV, then:
python scripts/build_bars.py --source icm --server-tz Europe/Athens raw/mt5_export/XAUUSD_M1.csv
```

Then run the session-open research path on real bars (see `scripts/dry_run.py` for the call shape).

## Design rules the code enforces

* Every join to bars goes through `asof_join` on `available_utc`; joining on a nominal date raises.
* A higher-timeframe bar is visible to a decision bar only when `visible_at <= ts_utc`.
* Labels charge the full round-trip spread; both barriers in one bar count as the stop.
* A live model may use at most 40 features; `MetaLabelModel.fit` refuses more.
* `RiskGate.check` is the only path to an order; the drawdown halt clears only via `rearm`.
* A clone must differ from its parent (`AgentIdentity.mutate` raises otherwise).
* Order ids are written before `order_send`; the paper broker rejects a repeated id.

## Layout

```
goldbot/
  data/ features/ labels/ specialists/ research/ risk/ execution/ webhook/
  allocator/ telegram/ engine/ agents/ api/ ops/
web/       React + TypeScript dashboard
scripts/   dry_run.py  build_bars.py  download_dukascopy.sh  research_pass.py  fetch_data_release.py ...
config/    settings.yaml  accounts.yaml
docs/      DESIGN.md  RUNBOOK.md
tests/
```
