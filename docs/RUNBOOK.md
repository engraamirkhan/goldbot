# goldbot runbook (owner's guide)

**Start with the one-page [owner guide](OWNER_GUIDE.md) (setup checklist, daily use, decisions); this runbook has the detail.**

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

## 0. Free hosting on Oracle Cloud, from a Mac (recommended)

goldbot can run with **no Windows machine and no monthly cost** on two Oracle Cloud "Always Free" virtual machines.
Everything below is done from your Mac: the Oracle website in a browser, and the **Terminal** app for SSH. Sections
1-2 (a Windows VPS) remain the alternative.

| VM | Oracle shape (Always Free) | What runs there |
| --- | --- | --- |
| **brain** | VM.Standard.A1.Flex (ARM), 4 OCPU, 24 GB, Ubuntu 24.04 | engine, supervisor, scheduler (retraining), dashboard API, Telegram, news |
| **mt5** | VM.Standard.E2.1.Micro (x86), 1 GB, Ubuntu 24.04 | MT5 terminal under Wine + the goldbot bridge |

The engine reaches the terminal through the bridge (`goldbot/execution/bridge.py`) on Oracle's private network.
MT5 under Wine is less proven than MT5 on Windows: it is used for the demo phase; before real money the demo
record decides whether it is reliable enough.

