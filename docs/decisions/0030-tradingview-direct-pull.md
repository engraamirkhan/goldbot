# 0030: TradingView direct pull with the owner's session cookie

Status: accepted (owner decision 2026-10-18, terms and account risk accepted by the owner; recorded by the implementer)

## Context
docs/plans/tradingview-integration.md read the owner's indicators only through alerts and chart-export CSVs, with "no
scraping, no automated TradingView login, no stored TradingView credentials". On 2026-10-18 the owner chose both
alerts and an unofficial direct pull, after being told it breaks TradingView's terms and risks suspension and the loss
of invite-only access: "once we connect to tradingview account, you should be able to pull and read any data from it
the system needs, you won't need me". The program then asked for owner input "negligible to none": the pull becomes
the primary TradingView source, with discovery of the saved layouts and automatic plot naming.

## Decision
- `goldbot/data/tv_pull.py` reads the saved layouts, their Pine studies and inputs, each script's metadata and the
  bars and plot values from TradingView's chart websocket, into `tv_indicators` (`inbox.long_rows` schema plus `origin`
  `pull_live`/`pull_history`), study metadata in `state/tv.db` `tv_study_meta` keyed by `meta_hash8`.
- Credentials: the browser cookies `sessionid` and `sessionid_sign`, each in the keyring (`tradingview-sessionid`,
  `tradingview-sessionid-sign`), read per pass and held in memory only; no automated login, no password stored.
- Read-only by construction (goldbot/data/tv_wire.py): the network interface has a GET and a websocket only; GETs are
  allowed to five read endpoints and websocket frames to the methods a chart tab sends to draw itself.
- Polite: one request per `min_interval_s` (5 s), capped history pages, polling only when a new bar can have closed, a
  rolling daily request budget, exponential backoff, the full 6 h backoff after a refused cookie.
- Fail soft: a pass never raises; the alert webhook does not import the pull. Sign-in state goes through
  goldbot/ops/tv_pull_health.py (`record_ok`, `record_expired`; one WARN and one owner notice per expiry).
- Off by default (`tv_pull.enabled: false`); the owner's machine enables it in settings.local.yaml.
- This supersedes the plan's "no stored TradingView credentials" line for the pull only. The unused
  `tradingview-ideas-cookie` key in config/accounts.yaml stays unused (the ideas scraper is not built).

## Consequences
- The account can be suspended and invite-only access lost at TradingView's discretion; the alerts path (and the CSV
  import) stays as the fallback, so losing the pull loses convenience, not the system.
- The protocol is undocumented and can change without notice. The test fixtures (tests/data/tv_pull) are hand-built
  from the community-documented protocol, not captured traffic; real sanitised frames from the local probe
  (tests/fixtures/tv/) should replace them, and the first live pass is the first real test of the endpoints.
- Invite-only outputs are the author's work: the compiled script source is never stored, and the pulled rows stay in
  gitignored `data/` and `state/`, never in a public release.
- Backfilled rows are history (`pull_history`, available at close + p95 live latency) and carry repaint risk like
  a chart export; consumers must treat them as unvalidated until compared with live receipts.
