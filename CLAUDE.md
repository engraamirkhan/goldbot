# goldbot — standing instructions for Claude sessions

Design doc (source of truth for every decision): https://claude.ai/code/artifact/9a4c5e58-38a6-4b7f-bc92-689e2d887f55
Owner: Aamir (engraamirkhan on GitHub). Personal project; keep everything inside this repo and `~/Personal project`.

## Non-negotiables
- Never ask the owner for passwords or tokens in chat. Credentials go into the OS keyring via `goldbot.ops.accounts` prompts.
- Demo accounts until the paper->tiny-live gate is recorded in `state/phase_state.json`; live needs `unlock_live` + typed phrase.
- Owner confirms entries (Telegram/dashboard, 90 s); everything after entry is automatic. Exits are never gated.
- RiskGate is the only path to an order. Max 40 features per live model. Every join to bars via `asof_join` on `available_utc`.
- Every change goes through CI (`.github/workflows/ci.yml`); failures are posted as GitHub issues labelled `ci` — read those, the log host is blocked from Claude sandboxes.

- The repo is PUBLIC: never commit or push secrets or account identifiers (passwords, tokens, MT5 login numbers,
  Telegram ids, hosts) — not in code, config, docs, commits or issue comments. MT5 logins and passwords live only in
  the VPS keyring (`python -m goldbot.ops.accounts add <account>`); `config/accounts.yaml` keeps `login: null`.

## Lifecycle agents (`.claude/agents/`)
Every piece of work runs through them: `architect` (plan against DESIGN/TRACEABILITY, read-only) -> `implementer`
(failing tests first) -> `test-engineer` (all gates below) -> reviews: `code-reviewer` always, `quant-reviewer` for
research/labels/features/costs/gates, `trading-safety-reviewer` for engine/risk/execution/approvals,
`security-reviewer` before every push -> `release-manager` (PR, CI, merge when green, progress on issue #34).
Trading improvement loop: `performance-analyst` (shadow/live trades by timeframe, family, session, costs) ->
`strategy-researcher` (pre-registered trials across 15m/1h/4h/1d within the quarter's budget) -> `evaluator`
(verdict against the pre-registered rule and the gates). Reviewers and evaluators are independent and read-only;
subagents do not spawn subagents. Operating contract: the owner confirms each entry with one click (90 s);
every exit is automatic.

## Environment facts
- Claude cloud sandbox and the Mac's Cowork VM cannot reach market-data hosts (Dukascopy, FRED) or download GitHub Actions logs; GitHub API and git push work from the Mac VM (token via device flow, stored only in the VM).
- Data pulls run as GitHub Actions (`data-dukascopy.yml`) and publish Parquet to release `data-v1`; `scripts/fetch_data_release.py` loads them.
- MetaTrader5 Python package is Windows-only: execution runs on the VPS (`goldbot/ops/vps_bootstrap.ps1`, services via NSSM, entry points in `goldbot/ops/run.py`).

## Dev loop
Python records/settings/API contracts are pydantic (`goldbot/base.py` Record/FrozenRecord, `goldbot/config.py` Settings,
`goldbot/api/schema.py`); everything is type-checked with mypy. Timestamps: use `goldbot.data.timeutil.epoch_ns`, never
`.asi8`/`.view("i8")` (pandas 3 keeps s/ms/us units).

All gates at once, in parallel (~90 s): `scripts/gates.sh --web` (pytest-xdist `-n auto` + every check side by side; logs in ~/tmp/gates).
Never run gates one after another, and never edit files while gates run.

Backend: `pip install -e ".[dev]" && pre-commit install`
- lint `ruff check goldbot tests scripts` · types `mypy` · unit `pytest -m "not integration"` · integration `pytest -m integration && python scripts/dry_run.py 1`

Frontend (`cd web && npm ci`):
- lint `npm run lint` · types `npm run typecheck` · unit `npm test` · build `npm run build`
- e2e `npm run test:e2e` (real API via scripts/e2e_server.py; in a Claude sandbox set
  `PW_CHROMIUM_PATH=/opt/pw-browsers/chromium-*/chrome-linux/chrome`)
- API contract: after changing goldbot/api, run `python scripts/export_openapi.py && npm --prefix web run gen:api` and commit both files

CI (`.github/workflows/ci.yml`) runs pre-commit, backend lint/typecheck/unit/integration, frontend lint/typecheck/unit,
api-contract and e2e as separate jobs; run the same commands locally before pushing.