### 0.1 Oracle account (browser)
1. Sign up at oracle.com/cloud/free (a card is needed for verification). Pick a **home region** close to London
   (IC Markets' MT5 servers are in London/NY4); the region cannot be changed later.
2. Upgrade the account to **Pay As You Go** (Billing -> Upgrade). Always Free resources stay free; without the
   upgrade Oracle may reclaim VMs it considers idle. Set a budget alert of $1 (Billing -> Budgets) so any charge
   would tell you at once.
3. On your Mac, make an SSH key once: open Terminal and run `ssh-keygen -t ed25519` (press Enter at each question).

### 0.2 Create the two VMs (browser)
Compute -> Instances -> Create, both in the same VCN (the wizard's default network):
1. **brain**: image Ubuntu 24.04, shape *Ampere* VM.Standard.A1.Flex with 4 OCPU / 24 GB, paste the contents of
   `~/.ssh/id_ed25519.pub` (`cat ~/.ssh/id_ed25519.pub` in Terminal). If it says *out of capacity*, try another
   availability domain or retry later; this is common for free ARM VMs.
2. **mt5**: image Ubuntu 24.04, shape *AMD* VM.Standard.E2.1.Micro, same SSH key.
3. Note each VM's **public IP** and the brain's **private IP** (instance page -> Primary VNIC). Do not put these in
   the repository, an issue or a chat.
4. No firewall changes: the brain reaches the bridge through an SSH tunnel (port 22, open by default inside the
   VCN) and the dashboard goes out through Cloudflare Tunnel. The bridge itself listens only on the MT5 box's
   127.0.0.1.

### 0.3 The MT5 box
```bash
ssh ubuntu@<mt5 public ip>
git clone https://github.com/engraamirkhan/goldbot.git /tmp/goldbot
sudo bash /tmp/goldbot/goldbot/ops/linux/mt5_bootstrap.sh
```
Log in to your IC Markets demo **once** (MT5 keeps the login; goldbot never sees the password):
1. On the VM: `sudo systemctl start goldbot-vnc`.
2. On your Mac, a second Terminal window: `ssh -L 5900:localhost:5900 ubuntu@<mt5 public ip>`.
3. In Finder: Go -> Connect to Server -> `vnc://localhost:5900` (macOS Screen Sharing; leave the password empty).
4. In that window start the terminal if it is not open (`sudo -u mt5 env DISPLAY=:99 wine 'C:\MT5\ICMarkets\terminal64.exe' &`
   on the VM), then File -> Login to Trade Account: your login, password, server **ICMarketsSC-Demo**, tick
   **Save password**. Check `XAUUSD` is in Market Watch, and enable Tools -> Options -> Expert Advisors ->
   *Allow algorithmic trading*. Close the terminal.
5. `sudo systemctl stop goldbot-vnc`, close the tunnel window.

Bridge address and token:
```bash
goldbot-mt5 accounts bridge-serve icm-demo     # your MT5 login number, then 127.0.0.1:8765; prints a token ONCE
sudo systemctl start goldbot-bridge@icm-demo
```
Copy the token straight into the next step; do not save it anywhere else. The bridge refuses to start unless the
terminal is logged in to that login on ICMarketsSC-Demo as a **demo** account.

### 0.4 The brain
```bash
ssh ubuntu@<brain public ip>
git clone https://github.com/engraamirkhan/goldbot.git /tmp/goldbot
sudo bash /tmp/goldbot/goldbot/ops/linux/brain_bootstrap.sh   # ends by printing a "ssh-ed25519 ..." key line
```
On the MT5 box: `sudo goldbot-mt5-authorize '<that ssh-ed25519 line>'` (the key may only forward to the bridge).
Back on the brain:
```bash
sudo goldbot-tunnel <mt5 private ip>
goldbot run setup                              # guided: every secret and setting, in order (below)
```
`goldbot run setup` walks through eight steps. Each one says what the value is, where to get it, and what happens
without it. Secrets are hidden as you type and never shown again. Press Enter to keep a value that is already set, or
to skip an optional step. You can run it again at any time: it only asks about what you choose to change.

| Step | What | Where to get it |
| --- | --- | --- |
| 1 | MT5 demo login number and password (`icm-demo`) | IC Markets welcome email, or MT5 -> File -> Login to Trade Account |
| 2 | Bridge address (press Enter for `http://127.0.0.1:8765`) and bridge token | the token printed once by `bridge-serve` in 0.3 |
| 3 | Telegram bot token | @BotFather -> /newbot (section 2.8) |
| 4 | Your Telegram user id (the only user who may approve) | message @userinfobot |
| 5 | Dashboard owner email | your own address |
| 6, 7 | optional: `anthropic-api-key`, `github-token` | console.anthropic.com; GitHub fine-grained token, Contents: read and write |
| 8 | Backups: namespace, region, bucket, restic password, writer keys | the console steps in 0.6 (skip now, run setup again later) |

Secrets go only into the secret store: on these VMs `/opt/goldbot/state/.secrets.json`, readable only by the
`goldbot` service user (a server has no desktop keyring); Oracle encrypts the disk at rest. Steps 4 and 5 are
written to `/opt/goldbot/config/settings.local.yaml` (mode 0600, on the VM only, never committed); the wizard
keeps every other line already in that file.

Dashboard: create the Cloudflare tunnel as in section 2.7, then on the brain `sudo cloudflared service install
<tunnel token>`.

Check everything **before** starting the services:
```bash
goldbot run preflight
```
It changes nothing. Each line is ✅, ⚠️ or ❌; every ❌ prints the exact command that fixes it. It checks: the
settings load, the owner email, the Telegram token and allow-list, the tunnel service, the bridge address and token,
the bridge answering through the tunnel, the terminal logged in to the registered demo account (login, server, demo),
the dashboard build (`web/dist`), the database files being private (0600), backups (a warning only), the clock
(chrony), disk and memory, and the deploy timer. Fix and repeat until the last line says **READY**; it exits 1
while anything blocking is left.

By hand (if the wizard cannot be used), the same values:
```bash
goldbot accounts add icm-demo                  # MT5 login number, then password
goldbot accounts bridge-use icm-demo           # http://127.0.0.1:8765, the token from 0.3
goldbot accounts set telegram-bot-token        # from @BotFather (section 2.8)
goldbot accounts set anthropic-api-key         # optional: staff agents and headline scoring
goldbot accounts set github-token              # optional: bar sync and the shared trial registry
sudo -u goldbot nano /opt/goldbot/config/settings.local.yaml
#   auth: {owner_email: <you>}
#   telegram: {allowed_user_ids: [<your id>]}
```
then `sudo chmod 600 /opt/goldbot/config/settings.local.yaml`, and the backup keys as in 0.6.

Start everything (supervisor first):
```bash
sudo systemctl start goldbot-supervisor goldbot-api goldbot-telegram goldbot-news goldbot-scheduler
sudo systemctl start goldbot-engine@icm-demo
goldbot run health
```
`systemctl status goldbot-engine@icm-demo` shows a service; `journalctl -u goldbot-engine@icm-demo -f` follows its
log. If the bridge is unreachable the engine exits and restarts every 10 s; it trades nothing meanwhile, and open
positions keep their broker-side stop and target.

### 0.5 Daily use
Only your Mac's browser (the dashboard) and Telegram (the one-click Approve).

**Morning digest.** Every day at 06:45 UTC (before London; change it with `telegram.digest_at: "HH:MM"` in
`config/settings.local.yaml`) Telegram sends one short message:
* **status:** ✅ all good, or ⚠️ attention with the failing and warning health checks named (details: `goldbot run health`);
* **yesterday** (the previous UTC day): proposals approved / rejected / expired, trades closed with net $ and R, open
  positions now;
* **risk:** drawdown stage and % from peak, daily and weekly loss against the caps, and any halt (yours, drift,
  supervisor, drawdown);
* **research:** the next pre-registered trial or the quarter's trial budget, and the best and worst attribution cell
  that is not noise;
* **for you:** approvals pending, the number of owner decisions open (see OWNER_GUIDE), and a deploy on offer.

It is sent once a day. If the Telegram service was down at 06:45 it sends the digest when it starts, if that is
within 6 hours; later than that it skips the day. A file it cannot read shows as "could not read" on that line
and the rest still arrives. It never contains account numbers or emails.

**Updates: one click on Telegram, or by hand.** Nothing is ever deployed without you.
* When a new version is merged and has passed CI, Telegram shows *New version ready* with the list of changes and
  **[Deploy] [Skip]**. Deploy restarts the services within a minute (never while an entry waits for your click; open
  positions keep their broker-side stops and the engine reconciles on start), checks every service is still running
  after 60 s, and **rolls back by itself** if not. Telegram then says *deployed* or *rolled back* with the reason.
* The MT5 box (terminal + bridge, rarely changed) is updated by hand only: `sudo goldbot-deploy latest` on it when a
  release note mentions the bridge.
* By hand on either VM: `sudo goldbot-deploy latest` (or a specific commit id). Same checks, same rollback.
* The health check `deploy` shows the last result. If the deploy script itself changed, review it and run
  `sudo install -m 755 -o root -g root /opt/goldbot/goldbot/ops/linux/goldbot-deploy.sh /usr/local/sbin/goldbot-deploy`.

### 0.6 Backups and restore (brain)
The scheduler backs up the brain's state every day at 22:15 UTC with `restic`. The backup is encrypted on the
brain and sent to **Oracle Object Storage** (free tier, 20 GB). It never goes to GitHub. Each backup holds:

* `core.db` and `aux.db`, copied consistently while the services run;
* the state JSON files;
* the trial registry;
* the models, with their checksums;
* the brain's own Parquet: ticks, fills, trades and the decisions journal.

Bars from releases and secrets are left out. Every Sunday at 10:00 UTC a restore drill restores the latest backup to
a temporary folder and checks it. Health shows `backup_age` (warn after 26 h, fail after 72 h; Telegram tells you
at the first warning), `restore_drill` (fail when the drill fails) and `backup_prune` (warn when the monthly
retention run from your Mac is older than 45 days).

**Why two keys.** The brain only adds backups; it never deletes old ones (no `restic forget` or `prune` on the
brain). Old backups are thinned once a month **from your Mac** with a second key. If the brain is ever compromised,
its key cannot wipe the backup history, and the bucket keeps every overwritten or deleted object for 30 more days
(versioning), so even a deleting key cannot remove history at once. Decision record:
`docs/decisions/0002-backup-scope-and-thresholds.md`.

**One-time setup, in the Oracle Cloud console (browser):**
1. *Storage -> Object Storage -> Buckets -> Create bucket*: name `goldbot-backup`, Standard tier, private (the
   default). Note the **namespace** (shown on the bucket page) and your **region identifier** (e.g.
   `eu-frankfurt-1`, top right -> region -> manage regions).
2. On the bucket page: *Edit* next to **Object Versioning** -> *Enable*. Then *Lifecycle Policy Rules -> Create
   rule*: name `expire-old-versions`, target **Previous object versions**, action **Delete**, after **30 days**
   (more is fine; never less), enabled. Deleted or overwritten backup files stay recoverable for those 30 days.
3. Two users, two groups, two keys (*Identity -> Domains -> Default -> Users / Groups*):
   * **Brain writer** (user and group `goldbot-backup-writer`): add and read objects, no delete. Policy:
     `Allow group goldbot-backup-writer to read buckets in tenancy where target.bucket.name='goldbot-backup'` and
     `Allow group goldbot-backup-writer to manage objects in tenancy where all {target.bucket.name='goldbot-backup', request.permission!='OBJECT_DELETE'}`.
     restic also removes its own lock files; with no delete right those removals fail, which goldbot logs as a
     warning only (your Mac's monthly run clears the leftover locks). The no-delete condition is **mandatory**: in
     Oracle the same delete right also removes old versions, so versioning alone does not protect backups from a
     compromised server. If the first `goldbot run backup` below fails on a permission error, stop and ask; the
     fix is an append-only backup target (restic rest-server), never dropping the condition.
   * **Retention** (user and group `goldbot-backup-retention`): full object rights on the bucket, used only from
     your Mac: `Allow group goldbot-backup-retention to manage objects in tenancy where target.bucket.name='goldbot-backup'`.
   On each user: *Customer secret keys -> Generate secret key* (`goldbot-restic-brain`, `goldbot-restic-retention`).
   Copy each **secret** (shown only once) and **access key**. The brain gets only the writer key.
4. In your password manager, generate a long random **restic password** and save it there *first*. Without it no
   backup can ever be restored, and the brain is the only other place that holds it.

**On the brain**: `goldbot run setup` (step 8) asks for the namespace, region, bucket, password and the writer key,
and stores the four `restic-*` keys; then run the last four commands below. By hand instead (each command asks for
its value; nothing is echoed):
```bash
goldbot accounts set restic-repository     # s3:https://<namespace>.compat.objectstorage.<region>.oraclecloud.com/goldbot-backup/goldbot
goldbot accounts set restic-password       # the password from your password manager
goldbot accounts set restic-s3-access-key  # the BRAIN WRITER key: access key
goldbot accounts set restic-s3-secret-key  # the BRAIN WRITER key: secret
goldbot run backup --init                  # creates the encrypted repository (once)
goldbot run backup                         # first backup now; prints the snapshot id and size
goldbot run restore-drill                  # restores it to a temp folder and verifies it
goldbot run health                         # backup_age and restore_drill: OK
```

**Restore after losing the brain** (target: running again within 1 hour; positions keep their broker-side stops
meanwhile, and the engines reconcile with the broker on start):
1. Build a new brain (section 0.4). Store the four `restic-*` keys again, as above (make a new customer secret key
   if the old one is lost), plus the other secrets from 0.4: secrets are never in the backup.
2. Restore and verify: `goldbot run restore --latest --to /var/lib/goldbot/restore`. The folder must be empty or
   missing; a restore never writes over live state. The command ends with `verified` or lists the problems.
3. Stop everything:
   `sudo systemctl stop goldbot-engine@icm-demo goldbot-scheduler goldbot-api goldbot-telegram goldbot-news goldbot-supervisor`.
4. Put the files in place. Delete stale database journals first: an old `-wal` file next to a restored database
   corrupts it.
   ```bash
   sudo -u goldbot rm -f /opt/goldbot/state/*.db-wal /opt/goldbot/state/*.db-shm
   sudo -u goldbot cp -a /var/lib/goldbot/restore/state/. /opt/goldbot/state/
   sudo -u goldbot cp -a /var/lib/goldbot/restore/models/. /opt/goldbot/models/
   sudo -u goldbot cp -a /var/lib/goldbot/restore/data/. /opt/goldbot/data/
   ```
   The bar history comes back from the data release at the next Saturday retrain. Use section 2.5 to load it sooner.
5. Start the supervisor first, then the rest (0.4), then run `goldbot run health`. Anything after the last backup
   (at most a day) is gone: approvals, orders and engine state are rebuilt from the broker. Trials recorded since
   then are still on release `research-v1`.

To restore one file only, restore into an empty folder as in step 2 and copy that file. Stop the service that
writes it first. Restored folders are created owner-only (mode 0700): they hold the databases unencrypted.

**Monthly retention, on your Mac** (health `backup_prune` reminds you after 45 days). Once, install restic
(`brew install restic`) and store four Keychain items; each `security` command prompts for the value:
```bash
security add-generic-password -a goldbot -s restic-repository -w               # same value as on the brain
security add-generic-password -a goldbot -s restic-password -w                 # from your password manager
security add-generic-password -a goldbot -s restic-retention-s3-access-key -w  # the RETENTION key: access key
security add-generic-password -a goldbot -s restic-retention-s3-secret-key -w  # the RETENTION key: secret
```
Then, on the first weekend of each month, from the repository folder:
```bash
scripts/backup_retention.sh --dry-run   # lists what would be forgotten (14 daily, 8 weekly, 12 monthly kept)
scripts/backup_retention.sh             # forgets, prunes, and uploads a marker the brain reads at its next backup
```
The script refuses to run on a goldbot server. After the brain's next backup, `backup_prune` shows OK.

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
| `data-macro.yml` | Tuesdays 04:41 UTC, or by hand | Downloads the FRED macro series (10y real yield, breakeven, broad dollar, gold VIX, 2y yield) and publishes `macro_fred.parquet` to the release `macro-v1`. | Failures as issue `data-macro failure` (labels `ci`, `data`). |
| `research.yml` | By hand only (Actions -> research -> Run workflow; inputs `specialist`, `from_year`, `to_year`, `rationale`, `macro`) | Walk-forward research for one specialist family (`session_open`, `mean_reversion`, `trend`, `breakout`) on the `data-v1` bars; with `macro` ticked, adds the macro features from `macro-v1`. Keeps the trial registry on release `research-v1`. | Issue `research: <specialist>` (label `research`). |
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
| `saturday_retrain` | Saturday 06:00 | Refreshes bars from `data-v1` and the macro series from `macro-v1` (a macro failure does not stop the retrain), decides waiting challengers (promote/retire), retrains new challengers. |
| `recalibrate` | Saturday 11:30 | Refits only the probability map of each champion and challenger on its recent shadow outcomes (every candidate, taken or not), bounded to +-0.05 per week; logged in `state\recalibration.jsonl`. Promotes nothing. |
| `tournament` | Saturday 12:00 | Population round: fitness, retirement, promotion to live, cloning, capital shares -> `state\agents.json`. |
| `agents_weekly` | Saturday 13:00 | Journal coach, improvement agent, research analyst. |
| `monthly_research` | First Sunday of the month 08:00 | Bounded label-grid research; summary in `state\research_<YYYY-MM>.md`. |
| `backup` | Daily 22:15 | Brain only: encrypted `restic` backup of the databases, state files, trial registry, models and the brain's own Parquet to Oracle Object Storage, append only (old snapshots are thinned monthly from your Mac to 14 daily, 8 weekly and 12 monthly); result in `state/backup_last.json` (section 0.6). |
| `restore_drill` | Sunday 10:00 | Restores the latest backup to a temporary folder and verifies checksums, database integrity, row counts and model checksums; result in `state/restore_drill_last.json`. |

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

MT5 demo accounts (asks for the login number, then the password; both go into the keyring, never into
`config/accounts.yaml`, which is in the public repo). The IC Markets demo is on server `ICMarketsSC-Demo`:

```powershell
cd C:\goldbot
.\.venv\Scripts\python -m goldbot.ops.accounts add icm-demo
.\.venv\Scripts\python -m goldbot.ops.accounts add vantage-demo
.\.venv\Scripts\python -m goldbot.ops.accounts list
```

`list` should show `password_stored=yes` and a login number for both demo accounts. (They are stored as keys
`mt5-login-icm-demo` / `mt5-icm-demo` and `mt5-login-vantage-demo` / `mt5-vantage-demo`.) Never put a login or
password in a file in `C:\goldbot`; a test fails CI if `config/accounts.yaml` ever carries a login.

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
.\.venv\Scripts\python scripts\fetch_data_release.py --years 2024 2026 --macro
```

`--macro` also loads the macro series (release `macro-v1`) into the store; it prints `macro 0` until the
`data-macro` workflow has run once. The engines do not use them yet; research does.

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

0. Before the first start of `goldbot-api`, name the owner in the server's own settings file (never in
   `config\settings.yaml`: the repository is public). Create or edit `C:\goldbot\config\settings.local.yaml`
   (not tracked by Git) and add:
   ```yaml
   auth: {owner_email: you@yourdomain}
   ```
   Then `nssm restart goldbot-api`. Without it the setup form refuses with "auth.owner_email is not set", and any
   other email is refused. Only this address can ever hold the owner role. If the address in the file and the
   owner account in `state\aux.db` ever differ, the API log warns "dashboard owner account(s) do not match
   auth.owner_email" and changes nothing: correct the file.
1. While no user exists, the API prints a one-time setup code at every start. Open
   `C:\goldbot\logs\goldbot-api.log` and find the latest line
   `goldbot first-run: open the dashboard and create the owner account with setup code <code>`.
   The code changes every time the API restarts, so always use the newest line.
2. Open your tunnel hostname in a browser. The login page offers the setup form: enter the **Setup code**, your
   email (the `owner_email` above) and a password of 12+ characters, then **Create owner account**.
3. The page shows an authenticator enrolment (an `otpauth://` link/QR) and **10 recovery codes**. Add the
   enrolment to your authenticator app; every later sign-in, and every re-arm, needs the 6-digit code from it.
   Copy the recovery codes into your password manager now: they are shown once (only their hashes are stored).
   Each one signs you in once if the authenticator is lost.
4. Other people: **Users** tab (owner only) -> email + role (`viewer` or `approver`; the owner role is never
   granted) -> **Create invite link**, and send them the link (72 h, single use). Approvers can approve, reject
   and halt; only the owner can re-arm, invite, change roles and manage users. Nobody can create an account any
   other way.

**Passwords and lost authenticators**

| Situation | What to do |
| --- | --- |
| Anyone wants a new password | Signed in: **Account** tab -> current password + authenticator code + new password (12+ characters, different from the current one). Their other sessions are signed out. |
| An approver or viewer forgot their password but has the authenticator | Login page -> **Forgot password** -> email, a current code, new password. All their sessions are signed out and you get a Telegram notice; if you did not expect it, **Disable** the account. Five wrong attempts lock that email for 15 minutes (20 from one network address lock that address). |
| You (the owner) forgot your password | Login page -> **Forgot password** -> email, a current authenticator code, new password **and one unused recovery code** (the code is spent). The authenticator alone never resets the owner. You get a Telegram notice either way. |
| An approver or viewer lost the authenticator | Owner: **Users** -> **Reset link** on their row, and send them the link (24 h, single use). It sets a new password and a new authenticator and signs them out everywhere. |
| You (the owner) lost the authenticator | Login page -> **Use a recovery code** -> email, password and one unused recovery code. Enrol the new authenticator it shows; the old one stops working. Then get 10 fresh codes (`POST /api/auth/recovery/codes` with password + new code; there is no button yet). The old codes stop working. |
| You lost the authenticator and every recovery code, or the password and the authenticator | On the server: stop `goldbot-api` and ask a Claude session to reset the owner in `state\aux.db`. There is no remote path, by design. |
| A sign-in says "too many attempts" | Wait 15 minutes after the last failure; the counter is kept in `state\aux.db`, so restarting the API does not clear it. Someone who knows your email can keep it locked this way; the audit log shows the failures and their address. |
| Someone should lose access | Owner: **Users** -> **Disable** (signs them out at once) or **Sign out everywhere**; **Enable** restores the account. The owner cannot be disabled or demoted. |

Every one of these is recorded in the audit log (`state\aux.db`, table `audit`; the state-files table in section 3 shows how to read it).

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
* `/status` on Telegram shows whether entries are halted, the approval mode and how many proposals are pending.


### Auto mode (entries without your click)

* By default every entry waits for your click (propose-and-approve). **Auto mode** means an entry that passes the
  RiskGate is placed without asking you. Every cap, stop and exit works exactly as before; only the click goes.
* It is never switched on by itself. Telegram sends one message, "Auto mode can be enabled: evidence ...; reply
  /mode auto <TOTP>", only after at least 100 proposals you approved or rejected since the last mode change, with no
  RiskGate breach (no 8% size-down, no 12% kill switch) and no measurable difference between the outcomes of the
  trades you approved and the ones you rejected (your veto is not adding value). The message quotes the numbers.
* To switch on: `/mode auto 123456` with your current authenticator code (the dashboard one). It is refused if the
  evidence no longer holds or the code is wrong. Telegram deletes the message with the code.
* To switch off at any time: `/mode propose` (no code needed), or `/halt` to stop new entries altogether.
* It switches back to propose-and-approve on its own after a 12% kill switch, and stays propose for the 30 days after
  any re-arm. `/status` shows the current approval mode; every change is in `state\audit.jsonl`.

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
| Dashboard users, invites, sessions and audit log | `state\aux.db` (SQLite; tables `users`, `invites`, `sessions`, `password_resets`, `recovery_codes`, `audit`; tokens and codes as hashes only). Latest audit entries: `python -c "import sqlite3; [print(b) for (b,) in sqlite3.connect('state/aux.db').execute('SELECT body FROM audit ORDER BY id DESC LIMIT 20')]"`. Logins survive an API restart or deploy (12 h sessions). `state\users.json` and `state\audit.jsonl` from before 2026-10-10 were imported once and are kept untouched as a backup; delete them only after a verified backup of aux.db. Copy aux.db (with its `-wal` file) only while `goldbot-api` is stopped: a plain copy of a live WAL database may be inconsistent. |
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
2. **Publish the measured costs for research** (nothing is committed to the repo). Once the swap is measured, set
   `costs.publish_release: true` in `config\settings.yaml` and restart `goldbot-scheduler`
   (`nssm restart goldbot-scheduler`). The `github-token` must be in the keyring
   (`python -m goldbot.ops.accounts set github-token`). From then on, every weekday right after `nightly_costs`, the
   scheduler uploads the IC Markets table as `costs_measured.json` to the GitHub release `costs-v1`. It holds costs
   only: spread, slippage, commission and swap per lot, with `measured_at` and, per field, "measured" or "prior" and
   the tick, fill or lot count behind it. It never holds an account id, login, balance or equity. The upload is
   refused, and nothing is published, while the swap is unmeasured or there are fewer than 50 fills, so a prior is
   never published as a measurement. To publish at once, or to check the file first:

   ```powershell
   .\.venv\Scripts\python -m goldbot.ops.run publish-costs                  # upload now
   .\.venv\Scripts\python -m goldbot.ops.run publish-costs --out $env:TEMP\costs.json   # look, don't upload
   ```

   The research workflow (`research.yml`) downloads that asset when it exists and passes `--cost-table`, so the
   trial's net results use the broker's measured swap, commission and slippage. Without it, research charges the
   settings priors and the report's "cost source" line starts with "PRIORS ONLY". With publishing on, the health
   check `costs:published` warns when the table was never published or is older than 8 days; its reason includes
   the last refusal or upload error (also in `state\costs_published.json`).
3. **Scheduled jobs:** `nightly_costs`, `model_watch` and `agents_daily` show "ok"; `calendar_archive` shows
   "ok" after 06:10. A "failed" job shows its first error line in "Last error".
4. **Supervisor heartbeat:** `state\supervisor.json` has a current `ts`, `"halt": false` and an empty
   `stale_engines` list.
5. **Agent reports** arrived on Telegram and the **Agents** tab (only with `anthropic-api-key`).

---

## 5. Going live (demo -> tiny-live)

**Is the gate met? Ask the code first.** It evaluates every roadmap gate from the evidence and records nothing:

```powershell
.\.venv\Scripts\python -m goldbot.ops.run gates            # each gate MET / NOT MET, item by item, plus the stop rule
.\.venv\Scripts\python -m goldbot.ops.run gate-evidence chaos_drill --passed yes --detail "killed terminal mid-position, stops held"
.\.venv\Scripts\python -m goldbot.ops.run gate-evidence shuffle_auc --value 0.503   # also asof_violations, feed_mismatch_share
```

`gates` prints each item with its evidence and the threshold from `config/settings.yaml` `gates:` and writes
`state\gate_report.json`. The paper-to-tiny-live gate needs: the backtest-to-paper gate recorded, at least 150
closed demo trades since it, at least 182 days of paper, paper expectancy no more than 50% below the backtest's,
measured slippage within 30% of the modelled slippage, a passed chaos drill, and no single loss larger than the
weekly cap. Values marked `PROPOSED` in `settings.yaml` (the DSR bar, the "at chance" tolerance, feed agreement,
minimum days, trades per broker) must be signed off by you before the first paper trade; change them only before
that, never to pass a gate. The paper and live record is `state\closed_trades.jsonl`; until the engine writes it,
the paper and live items read "not met".

**Stop rule.** `gates` and the health check `stop_rule` also evaluate the design's stop rule: after 18 months of
paper plus live, more than 500 pooled trades with the lower 90% confidence bound on expectancy still below zero, or
any single trade losing more than the weekly cap (5% of equity). A breach makes health FAIL with
`STOP RULE BREACHED`, which Telegram announces once. It **does not** halt, close or resize anything; the RiskGate's
caps and kill switch keep working as before. The design says the project stops: send `/halt` (or press Halt on the
dashboard) and review before doing anything else.

Live trading is locked in two independent places, and **both** must agree:

1. **The phase gate file** `C:\goldbot\state\phase_state.json` must list the gate `paper_to_tiny_live` in
   `gates_passed`. Without the file goldbot assumes `{"phase": 0, "gates_passed": []}`. Only
   `python -m goldbot.ops.run record-gate <gate> --evidence state\gate_report.json` writes this file (gates in
   order, with a timestamp and the evidence file's hash): you record the gate yourself, and only when `gates` shows
   it MET. Neither `gates` nor the health check ever records a gate. Do not create the file to "try live". The
   only key the unlock reads is `gates_passed`; the gate report reads `gate_log` timestamps for the phase clocks.
   The recorded form is:

   ```json
   {"phase": 3, "gates_passed": ["foundation_to_backtest", "backtest_to_paper", "paper_to_tiny_live"], "gate_log": [...]}
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
agent spend, stuck approvals, backup age and the restore drill) as ok / warn / fail with a one-line reason, and exits 1 on any fail. `--json`,
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
