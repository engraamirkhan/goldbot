# goldbot runbook (owner's guide)

This is the step-by-step guide for running goldbot on the Windows VPS. It is written for the owner, not for a
developer. Every command, service name, file and setting below exists in this repository; where a step depends
on something outside the repository (Windows, Cloudflare, Telegram, Node.js) the guide says so.

Conventions used below:

* All commands are typed in **PowerShell on the VPS**, in the folder `C:\goldbot` (the bootstrap script puts the
  repository there). Start every session with `cd C:\goldbot`.
* goldbot's Python lives in `C:\goldbot\.venv`, so commands are written as `.\.venv\Scripts\python -m ...`.
* Anything in angle brackets, such as `<tunnel token>`, is a placeholder for your own value. Never paste a real
  password, token or key into a chat, an issue or a file in the repository. goldbot asks for secrets itself and
  keeps them in Windows Credential Manager (the "keyring").
* All times are **UTC**.
* "State folder" means `C:\goldbot\state`. "Logs folder" means `C:\goldbot\logs`.

---

## 1. What runs where

### On the VPS: eight Windows services

The bootstrap script (`goldbot/ops/vps_bootstrap.ps1`) registers these services with NSSM. Each one runs
`C:\goldbot\.venv\Scripts\python.exe -m goldbot.ops.run <command>` (entry points in `goldbot/ops/run.py`), restarts
itself 5 seconds after a crash, starts automatically with Windows, and writes its output to
`C:\goldbot\logs\<service>.log` and errors to `C:\goldbot\logs\<service>.err` (rotated at 10 MB).

| Service | Command | What it does |
| --- | --- | --- |
| `goldbot-supervisor` | `supervisor` | Every 5 s adds up both engines' equity, enforces the combined loss caps (1.5% daily, 4% weekly, 12% drawdown) and writes its heartbeat `state\supervisor.json`. Engines refuse new entries if this heartbeat is missing or older than 60 s. |
| `goldbot-engine-icm` | `engine icm-demo` | Trading engine for the IC Markets demo account (terminal `C:\MT5\ICMarkets`). Proposes entries, manages open trades, runs the shadow book (IC Markets is the canonical-cost broker). |
| `goldbot-engine-vantage` | `engine vantage-demo` | Trading engine for the Vantage demo account (terminal `C:\MT5\Vantage`). |
| `goldbot-api` | `api` | The dashboard and its API on `http://localhost:8787` (only reachable from outside through the Cloudflare tunnel). |
| `goldbot-webhook` | `webhook` | TradingView alert receiver on port 8443. |
| `goldbot-scheduler` | `scheduler` | Runs the timed jobs below (costs, retraining, tournament, staff agents, calendar). |
| `goldbot-telegram` | `telegram` | The Telegram bot: sends proposals with Approve/Reject buttons, posts outcomes and staff-agent reports, answers `/status` and `/halt`. |
| `goldbot-news` | `news` | Polls the RSS feeds in `config/settings.yaml` (`news.feeds`) every 300 s, scores gold-relevant headlines, feeds the news-shock blackout. |

The bootstrap script also installs Tailscale and `cloudflared`. goldbot itself only uses `cloudflared` (section 2.7);
Tailscale is optional.

### On GitHub (Actions)

| Workflow | When | What it does | Where results appear |
| --- | --- | --- | --- |
| `data-dukascopy.yml` | Saturdays 03:17 UTC (current year), or by hand | Downloads Dukascopy ticks, builds 1m bars, publishes Parquet files to the release `data-v1`. | Issues `data coverage <year>` (label `data-coverage`); failures as `data-dukascopy failure for <year>` (labels `ci`, `data`). |
| `research.yml` | By hand only (Actions -> research -> Run workflow; inputs `specialist`, `from_year`, `to_year`, `rationale`) | Walk-forward research for one specialist family (`session_open`, `mean_reversion`, `trend`, `breakout`) on the `data-v1` bars; keeps the trial registry on release `research-v1`. | Issue `research: <specialist>` (label `research`). |
| `ci.yml` | Every push, pull request, or by hand | Pre-commit, backend lint/types/unit/integration, frontend lint/types/unit, API contract, browser end-to-end tests. | On a failed push, one issue `CI failure on <commit> (<jobs>)` labelled `ci`, with each failed job's output. |

