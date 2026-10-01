# goldbot — standing instructions for Claude sessions

Design doc (source of truth for every decision): https://claude.ai/code/artifact/9a4c5e58-38a6-4b7f-bc92-689e2d887f55
Owner: Aamir (engraamirkhan on GitHub). Personal project; keep everything inside this repo and `~/Personal project`.

## Non-negotiables
- Never ask the owner for passwords or tokens in chat. Credentials go into the OS keyring via `goldbot.ops.accounts` prompts.
- Demo accounts until the paper->tiny-live gate is recorded in `state/phase_state.json`; live needs `unlock_live` + typed phrase.
- Owner confirms entries (Telegram/dashboard, 90 s); everything after entry is automatic. Exits are never gated.
- RiskGate is the only path to an order. Max 40 features per live model. Every join to bars via `asof_join` on `available_utc`.
- Every change goes through CI (`.github/workflows/ci.yml`); failures are posted as GitHub issues labelled `ci` — read those, the log host is blocked from Claude sandboxes.

## Environment facts
- Claude cloud sandbox and the Mac's Cowork VM cannot reach market-data hosts (Dukascopy, FRED) or download GitHub Actions logs; GitHub API and git push work from the Mac VM (token via device flow, stored only in the VM).
- Data pulls run as GitHub Actions (`data-dukascopy.yml`) and publish Parquet to release `data-v1`; `scripts/fetch_data_release.py` loads them.
- MetaTrader5 Python package is Windows-only: execution runs on the VPS (`goldbot/ops/vps_bootstrap.ps1`, services via NSSM, entry points in `goldbot/ops/run.py`).

## Dev loop
`pip install -e ".[dev]"` · `ruff check goldbot tests scripts --select E,F,W --ignore E501` · `pytest -q` · `python scripts/dry_run.py 1`
Web: `cd web && npm install && npm run build` (served by the API from `web/dist`).
