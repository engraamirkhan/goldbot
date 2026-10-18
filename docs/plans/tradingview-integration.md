# TradingView integration plan: reading the chart indicators (2026-10-18)

Goal (owner, 2026-10-18): goldbot reads every value the owner's TradingView indicators plot, including invite-only
ones, continuously, and uses them as features and specialist signals.
Sources: DESIGN "TradingView signals", "Aamir's indicator suite", "Account integrations", promotion table
("TradingView indicators: filter features only" until proven). TRACEABILITY F11 (partial), F12 (missing), X15-X17.

## The only legitimate ways to read them
1. **Live: one alert per indicator per timeframe, "Once per bar close"**, webhook body carrying every plot
   (`{{plot_0}}..{{plot_N}}` or `{{plot("Title")}}`, plus `{{time}}`, `{{interval}}`, `{{ticker}}`, `{{close}}`).
   Each bar becomes one row. The bar alert is its own heartbeat: a missing bar means NaN.
2. **History: the owner's "Export chart data" CSV** (visible plots, bars limited by plan).
No scraping, no automated TradingView login, no stored TradingView credentials.

**What invite-only authors can hide.** Only plotted series are readable; anything computed but not plotted is
unreachable. Authors can also: plot with `display.none` (may drop from the alert plot list or the CSV: check each
indicator once); draw with labels, lines or boxes (no plot value at all, so key-level bands drawn as lines cannot be
read); use `plotshape`/`plotchar` (value is 1 or NaN, readable as an event); offer only `alert()` with a fixed message
(no placeholders: we store the message text as an event, no plots). Per indicator, the first week records what is
actually readable.

## Gaps in today's code
- `goldbot/webhook/app.py:91` puts rows on a queue that nothing drains (no other reader of `app.state.queue`):
  alerts are lost. `:73` dedupe is an in-memory set, reset on restart. `:34` has no "bar" signal and no `plots`.
- `goldbot/ops/services/webhook.py:12` binds `0.0.0.0:8443`; no Linux unit exists in `goldbot/ops/linux/systemd/`.
- `goldbot/data/store.py:160` `asof_join` and `:62` `append` are reused unchanged except a `KEY_COLUMNS` entry.

## Stable HTTPS address (TradingView posts to ports 80/443 only, IPv4, 3 s timeout)
| Option | Owner does once | For | Against |
|---|---|---|---|
| **Tailscale Funnel (recommended)** | Approve Funnel in the Tailscale admin page (one click on the link the command prints) | Free, stable `https://<machine>.<tailnet>.ts.net/tv`, TLS automatic, no OCI ingress, no open port on the box, Tailscale already runs on the brain; `--set-path /tv` exposes only the webhook | TradingView's IP may not reach us (check `X-Forwarded-For` on the first alert); then the 256-bit secret alone authenticates |
| DuckDNS + Caddy | duckdns.org subdomain, token into keyring, OCI ingress TCP 443 | Real caller IP, so the TradingView IP list holds; Caddy enforces it | Opens 443 on the box, three moving parts (DNS updater, certs, firewall) |
The quick tunnel's random address is not usable. Reading taken: Funnel for phase 1 because it starts recording in a
day with nothing exposed but `/tv`; switch to DuckDNS + Caddy only if the security review requires the IP list.

## Owner steps (once)
1. TradingView plan with webhook alerts (Essential or higher; active alerts >= indicators x timeframes, e.g. 5 x 2 =
   10) and two-factor sign-in on (required for webhooks). Spending is the owner's decision.
2. On the brain, store the shared secret (prompted, hidden):
   `~/goldbot/.venv/bin/python -m goldbot.ops.accounts set tradingview-webhook-secret`
3. Claude runs `sudo tailscale funnel --bg --set-path /tv http://127.0.0.1:8443/tv`; the owner clicks the approval
   link it prints and gets the address privately (never in GitHub).