The VPS does not depend on GitHub to trade. It uses GitHub only to refresh bars (Saturday retrain) and to share
the research trial registry, both through the `github-token` secret (section 2.3).

### Schedule (from `config/settings.yaml`, section `scheduler`)

Weekdays are Mon-Fri. A job missed while the VPS was down is run once on restart if it is not older than its
`max_late_hours`; otherwise it is skipped and the skip is recorded.

| Job | When (UTC) | What it does |
| --- | --- | --- |
| `calendar_archive` | Daily 06:10 | Stores the Forex Factory week in the `calendar_events` table (feeds the news blackout). |
| `agents_presession` | Mon-Fri 06:30 | Macro and news analyst writes the pre-session briefing. |
| `nightly_costs` | Mon-Fri 23:10 | Builds each account's spread/slippage cost table `state\costs_<account>.json`, with the swap and commission the terminal reports (`state\broker_terms_<account>.json`, written by the engine every 6 hours); on Fridays also re-runs the account classifier (`state\classifier_<account>.json`). Until an account has been classified (the first Friday with 1,000+ London/New York ticks logged) its engine opens no positions and the health check warns "account class unknown"; on a Standard account the 15m families except session-open are off. |
| `model_watch` | Daily 23:30 | CUSUM check on any newly promoted champion; restores the previous champion on an alarm. |
| `agents_daily` | Mon-Fri 23:45 | Data steward, risk officer, execution auditor. |
| `saturday_retrain` | Saturday 06:00 | Refreshes bars from `data-v1`, decides waiting challengers (promote/retire), retrains new challengers. |
| `recalibrate` | Saturday 11:30 | Refits only the probability map of each champion and challenger on its recent shadow outcomes (every candidate, taken or not), bounded to +-0.05 per week; logged in `state\recalibration.jsonl`. Promotes nothing. |
| `tournament` | Saturday 12:00 | Population round: fitness, retirement, promotion to live, cloning, capital shares -> `state\agents.json`. |
| `agents_weekly` | Saturday 13:00 | Journal coach, improvement agent, research analyst. |
| `monthly_research` | First Sunday of the month 08:00 | Bounded label-grid research; summary in `state\research_<YYYY-MM>.md`. |

The staff-agent jobs and headline scoring only run when the `anthropic-api-key` secret is stored; otherwise the
job records "skipped" and headlines are collected unscored. Their total spend is capped by
`agents.monthly_cap_usd` and `news.daily_cap_usd` in `config/settings.yaml`.

---

## 2. First-time VPS setup (do these in order)

### 2.1 Run the bootstrap script

1. Open PowerShell **as Administrator**.
2. Download and run `goldbot/ops/vps_bootstrap.ps1` from the repository (for example open it on GitHub, copy it
   into a file `vps_bootstrap.ps1` on the VPS, then run `powershell -ExecutionPolicy Bypass -File .\vps_bootstrap.ps1`).
3. It installs Python 3.11, Git, NSSM, Tailscale and `cloudflared`; clones the repository to `C:\goldbot`; creates
   `C:\goldbot\.venv` and installs goldbot with the `live` extras (MetaTrader5, keyring, python-telegram-bot);
   installs two MT5 terminals into `C:\MT5\ICMarkets` and `C:\MT5\Vantage`; sets time sync to
   `time.cloudflare.com`; and registers the eight services above. **It does not start them.**

### 2.2 Log in to the two MT5 demo terminals

1. Start `C:\MT5\ICMarkets\terminal64.exe`, open your IC Markets **demo** account (File -> Login to Trade
   Account), tick "save password", and confirm `XAUUSD` is in Market Watch.
2. Do the same with `C:\MT5\Vantage\terminal64.exe` and your Vantage **demo** account. Vantage may call the symbol
   `XAUUSD+`; the engine resolves it.