4. For each saved chart (XAUUSD, 15m and 1h first) and each indicator: Alert, condition = the indicator (or "Any
   alert() function call" if that is all it offers), **Once per bar close**, longest expiry, Webhook URL = the
   address, message (values quoted because TradingView writes `NaN`, which is not JSON):
   `{"indicator":"smc","version":"1.0","symbol":"{{ticker}}","tf":"{{interval}}","bar_time":"{{time}}","fired_at":"{{timenow}}","signal":"bar","close":"{{close}}","plots":{"0":"{{plot_0}}","1":"{{plot_1}}","2":"{{plot_2}}"},"secret":"<secret>"}`
   (list as many `plot_N` as the indicator has; the Data Window shows the count.)
5. CSV history per timeframe: indicators visible, scroll back to load everything, Export chart data, ISO time, name
   `<indicator>_<version>_<tf>_<yyyymmdd>.csv`, then on the Mac:
   `scp ~/Downloads/smc_1.0_15_20261018.csv goldbot-brain:~/goldbot/data/inbox/tv/`
6. In the dashboard, name each plot once (e.g. 0 = "ema50", 3 = "buy_large", kind level/oscillator/event/state).
7. After three weeks of live bars, export the CSVs again for the repaint check.

## Code
**Phase 1 (one PR, a day; recording starts right after owner step 4).**
- `goldbot/webhook/app.py:27-37` `Alert`: add `plots: dict[str, float | None] = {}` and `close: float | None`
  (validator: strings, `NaN`/empty -> None), signal adds `bar`; `payload` kept, so old bodies still parse.
  `:66` `create_app(secret, inbox: TvInbox, enforce_ip, trust_local_proxy)`: write before ack, 503 if the write fails.
- new `goldbot/webhook/inbox.py`: SQLite WAL `state/tv.db`; `tv_alerts` (raw, `UNIQUE(signal_hash)`) and `tv_bar_plots`
  long table (indicator, version, symbol, tf, bar_time, plot_key, value, received_utc, available_utc, origin, intrabar).
  `TvInbox.put(row) -> bool`, `TvInbox.series(indicator, tf, since) -> DataFrame`.
- `goldbot/ops/services/webhook.py:12`: 127.0.0.1, `proxy_headers=True, forwarded_allow_ips="127.0.0.1"`.
- new `goldbot/ops/linux/systemd/goldbot-webhook.service`; RUNBOOK section 2.7.
- `available_utc = received_utc` (bar close + measured latency); `latency_s` already logged (`app.py:59`).

**Phase 2.** `goldbot/data/tv_csv.py` `import_tv_csv(path, *, indicator, version, tf) -> DataFrame` (origin
`csv_backfill`, `file_sha256`, `repaint_status="unverified"`, `available_utc` = bar close + p95 live latency);
`goldbot/research/tv_repaint.py` `validate(live, csv, *, min_bars=200, agree=0.99) -> RepaintReport` (verified at
>= 99 % agreement over >= 200 overlapping non-intrabar bars; a version change resets); `tv_plot_map` table + API +
dashboard screen (ui-ux lane); `tv_delivery_stats` and a missing-bar alarm.

**Phase 3.** `goldbot/features/tradingview.py` (family `tradingview`, F12): each mapped plot as level distance in ATR,
oscillator value, event bars-since, state one-hot; NaN on a missing bar; 1 % seeded blanking; intrabar and unverified
CSV rows excluded (F11); counted in the 40-feature cap. A shadow specialist per event plot records paper outcomes for
the performance-analyst; promotion beyond filter only by the DESIGN rule.

## Non-negotiables
Nothing here imports the engine, RiskGate or broker; exits untouched. Joins only via `asof_join` on `available_utc`;
integer times via `timeutil.epoch_ns`. Demo. Secret in keyring, addresses never in the repo. **Invite-only outputs are
the author's work: CSVs and tables stay in gitignored `/data/` and `/state/`, never in a public release.**

## Tests first
Phase 1 `tests/test_tv_webhook.py`: `test_bar_alert_with_plots_is_stored_before_the_ack`,
`test_old_body_without_plots_still_accepted`, `test_quoted_nan_and_empty_plots_become_none`,
`test_duplicate_after_restart_is_recognised`, `test_inbox_write_failure_returns_503`, `test_bad_secret_writes_nothing`,
`test_forwarded_ip_trusted_only_from_localhost`, `test_available_utc_is_received_time`.
Phase 2: `tests/test_tv_csv.py::test_asof_join_sees_a_plot_at_available_utc_not_one_ns_before`,
`::test_reimport_is_idempotent`, `::test_missing_time_column_fails_closed`;
`tests/test_tv_repaint.py::test_99_percent_agreement_verifies`, `::test_below_99_marks_repaints`,
`::test_199_bars_stays_unverified`, `::test_version_change_resets`.
Phase 3: `tests/test_tv_features.py::test_missing_bar_gives_nan_not_stale`, `::test_unverified_csv_never_trains`,
`::test_tv_features_count_toward_40`; `tests/test_tv_shadow.py::test_tv_signal_never_reaches_the_order_path`.

## Risks
1. Repainting: CSV history can differ from live; unverified rows never train.
2. Chart feed is OANDA, not the broker: align on `bar_time`; filters only.
3. Hidden or drawn-only values (key-level lines) cannot be read; an alert expiry or revoked access shows as missing bars.
4. Licensing of invite-only outputs (see above). 5. DESIGN's ideas scraper conflicts with the no-scraping rule: an ADR
   should drop it and the unused `tradingview-ideas-cookie` (`config/accounts.yaml:65`).

Docs: HANDOFF fragment; TRACEABILITY F11, F12, X15 (Funnel peer IP), new rows for plot series, CSV backfill, repaint
check ("Reading taken" lines above); RUNBOOK 2.7 and secrets table (lines 766-768, 841-842).