3. Check the server names against `config/accounts.yaml`: `icm-demo` expects `ICMarketsSC-Demo`, `vantage-demo`
   expects `VantageInternational-Demo`. If your terminal shows a different server name, change the `server:` line
   of that account in `config/accounts.yaml`.
4. Allow algorithmic trading in each terminal (Tools -> Options -> Expert Advisors).

### 2.3 Store the secrets (keyring)

goldbot never reads secrets from files in the repository. You type each one once into a hidden prompt and it
goes into Windows Credential Manager under the service name `goldbot`. The engine services cannot ask you for a
missing secret (they run in the background with no window), so store everything **before** starting services.

MT5 demo accounts (asks for the login number, which it writes into `config/accounts.yaml`, then the password):

```powershell
cd C:\goldbot
.\.venv\Scripts\python -m goldbot.ops.accounts add icm-demo
.\.venv\Scripts\python -m goldbot.ops.accounts add vantage-demo
.\.venv\Scripts\python -m goldbot.ops.accounts list
```

`list` should show `password_stored=yes` and a login number for both demo accounts. (The passwords are stored as
keys `mt5-icm-demo` and `mt5-vantage-demo`.)

Other secrets: each command asks `value for <key>` and hides what you type.

| Key | Command | Used by | Without it |
| --- | --- | --- | --- |
| `telegram-bot-token` | `.\.venv\Scripts\python -m goldbot.ops.accounts set telegram-bot-token` | `goldbot-telegram` | The Telegram service stops at start with "no telegram-bot-token in the keyring". |
| `anthropic-api-key` | `.\.venv\Scripts\python -m goldbot.ops.accounts set anthropic-api-key` | `goldbot-scheduler` (staff agents), `goldbot-news` (headline scoring) | Staff agents are off; headlines are kept but not scored, so there is no news-shock blackout (calendar blackouts still work). |
| `github-token` | `.\.venv\Scripts\python -m goldbot.ops.accounts set github-token` | `goldbot-scheduler` (bar refresh from `data-v1`, shared trial registry on `research-v1`) | The trial registry stays local to the VPS. Use a GitHub token for `engraamirkhan/goldbot` that can read and write release assets (Contents: read and write). |
| `tradingview-webhook-secret` | `.\.venv\Scripts\python -m goldbot.ops.accounts set tradingview-webhook-secret` | `goldbot-webhook` | The webhook service cannot start (it needs this value and has no window to ask in). Choose a long random string and use the same one in your TradingView alerts. |

`config/accounts.yaml` also names a key `tradingview-ideas-cookie`; no running code reads it yet, so skip it.

> **Check before starting services: which Windows user the services run as.** Windows Credential Manager is per
> Windows user. The bootstrap script registers the services with NSSM's default account and does not set one. If
> the services run as a different user from the one that stored the secrets above, they will not find them
> (symptoms: `goldbot-telegram` says "no telegram-bot-token in the keyring", engines crash asking for a
> credential). The fix is outside this repository: open each service with `nssm edit <service>` and, on the
> "Log on" tab, set it to the Windows account you used for the commands above. This step is not automated or
> tested by goldbot.

### 2.4 Edit `config/settings.yaml`

Open `C:\goldbot\config\settings.yaml` in Notepad. The file is checked strictly: a misspelt key stops every
service at start, so copy the key names exactly.

1. **Telegram allow-list.** There is no `telegram:` section in the file yet. Add one at the end (numbers only,
   no quotes; the second id is optional, for a backup phone account):

   ```yaml
   telegram:
     allowed_user_ids: [<your numeric Telegram user id>]
   ```

   The engines and the Telegram bot read this list; `config/accounts.yaml` also has an `allowed_user_ids` line,
   but nothing reads that one. To find your numeric id see section 2.8.
2. **News feeds.** `news.feeds` lists four RSS feeds (`forexlive`, `fxstreet`, `fed_press`, `bls`). They could not be
   tested from a development sandbox. Leave them for now; section 4 tells you how to check them after the first
   hour and replace any that fail (`<name>: <url>` lines under `feeds:`).
3. Leave `agents.monthly_cap_usd` (40) and `news.daily_cap_usd` (0.50) unless you want a different spend cap.

Note: the `risk:` section of `settings.yaml` is **not** passed to the engines today; the RiskGate uses its
built-in defaults in `goldbot/risk/gate.py` (currently the same numbers). Changing `risk:` values has no effect on
trading until that is wired in through a code change.

### 2.5 Load the bar history (recommended)

The engines warm-start from the 1m bars in `C:\goldbot\data`; without them a 1h agent needs weeks of live ticks
before it can decide. The Saturday retrain refreshes them, but load them once now:

```powershell
.\.venv\Scripts\python scripts\fetch_data_release.py --years 2024 2026
```

If the repository is private this needs a GitHub token. The script has a `--token` option, but do not type a
token on the command line (it stays in the PowerShell history); skip this step instead and let the first Saturday
retrain load the bars with the stored `github-token`.

### 2.6 Build the dashboard

The API serves the dashboard from `C:\goldbot\web\dist` (not in the repository). `vps_bootstrap.ps1` installs
Node.js 22 (as CI) and builds it; `vps_update.ps1` rebuilds it when `web/` changes. To build by hand:

```powershell
cd C:\goldbot\web
npm ci
npm run build
cd C:\goldbot
```

### 2.7 Cloudflare tunnel (dashboard from your phone, no VPN)

1. In the Cloudflare Zero Trust dashboard create a tunnel and a public hostname for it; route the hostname to
   `http://localhost:8787`.
2. On the VPS, in an Administrator PowerShell: `cloudflared service install <tunnel token>`. The token is a
   secret: type it on the VPS only.
3. The dashboard enforces its own login (email, password, authenticator code) and roles on the public URL.

The tunnel only carries the dashboard. TradingView alerts go to the webhook service on port 8443, which the
bootstrap script does not expose; set that up only when you start using TradingView alerts.

### 2.8 Telegram bot with @BotFather

1. In Telegram, open **@BotFather**, send `/newbot`, choose a name and a username. BotFather replies with the bot
   token. Store it with `.\.venv\Scripts\python -m goldbot.ops.accounts set telegram-bot-token` (section 2.3) and
   delete BotFather's message.
2. Open a chat with your new bot and press **Start**. A bot cannot message you until you have done this.
3. Your numeric user id: either use any "user info" Telegram bot (outside goldbot), or use goldbot itself: put a
   placeholder id such as `[1]` in `telegram.allowed_user_ids`, start the service
   (`nssm start goldbot-telegram`), send `/status` to your bot, then open
   `C:\goldbot\logs\goldbot-telegram.err` and find the line `command from non-owner <number> ignored`. That
   number is your id. Put it in `settings.yaml` in place of the placeholder and run
   `nssm restart goldbot-telegram`.

### 2.9 Start the services

Start the supervisor first (engines block entries until its heartbeat exists), then the rest. In an
Administrator PowerShell:

```powershell
nssm start goldbot-supervisor
nssm start goldbot-api
nssm start goldbot-telegram
nssm start goldbot-news
nssm start goldbot-scheduler
nssm start goldbot-engine-icm
nssm start goldbot-engine-vantage
nssm start goldbot-webhook
```

Skip `goldbot-webhook` until you have stored `tradingview-webhook-secret`. `nssm status <service>` shows whether
a service is running; if one keeps restarting, read its `.err` file in the logs folder.

### 2.10 Dashboard first run: create the owner account

1. While no user exists, the API prints a one-time setup code at every start. Open
   `C:\goldbot\logs\goldbot-api.log` and find the latest line
   `goldbot first-run: open the dashboard and create the owner account with setup code <code>`.
   The code changes every time the API restarts, so always use the newest line.
2. Open your tunnel hostname in a browser. The login page offers the setup form: enter the **Setup code**, your
   email and a password, then **Create owner account**.
3. The page shows an authenticator enrolment (an `otpauth://` link/QR). Add it to your authenticator app; every
   later sign-in, and every re-arm, needs the 6-digit code from it.
4. Other people: **Users** tab (owner only) -> email + role (`viewer`, `approver` or `owner`) -> **Create invite
   link**, and send them the link. Approvers can approve, reject and halt; only the owner can re-arm and manage
   users.

---

## 3. Daily operation

### Approving or rejecting an entry

An engine only proposes an entry; nothing is opened without your approval. Each proposal shows account,
direction, lots, entry, stop, target, probability and spread, and must be decided **within 90 seconds** or it
expires (`EXPIRED_UNAPPROVED`).

* **Telegram:** tap **Approve**, or one of **Reject: news / cost / discretion / duplicate / other**. The bot
  replies "sent to the engine", then posts the outcome (APPROVED / REJECTED / EXPIRED).
* **Dashboard:** **Approvals** tab, same buttons.
* The first decision wins (Telegram or dashboard). After an approval the engine re-runs the RiskGate at the
  current price; if a limit is hit at that moment the order is still refused.
* The reject reason matters: it is how the journal coach measures whether your vetoes add value.

Everything after entry is automatic: the stop and target are attached at the broker, time exits and
reconciliation run without asking. **Exits are never gated by approval or by a halt.**

### Halt and re-arm

* **Halt** stops new entries on every engine. Open positions keep their stops and exits.
  * Telegram: `/halt` (optionally `/halt <reason>`), or on the dashboard: **Overview** -> **Halt new entries**
    (any approver).
  * It is recorded in `state\control.json`.
* **Re-arm** (resume entries) is only possible on the dashboard, by the owner: **Overview** -> enter the 6-digit
  authenticator code -> **Re-arm**. Telegram `/rearm` only tells you to use the dashboard. The same re-arm also
  clears an engine's 12% drawdown halt (the kill switch, which closes every position at market when it trips); each
  engine applies it once, and a restart does not clear the halt.
* `/status` on Telegram shows whether entries are halted and how many proposals are pending.

### Staff-agent reports

When `anthropic-api-key` is stored, the scheduler runs language-model staff agents. They read data only; none can
place, change or close a trade.

| Agent | When | Report about |
| --- | --- | --- |
| data steward | Mon-Fri 23:45 | data-quality events, scheduler job states |
| risk officer | Mon-Fri 23:45 | limits tripped, supervisor state, divergence from backtest |
| execution auditor | Mon-Fri 23:45 | slippage against the cost table, widened spreads, failed orders |
| macro and news analyst | Mon-Fri 06:30 | pre-session briefing from the calendar and headlines |
| journal coach | Saturday 13:00 | weekly trade review, value of your vetoes |
| improvement agent | Saturday 13:00 | weak spots; files hypotheses to `state\hypotheses.jsonl` |
| research analyst | Saturday 13:00 | runs up to two bounded trials on filed hypotheses and gives a verdict (promotes nothing) |

Each report arrives on Telegram, appears on the dashboard **Agents** tab, and is saved as
`state\agent_reports\<role>\<UTC time>.md`. Every run (cost, status) is logged in `state\agent_runs.jsonl`.

### Where things live

| What | Where |
| --- | --- |
| Engine status (equity, drawdown stage, spread, blackout, pending) | `state\engine_icm-demo.json`, `state\engine_vantage-demo.json` |
| Supervisor heartbeat and combined limits | `state\supervisor.json` |
| Owner halt | `state\control.json` |
| Proposals and decisions | `state\approvals\pending\`, `state\approvals\decisions\`, `state\approvals\done\` |
| Cost tables (per account, nightly) | `state\costs_<account>.json` |
| Swap and commission read from the terminal | `state\broker_terms_<account>.json` (notes say why a value fell back to settings) |
| Account classifier | `state\classifier_<account>.json` |
| Scheduler job states | `state\scheduler.json` (also on the **Feeds** tab) |
| News feed health | `state\news_feeds.json` |
| Agent spend | `state\agent_spend.json` (monthly), `state\news_spend.json` (headline scoring per day) |
| Population / league table | `state\population.json`, `state\agents.json` |
| Shadow records | `state\shadow_<model version>.json`; every candidate with its decision in `state\shadow_book.json` |
| Weekly recalibrations (before/after ECE) | `state\recalibration.jsonl`; also `recalibrations` in `models\registry.json` |
| Trained models and champions | `C:\goldbot\models\registry.json` |
| Trial registry | `state\research_registry.jsonl` |
| Monthly research summary | `state\research_<YYYY-MM>.md` |
| Dashboard users and audit log | `state\users.json`, `state\audit.jsonl` |
| Market data, ticks, fills, decisions journal, news, calendar | `C:\goldbot\data` (Parquet) |
| Phase gate | `state\phase_state.json` (section 5) |
| Service logs | `C:\goldbot\logs\<service>.log` / `.err` |

Back up the state folder and `config\` regularly; they are not in Git.

---

## 4. Checks after the first hour and the first day

**After the first hour**

1. **News feeds:** open `state\news_feeds.json`. Each feed shows `"ok": true` with an item count, or
   `"ok": false` with the error. Replace any failing URL in `config/settings.yaml` (`news.feeds`) and run
   `nssm restart goldbot-news`. A broken feed is skipped, never fatal.
2. **Feeds tab** on the dashboard, top table: each account should show terminal "connected", last tick under
   20 s, and a supervisor heartbeat under 60 s. A red row means one of these failed.
3. **Feeds tab**, "Scheduled jobs": every job should be listed with a "Next" time. "not run yet" is normal on
   day one (a new job waits for its first slot instead of firing at start). "scheduler silent" means
   `goldbot-scheduler` has not written its state for over 5 minutes: check `logs\goldbot-scheduler.err`.
4. **Telegram:** send `/status` to the bot; it should answer within seconds.
5. `.\.venv\Scripts\python -m goldbot.ops.accounts list`: demo accounts enabled, logins filled, passwords stored.

**After the first day (after 23:10 UTC on a weekday)**

1. **Cost tables:** `state\costs_icm-demo.json` and `state\costs_vantage-demo.json` exist. Until 50 real fills
   exist, slippage uses the prior from `costs.slippage_prior_usd`. Each table should carry `swap_long_usd_per_lot`
   and `swap_short_usd_per_lot` (the health check warns "no measured swap" otherwise; read the `notes` in
   `state\broker_terms_<account>.json` for the reason, e.g. a swap mode the conversion does not support). The
   commission becomes `"commission_measured": true` after the first closed demo position.
2. **Export the measured costs for research** (once the swap is measured, and again whenever the broker changes its
   swap, at least before each quarter's first research trial). On the VPS, in `C:\goldbot`:

   ```powershell
   .\.venv\Scripts\python -m goldbot.ops.run export-costs --out config\costs_measured.json
   git add config\costs_measured.json
   git commit -m "costs: measured IC Markets cost table"
   git push
   ```

   The command prints the swap and commission it exported and warns if the swap is still the prior. The research
   workflow (`research.yml`) passes `--cost-table config/costs_measured.json` whenever that file exists on the
   branch it runs from, so the trial's net results use the broker's measured swap, commission and slippage instead
   of the settings priors (the report's "cost source" line says which were used). If the VPS cannot push, copy the
   file to your computer and commit it there, or paste its contents into a new file on GitHub.
3. **Scheduled jobs:** `nightly_costs`, `model_watch` and `agents_daily` show "ok"; `calendar_archive` shows
   "ok" after 06:10. A "failed" job shows its first error line in "Last error".
4. **Supervisor heartbeat:** `state\supervisor.json` has a current `ts`, `"halt": false` and an empty
   `stale_engines` list.
5. **Agent reports** arrived on Telegram and the **Agents** tab (only with `anthropic-api-key`).

---

## 5. Going live (demo -> tiny-live)

Live trading is locked in two independent places, and **both** must agree:

1. **The phase gate file** `C:\goldbot\state\phase_state.json` must list the gate `paper_to_tiny_live` in
   `gates_passed`. Without the file goldbot assumes `{"phase": 0, "gates_passed": []}`. No code writes this
   file: you record the gate yourself, and only when the paper-to-tiny-live gate in the design's roadmap has
   actually been met (the evidence comes from the paper record, the shadow book and the journal). Do not create
   it to "try live". The only key the code reads is `gates_passed`. The recorded form is:

   ```json
   {"phase": 3, "gates_passed": ["paper_to_tiny_live"]}
   ```

   (`phase` is informational; the code does not read it.)
2. **The account switch**, which only the typed phrase can turn on:

   ```powershell
   .\.venv\Scripts\python -m goldbot.ops.accounts add icm-live
   .\.venv\Scripts\python -m goldbot.ops.accounts unlock-live icm-live
   ```

   `unlock-live` asks you to type exactly `ENABLE LIVE icm-live`. It refuses if the gate is not in
   `phase_state.json` ("refused: the paper -> tiny-live gate has not passed") or if the phrase differs in any
   character ("refused: confirmation phrase mismatch"). On success it sets `enabled: true` for that account in
   `config/accounts.yaml`. The same applies to `vantage-live` with `ENABLE LIVE vantage-live`.

Even if someone edits `enabled: true` by hand, goldbot will not return a live account while the gate file does
not list `paper_to_tiny_live`, and a live engine refuses to start ("live account not unlocked by the phase gate").

**What the current code does not do yet for live** (each needs a code change through CI before the first live
trade, not a manual workaround):

* The bootstrap script installs and registers only the two demo terminals and engines. There is no live
  terminal at `C:\MT5\ICMarkets-Live` / `C:\MT5\Vantage-Live` (the paths in `config/accounts.yaml`) and no
  `goldbot-engine-*` service for `icm-live` / `vantage-live`.
* `risk.risk_per_trade_tiny_live` (0.1%) in `settings.yaml` is not applied: engines size every trade with the
  RiskGate default of 0.5%.
* The daily and weekly loss caps (per account and the supervisor's combined ones) count from the equity when
  the engine started; they are not reset at the 00:00 UTC risk day or at the start of the week.

---

## 6. Troubleshooting

### Engines are running but block every entry

The engine journals every blocked candidate in the `decisions` table as `gate:<reasons>`; the risk officer's
nightly report explains them. The reasons (from `goldbot/risk/gate.py`) and what to do:

| Reason | Meaning | What to do |
| --- | --- | --- |
| `owner_halt` | You or an approver halted (`state\control.json`). An unreadable `control.json` also counts as halted. | Re-arm on the dashboard (owner + authenticator code). |
| `supervisor_halt` | The supervisor halted on combined limits (`combined_daily_cap`, `combined_weekly_cap`, `combined_drawdown_halt` in `state\supervisor.json`), **or** its heartbeat is missing/older than 60 s, or `supervisor.json` is unreadable. | Check `nssm status goldbot-supervisor` and the Feeds tab heartbeat; restart it if stopped. For a cap halt see the `daily_cap` row. |
| `drawdown_halt` | This account's equity fell 12% below its high-water mark. | Investigate before anything else. The dashboard Re-arm clears only the owner halt; the code has no command that clears this stage (an engine restart resets it, because the stage is kept in memory). |
| `news_blackout` | Within 15 min before to 30 min after a tier-1 event (CPI, NFP, FOMC, PCE), or 30 min after a high-relevance news shock. The active event is in `state\engine_<account>.json` under `blackout`. | Wait. If it never clears, check `calendar_archive` on the Feeds tab. |
| `stale_data` | The last tick is older than 20 s. | Check the MT5 terminal is open, logged in and connected; the Feeds tab shows "Last tick". |
| `spread_too_wide` | Spread above 45 points ($0.45). | Usually rollover or news; wait. |
| `max_positions` | Two positions already open on this account. | Normal. |
| `daily_cap` / `weekly_cap` | This account lost 2% / 5% measured from its equity when the engine started. | The engine does not yet reset its day/week starting equity at 00:00 UTC (`new_day` in `goldbot/risk/gate.py` is not called), so these clear only when the engine restarts. Review the losses before restarting. |
| `negative_ev`, `target_below_cost_floor` | After costs the trade has no edge, or the target is below 2.5x the round-trip cost. | Normal filtering; check the cost table if it happens to every candidate. |
| `min_lot_exceeds_risk`, `margin_level_floor` | Minimum lot would risk too much, or margin level would fall below 300%. | Account too small for the stop; normal on small demos. |
| `data_quality_error` | Reserved; the engine does not set it today. | - |

### Telegram is silent

1. `nssm status goldbot-telegram`; read `logs\goldbot-telegram.err`.
2. "no telegram-bot-token in the keyring" -> store it (section 2.3), and check the services' Windows user (the
   box in section 2.3).
3. "settings.yaml telegram.allowed_user_ids is empty" -> section 2.4.
4. "command from non-owner <id> ignored" -> the id in `allowed_user_ids` is not yours.
5. You never pressed **Start** in the bot chat (section 2.8).
6. Reports only: staff agents need `anthropic-api-key`; the first start does not replay old reports.

### No proposals at all

Usually there is nothing wrong. An engine proposes an entry for an agent only when **all** of these hold:

1. The agent has a **champion** model in `models\registry.json`. Champions appear only through the gates: the
   Saturday retrain makes a challenger, it shadow-trades for at least 4 weeks **and** 40 trades, and is promoted
   only if shadow Sharpe > 0.8 and within 0.5 of backtest, hit rate within one standard error, drawdown under
   1.5x backtest (`goldbot/research/promotion.py`). With no champion the engine never proposes for that agent.
2. The agent is **live** in the population (the Saturday `tournament` promotes from shadow to live only after
   at least 60 shadow trades and a deflated-Sharpe gate). See the **Agents** tab.
3. A candidate fires on a completed bar, the probability clears breakeven plus 2 points after costs, and the
   RiskGate allows it (table above).

As of the last research reports (GitHub issues `research: <family>`), no family has shown an edge at its default
settings, so expect no proposals until research finds one. Check the Saturday jobs on the Feeds tab
(`saturday_retrain`, `tournament`) and `state\agents.json` to see where each agent stands.

### A CI failure issue appeared (label `ci`)

CI opens one issue per failed push, titled `CI failure on <commit> (<failed jobs>)`, with the tail of each failed
job's output (Claude sandboxes cannot download Actions logs, so this issue is what they read). Failures on pull
requests show on the pull request instead. Nothing on the VPS changes because of a CI failure; just do not
`git pull` that commit onto the VPS. Hand the issue to a Claude session, or re-run CI from the Actions tab if it
was a network blip.

Issues labelled `ci` and `data` (`data-dukascopy failure for <year>`) mean a bar download failed; the next
Saturday run or a manual run of `data-dukascopy` retries it.

### Health check

`python -m goldbot.ops.run health` lists every check (settings, secrets present, disk, supervisor heartbeat, halt,
phase gates, each engine's state, stage and data quality, risk state, scheduler jobs, cost tables, news feeds,
agent spend, stuck approvals) as ok / warn / fail with a one-line reason, and exits 1 on any fail. `--json`,
`--out FILE`, `--baseline FILE` (fail only on new fails) and `--static` (no running services needed) exist for the
update script. The Telegram service runs the same checks every 5 minutes and messages you when a check turns fail
and when it recovers (needs `telegram.allowed_user_ids` and the Telegram service running; the `alerts` check shows
when that loop has stopped).

### Updating the VPS to a new version

```powershell
cd C:\goldbot
.\goldbot\ops\vps_update.ps1 -Ref main
```

The script saves a health baseline (`logs\health_before.json`), fetches, stops the services (inputs first, engines
last), checks out the new commit, reinstalls `.[live]`, rebuilds the dashboard if `web/` changed, starts the
services (supervisor first, engines next) and runs the health check again (`logs\health_after.json`). On any new
fail it rolls back to the previous commit. Exit codes: 0 healthy, 1 rolled back, 2 the rollback is unhealthy too.

* It refuses while a scheduler job is running (Saturday 06:00-13:00 UTC is the retrain); `-Force` overrides.
* Proposals open during an update expire. Positions keep their broker-side stop and target while engines are down.
* If the new commit changes `config/accounts.yaml` or `config/settings.yaml` that you edited on the VPS, the
  checkout fails and the script restores the old version untouched: merge those files by hand.
