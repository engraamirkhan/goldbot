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
| **brain** | VM.Standard.A1.Flex (ARM), 2 OCPU, 12 GB (the Always Free total since mid-2026), Ubuntu 24.04 | engine, supervisor, scheduler (retraining), dashboard API, Telegram, news |
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
1. **brain**: image Ubuntu 24.04, shape *Ampere* VM.Standard.A1.Flex with 2 OCPU / 12 GB (Oracle halved the free A1 total in 2026; more than that is billed or may be terminated), paste the contents of
   `~/.ssh/id_ed25519.pub` (`cat ~/.ssh/id_ed25519.pub` in Terminal). If it says *out of capacity*, try another
   availability domain or retry later; this is common for free ARM VMs.
2. **mt5**: image Ubuntu 24.04, shape *AMD* VM.Standard.E2.1.Micro, same SSH key.
3. Note each VM's **public IP** and the brain's **private IP** (instance page -> Primary VNIC). Do not put these in
   the repository, an issue or a chat.
4. No firewall changes: the brain reaches the bridge through an SSH tunnel (port 22, open by default inside the
   VCN) and the dashboard goes out through Cloudflare Tunnel. The bridge itself listens only on the MT5 box's
   127.0.0.1.

### 0.3 The MT5 box
Root runs the bootstrap, so it runs only a copy whose hash you checked on your Mac, at a commit on `main`
(`<SHA>`, `<HASH>`: docs/deploy/first-deploy.md P6 shows how to get both). Run it inside `tmux new -s boot`; it
ignores a dropped connection and logs to `/root/goldbot-bootstrap.log`.
```bash
ssh ubuntu@<mt5 public ip>
curl -fsSLo mt5_bootstrap.sh https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/linux/mt5_bootstrap.sh
echo "<HASH>  mt5_bootstrap.sh" | sha256sum -c - && sudo bash mt5_bootstrap.sh <SHA>
```
Log in to your IC Markets demo **once** (MT5 keeps the login; goldbot never sees the password):
1. On the VM: `sudo systemctl start goldbot-vnc`.
2. On your Mac, a second Terminal window: `ssh -L 5900:localhost:5900 ubuntu@<mt5 public ip>`.
3. In Finder: Go -> Connect to Server -> `vnc://localhost:5900` (macOS Screen Sharing; leave the password empty).
4. In that window the terminal should be open. If it is not, start it on the VM with
   `sudo systemctl start goldbot-mt5-terminal@icm-demo`. Its unit is the only thing that starts the terminal, and it
   refuses to start a second one. Never start it by hand with `wine`. The bootstrap installs MetaQuotes' generic terminal, which has **no IC Markets servers** (ADR 0020),
   so do not use File -> Login to Trade Account first. Instead: File -> Open an Account -> type `ICMarkets` in the
   search box -> pick **Raw Trading Ltd** (the company behind the ICMarketsSC servers) -> Next -> **Connect with an
   existing trade account** -> your login, password, server **ICMarketsSC-Demo**, tick **Save password** -> Finish.
   This downloads the server list into `Config/servers.dat`; later logins can use File -> Login as usual.
   Check `XAUUSD` is in Market Watch, and enable Tools -> Options -> Expert Advisors -> *Allow algorithmic trading*.
   Leave the terminal open. If you close it, its unit starts it again within 15 s.

   *Symptom when the server list is missing:* File -> Login with ICMarketsSC-Demo does nothing at all: no line in
   the Journal, no network connection, the corner keeps showing "No connection". Fix: the Open an Account flow above.
5. `sudo systemctl stop goldbot-vnc`, close the tunnel window.

**Later logins need no VNC** (owner directive 2026-10-20). With the password in the box's keyring
(`goldbot-mt5 accounts add icm-demo`, hidden prompts, once), the bridge logs the terminal in by itself when its
attach fails or `/health` finds no account, to the configured login only (`goldbot/ops/login.py`, settings
`mt5_login`). By hand: `goldbot-mt5 login icm-demo` prints `ok: icm-demo: logged in (demo, ICMarketsSC-Demo)` or
`failed: icm-demo: <reason>`, never the login or password. A refused password (`-6`) is marked in
`state/mt5-login/icm-demo.refused` (time and code only) and nothing sends it again, through any restart, until you
store it again with `goldbot-mt5 accounts set mt5-icm-demo` (that clears the mark), because the broker locks an
account after repeated bad passwords. If the password at the broker was reset and the stored one is right,
`goldbot-mt5 login icm-demo --retry-refused` tries exactly once. Other failures wait 20 s, 40 s, ... up to 10 min
between logins, and at most 6 logins an hour are sent; both are kept in `state/mt5-login/`, so restarts reset
neither. The very first
login on a freshly built terminal (no `Config/servers.dat` yet) may still need the Open an Account flow above: try the
command first and use VNC only if it fails.

The bridge never starts the terminal. `goldbot-bridge@icm-demo` requires `goldbot-mt5-terminal@icm-demo`, so starting
the bridge starts the terminal first. The bridge then waits up to 60 s for the terminal process and attaches to it
through the MT5 Python API with
`initialize(path=r"C:\MT5\ICMarkets\terminal64.exe", timeout=60000)` (`terminal_path` in `config/accounts.yaml`,
`MT5_INIT_TIMEOUT_MS` in `goldbot/execution/mt5_adapter.py`). Without `path=` it fails with IPC timeout `-10005`.
If a hand test from the Windows Python on the box shows `-10005`, check the path is given and the terminal runs
under the same user (`mt5`) and `DISPLAY=:99`.

Bridge address and token:
```bash
goldbot-mt5 accounts bridge-serve icm-demo     # your MT5 login number, then 127.0.0.1:8765; prints a token ONCE
sudo systemctl start goldbot-bridge@icm-demo
```
Copy the token straight into `goldbot run setup` on the brain (step 2); do not save it anywhere else. Do this
step only when the brain is installed and you are about to run setup, so the token is never left in a window. The bridge refuses every call
until it has seen the terminal logged in to that login on the registered demo server as a **demo** account. Until then it only
answers `GET /health`, with the terminal's state (`not_logged_in`, `wrong_account`, ...). On the box,
`curl -s 127.0.0.1:8765/health` shows that state, and the brain shows it as `terminal:icm-demo` (see "Terminal down").

### 0.3b Adding an account box (one MT5 box per account; first one `icm-probe` on `goldbot-mt5-probe`)
Each further MT5 account gets its own E2.1.Micro box built exactly like 0.3 (docs/ops/icm-probe-account-plan.md,
Option B): its own Wine prefix, terminal at `C:\MT5\ICMarkets`, bridge on the box's own `127.0.0.1:8765`. Only the
unit instances carry the account. On the brain each box gets its own tunnel, `goldbot-bridge-tunnel@<account>`, on
its own local port (`icm-probe`: **8766**). `icm-demo` keeps `goldbot-bridge-tunnel.service` on 8765, unchanged.
Hosts are SSH aliases in `~/.ssh/goldbot.conf`; no address, login or token goes into the repo, a commit or a chat.
`<SHA>`/`<HASH>` as in 0.3. The `icm-probe` entry in `config/accounts.yaml` must be on `main` at `<SHA>`
(`enabled: false`) before B8, because the bridge serves only an account it knows.

| Step | What | Who |
|---|---|---|
| B4 | Build the box (below) | Claude, after the owner approves the deploy |
| B5 | Login on the brain: `ssh -F ~/.ssh/goldbot.conf -t goldbot-brain goldbot accounts set mt5-login-icm-probe` (hidden prompt) | **Owner** |
| B6 | Copy the login to the box without showing it (below) | Claude |
| B7 | First login: `sudo systemctl start goldbot-mt5-terminal@icm-probe`, then `goldbot-mt5 login icm-probe` (password in the box keyring as `mt5-icm-probe`; prints ok/failed only). VNC (0.3 steps 1 to 5) only if it fails | Claude (VNC fallback: **Owner**) |
| B8 | Bridge listen address and token (below) | Claude |
| B9 | Tunnel key, env file and tunnel unit on the brain (below) | Claude (root; security review) |
| B10 | Enable the box's bridge and watchdog and the brain's tunnel; check `/health` and preflight | Claude |
| B11 | Cold restart of the box, recorded in the deploy note | Claude |

**B4, build** (on the box, inside `tmux new -s boot`):
```bash
curl -fsSLo mt5_bootstrap.sh https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/linux/mt5_bootstrap.sh
echo "<HASH>  mt5_bootstrap.sh" | sha256sum -c - && sudo bash mt5_bootstrap.sh <SHA> --account icm-probe
```
It installs the same units as 0.3, including `goldbot-mt5-terminal@.service` (the only launcher, `Restart=always`),
and enables `goldbot-mt5-terminal@icm-probe`, `goldbot-bridge@icm-probe` and `goldbot-bridge-watchdog@icm-probe.timer`.
Check: `systemctl is-enabled goldbot-mt5-terminal@icm-probe goldbot-bridge@icm-probe` prints `enabled` twice.
The terminal unit starts every terminal with **AutoTrading on**: before each start (after the one-terminal guard) it
runs `/usr/local/libexec/goldbot/mt5-experts-enable.sh` as mt5, which sets the `[Experts]` keys of `Config/common.ini`
(`AllowDllImport=0 Enabled=1 Account=1 Profile=1 Chart=0 Api=0 WebRequest=0`, the icm-demo terminal's own) while no
terminal runs, backs the file up to `~mt5/mt5-common-backup-<UTC>.ini` when it changes it, and leaves it alone when they
are set. A terminal with AutoTrading off refuses every order (10027): the bridge refuses entries itself
(`terminal_autotrading_off`) and its `/health` says `autotrading_off` (see "Terminal down"). Incident 2026-10-20:
icm-probe's common.ini had no `[Experts]` section. **Any terminal restart (crash, watchdog, reboot, deploy) turns
AutoTrading back on, so switching it off by hand in the terminal is not a lasting stop**: to stop trading use `/halt`
(or **Halt new entries** on the Overview); the kill switch closes everything. This script, the bridge probe and the
watchdog are root-owned copies that `goldbot-deploy` does NOT update (it never installs root-run code from the mt5
tree): a box picks up new ones only through `mt5-box-upgrade install` (0.5) or a rebuild. A newer bridge's unknown
`/health` token is logged by an older watchdog and never acted on.

**B6, login to the box** (from the Mac, one line; `export` writes to a pipe only):
`ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-brain goldbot accounts export mt5-login-icm-probe | ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-mt5-probe goldbot-mt5 accounts set mt5-login-icm-probe --stdin`

**B8, bridge address and token** (from the Mac; the token is made on the Mac, sent to both ends, never printed):
`printf 127.0.0.1:8765 | ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-mt5-probe goldbot-mt5 accounts set mt5-bridge-listen-icm-probe --stdin`
`t=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'); printf %s "$t" | ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-mt5-probe goldbot-mt5 accounts set mt5-bridge-token-icm-probe --stdin && printf %s "$t" | ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-brain goldbot accounts set mt5-bridge-token-icm-probe --stdin; unset t`
`printf http://127.0.0.1:8766 | ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-brain goldbot accounts set mt5-bridge-url-icm-probe --stdin`
(Not `bridge-serve`: it prints the token on screen.)

**B9, the brain side** (on the brain, as root):
1. Nothing listens on 8766 yet: `ss -ltn 'sport = :8766'` shows only the header line.
2. Authorise the brain's tunnel key on the box (port 8765 only, unchanged):
   `sudo -u goldbot cat /var/lib/goldbot/.ssh/id_ed25519.pub`, then on the box `sudo goldbot-mt5-authorize '<that line>'`.
3. The env file, root-owned 0644 (the box's private address from the OCI console, typed on the brain only):
   `printf 'MT5_HOST=%s\nLOCAL_PORT=8766\n' '<probe box private ip>' | sudo install -m 644 -o root -g root /dev/stdin /etc/goldbot/bridge-icm-probe.env`
4. The tunnel template, if this brain was built before it existed: root downloads it at `<SHA>` and checks the
   sha256 you computed on the Mac (as for goldbot-deploy in 0.5), never from `/opt/goldbot`:
   `curl -fsSLo /root/goldbot-bridge-tunnel@.service https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/linux/systemd/goldbot-bridge-tunnel@.service && echo "<UNIT HASH>  /root/goldbot-bridge-tunnel@.service" | sha256sum -c - && sudo install -m 644 -o root -g root /root/goldbot-bridge-tunnel@.service /etc/systemd/system/ && sudo systemctl daemon-reload`

**B10, enable and check.** On the box: `sudo systemctl start goldbot-bridge@icm-probe` and
`curl -s 127.0.0.1:8765/health` (terminal state `ok`). On the brain:
`sudo systemctl enable --now goldbot-bridge-tunnel@icm-probe`, then `curl -s 127.0.0.1:8766/health` (the same state,
through the tunnel) and `goldbot run preflight --account icm-probe` (bridge_server_tz, the terminal's account, hedging all ok;
its `tunnel_service` line still looks at the icm-demo tunnel unit).
The tunnel unit refuses a missing or non-numeric `LOCAL_PORT`, and 8765 for any account but `icm-demo`
(`journalctl -u goldbot-bridge-tunnel@icm-probe -n 20`).

**B11, cold restart.** `sudo reboot` the box. Within 5 minutes, with no hands: `goldbot-xvfb`,
`goldbot-mt5-terminal@icm-probe` and `goldbot-bridge@icm-probe` active, exactly one `terminal64.exe`
(`pgrep -c -u mt5 -f '[t]erminal64[.]exe'` prints 1), `/health` ok on the box and through the brain's 8766. Record the
times in the deploy note. Later upgrades of this box use 0.5 "Attended MT5 box upgrade" with
`--account icm-probe` on every phase of `mt5-box-upgrade`.

**Failure and recovery on an account box.** Terminal down: "Terminal down" below, with the box's alias and account.
Tunnel down (`journalctl -u goldbot-bridge-tunnel@<account>`): it restarts every 5 s by itself; a wrong env file is
fixed by rewriting it (B9 step 3) and `sudo systemctl restart goldbot-bridge-tunnel@<account>`. Box lost (Oracle
reclaim, disk): rebuild with B4, then B6 to B11; the owner stores the password again (`goldbot-mt5 accounts add
<account>`), and VNC (B7 fallback) is needed only if `goldbot-mt5 login` fails on the fresh terminal.
`icm-demo` is not touched by any step here; if its engine shows `terminal:icm-demo` during this work, stop and check
that nothing listens on 8765 except `goldbot-bridge-tunnel.service`.

### 0.4 The brain
```bash
ssh ubuntu@<brain public ip>
curl -fsSLo brain_bootstrap.sh https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/linux/brain_bootstrap.sh
echo "<HASH>  brain_bootstrap.sh" | sha256sum -c - && sudo bash brain_bootstrap.sh <SHA>   # ends with a "ssh-ed25519 ..." line
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
goldbot accounts set deadman-url              # the Healthchecks.io ping URL ("Outside alert" in section 6)
sudo -u goldbot nano /opt/goldbot/config/settings.local.yaml
#   auth: {owner_email: <you>}
#   telegram: {allowed_user_ids: [<your id>]}
```
then `sudo -u goldbot chmod 600 /opt/goldbot/config/settings.local.yaml`, and the backup keys as in 0.6.

Start everything (supervisor first):
```bash
sudo systemctl start goldbot-supervisor goldbot-api goldbot-telegram goldbot-news goldbot-scheduler
sudo systemctl start goldbot-engine@icm-demo
goldbot run health
```
`systemctl status goldbot-engine@icm-demo` shows a service; `journalctl -u goldbot-engine@icm-demo -f` follows its
log. If the bridge is unreachable the engine exits and restarts every 10 s; it trades nothing meanwhile, and open
positions keep their broker-side stop and target.

#### Bridge smoke (BACKLOG 1: one real 0.01-lot round trip, demo only)
Proves the brain can trade the demo terminal through the bridge: it prints the login (masked, `****123`), server,
balance and a live XAUUSD tick, then, after **your one click**, buys the minimum lot with a stop and target about
$1 away and closes it at once. It refuses anything but a demo account (exit 2).

Before you start, check all of these:
* the market is open: not the weekend, and not the daily break at 00:00 server time (21:00 UTC until 25 Oct,
  22:00 UTC after it);
* the MT5 box runs exactly the brain's commit: on the MT5 box run `sudo goldbot-deploy <sha>` with the commit the
  brain runs, never `latest`. Its bridge refuses the smoke's order unless the terminal reports a demo account. The
  smoke refuses (exit 4) unless both commits are known and equal;
* the engine has run once on this account: `state/risk_icm-demo.json` exists (the day start and high-water mark the
  limits are judged by);
* `state/classifier_icm-demo.json` shows `"class": "raw"` or `"standard"`. While it is missing or `unknown` the
  RiskGate refuses (exit 7) until a nightly `nightly_costs` run classifies the account (it runs every night while the
  class is unknown), unless the account declares `account_class` in `config/accounts.yaml` (icm-demo: `raw`), which
  is then the class from the first run;
* the supervisor is running (`systemctl is-active goldbot-supervisor` says `active`);
* no deploy is waiting for you: do **not** approve a Telegram *Deploy* until the smoke has finished.

Then, on the brain, inside `tmux` (so a dropped SSH connection cannot kill it mid-order):
```bash
tmux new -s smoke
sudo systemctl stop goldbot-engine@icm-demo     # the engine only: KEEP the supervisor running
sleep 160                                       # the smoke waits for the engine heartbeat to be 150 s old
goldbot run bridge-smoke icm-demo               # approve the card on Telegram or the dashboard within 90 s
sudo systemctl start goldbot-engine@icm-demo    # always start the engine; on exit 5 or 6 close the position first
```
Always start the engine again at the end. On exit 5 or 6 a smoke position may still be open, so close it first
(below), then start the engine.
Once it prints `sending BUY`, never press Ctrl-C and never close the SSH session until it prints its exit line: the
order may already be at the broker, and only the smoke itself looks for it and closes it. While the engine is stopped,
expect the health alert for the engine loop (`engine_loop:icm-demo` FAIL) on Telegram; it clears once the engine is
started again.

Paste the output on issue #34 (it never shows the full login). What each exit code means, and what to do:
* **0**: opened and closed. Nothing to do.
* **1**: failed with nothing left open. Either the broker rejected the order, or the fill's stop or target was
  missing or not what was sent (`closed, unverified`) and it was closed at once. A Python traceback before
  `sending BUY` also exits 1; then nothing was sent. Read the last lines and re-run once the cause is fixed.
* **2**: the terminal is not the demo account of `icm-demo`. Nothing was sent.
* **3**: not approved (no click within 90 s, or you rejected it). Nothing was sent.
* **4**: a precondition failed and nothing was sent. The usual causes: the engine is still running or its heartbeat
  is under 150 s old (wait and re-run); `state/risk_icm-demo.json` is missing (start the engine once); the calendar
  archive is empty (the scheduler's `calendar_archive` jobs, daily at 06:10 UTC and Sunday at 18:00 UTC, have not
  fetched the week yet); the supervisor is down (start it); another smoke holds the lock; the MT5 box runs another
  or an unknown commit; no bridge endpoint in the keyring.
* **5 LEFT OPEN**: a smoke position could not be closed after three tries (Telegram says so too). Close it
  **before** restarting the engine: re-run the smoke (it closes a leftover smoke position first, then stops if it
  cannot), or open the terminal over VNC (0.3) and close the position with magic 260199 by hand.
* **6 UNKNOWN**: the order call failed and no fill was found within 25 s (it is never sent twice). Look for a
  position with magic 260199: re-run the smoke (it closes a leftover first) or use VNC (0.3), and close it before
  restarting the engine.
* **7**: the RiskGate refused; the reasons are printed. Common ones: `account_class_unknown` (see above),
  `bridge_incompatible` (update the MT5 box), a halt, a cap, stale data, the news blackout.

The smoke's position is listed apart in the broker ledger (`smoke_positions`), not as a missing trade.

### 0.5 Daily use
Only your Mac's browser (the dashboard) and Telegram (the one-click Approve).

**Morning digest.** Every day at 06:45 UTC (before London; change it with `telegram.digest_at: "HH:MM"` in
`config/settings.local.yaml`) Telegram sends one short message:
* **status:** ✅ all good, or ⚠️ attention with the failing and warning health checks named (details: `goldbot run health`);
* **yesterday** (the trading day that ended at 22:00 UTC, 23:00 UK in summer; the same day the month total uses):
  proposals approved / rejected / expired, trades closed with net $ and R, the month so far, open positions now. A
  trade closed after 22:00 UTC counts on the next day. Saturday's digest shows Friday. Sunday's and Monday's say
  "Weekend: market closed", because Sunday evening's trades count on Monday and show in Tuesday's digest;
* **risk:** drawdown stage and % from peak, daily and weekly loss against the caps (these still reset at 00:00
  UTC), and any halt (yours, drift, supervisor, drawdown);
* **research:** the next pre-registered trial or the quarter's trial budget, and the best and worst attribution cell
  that is not noise;
* **for you:** approvals pending, the number of owner decisions open (see OWNER_GUIDE), and a deploy on offer.

It is sent once a day. If the Telegram service was down at 06:45 it sends the digest when it starts, if that is
within 6 hours; later than that it skips the day. A file it cannot read shows as "could not read" on that line
and the rest still arrives. It never contains account numbers or emails.

* **A deploy is done only when you can open the dashboard at its address and sign in** (sections 2.7 and 2.10 are part of it).

**Updates: one click on Telegram, or by hand.** Nothing is ever deployed without you.
* When a new version is merged and has passed CI, Telegram shows *New version ready* with the list of changes and
  **[Deploy] [Skip]**. Deploy restarts the services within a minute (never while an entry waits for your click; open
  positions keep their broker-side stops and the engine reconciles on start), checks every service is still running
  after 90 s, and **rolls back by itself** if not. Telegram then says *deployed* or *rolled back* with the reason.
* The MT5 box (terminal + bridge, rarely changed) is updated by hand only: `sudo goldbot-deploy latest` on it when a
  release note mentions the bridge.
  Its Windows-Python packages come only from `goldbot/ops/linux/mt5-bridge-requirements.txt`: exact versions and
  sha256 hashes rendered from `uv.lock` (`python scripts/bridge_requirements.py write`; CI fails when it is stale),
  wheels only. A deploy reinstalls them when `uv.lock` or that file changed. An MT5 box set up before this file
  existed (unpinned packages, maybe pydantic older than 2.12) should be brought onto it once, by hand, **with the book
  flat or at the weekend** (restarting the bridge cuts the engine off from the terminal for a moment):
  1. `sudo systemctl stop goldbot-bridge@icm-demo` (one unit per account; stop each). Wine cannot replace a
     pydantic_core file the running bridge has loaded, so a running bridge can leave a half-installed package.
  2. `sudo -u mt5 -H env WINEDEBUG=-all DISPLAY=:99 wine /home/mt5/.wine/drive_c/users/mt5/AppData/Local/Programs/Python/Python311/python.exe -m pip install --require-hashes --no-deps --only-binary :all: -r Z:/opt/goldbot/goldbot/ops/linux/mt5-bridge-requirements.txt`
     (the Windows Python by its full path, never a bare `wine python`, which runs any `python.exe` in the current
     directory first; a hash mismatch stops it with nothing changed: do not work around it, report it, it means a download was not
     what uv.lock recorded)
  3. `sudo -u mt5 -H env WINEDEBUG=-all DISPLAY=:99 wine /home/mt5/.wine/drive_c/users/mt5/AppData/Local/Programs/Python/Python311/python.exe -c "import pydantic, pydantic_core, MetaTrader5, goldbot.execution.bridge; print(pydantic.VERSION)"`
     must print 2.12 or newer and no error.
  4. `sudo systemctl start goldbot-bridge@icm-demo` (each account). Wait about 20 s, then
     `systemctl is-active goldbot-bridge@icm-demo` must print `active` and `systemctl show -p NRestarts
     goldbot-bridge@icm-demo` must not have gone up (the unit restarts itself every 10 s, so `active` alone can hide a
     crash loop). Finally run preflight on the brain: it checks the bridge answers through the tunnel.
* The Linux units set `GOLDBOT_ALLOW_PLAINTEXT=1` (the file secret store is deliberate there: no desktop keyring).
  `goldbot-deploy` does not install units: after this change, reinstall them (copy `goldbot/ops/linux/systemd/*` to
  `/etc/systemd/system`, `daemon-reload`, restart). Until then nothing breaks for reads of existing secrets; only a
  new `accounts set` from a unit environment would be refused.
* By hand on either VM: `sudo goldbot-deploy latest` (or a specific commit id). Same checks, same rollback.
  Run it inside `tmux` (`tmux new -s deploy`; after a dropped connection, `tmux attach -t deploy`), or as
  `sudo systemd-run --wait --pty --unit goldbot-deploy-manual /usr/local/sbin/goldbot-deploy <args>`. The script
  ignores a hang-up and keeps going either way,
  but only then do you see its last lines. `/var/lib/goldbot-deploy/deploys.jsonl` (and Telegram) is the record of
  what happened; the end of the terminal output can be lost.
* **What ran before runs after (incident 2026-10-20).** Before it stops anything, goldbot-deploy records the units
  that are active. One unit that fails to start never keeps the others down. A rollback starts only those units, and
  after any rollback (successful or not) starts each of them again that is not active; the record (and Telegram) ends
  with `running: ...; not running: ...`. A unit that was not active before (for example a newly enabled
  `goldbot-engine@<account>`) is not started by a rollback. Such a new engine is started last, without waiting, and
  judged on its own: if it is down because its bridge is not ok (`bridge:`, `terminal:` or `bridge_compat:<account>`
  in `goldbot run health`), the deploy stays and reports *deployed_degraded* with `engine@<account> blocked by its
  bridge (<check>)`: log that MT5 box in (section 0.3b), then `sudo systemctl restart goldbot-engine@<account>`. If
  its bridge is ok, the deploy rolls back (its journal says why: `journalctl -u goldbot-engine@<account> -n 100`). An
  engine that was running before and is not after still rolls the deploy back. If a record says `failed` with
  `not running: <units>`: `systemctl status <unit>` and `journalctl -u <unit> -n 100` for each, fix the cause, then
  `sudo systemctl start <unit>`; check `goldbot run health` is back to its pre-deploy state.
* **Going back by hand:** `sudo goldbot-deploy --rollback <older commit id>`. It accepts only an older commit of the
  one that runs, on `main`, that passed CI or ran on this box before (`/var/lib/goldbot-deploy/deploys.jsonl`). Same
  lock, same wait for a pending entry, same restart order and 90 s check. If the rollback is unstable, the version
  that ran before is restored. Telegram reports *rolled_back_manual* (or *rollback_reverted*); the `deploy` health
  check warns until the next deploy. A pending Telegram deploy approval is cancelled by the rollback. Telegram offers
  the newer version again: tap **Skip** until the fix is merged. After a rollback the health check
  `schema_skew:<account>` may warn: the older engine is reading state the newer one wrote and ignoring its new
  fields (it names the files). That is expected after a rollback. If it **fails**, an approve/reject click was
  dropped because the older release could not read it; that proposal expired unapplied. Roll forward (or approve
  again on the next card). The failure turns into a warning an hour after the refusal (the warning keeps its time);
  another refused click fails and alerts again. The counts live in the running engine: a restart clears them, but
  the engine rewrites `engine_<account>.json` only on a tick or a bar, so health keeps showing the old block until
  the first tick after the restart (over a weekend: the Sunday open). Every other service (supervisor, scheduler,
  telegram, news, api) reports the same way as `schema_skew:svc:<service>` (from `heartbeat_<service>.json`,
  refreshed every minute, so a restart clears it within a minute). `schema_skew:svc:telegram` matters most: the
  Telegram service carries the owner's decisions. A scheduler warning means the older release is ignoring a newer
  job-control field in `aux.db` (for example a pause): check `job:<name>` and roll forward before the job's next
  slot if that field matters. A heartbeat from a release before this check carries no block and is skipped. "block unreadable: the
  process could not build it" means the summary itself failed in that service (a bug, traceback once in its log):
  the skew of that process is unknown until it is fixed; file an issue and restart the service.
* **After a rollback below `04f6266`, once you have rolled forward again: remove the decider identities.** A
  release older than `04f6266` (BACKLOG 52) writes who decided into every proposal it archives in
  `approvals/done/*.json`: `decided_by` (a Telegram user id) and `decided_via` (`telegram:<id>` or
  `dashboard:<email>`). It also writes them into `control.json`. The newer release drops those fields when it reads
  a file and keeps only the channel. It rewrites `control.json` on its next halt, re-arm or mode change, but it never
  rewrites a done file, so the identities stay on disk. List the affected files with
  `sudo -u goldbot grep -rlE '"decided_(by|via)"' --include='*.json' /opt/goldbot/state/approvals/done`. If any are
  listed, scrub them in place with the block below. It is safe while the services run. It removes only the two
  fields and keeps the channel, the outcome, `decided_ts` and the file's modification time, which the dashboard's
  recent list and the daily digest read. Run the `grep` again: no output means you are done. **Do not delete done
  files to get rid of the identities.** They are more than history: the auto-mode evidence (decided proposals and
  their outcomes), the daily digest and the performance report all count them. The audit log (the audit table in
  `aux.db`) holds dashboard decisions with the user's email in every release. It holds Telegram decisions only
  from `3817db7`. So a Telegram decision made while the older release ran has no audit row, and after the scrub it
  is recorded only as "via Telegram". That is acceptable because only the allow-listed owner ids can decide on
  Telegram.

  ```bash
  sudo -u goldbot /opt/goldbot/.venv/bin/python - <<'PY'
  import json, os
  from pathlib import Path
  from goldbot.base import load_record_json, write_atomic
  from goldbot.telegram.approvals import Proposal
  for f in sorted(Path("/opt/goldbot/state/approvals/done").glob("*.json")):
      text = f.read_text()
      d = json.loads(text)
      if not {"decided_by", "decided_via"} & d.keys():
          continue
      st = f.stat()
      d["decided_channel"] = load_record_json(Proposal, text, source="approvals/done").decided_channel
      d.pop("decided_by", None)
      d.pop("decided_via", None)
      write_atomic(f, json.dumps(d))
      os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
      print("scrubbed", f.name)
  PY
  ```
* **After a rollback below `e98eec8`, once you have rolled forward again: reset the classifier's `last_known`.**
  From `e98eec8` the account classifier keeps `last_known` in `state/classifier_<account>.json`: the last Raw or
  Standard class it stored. A stored *unknown* adopts Raw on a single Friday run only when `last_known` is not
  Standard. An older release (the Friday `nightly_costs` re-run) keeps the field when it rewrites the file but never
  updates it, so if it stored Standard while it ran, `last_known` can still say Raw. Back on the newer release, the
  first Friday that stores Raw or Standard corrects it; until then, a stored unknown could be relaxed to Raw on one
  run instead of two. To close that gap, remove the field: the newer release then re-derives it on its next read
  (the stored class when it is Raw or Standard; for a stored unknown, Standard if any kept run was Standard).
  Nothing else in the file changes. Do not run it during Friday's 23:10 `nightly_costs` job.

  ```bash
  sudo -u goldbot /opt/goldbot/.venv/bin/python - <<'PY'
  import json
  from pathlib import Path
  from goldbot.base import write_atomic
  for f in sorted(Path("/opt/goldbot/state").glob("classifier_*.json")):
      d = json.loads(f.read_text())
      if "last_known" in d:
          del d["last_known"]
          write_atomic(f, json.dumps(d, default=str))
          print("reset", f.name)
  PY
  ```

  Doing nothing is also acceptable: the gap closes at the next Friday that stores a known class.
* Before anything is touched, every deploy and rollback also checks:
  * **aux.db.** A target from before schema compatibility (no `compat_back_to` in its `goldbot/db/migrations.py`)
    cannot open aux.db at schema 5. The script stops every service (inputs, supervisor, engines last), steps aux.db
    back 5 -> 4 as the service user with every row kept, then starts them (supervisor first). Any other version the
    target does not know is refused, with nothing changed. The next forward deploy re-applies 5.
  * **settings.local.yaml.** It is validated with the target's own `Settings`. A key the target does not know (for
    example `risk.min_lot_risk_cap` on code from before the October batch) would stop every service, so the deploy
    is refused and the message names the keys, never their values. Edit the file, or add
    `--drop-unknown-local-keys`: unknown keys are moved aside and the original is kept as
    `config/settings.local.yaml.<UTC time>.bak`. Put them back after the next forward deploy. An invalid value is
    never dropped. A `risk_day_anchor` or `feature_day_anchor` other than the fixed day is invalid:
    see "Settings refuse to load: a day anchor" in section 6.
  * **Scheduler jobs.** While a job runs, a manual deploy is refused and a Telegram approval waits. The scheduler is
    stopped first and checked again. `--interrupt-jobs` deploys anyway (that job's slot is not re-run).
  * Only `enabled` units are restarted, so a stray, disabled engine instance is never started.
* **Rollback floor.** `/etc/goldbot/rollback-floor` (optional, root-owned, one commit id): `--rollback` refuses any
  commit older than it. Set it after a deploy that must never be undone (for example the auth hardening,
  docs/deploy/2026-10-batch.md 8.1). Removing it is your decision. Both the box's git copy and GitHub (compare API)
  must confirm that the target descends from the floor. When GitHub cannot confirm (offline, rate limit), the
  rollback is refused with "GitHub can't confirm the floor"; to go ahead anyway, remove the floor deliberately
  (`sudo rm /etc/goldbot/rollback-floor`), roll back, and set it again.
  **Recommended:** once the release with ADR 0010 ("persisted state is read tolerantly") is deployed and healthy,
  set the floor to its commit. From that release on, an older version reads files a newer one wrote and ignores
  fields it does not know. A version from before it rejects them: it can skip closed trades, hide pending
  proposals, or fail to start the scheduler, shadow book or registry. You decide whether to set the floor.
* **Health compared with a baseline (brain).** Before the switch the script saves `goldbot run health --out
  logs/health_before_deploy.json` (as goldbot). After the 90 s check it runs it again (`logs/health_after_deploy.json`)
  and compares. If a check got worse (ok -> warn, warn -> fail, or a new warn/fail), the result is
  `deployed_degraded` with the check names, also on Telegram, and the `deploy` health check warns. There is no
  automatic rollback for this: exits keep running, and you decide (`goldbot run health`, then `--rollback` if needed).
  Checks that need the market open look the same before and after when it is closed.
* The health check `deploy` shows the last result.
* **goldbot-deploy installs Python packages from `uv.lock`** (`uv sync --frozen`, when `pyproject.toml` or
  `uv.lock` changes) from the release that brings that change. A box bootstrapped at or after that release already
  has it. An older box keeps the old root copy (`uv pip install`, unpinned) until you reinstall it with the
  hash-checked steps below. Do that before its next deploy.
* **Installing a new goldbot-deploy** (when a release changes `goldbot/ops/linux/goldbot-deploy.sh`, after the
  security reviewer signed it off). Root runs it, so root never takes it from `/opt/goldbot`:

  1. **On your Mac** (your own clone, not the box): check the commit is on GitHub's `main`, then note the script's hash at the
     commit you deploy.
     ```bash
     git -C <your goldbot clone> fetch -q origin && git -C <your goldbot clone> show <SHA>:goldbot/ops/linux/goldbot-deploy.sh | shasum -a 256
     git -C <your goldbot clone> merge-base --is-ancestor <SHA> origin/main && echo on-main
     ```
     Without `on-main` (or if `git show` says the commit is unknown), stop.
  2. **On the box**, root downloads that commit's copy from GitHub into a root-only folder, checks it against the
     hash from step 1, and installs it only if they match (`sha256sum -c` prints `OK`, otherwise nothing is installed):
     ```bash
     sudo install -d -m 700 -o root -g root /root/goldbot-deploy-new
     sudo curl -fsSL -o /root/goldbot-deploy-new/goldbot-deploy https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/linux/goldbot-deploy.sh
     echo "<HASH FROM STEP 1>  /root/goldbot-deploy-new/goldbot-deploy" | sudo sha256sum -c - \
       && sudo install -m 755 -o root -g root /root/goldbot-deploy-new/goldbot-deploy /usr/local/sbin/goldbot-deploy
     sudo rm -rf /root/goldbot-deploy-new
     ```
     Never install it from `/opt/goldbot` or from `sudo -u goldbot git show`. The service user can write that tree and
     its `.git` (`git replace` can swap the file), so a hash computed there proves nothing.
* **Release (Mac): `scripts/release.sh "<PR title>" <body-file>`** on `claude/gifted-goldberg-lcb7pl`. It merges
  origin/main, runs ruff + mypy, pushes, opens the PR with auto-merge, stages the commit on the brain, runs
  `scripts/ci_local.sh` beside GitHub's checks (GitHub is the gate), deploys the merge commit as soon as it merges
  (its tree is the tested PR head's, which the brain's CI check verifies itself), and checks the deploy record and
  the engine. Logs in `~/tmp/release/`.
* **Fast deploy (staging, adaptive window, watcher).** `sudo goldbot-deploy --stage <sha>` (run by `scripts/release.sh`
  right after the push, over SSH) pre-builds a commit under `/opt/goldbot-stage/<sha>` as the service user in a
  512 MB cgroup (`MemoryMax`; an out-of-memory kill ends the build, never the engine): it warms the uv cache and
  builds `web/dist`, with a manifest. It touches no service, is idempotent, refuses a non-sha, a non-brain box and a
  commit that is not a fast-forward of what runs, and the two newest stages are kept. The deploy reuses a stage only
  when its web tree equals the target's and the files hash to the manifest, else it builds as before (`staging
  failed` is harmless). Measured 2026-10-19 (b5b6c107): stable with fresh heartbeats after 34 s, engine down 3 s
  (was about 2 min 20 s with the fixed 90 s window).
  Health passes once every daemon has been active and un-restarted for 30 s, each service with a heartbeat
  (supervisor, scheduler, telegram, news, api, webhook, tv-pull) has written a fresh one since its restart and
  `supervisor.json` is new (engines are judged by "active, not restarted", they write only on ticks). A restart in
  the first 90 s rolls back: before the report at once, after it by `goldbot-deploy-watch-*` (check
  `journalctl -u 'goldbot-deploy-watch-*'` and the last line of `deploys.jsonl` 90 s after a deploy). A manual
  rollback and the recovery after a failed deploy still wait the full 90 s. Restart order: supervisor, engines, the
  rest. Failure modes: stage dir full (`rm -rf /opt/goldbot-stage/*` as the service user, then `git worktree prune`),
  watcher missing (`systemd-run` failed: the health checks still alert within 6 minutes). First use needs the new
  root script installed with the hash-checked steps above.
* **If a deploy says `refused`:** the detail says why and nothing was changed. **If it says `failed`:** the rollback
  did not recover every service. Run `journalctl -u 'goldbot-*' -n 200` and `goldbot run health`, then start the
  units by hand (supervisor first). For aux.db errors, see docs/deploy/2026-10-batch.md 5.1.

#### Attended MT5 box upgrade (Sat 24 Oct, 10:00 UK, owner on VNC)

One checked script, `goldbot/ops/linux/mt5-box-upgrade.sh`, runs the plan in three phases and prints a PASS/FAIL
table of states only (never Config/ contents, a login, a host or a token). Each phase can be run again. It refuses
unless the market is closed (Friday from 22:30 UTC, Saturday, Sunday until 21:00 UTC), no engine unit is active on the
box, and you attest the book is flat and the engine is stopped (the box cannot see the brain's engine or the
account's positions itself). Run every step inside `tmux new -s upgrade`. `<SHA>` is the approved commit on `main`
and `<HASH>` the script's hash (step 2); both go in the deploy note. Root runs the script, so it never runs from
`/opt/goldbot`, which the mt5 user can write.

1. **Brain: stop the engine and check the book is flat.** `is-active` must print `inactive`, and the dashboard (or
   Telegram) must show no open positions. If a position is open, stop here; do not close it by hand to make the test possible.
   ```bash
   sudo systemctl stop goldbot-engine@icm-demo && systemctl is-active goldbot-engine@icm-demo
   ```
2. **Mac: check the commit and note the script's hash.** Without `on-main`, stop.
   ```bash
   git -C <your goldbot clone> fetch -q origin && git -C <your goldbot clone> merge-base --is-ancestor <SHA> origin/main && echo on-main && git -C <your goldbot clone> show <SHA>:goldbot/ops/linux/mt5-box-upgrade.sh | shasum -a 256
   ```
3. **MT5 box: download it into a root-only folder and check the hash** (`sha256sum -c` must print `OK`).
   ```bash
   sudo install -d -m 700 -o root -g root /root/mt5-upgrade && sudo curl -fsSL -o /root/mt5-upgrade/mt5-box-upgrade https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/linux/mt5-box-upgrade.sh && echo "<HASH>  /root/mt5-upgrade/mt5-box-upgrade" | sudo sha256sum -c - && sudo chmod 700 /root/mt5-upgrade/mt5-box-upgrade
   ```
4. **Precheck** (plan step 1: market closed, book flat, engine stopped, `Config/` backed up). All rows must be PASS.
   ```bash
   sudo /root/mt5-upgrade/mt5-box-upgrade precheck <SHA> --book-flat --engine-stopped
   ```
5. **Install** (plan steps 2 to 5): `goldbot-deploy <SHA>`, then the four units, the watchdog/probe scripts and the
   terminal unit's AutoTrading script (`/usr/local/libexec/goldbot/mt5-experts-enable.sh`, 0.3b B4) from
   root's own clone at exactly `<SHA>`, then it stops the transient `goldbot-mt5-terminal-check`, kills the stray
   terminal, enables `goldbot-mt5-terminal@icm-demo` and leaves the watchdog timer **off**. It records the previous
   commit in `/var/lib/goldbot-mt5-upgrade/previous-sha` (root-only) and the boot id. The last row says `run: sudo reboot`.
   ```bash
   sudo /root/mt5-upgrade/mt5-box-upgrade install <SHA> --book-flat --engine-stopped
   ```
6. **Cold restart** (plan step 6). Have the VNC tunnel ready (0.3) and note the time. Wait 3 minutes, then log in again.
   ```bash
   sudo reboot
   ```
7. **Post-reboot** (plan steps 7 and 8). It checks that the machine really rebooted, that xvfb, the terminal and the
   bridge are active, that exactly one `terminal64.exe` runs, that `/health` is `ok` and that the bridge's restart
   count stays steady for 30 s. Only if every row passes does it enable `goldbot-bridge-watchdog@icm-demo.timer` and
   write the rollback floor (`/etc/goldbot/rollback-floor`) = `<SHA>`. A floor that already holds another commit is
   never overwritten (the row FAILs; change it deliberately).
   ```bash
   sudo /root/mt5-upgrade/mt5-box-upgrade post-reboot <SHA>
   ```
   The pass criteria of "Cold-restart test of the saved login" still apply on top: within 10 min of the reboot, on the
   brain `bridge:icm-demo` and `terminal:icm-demo` are ok, and Telegram showed the down alert within 6 min and then
   the recovery. Its steps 4 to 6 (kill test, wineserver owner, hardening check, write-up) follow.
8. **Clean up and resume.** Delete the download and, once you no longer need it, the `Config/` copy; then start the
   engine on the brain. Record unit states and times in the deploy note (no login, server name or host).
   ```bash
   sudo rm -rf /root/mt5-upgrade && sudo -u mt5 -H rm -rf /home/mt5/config-backup
   ```
   ```bash
   sudo systemctl start goldbot-engine@icm-demo
   ```

**If the cold-restart test fails** (any FAIL row in step 7, or no pass within 10 min). The script has changed nothing
more: the watchdog timer is still off and no floor is set, so a rollback is allowed. The market is closed until Sunday
22:00 UTC and the engine stays stopped, so there is time; do not hurry.
1. Read why, states only: `sudo journalctl -u goldbot-mt5-terminal@icm-demo -u goldbot-bridge@icm-demo -b -n 100 --no-pager`
   and `systemctl status goldbot-mt5-terminal@icm-demo goldbot-bridge@icm-demo --no-pager`.
2. `not_logged_in`: `goldbot-mt5 login icm-demo` (0.3 "Later logins need no VNC"). `wrong_account`, or the command
   fails: log in over VNC (0.3), or restore the `Config/` copy:
   ```bash
   sudo systemctl stop goldbot-mt5-terminal@icm-demo && sudo -u mt5 -H cp -a /home/mt5/config-backup/Config/. /home/mt5/.wine/drive_c/MT5/ICMarkets/Config/ && sudo systemctl start goldbot-mt5-terminal@icm-demo
   ```
   Then run step 7 again (reboot once more first if the test is to be repeated from cold).
3. More than one `terminal64.exe`, or a unit stuck:
   ```bash
   sudo systemctl stop goldbot-bridge@icm-demo goldbot-mt5-terminal@icm-demo && sudo pkill -u mt5 -f 'terminal64[.]exe'; sudo systemctl start goldbot-mt5-terminal@icm-demo
   ```
   then step 7 again.
4. Still failing after 30 minutes, or the terminal cannot be made to log in: go back to the previous version. This
   disables the terminal unit, puts the previous bridge unit back from root's own clone at the recorded commit, rolls
   the code back (accepted because no floor is set) and restarts the bridge, which starts the terminal itself, as before:
   ```bash
   P=$(sudo cat /var/lib/goldbot-mt5-upgrade/previous-sha) && sudo systemctl disable --now goldbot-mt5-terminal@icm-demo && sudo env P=$P bash -c 'set -e; [ ${#P} = 40 ]; d=$(mktemp -d /root/goldbot-ops.XXXXXX); trap "rm -rf -- $d" EXIT; git clone -q --filter=blob:none --no-checkout https://github.com/engraamirkhan/goldbot.git $d/r; git -C $d/r merge-base --is-ancestor $P origin/main; git -C $d/r checkout -q --detach $P; install -m 644 -o root -g root $d/r/goldbot/ops/linux/systemd/goldbot-bridge@.service /etc/systemd/system/; systemctl daemon-reload' && sudo goldbot-deploy --rollback $P && sudo systemctl restart goldbot-bridge@icm-demo
   ```
   Then `systemctl is-active goldbot-bridge@icm-demo`, `curl -s 127.0.0.1:8765/health` and, on the brain, preflight.
   Keep the engine stopped until `/health` shows `"terminal": "ok"`. Open a postmortem in `docs/incidents/` and
   tell the program director within 24 h what failed and the new date.

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
at the first warning; it also warns when a backup took 10 min or more, because restic cancels a backup near 20 min
under the brain's no-delete key, ADR 0002), `restore_drill` (fail when the drill fails) and `backup_prune` (warn when the monthly
retention run from your Mac is older than 45 days).

**Object Storage size (`object_storage`).** After each nightly backup (also a failed one) the brain sums every object
version in the bucket, previous versions included, through the bucket's S3-compatible API signed with the restic
keys already in the keyring (no new credential; nothing to install), and writes `state/object_storage_size.json`.
Health warns at 15 GB and fails at 19 GB of the 20 GB free tier (`backup.storage_warn_gb`, `storage_fail_gb`);
Telegram tells you at the first warning. A missing, older-than-48-h or unreadable reading shows "size unknown" as a
warning, never OK (usually the restic keys cannot list the bucket: run `python -m goldbot.ops.storage_size` once, it
prints the size or the error class).
When it warns on size: nothing is lost yet; run the monthly retention from your Mac (`scripts/backup_retention.sh`);
previous versions then leave after the 30-day expiry (ADR 0002), so usage falls about a month later. If it fails,
tell the sre the same day: the next backup could be refused.

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
   Lifecycle rules only run when Object Storage itself may act on the bucket. Add this service policy once (in
   *Identity -> Policies*, in the root compartment):
   `Allow service objectstorage-<region> to manage object-family in tenancy where target.bucket.name='goldbot-backup'`.
   Use your region identifier, for example `objectstorage-eu-frankfurt-1`.
3. Two users, two groups, two keys (*Identity -> Domains -> Default -> Users / Groups*):
   * **Brain writer** (user and group `goldbot-backup-writer`): add and read objects, no delete. Policy:
     `Allow group goldbot-backup-writer to read buckets in tenancy where target.bucket.name='goldbot-backup'` and
     `Allow group goldbot-backup-writer to manage objects in tenancy where all {target.bucket.name='goldbot-backup', request.permission!='OBJECT_DELETE'}`.
     restic also tries to remove its own lock files. With no delete right those removals fail, so every backup
     leaves a lock behind (restic prints `error in cleanup handler: ... 403`, and the backup still succeeds). The
     brain's read-only commands (restore, drill, `check`) therefore run with `--no-lock` and are never blocked by
     those locks. Your Mac's monthly run clears them (ADR 0002 update 2026-10-16). The no-delete condition is **mandatory**: in
     Oracle the same delete right also removes old versions, so versioning alone does not protect backups from a
     compromised server. If the first `goldbot run backup` below fails on a permission error, stop and ask; the
     fix is an append-only backup target (restic rest-server), never dropping the condition.
   * **Retention** (user and group `goldbot-backup-retention`): full object rights on the bucket, used only from
     your Mac: `Allow group goldbot-backup-retention to manage objects in tenancy where target.bucket.name='goldbot-backup'`.
   On each user: *Customer secret keys -> Generate secret key* (`goldbot-restic-brain`, `goldbot-restic-retention`).
   Copy each **secret** (shown only once) and **access key**. The brain gets only the writer key.
4. In your password manager, generate a long random **restic password** and save it there *first*. Without it no
   backup can ever be restored, and the brain is the only other place that holds it.

**What was set up on 16 Oct 2026 (by the program director, with the OCI CLI from the Mac).** These are the same
objects as steps 1-3, created on the command line. No identifiers are recorded here: the namespace, OCIDs and email
addresses stay in the Oracle console.
* The bucket `goldbot-backup` (Standard tier, private), with Object Versioning enabled.
* A lifecycle rule that deletes **previous object versions** after 30 days, and the `objectstorage-<region>`
  service policy that lets that rule run (step 2).
* Two identity-domain users, `goldbot-backup-writer` and `goldbot-backup-retention`. Identity-domain users must
  have an email address, so each got a plus-addressed form of the owner's email. Nothing is sent to them, and they
  have no console password.
* Two groups of the same names, one user in each.
* One policy, `goldbot-backup-access`, holding the step 3 statements: the writer group may read the bucket and
  manage its objects with the condition `request.permission!='OBJECT_DELETE'`; the retention group may manage
  objects.
* A customer secret key for the **writer** only, stored on the brain as `restic-s3-access-key` /
  `restic-s3-secret-key`. The **retention key is not created yet**. Make it when you set up the monthly retention
  on your Mac (below): on the `goldbot-backup-retention` user, *Customer secret keys -> Generate secret key*, then
  put the two values straight into your Mac's Keychain.

*A new policy takes several minutes to apply.* Until it has, the writer key gets `403 Forbidden`. On 16 Oct the
first `goldbot run backup --init` failed with a 403 on a HEAD request for an object that did not exist yet (restic
checking that no repository is there). Wait 5-10 minutes and run it again. Do not loosen the policy: the same
command succeeded unchanged once the policy had propagated.

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
Do not run it between 22:00 and 23:30 UTC (the nightly backup) or on Sunday between 09:45 and 11:00 UTC (the drill).
Its prune deletes data the brain may be reading at that moment. The brain's backup then fails, or the drill fails
loudly, but nothing is lost. If that happens, run the brain's job again afterwards. The script's first step,
`restic unlock`, clears the locks the brain's key could not remove (every lock older than 30 minutes).

**If the drill or a restore says `repository is already locked`:** the brain is running a goldbot version from
before 16 Oct 2026, whose drill took a lock. Deploy the current version; leftover locks need no action. Do not run
`restic unlock --remove-all` on the brain, and never give the brain's key the delete right.

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
| `data-dukascopy.yml` | Saturdays 03:17 UTC (current year), or by hand (inputs `from_year`/`to_year`, or `months` for a targeted refetch, section 6) | Downloads Dukascopy ticks, builds 1m bars, publishes Parquet files to the release `data-v1`. | Issues `data coverage <year>` (label `data-coverage`); failures as `data-dukascopy failure for <year>` (labels `ci`, `data`). |
| `data-macro.yml` | Tuesdays 04:41 UTC, or by hand | Downloads the FRED macro series (10y real yield, breakeven, broad dollar, gold VIX, 2y yield) and publishes `macro_fred.parquet` to the release `macro-v1`. | Failures as issue `data-macro failure` (labels `ci`, `data`). |
| `data-positioning.yml` | Saturdays 05:23 UTC, or by hand (input `from_year` re-downloads COT from that year) | Downloads COMEX gold Commitments of Traders (CFTC) and GLD holdings (SPDR archive) and publishes `positioning.parquet` to the release `positioning-v1`, each row with a conservative `available_utc`. If the GLD archive is blocked, the run stays green, keeps the published GLD rows and says `GLD SOURCE UNAVAILABLE` in its summary. | Failures (COT download or upload) as issue `data-positioning failure` (labels `ci`, `data`). |
| `research.yml` | By hand only (Actions -> research -> Run workflow; inputs `specialist`, `from_year`, `to_year`, `rationale`, `macro`) | Walk-forward research for one specialist family (`session_open`, `mean_reversion`, `trend`, `breakout`) on the `data-v1` bars; with `macro` ticked, adds the macro features from `macro-v1`. Keeps the trial registry on release `research-v1`. | Issue `research: <specialist>` (label `research`). |
| `ci.yml` | Every push, pull request, or by hand | Pre-commit, backend lint/types, unit (6 shards) and integration (10 shards), frontend lint/types/unit, API contract, browser end-to-end tests, all in parallel (about 3 min; ADR 0035). Locally: `scripts/ci_local.sh` (at most `CI_LOCAL_MAX_JOBS`, default 12, lanes at once). | On a failed push, one issue `CI failure on <commit> (<jobs>)` labelled `ci`, with each failed job's output. |

The VPS does not depend on GitHub to trade. It uses GitHub only to refresh bars (Saturday retrain) and to share
the research trial registry, both through the `github-token` secret (section 2.3).

### Schedule (from `config/settings.yaml`, section `scheduler`)

Weekdays are Mon-Fri. A job missed while the VPS was down is run once on restart if it is not older than its
`max_late_hours`; otherwise it is skipped and the skip is recorded.

| Job | When (UTC) | What it does |
| --- | --- | --- |
| `calendar_archive` | Daily 06:10 | Stores the Forex Factory week in the `calendar_events` table (feeds the news blackout). |
| `daily_review` | Daily 06:15 (before the 06:45 morning report) | Learning-ops review of the day before on the three learning hosts: costs per broker and session, fill rate, refusal reasons, volume against the ~290 a day plan, arm health (`breached`/`ok` only), data and clock integrity -> `state/aux/daily_review/<day>.json` (Mondays also `weekly-<sunday>.json` with up to 4 variant proposals for the research director). Retires an arm at its pre-registered H-61 stop (the engine stops its new entries within a minute; its open trades keep every exit) and refits a cost cell after 200 fills. Opens or comments on a `learning-ops` issue on an incident or two low-volume days, and comments once a day on #34, with the `github-token`; without it the text is in `state/aux/daily_review/outbox/`. Its two lines are in the morning report. By hand: `python -m goldbot.ops.run daily-review --date YYYY-MM-DD --dry-run`. Nothing to do. |
| `agents_presession` | Mon-Fri 06:30 | Macro and news analyst writes the pre-session briefing. |
| `nightly_costs` | Mon-Fri 23:10 | Builds each account's spread/slippage cost table `state\costs_<account>.json`, with the swap and commission the terminal reports (`state\broker_terms_<account>.json`, written by the engine every 6 hours); re-runs the account classifier (`state\classifier_<account>.json`) every night while the class is unknown and on Fridays once it is known. Until an account has been classified (1,000+ London/New York ticks logged) or declares `account_class` in `config/accounts.yaml` (icm-demo: `raw`), its engine opens no positions and the health check warns "account class unknown"; a measured class that disagrees with the declared one makes the stricter (Standard) win and the health check warns "declared raw, measured standard: check the account type"; on a Standard account the 15m families except session-open are off. |
| `limits_job` | Daily 23:05 (never after midnight UTC) | ADR 0031: decides the adaptive risk limits of each demo account with ceilings (`icm-demo`) for the next day, from all its demo trades and the shadow book since each model went live. Stored in `core.db` (`adaptive_state`, append-only `adaptive_changes`); the engine applies it at 00:00 UTC. Every change sends you a Telegram FYI (nothing to approve). Health check `limits_controller:<account>` fails when the last decision is more than 26 h old; the engine then trades the default limits. |
| `model_watch` | Daily 23:30 | CUSUM check on any newly promoted champion; restores the previous champion on an alarm. |
| `agents_daily` | Mon-Fri 23:45 | Data steward, risk officer, execution auditor. |
| `saturday_retrain` | Saturday 06:00 | Refreshes bars from `data-v1` and the macro series from `macro-v1` (a macro failure does not stop the retrain), decides waiting challengers (promote/retire), retrains new challengers. |
| `recalibrate` | Saturday 11:30 | Refits only the probability map of each champion and challenger on its recent shadow outcomes (every candidate, taken or not), bounded to +-0.05 per week; logged in `state\recalibration.jsonl`. Promotes nothing. |
| `tournament` | Saturday 12:00 | Population round: fitness, retirement, promotion to live, cloning, capital shares -> `state\agents.json`. |
| `agents_weekly` | Saturday 13:00 | Journal coach, improvement agent, research analyst. |
| `monthly_research` | First Sunday of the month 08:00 | Bounded label-grid research; summary in `state\research_<YYYY-MM>.md`. |
| `backup` | Daily 22:15 | Brain only: encrypted `restic` backup of the databases, state files, trial registry, models and the brain's own Parquet to Oracle Object Storage, append only (old snapshots are thinned monthly from your Mac to 14 daily, 8 weekly and 12 monthly); result in `state/backup_last.json` (section 0.6). |
| `restore_drill` | Sunday 10:00 | Restores the latest backup to a temporary folder and verifies checksums, database integrity, row counts and model checksums; result in `state/restore_drill_last.json`. |
| `broker_ledger` | Daily 23:25 | Read-only: each account's balance, equity, margin and currency, the deposits and withdrawals in the broker's deal history, the balance tied out against the day's deals, and every goldbot position compared with the broker by position id. Stored in `aux.db`; health check `reconcile:<account>` (section 6, "Broker reconciliation"). |
| `tick_record`, `tick_record_winter` | Daily 21:10 and 22:10 | Read-only, and only while gold is closed (New York's 17:00-18:00 daily break, which is 21:00 UTC in US summer time and 22:00 UTC in winter, or the weekend; the other slot skips): the broker's own XAUUSD ticks of the oldest closed server day not yet recorded (missed days within 7 are back-filled, one a run, and the weekend runs catch up) -> `state/ticks/<account>/<date>.parquet`; days older than `ticks.retain_days` (240) are deleted. Research data for the H-A holiday check; health check `ticks:<account>` (section 6, "Broker tick record"). |

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
| `tradingview-sessionid`, `tradingview-sessionid-sign` | `python -m goldbot.ops.accounts set tradingview-sessionid` (and `... set tradingview-sessionid-sign`) | `tv-pull` (2.7c) | The direct pull does not run; health `tv_pull` WARNs once it is enabled. The browser cookies `sessionid` and `sessionid_sign`; they expire when TradingView signs you out. |

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

`--macro` also loads the macro series (release `macro-v1`, published since 2026-10-13 and refreshed every Tuesday
by `data-macro`) into the store; `macro 0` means nothing new since the last load. The engines do not use them yet;
research does. Add `--positioning` to load the
COT and GLD holdings rows (release `positioning-v1`, table `positioning`); it prints `positioning 0` until the
`data-positioning` workflow has run once. Research only (`research_pass.py --positioning`); the engines do not use them.

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

### 2.6b UI snapshots: before/after shots for every UI change (developer machine)

Every screen and tab, desktop 1440 and phone 390, light and dark, from the seeded e2e server (no real data). Run it
on the base branch and again on the change, and attach both sets to the review (ui-ux-designer Definition of done):

```bash
npm --prefix web run build && node scripts/ui_snapshots.mjs before
# ... the UI change, then:
npm --prefix web run build && node scripts/ui_snapshots.mjs after
```

Shots land in `~/tmp/ui-shots/<label>/<viewport>-<theme>/` (nothing is written in the repo). `fold-*.png` is what
the owner sees on opening Home and Trades; the full-page shots stretch the page, so the phone's bottom bar shows
mid-page and the desktop rail stops at 900 px there (not a bug in a real browser). `UI_SHOTS_VIEWPORTS=desktop,phone,narrow`
adds 360 px; `PYTHON`, `UI_SHOTS_PORT` (default 8795) and `PW_CHROMIUM_PATH` as for the e2e tests.

### 2.7 Cloudflare tunnel (dashboard from your phone, no VPN)

1. In the Cloudflare Zero Trust dashboard create a tunnel and a public hostname for it; route the hostname to
   `http://localhost:8787`.
2. On the VPS, in an Administrator PowerShell: `cloudflared service install <tunnel token>`. The token is a
   secret: type it on the VPS only.
3. The dashboard enforces its own login (email, password, authenticator code) and roles on the public URL.

The tunnel only carries the dashboard. TradingView alerts go to the webhook service, which listens on 127.0.0.1:8443
only; see "TradingView alerts: reading your indicators" below.

### 2.7b TradingView alerts: reading your indicators

The webhook records every value your indicators plot, one alert per indicator per timeframe, so research and features can
use them (opt-in, filter features only until proven). Each bar becomes rows in the `tv_indicators` dataset; a bar whose
alert never arrives is simply missing (NaN in features). The alert is saved to `state/tv.db` before goldbot answers, and a
failed save answers 503 so TradingView retries.

1. On the Oracle brain, section 2.7d does this step (secret generated on your Mac and piped, never shown; Tailscale
   Funnel on `/tv`). Elsewhere: store the shared secret (hidden prompt): `python -m goldbot.ops.accounts set
   tradingview-webhook-secret`; start `goldbot-webhook`. It listens on 127.0.0.1 only; put your HTTPS reverse proxy (for
   example Tailscale Funnel, path `/tv`) in front. Keep the public address private. If the proxy cannot pass
   TradingView's address in `X-Forwarded-For`, set `GOLDBOT_TV_ENFORCE_IP=0` for the service: the secret then
   authenticates alone.
2. On the chart (XAUUSD, 15m and 1h first) create one alert per indicator: **Condition** = the indicator (or "Any alert()
   function call" if that is all it offers), **Trigger** = **Once per bar close** (never "once per bar", which sends
   half-formed bars), longest expiry, **Webhook URL** = your address, and this **Message** (every value is quoted because
   TradingView writes `NaN` for an empty plot, which is not valid JSON; use `plot_0` to `plot_19` as far as the indicator
   has plots, the Data Window lists them):

```json
{"indicator":"smc","version":"1.0","symbol":"{{ticker}}","tf":"{{interval}}","bar_time":"{{time}}","fired_at":"{{timenow}}","price":"{{close}}","plots":{"plot_0":"{{plot_0}}","plot_1":"{{plot_1}}","plot_2":"{{plot_2}}","plot_3":"{{plot_3}}","plot_4":"{{plot_4}}","plot_5":"{{plot_5}}","plot_6":"{{plot_6}}","plot_7":"{{plot_7}}","plot_8":"{{plot_8}}","plot_9":"{{plot_9}}","plot_10":"{{plot_10}}","plot_11":"{{plot_11}}","plot_12":"{{plot_12}}","plot_13":"{{plot_13}}","plot_14":"{{plot_14}}","plot_15":"{{plot_15}}","plot_16":"{{plot_16}}","plot_17":"{{plot_17}}","plot_18":"{{plot_18}}","plot_19":"{{plot_19}}"},"secret":"<the stored secret>"}
```

   Change `indicator` and `version` per indicator (they name the data: `smc@1.0`); type the secret into the alert
   yourself, never into chat or a file. Alerts need a TradingView plan with webhooks and two-factor sign-in on.
3. Check it: the dashboard endpoint `/api/tradingview` lists each indicator and timeframe with its last bar, alerts in the
   last 24 h, plots seen and gaps (a market-closed stretch also counts as missing bars).

4. History from a chart export (optional, lets research use the indicator before weeks of alerts have collected). On the
   XAUUSD chart at the timeframe you want (15, 60, 240 or 1D), with the indicator visible and the chart scrolled back to
   load all its bars, open the **chart's top-bar menu next to the layout name** (the ... or cloud menu) and choose
   **Export chart data...** (time format ISO, all indicator plots ticked). Copy the file to the brain with one line from
   your Mac (placeholders: your file, your brain's SSH name), then import it on the brain with one line:

   ```
   scp ~/Downloads/<file>.csv <brain-ssh-name>:~/goldbot/data/
   ~/goldbot/.venv/bin/python -m goldbot.ops.run tv-import ~/goldbot/data/<file>.csv --indicator smc --version 1.0 --tf 15
   ```

   `--indicator` and `--version` must be spelled exactly as in the alert message; `--tf` is 15, 60, 240 or 1D. The
   import refuses a file with no `time` column, times out of order, spacing that is not whole bars of that timeframe, a
   bar in the future or a cell that is not a number, and writes nothing then. Repeating it is harmless. Imported bars are
   marked `unvalidated (repaint risk)`: a chart export can differ from what the indicator showed live. After about
   three weeks of live alerts (at least 200 bars both ways), export again, import again, and run
   `~/goldbot/.venv/bin/python -m goldbot.ops.run tv-repaint-check --indicator smc --version 1.0 --tf 15`; it
   prints the match per plot and marks the history validated only at 99 % or more. `/api/tradingview` shows each
   indicator's imported bars and its repaint status.

Limits: an invite-only script exposes only the plots and `alert()` calls its author allows. A series that is drawn
(lines, boxes, labels) or computed but not plotted cannot be read, and a plot set to `display.none` may be missing from
the alert; check each indicator once. Data stays in `data/` and `state/` (private), never in a public release.

### 2.7c TradingView direct pull (your indicators without alerts; ADR 0030)

goldbot reads your saved TradingView layouts, finds every indicator on them with the inputs you saved, and pulls their
plots and the bars into `tv_indicators` from TradingView's own chart connection. It only reads: it never creates or
changes an alert, a layout or anything else on your account. This is not an official TradingView interface; you
accepted the terms risk (possible account suspension, loss of invite-only access) on 2026-10-18. Your one step:

1. The two browser cookies `sessionid` and `sessionid_sign` go into the secret store as `tradingview-sessionid` and
   `tradingview-sessionid-sign`. On the Oracle brain, section 2.7d step T1 copies them from your Mac's Keychain without
   the values appearing anywhere. Elsewhere: in a browser signed in to TradingView, open the developer tools,
   Application (Chrome) or Storage (Safari/Firefox) > Cookies > `https://www.tradingview.com`, copy each value and store
   it at the hidden prompt of `python -m goldbot.ops.accounts set tradingview-sessionid` (and `... set
   tradingview-sessionid-sign`).
2. Claude switches `tv_pull.enabled: true` in `config/settings.local.yaml` and installs the unit (not part of the
   bootstrap, not installed by `goldbot-deploy`): section 2.7d steps T2 to T5. `tv-pull follow` runs a pass after every
   15m bar close; `tv-pull sync` runs one pass; `tv-pull status` shows the last one (bars received per timeframe, new
   bars, repaints, studies) and reaches no network.

One puller per TradingView account, and that is the brain. Two passes on one host never overlap (`state/tv_pull.lock`:
the second is skipped with "another tv-pull pass is running"), but a pull on another machine cannot be seen: never run
`tv-pull follow` on the Mac, and run the Mac probe (`scripts/tv_local`) only by hand, once, outside :01-:05, :16-:20,
:31-:35 and :46-:50 UTC (the brain's pass minutes). Its requests are not counted in the brain's daily budget
(`tv_pull.max_requests_per_day`).

When TradingView signs the session out, health shows `tv_pull` WARN and you get one Telegram notice: sign in again in
the browser, then repeat step 1 (2.7d T1 on the brain; a new cookie is tried at once). Alerts (2.7b) keep working
whatever the pull does.

### 2.7d TradingView on the Oracle brain: rollout, checks, rollback (Claude runs it)

Order matters: each unit is enabled only after what it needs is in place, because an enabled unit that crash-loops
(no secret, `tv_pull.enabled: false`) makes every later `goldbot-deploy` fail its 90 s health window and roll back.
Both units are restarted by every deploy from then on, but their unit files are copies: when a release changes
`goldbot-tv-pull.service` or `goldbot-webhook.service`, repeat its `install` line (T4 / W3). Root installs a unit only from its own fresh
clone of the deployed commit `<SHA>` (public: the commit `goldbot-deploy` last recorded as deployed in
`/var/lib/goldbot-deploy/deploys.jsonl`), never from `/opt/goldbot`, which the service user can write
(docs/AGENT_STANDARDS.md "Root-run tooling"), and only a full 40-character commit on `main` (forks share GitHub's object store, so a commit id alone does not prove it is ours). Every command that touches
goldbot's state runs as the `goldbot` user (the `goldbot` wrapper does that): a root-run pull would leave root-owned
`.lock` files and partitions under `/opt/goldbot/data` that the services then cannot write.

"Mac:" lines run in a goldbot checkout on your Mac at the deployed release (`<checkout>`); "brain:" lines after
`ssh -F ~/.ssh/goldbot.conf goldbot-brain`. Your Mac may ask whether `python` may use the Keychain item: click Allow, never
Always Allow (that would let any later script run with that `python` read it without asking).
That and the two Tailscale sign-in/approval clicks (W4) are your only steps.

**Precondition.** The release that has `accounts set --stdin` / `export` and `state/tv_pull.lock` is deployed and
healthy (`goldbot run health`; the last line of `/var/lib/goldbot-deploy/deploys.jsonl`).

**TradingView pull**

* T1, Mac (secrets by pipe, never on screen, in argv, history or a log; `export` writes only into a pipe (never a terminal or a file), and an
  empty pipe stores nothing):
  `uv run --directory <checkout> python -m goldbot.ops.accounts export tradingview-sessionid | ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-brain goldbot accounts set tradingview-sessionid --stdin`
  then the same line with `tradingview-sessionid-sign` in both places. Each prints `stored ... in
  /opt/goldbot/state/.secrets.json (plaintext, owner-only file)`; the brain has no desktop keyring (2.3).
* T2, brain (switch it on; only appends when the file has no `tv_pull:` key yet, otherwise edit that block by hand):
  `sudo -u goldbot sh -c 'f=/opt/goldbot/config/settings.local.yaml; grep -q "^tv_pull:" "$f" || printf "tv_pull:\n  enabled: true\n" >> "$f"'`
  then check the settings still load (a typo stops every service at its next restart):
  `goldbot run tv-pull status` must print `tv_auth=...`, not a settings error.
* T3, brain (one pass by hand first, so a fault shows here and not as a crash loop):
  `goldbot run tv-pull sync` must end with `tv-pull: ok, ...` and per timeframe `N bars received`. A first pass
  backfills (up to 5 history pages per timeframe at one request per 5 s: a few minutes). `FAILED: ... cookie` means T1
  stored a stale cookie: sign in again in the browser and repeat T1.
* T4, brain (install and start the follow service):
  `sudo env S=<SHA> U=goldbot-tv-pull bash -c 'set -e; [ ${#S} = 40 ]; d=$(mktemp -d /root/goldbot-ops.XXXXXX); trap "rm -rf -- $d" EXIT; git clone -q --filter=blob:none --no-checkout https://github.com/engraamirkhan/goldbot.git $d/r; git -C $d/r merge-base --is-ancestor $S origin/main; git -C $d/r checkout -q --detach $S; install -m 644 -o root -g root $d/r/goldbot/ops/linux/systemd/$U.service /etc/systemd/system/; systemctl daemon-reload' && sudo systemctl enable --now goldbot-tv-pull`
* T5, brain, checks (right away, then after the next 15m close + 1 min):
  `systemctl is-active goldbot-tv-pull` = active; `systemctl show -p NRestarts --value goldbot-tv-pull` = 0;
  `goldbot run tv-pull status` shows a pass newer than T3 with `new` live bars; `goldbot run health` shows
  `heartbeat:tv-pull` ok and `tv_pull` ok; `journalctl -u goldbot-tv-pull -n 20` has `tv pull ok` lines and no
  traceback.

**Webhook (alerts, 2.7b)**

* W1, Mac, once (a new 256-bit secret straight into your Mac Keychain; the Keychain is its system of record, so a
  rebuilt brain gets it back with W2). First check none exists, since a new one breaks every alert already set up:
  `uv run --directory <checkout> python -m goldbot.ops.accounts export tradingview-webhook-secret | wc -c` prints 0
  with `refused: ... not stored here` when there is none (a length, never the value). Then:
  `uv run --directory <checkout> python -c "import secrets; print(secrets.token_urlsafe(32))" | uv run --directory <checkout> python -m goldbot.ops.accounts set tradingview-webhook-secret --stdin`
* W2, Mac (copy it to the brain):
  `uv run --directory <checkout> python -m goldbot.ops.accounts export tradingview-webhook-secret | ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-brain goldbot accounts set tradingview-webhook-secret --stdin`
* W3, brain (the unit needs the secret before it starts: without it the service restarts every 5 s):
  `sudo env S=<SHA> U=goldbot-webhook bash -c 'set -e; [ ${#S} = 40 ]; d=$(mktemp -d /root/goldbot-ops.XXXXXX); trap "rm -rf -- $d" EXIT; git clone -q --filter=blob:none --no-checkout https://github.com/engraamirkhan/goldbot.git $d/r; git -C $d/r merge-base --is-ancestor $S origin/main; git -C $d/r checkout -q --detach $S; install -m 644 -o root -g root $d/r/goldbot/ops/linux/systemd/$U.service /etc/systemd/system/; systemctl daemon-reload' && sudo systemctl enable --now goldbot-webhook`
  then `curl -s http://127.0.0.1:8443/health` prints `{"ok":true,"stored":...}`.
* W4, brain (the public HTTPS address; Tailscale may already be installed: `tailscale status`).
  If not: the three apt lines of docs/deploy/first-deploy.md section 3 A step 2, whose `sudo tailscale up` prints a sign-in link:
  you open it and sign in (your click). Then:
  `sudo tailscale funnel --bg --set-path /tv http://127.0.0.1:8443/tv`
  The first time it prints an approval link to enable Funnel on your tailnet: you open it and approve (your click).
  `tailscale funnel status` then shows `https://<brain>.<tailnet>.ts.net/tv`. That address stays private: in your
  Terminal and TradingView only, never in GitHub, chat or a file in the repository.
* W5, checks from outside (owner-verified done): from the Mac,
  `curl -s -o /dev/null -w '%{http_code}\n' -X POST -d '{}' https://<brain>.<tailnet>.ts.net/tv` prints `403`
  (Funnel reaches the app; your Mac is not a TradingView address). Then create the alerts (2.7b step 2): the Webhook URL
  is that address; for the message's `secret`, put the secret on the clipboard with
  `uv run --directory <checkout> python -m goldbot.ops.accounts export tradingview-webhook-secret | pbcopy; (sleep 45; pbcopy </dev/null) &`
  and paste it in place of `<the stored secret>` within 45 s (the clipboard then empties itself; run the line again
  for the next alert). A clipboard-history app (Raycast, Alfred, Maccy, Paste) keeps every copy: pause it first, or
  delete that entry afterwards; Universal Clipboard may also have passed it to your other Apple devices for a few
  minutes. `export` writes only into a pipe (a terminal or `> file` is refused), but it cannot tell a pipe into a
  printing command (`| cat`) from one into ssh or pbcopy: pipe it into nothing else. After the alert's first bar close:
  `journalctl -u goldbot-webhook -n 20` shows `POST /tv ... 200`, and `goldbot run health` shows `heartbeat:webhook` ok.
  All `403` with "ip not allowed" means Funnel did not pass TradingView's address in `X-Forwarded-For` (not verified
  yet on this host): `sudo systemctl edit goldbot-webhook`, add `[Service]` and `Environment=GOLDBOT_TV_ENFORCE_IP=0`,
  `sudo systemctl restart goldbot-webhook`; the 256-bit secret then authenticates alone (2.7b).

**What is watched (and the promise)**

| Failure | Detection | Who hears | Recovery |
|---|---|---|---|
| `goldbot-tv-pull` or `goldbot-webhook` dead or hung | `heartbeat:tv-pull` / `heartbeat:webhook` silent > 5 min: WARN | Telegram at the next health poll (NOTIFY_ON_WARN) | `sudo systemctl restart <unit>`; `journalctl -u <unit> -n 50` |
| Cookie expired or signed out | `tv_pull` WARN (`expired`) | one Telegram notice per expiry | sign in again in the browser, T1 |
| Pull runs but never succeeds | `tv_pull` WARN after 36 h without a successful pass | Telegram digest | `goldbot run tv-pull status`: the reason |
| TradingView bars disagree with the reference feed | `tv_reconcile` (daily 23:15): bar flagged `dq`, plots NaN, `dq_events` row | `dq_events` (source tradingview), no alert | none needed (the bar is not used) |
| Two pulls on the brain at once | `state/tv_pull.lock`: the second pass is skipped, no request | the `sync` output | wait for the running pass |

These are WARNs, never FAILs: TradingView is an optional second feed and must never block trading or exits. Nothing in
the engine reads it. Both state files are covered by the nightly backup (`state/tv.db`, `state/tv_auth.db`); the secret
store is not (by design): after a restore, repeat T1 and W2 from the Mac.

Writers into `tv_indicators` (webhook sync, the pull, `tv_reconcile`) are safe together: each append holds the
partition's exclusive flock for its whole read-merge-write and readers take a shared one (goldbot/data/store.py);
`origin` is in the key, so an alert row and a pulled row of the same bar never replace each other; `tv_reconcile`
rewrites only pulled rows, on their own key; the webhook inbox and the pull log share `state/tv.db` (SQLite WAL,
busy_timeout 5 s).

Budgets to measure after the first day and record in the deploy note (targets, not yet measured):
`systemctl show -p MemoryCurrent --value goldbot-tv-pull goldbot-webhook` (target under 400 MB and 250 MB),
`du -sh /opt/goldbot/data/tv_indicators` per day (expected well under 50 MB/day), and
`goldbot run tv-pull status` requests per pass (the default budget is 3000 a day).

**Rollback** (each line independent; data already pulled stays, marked `pull_live`/`pull_history`, and is harmless):

* Pull: `sudo systemctl disable --now goldbot-tv-pull && sudo -u goldbot rm -f /opt/goldbot/state/heartbeat_tv-pull.json`,
  then set `tv_pull.enabled: false` in `settings.local.yaml`. To revoke the cookie itself, sign out of TradingView in the
  browser (that ends the session for every copy).
* Webhook: `sudo tailscale funnel --set-path /tv off`, then `sudo systemctl disable --now goldbot-webhook && sudo -u goldbot rm -f /opt/goldbot/state/heartbeat_webhook.json`;
  delete or pause the alerts in TradingView.
* Before any manual `goldbot-deploy` to a commit older than the release that brought `tv-pull`, disable
  `goldbot-tv-pull` first (that code has no `tv-pull` command: the unit would crash-loop and fail the deploy's health
  window).

### 2.7e The three indicators on the MT5 chart (requires owner approval; not installed)

**Status: requires owner approval; not installed.** Nothing copies these files to the MT5 box; the terminal and the
bridge are untouched. They are display aids only: goldbot's decisions keep coming from the Python code, never from
these plots.

`goldbot/ops/linux/mt5/indicators/` holds MQL5 ports of the three indicators goldbot computes in Python, with the
same inputs and defaults (pinned by `tests/test_mql5_ports.py`); each evaluates closed bars only, so nothing repaints:

| File | Mirrors |
|---|---|
| `goldbot_structure.mq5` | `features/smc_replica.py` structure (BOS/CHoCH), equal highs/lows, FVG, golden zone |
| `goldbot_sr_boxes.mq5` | `features/smc_replica.py` boxes and break-and-close, candle class; `features/box_course.py` next box |
| `goldbot_money_flow.mq5` | `features/smc_replica.py` money flow (tick volume) and `features/mfi_signals.py` signals |

They have not been compiled in CI (no MetaEditor there). Once the owner approves, on the MT5 box over VNC:

1. In MT5: File -> Open Data Folder; copy the three `.mq5` files into `MQL5/Indicators/goldbot/`.
2. Open each in MetaEditor (F4) and Compile (F7); fix any error in the repo first, never only on the box.
3. In the Navigator, refresh Indicators and drag each one onto the XAUUSD chart; keep the default inputs.
4. To remove: delete the indicator from the chart (the box rectangles go with it) and the files from the folder.

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
| You (the owner) lost the authenticator | No reset link works for the owner account (a stolen session must not be able to reset it). Login page -> **Use a recovery code** -> email, password and one unused recovery code. Enrol the new authenticator it shows; the old one stops working. Then get 10 fresh codes (`POST /api/auth/recovery/codes` with password + new code; there is no button yet). The old codes stop working. |
| You lost the authenticator and every recovery code, or the password and the authenticator | On the server: stop `goldbot-api` and ask a Claude session to reset the owner in `state\aux.db`. There is no remote path, by design. |
| A sign-in says "too many attempts" | Wait 15 minutes after the last failure; the counter is kept in `state\aux.db`, so restarting the API does not clear it. Someone who knows your email can keep it locked this way; the audit log shows the failures and their address. |
| Re-arm or auto mode says "too many attempts" (Telegram notice: wrong authenticator codes) | Five wrong codes lock re-arm and auto mode (dashboard and Telegram) for 15 minutes; sign-in, **/halt**, **/mode propose** and **/mode account** still work. If it was not you, someone holds a signed-in session: on the server, from the goldbot directory, run `python -m goldbot.ops.run revoke-sessions` (signs every account out; add `--state <dir>` if state is elsewhere: it refuses a directory without aux.db), then change your password. Failed sign-ins also lock re-arm and auto mode for the same 15 minutes. |
| Someone should lose access | Owner: **Users** -> **Disable** (signs them out at once) or **Sign out everywhere**; **Enable** restores the account. The owner cannot be disabled or demoted. |

| You (the owner) want to sign in without typing the password | Login page -> **Email me a sign-in link** -> your email -> **Send the link**. The page always says "If this address can use email sign-in, a link is on its way", whatever address is typed. Open the link on the device you want to sign in on, then enter the **authenticator code**: the link replaces the password, never the code. It works once, for 15 minutes; asking again or using a link cancels the others. At most 3 requests an hour per address and per network address. The option only appears once mail is set up (below). |

Every one of these is recorded in the audit log (`state\aux.db`, table `audit`; the state-files table in section 3 shows how to read it).

**Email sign-in link: one-time mail setup (owner only, optional)**

The link is sent with plain SMTP and STARTTLS. Gmail is free: it needs 2-Step Verification on the Google account and
an App Password (Google Account -> Security -> 2-Step Verification -> App passwords; name it `goldbot`; Google shows
16 letters once).

1. Store the App Password in the server's keyring (never in a file, an env var or the repository). On the brain
   `goldbot accounts set smtp-password`; on the Windows VPS
   `.\.venv\Scripts\python.exe -m goldbot.ops.accounts set smtp-password`. Paste it at the hidden prompt.
2. Add to the server's `config/settings.local.yaml` (not tracked by Git; placeholders shown, use your own values):
   ```yaml
   auth:
     owner_email: you@example.com
     mail:
       enabled: true
       host: smtp.gmail.com
       port: 587
       from_address: you@example.com       # the Gmail account the App Password belongs to
       dashboard_origin: https://dash.example.com   # your tunnel hostname, https only
   ```
3. Check it: `goldbot run mail-test` (VPS: `python -m goldbot.ops.run mail-test`) sends a test email to
   `auth.owner_email` and prints only `sent`, `not sent: <error type>` or what is missing. Then restart the API
   (`sudo systemctl restart goldbot-api`, VPS: `nssm restart goldbot-api`) so the login page offers the link.
4. If a link never arrives: the API log says `email sign-in link not sent: <error type>` (never the address or the
   link), and the audit table has `email_link_send_failed`. `SMTPAuthenticationError` means the App Password is wrong
   or revoked: store a new one (step 1); no restart is needed. If the audit table has `email_link_rate_limited`
   instead, the 3-an-hour limit was hit: wait an hour or sign in with the password. The `email_link` health check
   warns when that happens 3 or more times in 24 h. To turn the feature off, set `enabled: false` and restart the API.
5. Every sign-in with a link sends you a Telegram notice and an email ("new sign-in with an emailed link": time and a
   rough network such as `203.0.x.x`, never the link). If it was not you, your mailbox and your authenticator are
   both exposed: run `python -m goldbot.ops.run revoke-sessions` on the server (signs everyone out and cancels every
   open link), set `enabled: false`, then change your password and your email password. The accepted risk and its
   limits are in [ADR 0013](decisions/0013-email-sign-in-link-accepted-risk.md).

**Sign-in code by Telegram (owner only; no mail needed)**

The owner can sign in with the password and a 6-digit code sent to the goldbot Telegram chat instead of the
authenticator ([ADR 0028](decisions/0028-email-code-sign-in.md), amended 2026-10-17). Setup, once:

0. Store your Telegram user id (the same number as in `telegram.allowed_user_ids`; you can see it by sending `/start` to
   `@userinfobot`) in the server's keyring, never in a file or this chat: on the brain
   `goldbot accounts set telegram-owner-chat-id` (VPS:
   `.\.venv\Scripts\python.exe -m goldbot.ops.accounts set telegram-owner-chat-id`), paste the number at the hidden
   prompt (a prompt needs a terminal; piped input is not accepted), and restart the API (`sudo systemctl restart goldbot-api`, VPS: `nssm restart goldbot-api`). The bot token is the one the
   Telegram service already uses (`telegram-bot-token`). Turn on Telegram's two-step verification
   (Settings -> Privacy and Security) first: it is what protects this path. The chat must be your private chat with
   the bot (send it `/start` once); a group never works, and nothing is sent if the id is missing, not allowed or not
   a private chat (audit `code_channel_unavailable`). The login page then offers "Send me a code".
   `auth.code_channel: mail` switches back to the email path described in the previous section's steps 1-3.

The rest applies to either channel:

1. The code is checked with a secret key kept in the server's keyring as `email-code-hmac-key` (32 random bytes,
   created on the first request; never in `aux.db` or a backup). The VPS needs a real keyring: the
   `state/.secrets.json` fallback is for the brain only. If the keyring cannot be read, or the stored key is not
   32 bytes of hex, email codes stay off (the login page shows only the authenticator) and the API log says
   `email codes off: email-code-hmac-key ...`. To rotate the key (open codes stop working): brain
   `goldbot accounts set email-code-hmac-key`, VPS `.\.venv\Scripts\python.exe -m goldbot.ops.accounts set
   email-code-hmac-key`, paste 64 hex characters (for example from `python -c "import secrets; print(secrets.token_hex(32))"`),
   then restart the API.
2. A code works once, for 10 minutes; 3 wrong codes kill it; a resend (not before 60 s) cancels the old one. Mail
   shares the link's limit: 3 an hour per address and per network address. Wrong codes count towards the sign-in
   lockout.
3. After an email-code sign-in, **invite, reset link, role change, disable, enable and sign out everywhere** also ask
   for the authenticator code (or a recovery code, which is spent). Re-arm, auto mode, password change and new
   recovery codes ask for the authenticator as before. Approve, reject and halt never ask.
4. Three codes killed by wrong guesses, or one sign-in lockout, within 24 h **suspend email codes** (one Telegram
   notice; audit `email_code_suspended`): the page still answers but no email is sent. Sign in once with the password
   and the authenticator code to switch them back on (audit `email_code_resumed`).
5. Every Telegram-code sign-in sends a Telegram notice, writes the audit row `tg_code_login`, and shows a banner on the
   dashboard after every owner sign-in (time, a rough network such as `203.0.x.x`, last 7 days) with **Sign out
   everywhere** (after a code sign-in it asks for your authenticator code). If it was not you, someone has your
   password: sign out everywhere (or run `python -m goldbot.ops.run revoke-sessions` on the server), change your
   password. The code message is deleted from the chat once used, replaced or expired. Turn codes off at once (remove
   `telegram-owner-chat-id` from the keyring, restart the API) on any live-money incident or if Telegram two-step
   verification is off on your account (ADR 0028's revert trigger). With `auth.code_channel: mail` the notice is also
   emailed and the trigger is the Gmail account losing 2-Step Verification.

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
* **Warning: if the kill switch trips, every open position on the account is closed at market, including trades you
  opened by hand on other symbols** (any symbol, any magic; ADR 0016 amendment, BACKLOG 62). There is no prompt
  first: exits are never gated. A symbol with no live quote is retried until it closes. Pending orders you placed by
  hand are not cancelled; delete them yourself after a trip. Keep manual trades off this account if you do not want
  them closed.
* `/status` on Telegram shows whether entries are halted, the approval mode and how many proposals are pending.
* **Health `kill_switch:<account>`** (WARN on Telegram 5 min after a 12% trip, FAIL after 30 min of open market):
  the kill switch could not close every position yet; the engine re-sends each due close every tick. Check
  `engine:<account>` for a broker or quote fault, then close the listed positions by hand in the MT5 terminal. The
  check clears when the engine reads the whole account flat. Outside market hours it stays a WARN: the closes go out
  when the market opens.

### Demo risk limits (ADR 0032)

* `icm-demo` trades with higher limits than the defaults: daily loss cap 5% (and never more than the £50 daily limit),
  weekly cap 10%, and one 0.01 lot allowed when it risks at most 1%. These are the ceilings you approved on
  2026-10-18. They are set in config/accounts.yaml under `risk_overrides`, apply to demo accounts only, and cannot be
  set higher there.
* If the account drops 8% below its high-water mark (the size-down stage), it goes back to the default limits by
  itself and stays there, restarts included. The trade journal records `limits_reverted`, and the engine state file
  shows `limits_reverted: true`.
* To go back to the demo limits, wait until the account is out of size-down (the dashboard shows the stage as
  normal), then **re-arm** on the dashboard as above. A re-arm while the account is still in size-down or halted is
  refused (`limits_reset_refused`); re-arm again once the stage is normal.
* Live and tiny-live accounts never use these limits.

### Adaptive risk limits (ADR 0031)

* On `icm-demo` the approved values above are **ceilings**: the nightly `limits_job` (23:05 UTC) sets the limits for
  the next day between the defaults (daily 2%, weekly 5%, smallest-trade exception off) and those ceilings, from the
  account's own demo trades. They rise one small step at a time (after 60 demo trades, 3 good days in a row and 10 new
  trades; never within 5 days of a cut), only at 00:00 UTC, and they drop at once on a 4% drawdown, 4 big losses in a
  row, the size-down stage or weak results. The smallest-trade exception starts at 1% for the first 60 such trades
  and switches off for good if they lose 10R in total.
* What you see: a Telegram FYI on every change ("Limit change (demo, icm-demo): ..."), with the old and new values in
  £ and %, the reason and the time. Nothing to approve. The engine state file shows the current values
  (`limits_current`; its reason starts with `adaptive:`).
* If `limits_controller:icm-demo` FAILs in health (no decision for over 26 h): the account trades the default limits,
  which is the safe side. Check the scheduler (`job:limits_job` in health, `python -m goldbot.db scheduler-status`);
  the next 23:05 run decides again. A run after midnight is not caught up; the limits wait for the next night.
* The 8% revert (above) still wins: limits go back to the defaults until you re-arm.
* To switch it off and trade the fixed ceilings again: set `risk: { adaptive_limits: false }` in the server's
  `config/settings.local.yaml` and restart the engine.
* Replay (validation only, never a tuning step):
  `python scripts/replay_limits.py --account icm-demo --from 2026-10-20 --to 2026-11-30 --state state --models models`
  prints the decision each day would have had. It writes nothing. Each run is recorded in ADR 0031's replay log.
* Not yet measured: the cost/model rule (BACKLOG 90) cannot fire until a 20-day cost series exists.


### icm-demo learning host (ADR 0043 amendment 2026-10-26, ADR 0037 amendment 6)

* Since 2026-10-26 `icm-demo` is a second learning host: `probe_only: true`, the intraday and swing exploration arms,
  `max_positions: 18`. `icm-probe` keeps the scalp arms, the cost probes and the shadow book. Neither account trades a
  strategy, so the phase gate's paper clock gathers no new trades while this lasts; promotion evidence comes from the
  shadow book (account-independent).
* Rolling it out: deploy, restart `goldbot-engine@icm-demo` and `goldbot-engine@icm-probe`. Check `engine_icm-demo.json`
  shows `probe_only: true`, the dashboard lists both under the supervisor's probe engines (not in the combined limits),
  and the first `exploration:` decisions on icm-demo name only I/MB/MSA/W arms.
* **Re-role back to a strategy account** (Claude runs it; nothing here is the owner's except the TOTP of step 3):
  1. Trigger: the first promotion verdict (evaluator, H-61 "Promotion" step 5). Not before: the account has nothing
     to trade until then.
  2. On icm-demo set `exploration.enabled: false`, restart its engine, and wait until the account is flat (the
     exploration positions close at their own stop, target or hold; exits are never gated, nothing is closed by hand).
  3. While it is still `probe_only`: if the account is 8% or more below its high-water mark, run the X5 rotation (ADR
     0043 section 2; owner TOTP). X5 is not built yet: until it is, a mark at -8% or below blocks this step, and the
     re-role waits for it.
  4. Set `probe_only: false`, remove `max_positions` (both are refused at load off a probe host), and leave the
     intraday and swing arms either moved back to icm-probe or retired (no arm on two hosts).
  5. Open a new evidence epoch for icm-demo before any strategy fill counts: fills opened before the re-role date are
     quarantined in the limits evidence (`limits_job._quarantined`, today one global start in
     `research.promotion.EVIDENCE_START_UTC`: a per-account start is a code change, its own lane), and the phase
     gate's `paper_start` (`backtest_to_paper` in `state/phase_state.json`) is a phase-gate record: an owner-level
     decision, escalated with a recommendation, never edited here.
  6. Restart icm-demo's engine and check its engine file shows `probe_only: false` and entries come from the live
     agents again.

### Auto mode (entries without your click)

* By default every entry waits for your click (propose-and-approve). **Auto mode** means an entry that passes the
  RiskGate is placed without asking you. Every cap, stop and exit works exactly as before; only the click goes.
* **Demo accounts enter automatically** (ADR 0036, your 2026-10-18 instruction): `icm-demo` has `approval_mode: auto`
  in `config/accounts.yaml`, so its entries are placed without a click while the MT5 terminal reports a demo account.
  Telegram sends "Opened automatically (demo): ..." for each (information only, no buttons). Real-money accounts
  always wait for your click: `approval_mode: auto` on a live account stops the engine at start, and `/mode auto` or
  the dashboard never make a live account automatic. `/mode propose` also returns the demo account to asking you;
  the 12% kill switch and the 30-day window after a re-arm do too. To let the demo account enter automatically again
  after `/mode propose`: `/mode account` on Telegram, or "Use each account's setting" on the dashboard's entry-mode
  card (no code needed; real-money accounts keep asking you; ADR 0041).
* **Cost probes on demo** (ADR 0037): while `icm-demo` holds no position, the engine may open one 0.01-lot trade that
  copies a challenger's shadow entry, only to measure real costs (slippage, spread, latency, swap). One at a time,
  challengers taken in turn, never chosen by results; a real trade always comes first (an open probe is closed to make
  way). They pass the RiskGate and count toward the demo daily limit, but stop for the day once they have lost 20% of
  it (£10 of £50; `budget_share`) or less than 20% of it is left. Exits are automatic and no Telegram message is
  sent for them. In MT5 they show magic number base + 97 and a comment starting `cp-`. They never count as trades for the phase gate or the tournament: they are kept apart in
  `state\aux.db` (table `cost_probes`; the journal shows `cost_probe:` rows). To stop them: set
  `cost_probe: { enabled: false }` under `icm-demo` in `config/accounts.yaml` and restart the engine service;
  `/mode propose` or `/halt` stops them too. A live account with probes enabled refuses to load.
* **Moving probes to their own account `icm-probe`** (ADR 0037 amendment 4): probes then stop using icm-demo's £50
  day and the combined limits; icm-probe has its own £20 day and never sends a real trade. On the MT5 box, open the IC
  Markets GBP demo in its own terminal on its own box (`goldbot-mt5-probe`, `C:\MT5\ICMarkets\terminal64.exe`) with its bridge, and run
  `python -m goldbot.ops.accounts add icm-probe` (login, password and bridge keys go to the keyring, never the file).
  Then in `config/accounts.yaml`: under `icm-probe` set `enabled: true`; under `icm-demo` set `shadow_host: false` and
  `cost_probe: { enabled: false, ... }`; enable `goldbot-engine@icm-probe` and restart both engine services. Doing only the first edit makes every service
  refuse to load ("2 enabled accounts are the shadow host"). To go back, reverse the three lines.
* It is never switched on by itself. Telegram sends one message, "Auto mode can be enabled: evidence ...; reply
  /mode auto <TOTP>", only after at least 100 proposals you approved or rejected since the last mode change, with no
  RiskGate breach (no 8% size-down, no 12% kill switch) and no measurable difference between the outcomes of the
  trades you approved and the ones you rejected (your veto is not adding value). The message quotes the numbers.
* To switch on: `/mode auto 123456` with your current authenticator code (the dashboard one). It is refused if the
  evidence no longer holds or the code is wrong. Telegram deletes the message with the code.
* To switch off at any time: `/mode propose` (no code needed), or `/halt` to stop new entries altogether.
  `/mode account` (no code needed) hands entries back to each account's own setting: demo automatic, real money asks.
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
`state\agent_reports\<role>\<UTC time>.md`. Every run (cost, status) is logged in `state\aux.db` (table `agent_runs`)
and copied to `state\agent_runs.jsonl`.

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
| Scheduler job states | `state\aux.db` table `scheduler_jobs`; readable copy `state\scheduler.json` (also on the **Feeds** tab) |
| News feed health | `state\news_feeds.json` |
| Agent spend | `state\aux.db` table `agent_spend` (monthly; readable copy `state\agent_spend.json`), `state\news_spend.json` (headline scoring per day) |
| Telegram outbox (what was sent) | `state\aux.db` tables `outbox_sent`, `outbox_cursor` |
| Files as they were before the database took over | `state\pre-db\` (kept, never written again) |
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
   only: spread, slippage, commission and swap per lot, with `measured_at` and, per field, "measured", "prior" or
   (slippage only) "mixed", and the tick, fill or lot count behind it. After 10 recorded broker-tick days
   (`tick_record`) it also carries `spread_profile`: the spread's median and p90 by UTC hour and session, aggregates
   only. It never holds an account id, login, balance or equity, or a single tick. The upload is refused, and
   nothing is published, while the swap or the spread is unmeasured. Fills are not needed: until a session has 50
   fills its slippage is published as the prior and labelled so, never as a measurement. On a GBP account the swap
   and commission are converted to USD at the terminal's own rate (the note in `broker_terms_<account>.json` names
   it). To publish at once, or to check the file first:

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
nightly report explains them. `goldbot run health` shows the dashboard's banner for each enabled account on its
`entry_status:<account>` line (same builder as the dashboard and `/status`): ok while open or waiting (market closed,
a blackout, a cap that resets), WARN when only your halt blocks, FAIL for any other block. The dashboard, `/status`
and health show enabled accounts only (`config/accounts.yaml`): a disabled account's leftover
`state/engine_<account>.json` is ignored there, but the supervisor still reads it, so move it aside (e.g. to
`state/retired/`) once that account holds no open position.

Switching an account off: set `enabled: false` in `config/accounts.yaml` and restart its engine. With nothing of its
own open the engine refuses (`refusing to start <account>: enabled: false ...` in the journal, exit code 78, never
restarted: `RestartPreventExitStatus=78`, NSSM `AppExit 78 Exit`); with positions open it winds down
(`account_disabled` below) and refuses at the next restart once flat. Also run
`sudo systemctl disable goldbot-engine@<account>`: a deploy restarts every systemd-enabled engine unit and reports a
refused one as not started. A live account starts only when it is enabled and the paper -> tiny-live gate is recorded;
otherwise it refuses the same way, even with positions open.

The reasons (from `goldbot/risk/gate.py`) and what to do:

| Reason | Meaning | What to do |
| --- | --- | --- |
| `owner_halt` | You or an approver halted (`state\control.json`). An unreadable `control.json` also counts as halted. | Re-arm on the dashboard (owner + authenticator code). |
| `account_disabled` | The account is `enabled: false` in `config/accounts.yaml` but its engine had positions of its own open when it started, so it only winds down: no new entry, every exit (stop, target, time exit, Friday cut) runs as usual. The dashboard and health hide disabled accounts; the engine journal says `winding down`. | Nothing. Once flat, restart the engine (`sudo systemctl restart goldbot-engine@<account>`): it then refuses with exit code 78 and stays stopped. To trade again set `enabled: true` and restart. |
| `supervisor_halt` | The supervisor halted on combined limits (`combined_daily_cap`, `combined_weekly_cap`, `combined_drawdown_halt` in `state\supervisor.json`), **or** its heartbeat is missing/older than 60 s, or `supervisor.json` is unreadable. | Check `nssm status goldbot-supervisor` and the Feeds tab heartbeat; restart it if stopped. For a cap halt see the `daily_cap` row. |
| `drawdown_halt` | This account's equity fell 12% below its high-water mark. | Investigate before anything else. The dashboard Re-arm clears only the owner halt; the code has no command that clears this stage (an engine restart resets it, because the stage is kept in memory). |
| `news_blackout` | Within 15 min before to 30 min after a tier-1 event (CPI, NFP, FOMC, PCE), or 30 min after a high-relevance news shock. The active event is in `state\engine_<account>.json` under `blackout`. | Wait. If it never clears, check `calendar_archive` on the Feeds tab. |
| `stale_data` | The last tick is older than 20 s (either way: a tick stamped in the future counts too). | Check the MT5 terminal is open, logged in and connected; the Feeds tab shows "Last tick". |
| `tick_from_future` | The last tick is stamped more than 10 s ahead of this server's clock: wrong `server_tz` in a DST gap week, or a host or broker clock fault. | See "Server clock (US-EU DST gap weeks)" below. |
| `tick_error` | Processing a tick raised (a terminal or bridge fault, a full disk, a bug). The engine logs it (first of each type, then once a minute with a count) and carries on; exits run before anything that writes files or reads the account. Health fails `engine:<account>` while it blocks, and warns on Telegram (`tick_errors:<account>`) for 15 minutes after an error or when one happened since the previous health run. Clears when a whole decision bar closes with no error. | Read the engine log (`journalctl -u goldbot-engine@<account>` on the brain); if it repeats, restart the engine. Exits keep running. |
| `server_clock_mismatch` | The broker's measured UTC offset differs from `server_tz` by more than 5 min while gold trades (`server_clock` in `engine_<account>.json`). Clears after 60 s of agreeing ticks. | See "Server clock (US-EU DST gap weeks)" below. |
| `bridge_incompatible` | The MT5 bridge runs older or different code than the brain: it lacks a method the brain calls (e.g. `loss_per_lot`), the protocol versions do not meet each other's `min_compatible`, it converts with another (or an invalid) `server_tz`, or it does not report these at all (a bridge from before this check); also while the engine has not confirmed the bridge (`unchecked`: starting, never reached, or last confirmed over 5 min ago). Checked at engine start and every 60 s off the tick loop; `bridge_compat` in `engine_<account>.json` names what is missing. Exits are unaffected. | See "Bridge compatibility" below. |
| `market_closed` | The broker's session table (read once per server day; static hours if the read fails) says the market is closed, e.g. a holiday early close. | Wait for the reopen; `session_source` in `engine_<account>.json` says whether the broker's own hours are used. |
| `spread_too_wide` | Spread above 45 points ($0.45). | Usually rollover or news; wait. |
| `max_positions` | Two positions already open on this account. | Normal. |
| `daily_cap` / `weekly_cap` | This account lost 2% / 5% from its equity at the start of the risk day (00:00 UTC) / risk week (Sunday 00:00 UTC). | It clears by itself at the next boundary (`new_day`, goldbot/risk/gate.py:534); a restart does not clear it. Review the losses. |
| `negative_ev`, `target_below_cost_floor` | After costs the trade has no edge, or the target is below 2.5x the round-trip cost. | Normal filtering; check the cost table if it happens to every candidate. |
| `min_lot_exceeds_risk`, `margin_level_floor` | Minimum lot would risk too much, or margin level would fall below 300%. | Account too small for the stop; normal on small demos. On a small demo, 15m needs the minimum-lot exception: `risk: { min_lot_risk_cap: 0.01 }` in `config\settings.local.yaml` (never applies in tiny-live). |
| `fx_conversion_unavailable` | The account is not in USD and the terminal gave no USD -> account-currency conversion (`order_calc_profit` / tick value), or the terminal's symbol info was unreadable. | Check the terminal is logged in and XAUUSD is in Market Watch; entries resume when it answers. Exits are unaffected. |
| `daily_loss_limit_abs` | Today's loss plus this trade's full stop risk would exceed min(2% of the day-start equity, the account's `daily_loss_limit_abs` in `config\accounts.yaml`, e.g. £50 on icm-demo). Equal is allowed; the supervisor halts once the combined loss reaches the limit. | Normal; clears at 00:00 UTC. `engine_<account>.json` shows `daily_loss_limit` and `daily_loss_remaining`. |
| `supervisor_halt` with `mixed_account_currencies` | Running engines report different deposit currencies (e.g. GBP and USD); equities are never summed across currencies. Health names the files. | Stop the engine that should not run (or delete its leftover `engine_<account>.json`), see "Currency rollout" below. |
| `supervisor_halt` with `engine_startup_refused` | An engine's start failed (a refused terminal account, e.g. a bridge older than protocol v4, or an unreadable positions read): its `engine_<account>.json` carries `startup_error`, and its last-known exposure still counts. The `engine:<account>` health line names the cause. | Fix the cause and restart that engine (its first state write clears the halt), or retire that account: stop its engine (`sudo systemctl disable --now goldbot-engine@<account>`) and move `state/engine_<account>.json` aside (e.g. to `state/retired/`), only once it holds no open position. |
| `supervisor_heartbeat_future` | `supervisor.json` is stamped more than 5 s ahead of this server's clock (a clock step, a bad write, or a state backup restored from a box whose clock ran fast). It can no longer prove the supervisor is alive, so entries stop; a `ts` that is not a number reads as `supervisor_state_unreadable`. Health fails the `supervisor` line with how far ahead. | Check the host clock (`chronyc tracking` on the brain; preflight's `time_sync` line), then restart the supervisor service: its next write clears it. |
| `supervisor_halt` with `engine_heartbeat_future` | An `engine_<account>.json` is stamped more than 5 s ahead of the supervisor's clock: entries stop on every account. Health names the file on the `supervisor` line and fails that `engine:<account>` line. | Check the host clock, then restart that engine (its next state write clears the halt), or move a leftover file aside if that engine no longer runs. |
| `data_quality_error` | Reserved; the engine does not set it today. | - |

### Settings refuse to load: a day anchor

A deploy is refused with "settings.local.yaml does not validate with the target code (keys: risk_day_anchor)" (or
`feature_day_anchor`), and `--drop-unknown-local-keys` does not fix it (the key is known, its value is invalid). A
service started on such a file fails at start; its log shows one of:

```
risk_day_anchor
  Value error, risk_day_anchor is fixed at UTC 00:00 (got <tz> <time>): the loss caps reset at 00:00 UTC in code (timeutil.risk_day) and this setting cannot move that. Delete risk_day_anchor from config/settings.local.yaml.
```

```
feature_day_anchor
  Value error, feature_day_anchor is fixed at America/New_York 13:30 (got <tz> <time>): daily bars and features close at 13:30 New York in code (timeutil.feature_day) and this setting cannot move that. Delete feature_day_anchor from config/settings.local.yaml.
```

`<tz> <time>` is the value the server set. These two days are fixed in code (BACKLOG 57): a different value never
moved the loss-cap reset or the daily bar, it only looked as if it did, so it is now refused. **Fix:** on whichever host refused
(the brain, or the mt5 host, which runs the same check under ROLE=mt5 with ROOT=/opt/goldbot), delete the named key
(the `risk_day_anchor:` or `feature_day_anchor:` line, and its indented `tz:`/`time:` lines, if any) from
`config/settings.local.yaml` (`sudo -u goldbot nano /opt/goldbot/config/settings.local.yaml`), then re-run the deploy
(or, if a service failed at start, restart it). Do not edit `config/settings.yaml`. Moving the risk day (for example to the 22:00 UTC roll) is a code change with a
trading-safety review, not a setting.

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

### Missing days in data-v1: targeted refetch (`data-dukascopy`, input `months`)

A month can clear the 15,000-bar check and still have lost whole weekdays (the day-by-day table on the
`data coverage <year>` issue, from `scripts/check_coverage.py`, lists them). Neither the Saturday run nor
`retry_flagged` downloads such a month again. Refetch exactly the affected months with one dispatch:

```
gh workflow run data-dukascopy.yml --ref main -f months=2024-01,2024-06,2026-04
```

(or Actions -> data-dukascopy -> Run workflow, field `months`). What it does:

- `months` is comma-separated `YYYY-MM`, 2010-01 up to the current month, at most 24 per dispatch. Anything else
  fails the `plan` job before any download (`scripts/dukascopy_months.py`). `from_year`/`to_year` are ignored, and
  `full_refresh` or `retry_flagged` ticked together with `months` is refused.
- One `build-year` job per year in the list (two at a time). Each downloads only its listed months, flagged or
  not, and keeps every other month exactly as published. If the year has no published file, the job refuses
  and downloads nothing, so a year never loses its unlisted months.
- Each listed month is merged day by day (`merge_never_shrink`): a day is replaced only by one with more bars,
  or by an unflagged copy of a bid/ask-imputed day with the same number of bars. A throttled download can add
  or repair days; it never removes one.
- Cost: about 1-2 runner-minutes a month when Dukascopy answers, plus about 3 minutes per year job. When it
  throttles, up to about 80 minutes a month; a year job stops at 340 minutes and then publishes nothing
  (the published file stays as it was). If a year job times out, dispatch that year's months again in
  lists of 4 or fewer.

Afterwards, check the `data coverage <year>` issues: the day-by-day section should no longer list the repaired
days. A day that is still missing after two refetches is a gap in Dukascopy's archive; it stays listed
on the issue and is not imputed.

### US calendar: a type refused for mass cancellations

A `data-us-calendar failure` issue whose report says `REFUSED for this run (would cancel more future events than
allowed ...)` means one download would have cancelled more than 2 future events of one type. That is usually a
truncated download. It can also be a real schedule change, such as a shutdown or an agency moving a series. Nothing
is lost: the type's published rows are kept and its `last_ok` does not move.

1. **Verify the cancellations against the agency's own schedule** (BLS, Census, BEA, DOL or the Fed). Check that the
   listed dates really were cancelled or moved. If the next run's download is complete again, do nothing more.
2. **If the change is real, record it.** Either:
   - **Hand-check it (preferred).** Add each cancelled date to `MANUAL_EVENTS` in `goldbot/data/us_calendar.py`,
     with its event type, original date, cancellation time and source, in a reviewed PR. The next run then keeps
     history and records the cancellation exactly.
   - **Or apply it once.** Run the workflow once with `--allow-cancellations TYPE`: either a one-off
     `workflow_dispatch` edit, or locally with
     `python scripts/us_calendar_data.py --existing prev/us_calendar.parquet --allow-cancellations TYPE`, then upload
     the file. Every use is written to the step summary as an `AUDIT:` line naming the type and the number of
     cancellations. Never leave the flag in the scheduled run.

### Health check

`python -m goldbot.ops.run health` lists every check (settings, secrets present, disk, supervisor heartbeat, halt,
phase gates, each engine's state, stage and data quality, risk state, scheduler jobs, cost tables, news feeds,
agent spend, stuck approvals, backup age, the restore drill and each account's measured server clock offset) as ok / warn / fail with a one-line reason, and exits 1 on any fail. `--json`,
`--out FILE`, `--baseline FILE` (fail only on new fails) and `--static` (no running services needed) exist for the
update script. The Telegram service runs the same checks every minute and messages you when a check turns fail
and when it recovers (needs `telegram.allowed_user_ids` and the Telegram service running; the `alerts` check shows
when that loop has stopped). When the whole brain is down, the outside dead man's switch tells you instead ("Outside
alert" below; the `deadman` check says whether it is set).

### Order not confirmed

Health WARN "order(s) not confirmed by the broker" (BACKLOG 72): on the MT5 box, open the terminal and look in Trade for a
XAUUSD position with the engine's comment and in History for its deals; if it is open without a stop, set the stop the
notice names. The engine keeps looking and refuses new entries until it settles; the WARN clears by itself.

### Closed-trade record lost (`closed_records_lost`)

Health FAILs when a closed trade's record could not be written after its retries. An exploration close that is lost
counts against its arm's budget on its own day. After that day the arm counts as already traded (no first trade)
until the record is written, and the journal says `exploration_blocked_lost_record` once a day. Claude runs the repair
on the brain:

```bash
sudo systemctl stop goldbot-engine@icm-probe      # the engine only: keep the supervisor (bridge) running
python -m goldbot.ops.run repair-lost-record icm-probe --dry-run   # what it would write
python -m goldbot.ops.run repair-lost-record icm-probe             # writes the records, clears the lost list
sudo systemctl start goldbot-engine@icm-probe
```

It holds the account's engine lock while it runs: the engine will not start until the repair ends, and a
running engine makes the repair refuse. It writes each record from the broker's deals, marked `censored`. It names the ones it leaves: `not_exploration` (a
strategy's or cost probe's record, written by hand from the MT5 History), `no_exit_deal` and `deals_unreadable`
(try again later). It never places or closes an order. Exit codes: 0 = all repaired, 1 = some left, 2 = refused
(the engine is running, the account is unknown, or there is no bridge endpoint).

### Outside alert (dead man's switch)

Telegram alerts come from the brain itself, so a dead brain cannot report itself. A free check on Healthchecks.io
covers that: the Telegram service's health pass (every minute) pings it every `telegram.deadman_every_min` (2)
minutes while health has no FAIL, and pings `<url>/fail` (body: the failing check names, nothing else) on a FAIL or
when a Telegram alert could not be sent. Healthchecks.io emails you, or messages you on Telegram through its own bot,
when the pings stop or a fail ping arrives. It does not depend on this server. Code: `goldbot/ops/deadman.py`,
ADR 0014.

**Set up (once, 5 min; docs/deploy/first-deploy.md P7 and step 8b):**
1. healthchecks.io -> sign up (free Hobbyist plan) -> **Add Check**: name `goldbot brain`, Schedule **Simple**, Period
   **2 minutes**, Grace **3 minutes** (so a dead brain is reported within about 6 minutes). If you change
   `telegram.deadman_every_min`, change the Period to match.
2. Integrations: email is on by default; add **Telegram** (Healthchecks.io's own bot) if you want it on your phone too.
   Delete nothing else; the default integrations are enough.
3. Copy the check's **ping URL** (`https://hc-ping.com/<uuid>`). It is a secret: anyone with it can mark the check up.
   Keep it in your password manager only. brain: `goldbot accounts set deadman-url` and paste it at the hidden prompt.
   Never put it in config, a shell command line, the repository or an issue.
4. Within 2 minutes the check turns **up** on healthchecks.io, and `goldbot run health` shows `deadman` ok.

**Health `deadman` WARN:** "not set" -> step 3; "not an https:// URL" / "has no host" -> store the URL again, exactly
as copied. The WARN is not sent to Telegram (it shows in `/status` and the digest).

**Healthchecks.io says DOWN** (no ping within period + grace). Check in this order:
1. Can you reach the brain at all (`ssh brain`)? No: the host is down or unreachable. Oracle console -> the instance
   -> start or reboot it. Open positions keep their broker-side stop and target meanwhile; exits need the engine and
   the bridge, so restoring the brain is the priority.
2. `systemctl is-active goldbot-telegram`; not active -> `journalctl -u goldbot-telegram -n 50`, fix, then
   `sudo systemctl restart goldbot-telegram`. The pings come from this service, so a crashed Telegram service is
   reported here. That is intended: it is also your alert path.
3. Active, but no pings: `journalctl -u goldbot-telegram | grep "dead man"`. "ping failed: HTTP 404" -> the check was
   deleted or the URL is wrong (store it again). "URLError" or "TimeoutError" -> outbound HTTPS from the brain is
   blocked (Oracle security list or egress) or healthchecks.io is down. The log never prints the URL.
4. "health pass" exceptions in the same log: the checks themselves crash. No ping is sent then, by design.

**Healthchecks.io says DOWN after a fail ping:** health has a FAIL, or a Telegram alert failed to send. The ping's
body (Healthchecks.io -> the check -> Events) names the failing checks; run `goldbot run health` for the reasons. If
the body says `telegram_alert_undelivered`, Telegram rejected the alert: check the bot token and `allowed_user_ids`
("Telegram is silent" above). The check turns up again by itself at the first healthy pass.

**Rehearse** (after any change to the brain's alerting, and once a quarter): `sudo systemctl stop goldbot-telegram`;
expect the DOWN email within about 6 minutes; `sudo systemctl start goldbot-telegram`; expect UP within 2 minutes.

### Engine loop stuck, and the watchdogs (brain and MT5 box)

**What protects you.** Each engine's loop writes `state/heartbeat_engine-<account>.json` on every pass, at most
every 15 s, with or without ticks. It also pings the systemd watchdog. If the loop stops for 300 s, systemd ends the
engine with SIGABRT, which writes every thread's Python stack to the journal, and starts it again. The start
reconciles orders and positions with the broker, as after any restart. Stops stay with the broker the whole time,
and no exit waits on any of this. The start itself (bridge connect, warm start, model load) may take up to 15
minutes; a start that hangs longer is ended and started again.

**`engine_loop:<account>` FAIL** ("engine loop silent for ..."; alerted on Telegram after 150 s of silence, market
open or not):
1. `sudo journalctl -u goldbot-engine@<account> -n 300`. "Watchdog timeout" followed by a Python stack means systemd
   killed a stuck loop. Keep that stack: it is the root cause, and it goes into a postmortem in `docs/incidents/`.
2. Wait 5 minutes. After a watchdog restart the check goes WARN ("engine starting") and then ok. It needs no action.
3. If it is still failing, `systemctl status goldbot-engine@<account>`:
   - restarting every few seconds: the start fails. The `engine:<account>` line or `startup_error` in
     `state/engine_<account>.json` names the cause, which is often the bridge (`bridge:<account>`, see below);
   - "activating" for many minutes: a slow start. At 15 minutes systemd ends it and the check FAILs "still starting";
   - active but silent: `sudo systemctl restart goldbot-engine@<account>`. Restarting is safe (reconciliation on start).
4. **"engine start silent" or "still starting after ..."**: the same steps. A start never blocks exits, because
   positions it has not yet reconciled keep their stops at the broker.

**Bridge restarted by its watchdog (MT5 box).** The bridge runs under Wine, which cannot ping systemd, so
`goldbot-bridge-watchdog@<account>.timer` probes `GET /health` on 127.0.0.1:8765 every 2 minutes instead. After 3
failed probes 20 s apart it restarts `goldbot-bridge@<account>`. It skips the first 5 minutes after a start and any
unit restarted while it probed. When the brain shows `bridge:<account>` FAIL, or the engine restarts because of a
lost bridge reply:
1. On the MT5 box, `sudo journalctl -u goldbot-bridge-watchdog@<account> -n 50` shows whether it restarted the bridge
   ("did not answer GET /health 3 times; restarting it"), and `sudo journalctl -u goldbot-bridge@<account> -n 200`
   shows the bridge's own log.
2. If it keeps restarting, open the terminal over VNC (0.3). Check that it is logged in, that algo trading is
   enabled, and that no dialog is open.
3. `systemctl list-timers 'goldbot-bridge-watchdog@*'` shows that the timer is running. To pause it for maintenance,
   run `sudo systemctl stop goldbot-bridge-watchdog@<account>.timer`, and `start` it again afterwards.

The same watchdog also reads the terminal's state from `/health`. The probe runs as `nobody` and passes on a single
word, never the reply itself. The watchdog acts on that word alone:
- `not_running` or `not_connected` on all 3 probes, with the terminal unit up for at least 10 min: it restarts
  `goldbot-mt5-terminal@<account>`, which restarts the bridge with it.
- The bridge restarting 3 times without settling while the terminal has been up for 10 min: the terminal is hung and
  the bridge cannot attach to it, so the watchdog restarts the terminal unit.
- `not_logged_in` or `wrong_account`: it logs the state and never restarts anything. On `not_logged_in` the bridge's
  background self-heal thread (every 30 s) logs the terminal in from the keyring, never inside `/health` or an RPC,
  with the backoff, hourly cap and refused-password mark of 0.3. Each login takes at most 15 s; meanwhile `/health`
  answers within 2 s as `not_logged_in` with `"login": "in_progress"`, and RPCs get a prompt 503 (the engine sends a
  close again). `/health` also shows `"login": "refused"`, `"rate_limited"` or `"waiting"` when a login is held
  back. Never on `wrong_account`.
It restarts the terminal at most once every 10 min.

### Terminal down (`terminal:<account>`)

The terminal runs in its own unit, `goldbot-mt5-terminal@<account>`. That unit is the only thing that starts it. It
restarts the terminal 15 s after it dies (on Ubuntu 24.04 the wait grows to 10 min) and refuses to start a second
copy. The brain's `terminal:<account>` check reads the state from the bridge. In every state below, entries are
blocked, stops and targets stay with the broker, and nothing is lost by waiting:
- **"not running on the MT5 box; it is being restarted"**: normally clears within 1 to 2 minutes, because the unit
  starts the terminal again and the bridge re-attaches. If it still fails after 10 minutes, on the box run
  `systemctl status goldbot-mt5-terminal@<account> goldbot-bridge@<account>` and
  `sudo journalctl -u goldbot-mt5-terminal@<account> -n 100`. The line "a terminal64.exe already runs for mt5" means
  a terminal was started by hand. Stop that copy with `sudo pkill -u mt5 -f terminal64.exe`, then run
  `sudo systemctl start goldbot-mt5-terminal@<account>`.
- **"running but not logged in (saved login lost?)"**: the bridge logs the terminal in from the keyring by itself,
  to the configured login only, within about 30 s (its self-heal thread), and serves once it sees
  the right account. This includes a terminal at the "Select a company to open an account with" wizard: the bridge's
  attach fails there (`mt5.initialize failed: (-10005, 'IPC timeout')`) and it logs in from the keyring before
  attaching again. If it stays down, on the box run `goldbot-mt5 login <account>`: `ok` clears it; `failed: ...`
  names why (`no password stored`: `goldbot-mt5 accounts add <account>`; `password refused`: nothing is sent until
  you store it again with `goldbot-mt5 accounts set mt5-<account>`, see 0.3; `curl -s 127.0.0.1:8765/health` shows
  `"login": "refused"`; `refused: ...`: the login is not the registered account, see the last bullet; `-10005` at the wizard:
  `goldbot-mt5 login <account> --dismiss-wizard`, which presses Escape on the terminal window first, unverified).
  `sudo journalctl -u goldbot-bridge@<account> -n 100` shows the bridge's tries. Only if the command keeps failing:
  stop the watchdog timer (`sudo systemctl stop goldbot-bridge-watchdog@<account>.timer`), log in over VNC as in
  0.3, steps 1 to 5, ticking *Save password*, and start the timer again afterwards. If the bridge had already seen the right account before the login was
  lost, it keeps passing calls through: reads and closes keep flowing (exits are never gated), and the terminal
  answers what it can. Orders still fail the account check on every send.
- **"AutoTrading is off: the terminal refuses every order (10027)"** (`autotrading_off`): the terminal is on the
  registered account and connected, so reads and closes are served, but its AutoTrading (Algo Trading) button is off
  and it refuses every order and close with 10027 (the bridge refuses entries first, `terminal_autotrading_off`).
  goldbot cannot close anything meanwhile (time exits, Friday cut, kill switch): with open positions the check reads
  **CRITICAL** and only their broker stop and target protect them, so act at once. The watchdog logs it and never
  restarts for it. Restart the terminal unit; its start turns AutoTrading on (0.3b B4):
  `sudo systemctl restart goldbot-mt5-terminal@<account>` (the bridge restarts with it). If the token stays, the box
  predates the script: `sudo journalctl -u goldbot-mt5-terminal@<account> -n 20` shows no `AutoTrading:` line. Until
  its next attended upgrade (0.5) installs it, do it once by hand, with the watchdog timer stopped:
  `sudo systemctl stop goldbot-bridge-watchdog@<account>.timer goldbot-mt5-terminal@<account> && sudo -u mt5 -H bash /opt/goldbot/goldbot/ops/linux/mt5/mt5-experts-enable.sh && sudo systemctl start goldbot-mt5-terminal@<account> goldbot-bridge@<account> goldbot-bridge-watchdog@<account>.timer`
  (`/opt/goldbot` must be at a commit with the script: `sudo goldbot-deploy latest` first). The check then reads ok.
- **An entry refused `terminal_precheck_failed: order_check: none: (-2, 'Invalid "comment" argument')`**: the
  terminal's `order_check` rejected the order comment as over 29 characters (measured 2026-10-20, MetaTrader5 package
  5.0.6231 under Wine; 30 or more is refused, nothing is sent). goldbot sends at most 29 (`MT5_COMMENT_MAX`), so this
  means the box runs code from before that fix: `sudo goldbot-deploy latest`. Any other `order_check: none: ...` names
  the request field the terminal refused.
- **"not connected to the broker server"**: this is a warning for the first 5 minutes, then a FAIL. Check the box's
  network, then check the terminal's corner indicator over VNC. After 10 minutes the watchdog restarts the terminal.
- **"logged in to a different account than registered"**: while the terminal is on another account, the bridge
  refuses every call, reads and closes included. It never reads or closes positions on an account it was not set up
  for. Your positions on the registered account keep their stops and targets at the broker. Over VNC, log in to the
  registered demo account (0.3). Calls flow again on their own once it is back. Never change `config/accounts.yaml`
  to match the terminal.

### Cold-restart test of the saved login (MT5 box)

This tests that the terminal comes back logged in after a full reboot, with no help from you. Plan:
docs/proposals/2026-10-mt5-terminal-lifecycle.md section 2. Only run it when all of these hold: you can be reached
(Telegram, and the VNC tunnel ready), the market is closed, it is outside London and New York hours, the book is
flat, and the engine is stopped. Do not run it on a classifier Friday or a clock-change Sunday. The new units must be
installed by an approved deploy first.

**Back up `Config/` first.** It holds the saved login. Keep the copy on the MT5 box only, never off-box:
```bash
sudo -u mt5 -H sh -c 'umask 077; mkdir -p ~/config-backup && chmod 700 ~/config-backup && cp -a ~/.wine/drive_c/MT5/ICMarkets/Config ~/config-backup/'
```
Rules for this copy:
- It stays owned by `mt5` in that 0700 directory, which is outside `/opt/goldbot` and outside anything that is synced
  or backed up.
- Never copy it off the box: no scp, no upload, no cron job.
- Never paste its contents, or a hash of it, into an issue, a comment or a chat.
- Delete it (`sudo -u mt5 -H rm -rf ~/config-backup`) once the test has passed, or keep it only there.

Steps (record only states and times):
1. Record the starting state. `systemctl is-active goldbot-xvfb goldbot-mt5-terminal@icm-demo goldbot-bridge@icm-demo`,
   `pgrep -c -u mt5 -f terminal64.exe` (expect 1), `curl -s 127.0.0.1:8765/health` (expect `"terminal": "ok"`), and
   `free -m` for memory and swap.
2. `sudo reboot`. Start a timer.
3. **Pass within 10 min**, all of these:
   - xvfb, the terminal and the bridge are all active, and exactly one terminal64.exe runs;
   - `/health` shows `"terminal": "ok"`;
   - on the brain, `bridge:icm-demo` and `terminal:icm-demo` are ok;
   - Telegram showed the down alert within 6 min, followed by the recovery.
4. `sudo systemctl kill -s KILL goldbot-mt5-terminal@icm-demo`. The terminal must come back within its backoff, the
   bridge must re-attach, and there must still be exactly one terminal. Record the times.
   **Which unit owns wineserver, and does exit 76 loop?** These steps check both:
   - Run `systemd-cgls -u goldbot-mt5-terminal@icm-demo` and `systemd-cgls -u goldbot-bridge@icm-demo`, and record
     which unit lists `wineserver`. The first Wine process to start launches it, so normally that is the terminal
     unit. If it is the bridge's, then stopping the bridge (and so every exit 76) also kills the terminal's Wine. Write
     that down as a finding.
   - `sudo journalctl -u goldbot-bridge@icm-demo -b | grep -c "launched .* MT5 terminal"` should print 0. Also check
     that `systemctl show -p NRestarts goldbot-bridge@icm-demo` does not keep rising over 5 minutes.
   - If exit 76 repeats (the bridge launches a terminal, is stopped, and launches one again), stop the bridge, run
     `sudo systemctl restart goldbot-mt5-terminal@icm-demo` (the bridge starts again behind it), and open a postmortem.
5. **Hardening check.** `goldbot-mt5-terminal@.service` carries `NoNewPrivileges=yes` and `PrivateTmp=yes` commented
   out. They stay off until this step shows the terminal still works with them under Wine. Enable them one at a time
   with a drop-in (`sudo systemctl edit goldbot-mt5-terminal@icm-demo`), then
   `sudo systemctl restart goldbot-mt5-terminal@icm-demo`. Pass when, within 5 min, the terminal and the bridge are
   active and `/health` shows `"terminal": "ok"`. `PrivateTmp=yes` is the likely failure: the terminal gets its own
   `/tmp`, so it also gets its own wineserver socket and the bridge cannot attach. If either key fails, remove the
   drop-in and restart. Record pass or fail for each key; the unit file is changed only through a PR.
6. Write up the result in `docs/incidents/`. Record unit states and times only. Never include the login, the server
   name, a hostname, or any VNC or tunnel details.

**If the login is lost** (`terminal: not_logged_in`):
1. Stop the watchdog timer.
2. Log in over VNC (0.3), or restore the copy:
   `sudo systemctl stop goldbot-mt5-terminal@icm-demo`, then
   `sudo -u mt5 -H cp -a ~mt5/config-backup/Config/. ~mt5/.wine/drive_c/MT5/ICMarkets/Config/`, then start the
   terminal unit again.
3. Reboot once more.
4. If the login is lost again, disable the terminal unit, start the terminal the old way and open a postmortem.

Finish well before Sunday's open.

**Installing on a box set up before the watchdogs.** `goldbot-deploy` does not reinstall units:
- Brain: install `goldbot-engine@.service` from root's own clone at the deployed commit (as in 0.4), then run
  `sudo systemctl daemon-reload` and restart the engines.
- MT5 box: from the same clone, install these: the two `goldbot-bridge-watchdog@` units,
  `goldbot-mt5-terminal@.service`, the new `goldbot-bridge@.service`, and the `goldbot-bridge-watchdog.sh` and
  `goldbot-bridge-probe.sh` scripts as `/usr/local/sbin/goldbot-bridge-watchdog` and
  `/usr/local/sbin/goldbot-bridge-probe` (root-owned, 755). Then run `sudo systemctl daemon-reload`. Stop any terminal
  started by hand, run `sudo systemctl enable goldbot-mt5-terminal@icm-demo`, and restart the bridge (the terminal unit
  starts first). Finally run `sudo systemctl enable --now goldbot-bridge-watchdog@icm-demo.timer`.
- Then set the rollback floor (`/etc/goldbot/rollback-floor`, 0.5) to the watchdog commit. A new brain gets it from
  the bootstrap, which writes the bootstrap commit there when no floor exists. Why it matters: under `Type=notify`,
  an older release never reports ready. It still starts and trades for up to 15 min, until `TimeoutStartSec`. Then
  systemd stops it with SIGTERM and starts it again, over and over. Every cycle restarts the engine in the middle of
  whatever it was doing, and `systemctl restart` (and so a deploy) waits up to 15 min and then fails.

### Broker reconciliation (`reconcile:<account>`)

One check per account, the worse of two daily reports: the feed check (`feed_reconcile`, the engine's 1m bars against
the broker's) and the broker ledger (`broker_ledger`). The feed part:

* **warn** "engine bars missing for N of M broker minutes (... a coverage gap, not a price fault)": the engine was
  stopped (a deploy, a restart, terminal login work) or not collecting for those minutes. Nothing to fix in the data;
  `engine:<account>` and `engine_loop:<account>` show whether it runs now. It clears with the next weekday run.
* **warn / fail** "divergence X% over N engine minutes": engine prices more than 2 points outside the broker's M1
  minute range (a price the broker never quoted that minute, or a feed shifted in time), or engine bars the broker
  does not have, while the engine ran (fail above 5% with at least 60 minutes compared). "N closes inside the broker's
  minute range but off its close" is informational: the engine polls `last_tick`, so its close is the last tick it saw,
  often a few hundred ms before the broker's last tick of the minute (about 20% of minutes, up to ~$0.90). Compare the
  feeds before trusting new signals. The minute either side of an engine stop is not compared; many such minutes
  (over 5% of matched warns, over 20% fails) mean the engine restarted again and again: read its journal.
* **fail** "engine bars missing ... while the engine was up (loop heartbeat): a stalled feed": the engine ran but
  stored no bars for most of its run. Check `bridge:<account>`, the MT5 terminal's connection and the engine log,
  then restart the bridge and the engine.
* **fail** with "the engine stored no ticks in the window": the engine collected nothing for a whole trading day.

The ledger part:

* **warn**: a position differs between goldbot and the broker. `missing` means the broker has it (open, or closed by
  its deals) and goldbot has no record; `extra` means goldbot has a record the broker knows nothing of; `mismatch`
  names the field (side, lots, entry or exit price, P&L, P&L currency). Also warns on broker adjustment deals (a
  charge, correction, bonus or interest), on an unreadable orders or closed-trade file, and when the last report is
  more than 48 hours old.
* **fail**: the balance moved by more than the tolerance beyond what the day's deals explain (trade P&L, commission,
  swap, fees, deposits and withdrawals). Something changed your balance that the deal history does not show. Compare
  with the broker's statement before the next trade.

The health reason gives counts only. To see the amounts, run this on the brain:

```bash
sudo -u goldbot /opt/goldbot/.venv/bin/python -m goldbot.ops.ledger show <account> --state /opt/goldbot/state
```

It prints the latest report, the daily snapshots and the deposits and withdrawals of the last 31 days (`--days N` for
more). It opens `aux.db` read-only, so it is safe to run while the services are up. The output contains your
balances, so keep it off GitHub.

The first run is a baseline with nothing to tie out against. Its deposits are listed with `"baseline": true` because
they are already in the opening balance. A balance operation marked `"follows_cancel": true` may be the broker
reversing a cancelled deal, not a deposit or withdrawal of yours; check the broker's statement. Nothing here trades:
the job only reads the account, the positions and the deal history.

### Broker tick record (`ticks:<account>`)

The daily `tick_record` job asks the terminal (`copy_ticks`, read-only, through the scheduler's bridge connection) for
every tick of each closed server day. It runs only while gold is closed, because the bridge answers one request at a
time and the engine's orders must never wait behind it. It pulls in small pages with a pause between them. It stops
(and the day is pulled on a later run) if a page takes over 2 s, the run takes over 5 minutes, or the market is about
to reopen. It writes one file per server date to `state/ticks/<account>/<date>.parquet`,
with `index.json` beside it (rows, London/New York ticks, median spread, size and sha256 of each file). Only the
account label (for example `icm-demo`) is in the paths and files. A busy day is under 1 MB (about 0.6 MB per 50,000
ticks), so 240 days is about 150 MB an account; the files are included in the nightly backup because the terminal
cannot give old days back. **warn** "thin day (< 1000 London/NY ticks ...)": on a market holiday this is expected (it
is what the H-A check measures); on a normal weekday check `bridge:<account>` and the terminal's connection, then
delete that day's `.parquet` file so the next run pulls it again (only within the 7-day back-fill). **warn** "newest
recorded day is N days old": the job is failing; read `job:tick_record` in the health report. Nothing here trades,
and the engine does not read these files. Research reads them with `goldbot.data.ticks.load_day("icm-demo",
"2026-11-26", state_dir="state")`, which refuses a file whose sha256 no longer matches the index.

### Currency rollout (one time: the update that brings currency-correct sizing, BACKLOG 28/25/29)

On the two Oracle VMs follow the ordered rollout in [docs/deploy/2026-10-batch.md](deploy/2026-10-batch.md) instead (MT5 box first, aux.db rollback, minimum-lot setting, gap-week clock check).

Engines now publish their deposit currency and the supervisor halts every account when running engines disagree
(`mixed_account_currencies`). Do this update with every engine stopped, not engine by engine:

1. Stop all engines (`nssm stop goldbot-engine-icm` and `nssm stop goldbot-engine-vantage` on Windows; `sudo systemctl stop goldbot-engine@icm-demo` on the Oracle brain), then the supervisor.
2. Delete `state\engine_<account>.json` of every account that will not run (with this release `vantage-demo` is
   `enabled: false` in `config\accounts.yaml`). A file older than 60 s does not trigger the currency halt, and if
   its currency differs (or is missing) its money is not summed, but its open lots still count as exposure and the
   health check warns about it until you delete it.
3. Deploy (`vps_update.ps1`, below).
4. Start the supervisor, then all engines together.
5. Check `state\supervisor.json`: `"halt": false` and `"currencies": {"icm-demo": "GBP"}`. If it shows
   `mixed_account_currencies`, the health check names the offending file.

### Approval card money rollout (one time: proposals carry the account-currency risk)

From this update the engine writes new fields on every proposal (currency, risk in the account currency, the
minimum-lot note). Proposal records refuse unknown fields, so an old Telegram or API process cannot read a
new-format proposal: it would never show it, and the proposal would expire unseen. Restart the three together, with
the engine last, so no new-format proposal is written while an old reader runs:

1. Stop the engines first (`nssm stop goldbot-engine-icm-demo`, and any other engine), then `goldbot-telegram` and
   `goldbot-api`. On the Oracle brain: `sudo systemctl stop 'goldbot-engine@*'`, then the telegram and api units.
2. Deploy (`vps_update.ps1`, below; it already stops engines last and starts them after the other services).
3. Start `goldbot-api` and `goldbot-telegram`, check both are running, and only then start the engines.
4. Rolling back: stop the engines first again, so an old engine never runs beside a new reader and the reverse.

`vps_update.ps1` follows this order on its own; the steps matter when you restart services by hand.

### Server clock (US-EU DST gap weeks)

MT5 stamps every tick, bar and deal in the broker's *server time*; `server_tz` in `config/accounts.yaml` converts it
to UTC. Two clocks are in use: `Europe/Athens` (UTC+2/+3, switching on EU dates) and `NY+7` (UTC+2/+3, switching on
US dates: "midnight = 5 pm New York"). IC Markets documents the NY+7 clock. They agree except in the gap weeks,
when a wrong zone shifts the session table, the rollover gate, the Friday 21:30 weekend rule and every timestamp by
one hour. The `server_clock:<account>` health check measures it: it reads the broker's latest tick, compares it
with this server's UTC clock and fails (Telegram alert) when they differ by more than 5 minutes while gold trades.
Over a weekend it says "market closed: not measured", which is fine.

Next gap trading days (check on one of them, during market hours):

| Gap | Trading days | NY+7 | Europe/Athens |
| --- | --- | --- | --- |
| Oct/Nov 2026 | Mon 26 Oct - Fri 30 Oct 2026 | UTC+3 | UTC+2 |
| March 2027 | Mon 15 Mar - Fri 26 Mar 2027 | UTC+3 | UTC+2 |
| Oct/Nov 2027 | Mon 1 Nov - Fri 5 Nov 2027 | UTC+3 | UTC+2 |

The engine runs the same comparison on every account refresh and refuses entries while it fails (gate reason
`server_clock_mismatch`, plus `tick_from_future` when ticks are stamped more than 10 s ahead of this server's
clock); exits (stops, time exits, the weekend rule, the kill switch) are never held back by it.

Three rules look at the broker's clock; each owns one case (all block entries only):

| Rule | Measures | Owns |
| --- | --- | --- |
| `clock_skew` (data-quality error) | rolling median of broker tick time minus this server's clock over 120 new ticks; blocks beyond 10 s | a broker or host clock drifting by seconds (bars would close before their last ticks) |
| `tick_from_future` | the latest tick alone, more than 10 s ahead | a sudden jump ahead (wrong zone, clock step) before the median catches up; the same 10 s threshold, so a few seconds fast never trips it |
| `server_clock_mismatch` | the latest tick against the configured `server_tz`, over 5 minutes while gold trades | a whole-hour zone error (the DST gap weeks) or a host clock far off; names the zone when the other known zone explains it |

Steps on the brain (or the VPS), on a gap day while the market is open:
1. `goldbot run preflight` first: its `time_sync` line must be ✅ (chrony synchronised); a host clock that is off
   reads as a mismatch too. Then `goldbot run health` (or `python -m goldbot.ops.run health`) and read the
   `server_clock:<account>` line. `ok ... server clock UTC+3 matches NY+7` means the configured zone is right.
2. `fail ... server clock reads UTC+3, which is NY+7 now, but Europe/Athens gives UTC+2 (US-EU DST gap week)`
   means the zone is wrong. Any other `fail` ("no known server zone explains it: host clock or feed") is NOT a zone
   problem: check the host clock (`chronyc tracking` on the brain, `w32tm /query /status` on Windows; the offset must
   be well under a second) and the feed. Cross-check by eye: the MT5 terminal's Market Watch shows the server time;
   subtract the real UTC time (`date -u` on the brain).
3. Fix a wrong zone: set `server_tz` for that account in `config/accounts.yaml` to the zone the broker runs (`NY+7`
   or `Europe/Athens`) on the brain AND on the MT5 box (the bridge converts with its own copy; preflight's
   `bridge_server_tz` line blocks when the two differ), commit it, deploy, then restart the bridge and the engine.
   Run `goldbot run health` again until the line is `ok`.
4. Set `NY+7` only on a gap day when the probe has shown UTC+3 against Athens' UTC+2 (step 2), never on the
   research alone. Outside the gap weeks the two zones are identical and nothing can be learned.

`goldbot run preflight` runs the same measurement (`server_clock`, blocking on a measured mismatch).

### Entry windows (reopen, holidays, tier-2 news)

Three entry windows (BACKLOG 17/18, `goldbot/risk/windows.py`), each off until adopted in `risk.reopen`,
`risk.holidays` and `risk.blackout.tier2`. The engine publishes them in `windows` in `engine_<account>.json`; the
dashboard's Entry status and Telegram `/status` name them. Entries only: exits never read them.

| Code | Meaning | Clears |
|---|---|---|
| `reopen_window` | Just after the daily reopen (`after_daily_reopen_min`) or the weekly open (`after_monday_open_min`), read from the broker session table. Also raised when the session table cannot be read (fail closed). | By itself at the end of the window. |
| `holiday_thin` | Today's broker server date is on the holiday list (`config/holidays_cme_metals.yaml`). | By itself at server midnight of the first unlisted day. |
| `tier2_blackout` | Within `before_min` / `after_min` of a second-tier US release in the live calendar. | By itself `after_min` after the release. |
| `calendar_unavailable` | An enabled calendar window has nothing to judge by: the economic calendar's newest fetch is older than 7 days or does not cover the next 24 h, or the holiday list is missing, unreadable or does not cover today. | When the calendar fetch runs again (Feeds tab; `calendar_unavailable_reason` in the engine state says which), or when a holiday list with a new `version` covering today is deployed. Health (`check_entry_windows`) warns 90 days before the list runs out. |

### Bridge compatibility

The brain and the MT5 box are deployed separately (BACKLOG 42). The bridge reports on its unauthenticated
`GET /health` its `server_tz`, protocol `api_version` and `min_compatible`, the `methods` it serves and the commit it
runs; nothing there is a secret. The brain reads at most 64 KiB within a 5 s total deadline. The engine starts with
entries refused (`unchecked`) and compares the report with its own at start and every 60 s, on its own thread:

| Verdict | Means | Effect |
| --- | --- | --- |
| incompatible | a method missing, either side's `api_version` below the other's `min_compatible`, another or an invalid `server_tz`, or no report at all (or a body over 64 KiB) | entries refused (`bridge_incompatible`); health `bridge_compat:<account>` FAIL (Telegram); preflight `bridge_compat` blocks. Exits, closes and the kill switch are unaffected |
| commits differ | both boxes compatible, different commits (doc-only changes, or a deploy half done) | health warning only, no block, no Telegram |
| unchecked | the engine is starting, has only ever seen `/health` unreachable, or last confirmed the bridge over 5 min ago (a stuck check) | entries refused (`bridge_incompatible`). When the bridge itself answers, health `bridge_compat:<account>` warns "not confirmed" for the first 2 min, then FAILs (Telegram); an ok over 5 min old FAILs at once. The engine replaces a check thread hung over 15 s on its next 60 s tick and every `/health` read has a hard 5 s deadline, so it heals itself; if the FAIL persists, `sudo systemctl restart goldbot-engine@<account>` |
| unreachable | `/health` did not answer after the bridge was confirmed ok | no block by itself (no ticks either, so `stale_data` blocks) until the ok is 5 min old; `bridge:<account>` fails; an earlier incompatible is kept |

**Deploy order: the MT5 box first; brain-first only with a flat book.** From protocol v4 (`MIN_COMPATIBLE = 4`) a new
brain refuses to start against an older bridge: the bridge does not report the account margin mode, which must be
hedging (`engine:<account>` FAILs with "engine refused to start: ... deploy the MT5 box first"; `bridge_compat`
names the version). While the engine is down nothing manages open trades beyond their broker stops, so update the
brain first only when no position is open.

From protocol v5 (`MIN_COMPATIBLE = 5`, ADR 0016: the bridge method `account_positions` and `Position.swap`, used by
the equity cross-check before the 12% kill switch) the two versions start next to each other but refuse entries
(`bridge_incompatible`) until both run v5: a new brain lacks `account_positions` on an old bridge, and an old brain is
below the new bridge's `MIN_COMPATIBLE`. Exits, closes and the kill switch keep running either way. Deploy the MT5
box first and the brain straight after, so the entry pause lasts only the brain's deploy.

BACKLOG 66 (bridge notional ceiling, protocol unchanged at v5): deploy the MT5 box first (the ceiling guards any
brain), then the brain; until the brain is updated, a refusal ("bridge_notional_ceiling: ...", retcode 10006) shows
only in the bridge log on the MT5 box, with no Telegram notice and no "refused by the MT5 box" card.

**Recovery: an order refused by the MT5 box (BACKLOG 66).** The journal's order entry carries the bridge's message:
- `bridge_notional_ceiling: notional X > 6 x equity Y`: the brain sized above 6× this terminal's equity; nothing was
  placed and exits are unaffected. Compare the brain's and the terminal's equity and fx (`curl /health`, the bridge
  log on the MT5 box), and check for manual or other-EA XAUUSD positions, since every lot on the symbol counts. Auto
  entries resume at the next bar close.
- `bridge_notional_ceiling: unreadable:<field>`: a terminal read failed and every entry is refused. Check
  `journalctl -u goldbot-bridge@<account>`; if it is stuck, run `sudo systemctl restart goldbot-bridge@<account>`
  (the watchdog also restarts it).
- Repeated refusals mean an engine sizing bug: raise a trading-safety issue and never loosen the ceiling.

**Rollback order: roll back the brain first, then the MT5 box** (the reverse of a deploy), so no step leaves a newer
brain next to an older bridge.

Fix (the MT5 box first, always, when a release adds a bridge method):
1. On the MT5 box: `sudo goldbot-deploy <sha the brain runs>`, then `sudo systemctl restart goldbot-bridge@<account>`.
2. On the brain: `curl -fsS http://127.0.0.1:8765/health ; echo` shows `api_version`, `min_compatible`, `methods`,
   `commit`;
   `goldbot run preflight | grep bridge_compat` must be ✅.
3. The engine picks the new verdict up within 60 s (no restart needed); `goldbot run health | grep bridge_compat`.

A developer who adds, removes or changes a bridge method (its signature on `RemoteBroker` or `MT5Broker`, or a wire
record's fields) bumps `API_VERSION` in `goldbot/execution/bridge.py`, and `MIN_COMPATIBLE` when older peers can no
longer interoperate (`tests/test_bridge_compat.py` pins both to `protocol_digest()` and fails otherwise), and says
"deploy the MT5 box first" in the PR. The Telegram "New version ready" offer warns when an MT5 box runs an older
commit than the one offered.

### Order lane and close priority (BACKLOG 135, protocol v7)

From protocol v7 (ADR 0056) the bridge accepts requests concurrently and gives the terminal one call at a time from a
priority queue: closes and stop changes first, then `/health`, then reads, then entry orders. A request whose client
went is never run, a read queued past its client's timeout is dropped (504), and an entry queued past 10 s is refused
stale (409). With an account's **order lane** on (`order_lane: true` in `config/accounts.yaml`), the bridge starts a
child process (`goldbot run bridge-order-lane <account>`, never started by hand) with its own terminal connection that
sends entries only, so ticks, closes, the Friday cut and the kill switch keep flowing while the terminal sends one.
`/health` reports `order_lane`: `off`, `starting`, `separate` (up), or `down` (it failed or lost an order; entries are
back on the shared connection until the bridge restarts).

| Bridge | Exit delay with an entry in flight | Live account |
| --- | --- | --- |
| v6 | the entry's hold plus a 2 x 10 s read cycle (about 45-55 s at 35 s) | entries refused (`bridge_incompatible`, "a live account needs v7") |
| v7, lane off | the entry's hold plus one engine pass (about 36 s at 35 s) | allowed |
| v7, lane `separate` | under 5 s | allowed |

**Deploy (protocol v7).** `MIN_COMPATIBLE` stays 5, so a v6 brain and a v7 bridge work together either way round. Do
the MT5 boxes first anyway, one at a time, and the brain last. Each box's bridge restarts (about 15 s with no ticks;
the broker-side stops cover open positions), so pick a quiet minute or a flat book. Claude runs every step; nothing
needs the owner.
1. CI is green on main for the commit `<SHA>` that contains this change.
2. For each MT5 box (`goldbot-mt5` = icm-demo, `goldbot-mt5-probe` = icm-probe, `goldbot-mt5-vantage` = vantage-demo):
   `ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes <box> sudo goldbot-deploy <SHA>`. The deploy restarts
   `goldbot-bridge@<account>`, watches it for 90 s and rolls back by itself if it restarts again. No new package and
   no new unit: `goldbot-deploy` is all it needs.
3. Check each box: `ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes <box> curl -fsS http://127.0.0.1:8765/health`
   shows `"api_version": 7`, `"order_lane": "off"` and `"terminal": "ok"`.
4. The brain: `ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-brain sudo goldbot-deploy <SHA>` (or the Telegram
   Deploy). Then `goldbot run health | grep -e bridge_compat -e 'bridge:'`: every account ok (commits equal).
5. Soak for 4 h: `journalctl -u goldbot-engine@<account> --since -4h | grep -c tick_error` stays 0 on every
   account, and the bridge log shows the queue at work without errors:
   `ssh ... <box> journalctl -u goldbot-bridge@<account> --since -4h | grep -c "not run"` (abandoned reads dropped, normal)
   and `... | grep -c "queue worker error"` (must be 0).

**Turn the lane on (one demo box at a time; vantage-demo first, its entries are the slowest).**
1. Memory first: the lane is a second Wine Python (about 150-250 MB). `ssh ... goldbot-mt5-vantage free -m` must show
   at least 400 MB available; else stop and raise it with the SRE (the box is 2 GB).
2. Before: on the brain run `python scripts/bridge_latency_probe.py vantage-demo --minutes 60` (read-only, last_tick
   every 0.5 s) while the engine trades. Expect slow reads (over 1 s) at the times of entries.
3. A one-line change on a branch: `order_lane: true` under `vantage-demo` in `config/accounts.yaml`, through CI and
   merged as usual; deploy it to the vantage box only (step 2 of the deploy, with the new `<SHA>`), then the brain.
4. On the box within 2 min: `/health` shows `"order_lane": "separate"` and `"terminal": "ok"`; exactly one terminal:
   `ssh ... goldbot-mt5-vantage pgrep -c -u mt5 -f '[t]erminal64[.]exe'` prints `1`; two Python processes in the
   bridge unit: `systemctl status goldbot-bridge@vantage-demo` lists the bridge and `bridge-order-lane`.
5. After: `python scripts/bridge_latency_probe.py vantage-demo --minutes 240`. PASS: no read over 1 s at the time of
   any entry in the engine journal (`order_late` or a fill), and `/health` stayed `ok` throughout (no `not_running`).
   This is the drill that shows the MT5 terminal under Wine serves two connections at once; until it passes, no
   account counts as having the 5 s bound.
6. The kill-switch drill (live-readiness L10) on this box: flatten a demo position while an entry is in flight; the
   close lands within 5 s (`gate-evidence` records it).

**Rollback.** The lane: set `order_lane: false` (or remove the line), merge, deploy the box. A box whose `/health`
shows `not_running` or restarts after the lane came up: `sudo systemctl stop goldbot-bridge@<account>`, then the
lane rollback, then start it again. Protocol v7 itself: the brain first (`sudo goldbot-deploy --rollback <previous
SHA>`), then each box; on demo either order works (MIN_COMPATIBLE 5). A live account refuses entries on a v6 bridge,
by design.

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
* Rolling back to a version from before the agent/scheduler/outbox bookkeeping moved into `state\aux.db`
  (2026-10-10): the new version keeps `scheduler.json`, `agent_spend.json`, `telegram_outbox.json` and
  `agent_runs.jsonl` up to date, so the old version carries on where it left off, and the new version merges what
  the old one wrote when you update again. The old version's dashboard would refuse the newer `aux.db`, so the
  script steps `aux.db` back from schema 5 to 4 by itself when it installs such a version (rows kept; the next
  update re-applies 5). Only if you check out an old version by hand, stop the services and run the same step:

  ```powershell
  cd C:\goldbot
  foreach ($s in "goldbot-scheduler","goldbot-webhook","goldbot-news","goldbot-api","goldbot-telegram","goldbot-supervisor","goldbot-engine-icm","goldbot-engine-vantage") { nssm stop $s }
  .\.venv\Scripts\python.exe -c "import sqlite3; c = sqlite3.connect(r'state\aux.db'); v = c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]; assert v == 5, f'schema {v}: do not run this'; c.execute('DELETE FROM schema_version WHERE version = 5'); c.execute('PRAGMA user_version = 4'); c.commit()"
  ```

  It refuses unless the file is at schema 5. Later versions record which older versions can still open the file,
  so this step is not needed again for changes that only add tables.
* **aux.db schema 6 (email sign-in links, 2026-10-11) cannot be stepped back to 4.** Schema 6 only adds tables, so
  every release from state-store step 3 on (commit 596386d, 2026-10-10, schema 5 or later) opens a schema 6 file as
  it is. A release from before step 3 knows only schema 4. The updater and `goldbot-deploy` refuse to install such a
  release ("aux.db is at schema 6 ... no step-back is known; nothing changed"), and the one-liner above refuses too.
  Do not delete schema_version rows by hand to get round it. Roll back to a release from 2026-10-10 or later, or ask
  a Claude session for a 6 -> 4 step-back.
* The script stops the scheduler first and checks again that no job started in between; if one did, it was
  interrupted by the stop: the script starts the scheduler again, stops nothing else and refuses the update.

### MT5 terminal chart on the dashboard (`GET /api/chart/mt5`)

The terminal's own IC Markets chart, screenshotted, not rebuilt. On the MT5 box `goldbot-chart-capture.timer` (user `mt5`, every 60 s)
runs `import -window root` on display `:99` into `/home/mt5/chart/mt5.png` (atomic; it never touches the terminal or the bridge). The brain's
`goldbot-chart-fetch.timer` (user `goldbot`, every 60 s) pulls it with its own SSH key, which may run one command only
(`goldbot-chart-serve.sh`, a `tar` of that file; no shell, no forwarding), into `/opt/goldbot/state/chart/mt5.png`. The API returns the PNG
with an `X-Captured-At` header to any signed-in role, and 404 when the file is missing or older than 5 minutes (dashboard: show a placeholder).
The brain crops the capture to the chart pane (`goldbot/api/mt5_crop.py`: the largest dark plot rectangle plus its price scale and time axis; the XAUUSD pane when its title is readable, which needs the optional `tesseract` binary on the brain, else the largest pane), writes `state/chart/mt5_chart.png` and serves that, with `X-Chart-Cropped`, `X-Chart-Symbol`, `X-Chart-Timeframe` and `X-Chart-Gold` (`true`/`false`/`unknown`) headers; no pane found serves the full image with `X-Chart-Cropped: false`. The dashboard notes "MT5 is showing <SYM>" only when the symbol is known and not gold.
It shows whatever chart window the terminal has open (one timeframe, as configured; switching timeframes from the dashboard is out of scope).

Install (standalone: needs no `/opt/goldbot` update on the MT5 box, and touches neither the terminal nor the bridge; `<SHA>` is a merged main commit). Root installs the scripts and units from its own hash-checked clone, never from `/opt/goldbot`. On the MT5 box:
`sudo env S=<SHA> bash -c 'set -e; [ ${#S} = 40 ]; d=$(mktemp -d /root/goldbot-ops.XXXXXX); trap "rm -rf -- $d" EXIT; git clone -q --filter=blob:none --no-checkout https://github.com/engraamirkhan/goldbot.git $d/r; git -C $d/r merge-base --is-ancestor $S origin/main; git -C $d/r checkout -q --detach $S; install -d -m 755 -o root -g root /usr/local/libexec/goldbot; for f in goldbot-chart-capture.sh goldbot-chart-serve.sh; do install -m 755 -o root -g root $d/r/goldbot/ops/linux/$f /usr/local/libexec/goldbot/; done; for u in goldbot-chart-capture.service goldbot-chart-capture.timer; do install -m 644 -o root -g root $d/r/goldbot/ops/linux/systemd/$u /etc/systemd/system/; done; systemctl daemon-reload'`
On the brain (key, then the fetch units; the fetch script stays under `/opt/goldbot` there):
`sudo -u goldbot ssh-keygen -t ed25519 -N '' -C goldbot-chart -f /var/lib/goldbot/.ssh/chart_ed25519 && sudo env S=<SHA> bash -c 'set -e; [ ${#S} = 40 ]; d=$(mktemp -d /root/goldbot-ops.XXXXXX); trap "rm -rf -- $d" EXIT; git clone -q --filter=blob:none --no-checkout https://github.com/engraamirkhan/goldbot.git $d/r; git -C $d/r merge-base --is-ancestor $S origin/main; git -C $d/r checkout -q --detach $S; for u in goldbot-chart-fetch.service goldbot-chart-fetch.timer; do install -m 644 -o root -g root $d/r/goldbot/ops/linux/systemd/$u /etc/systemd/system/; done; systemctl daemon-reload' && sudo -u goldbot cat /var/lib/goldbot/.ssh/chart_ed25519.pub`
Then on the MT5 box, with that public key line in place of `<pubkey>`: `echo 'restrict,command="/usr/local/libexec/goldbot/goldbot-chart-serve.sh" <pubkey>' | sudo -u mt5 tee -a /home/mt5/.ssh/authorized_keys` and `sudo systemctl enable --now goldbot-chart-capture.timer`.
Last, on the brain: `sudo systemctl enable --now goldbot-chart-fetch.timer`. The brain's `goldbot-chart-fetch.sh` is in `/opt/goldbot` there, so the brain must run a version that has it.
Check: `sudo -u goldbot /opt/goldbot/goldbot/ops/linux/goldbot-chart-fetch.sh && ls -l /opt/goldbot/state/chart/mt5.png`.
Remove: `sudo systemctl disable --now goldbot-chart-fetch.timer` (brain) and `goldbot-chart-capture.timer` (MT5 box); delete the `goldbot-chart` line from `/home/mt5/.ssh/authorized_keys` (`sudo -u mt5 sed -i '/goldbot-chart/d' /home/mt5/.ssh/authorized_keys`) and `sudo rm -f /usr/local/libexec/goldbot/goldbot-chart-*.sh`.
Failure: a stale or missing picture is a 404 only; nothing trading depends on it. Look at `journalctl -u goldbot-chart-fetch` (brain) and `-u goldbot-chart-capture` (MT5 box).

### Program ledger sync (Mac -> brain)

`python -m goldbot.ops.run ledger sync` sends the Mac's new ledger rows to the brain's `state/program.db` over ssh with its own key, which may run one
command only (`goldbot-ledger-ingest.sh`, which ignores what the client asks for and runs `ledger ingest`: append-only, secret-scanned, bad rows rejected,
prints `ack N`). Install (owner steps, once): on the Mac `ssh-keygen -t ed25519 -f ~/.ssh/goldbot_ledger -N ''`; on the brain install the script as root
(`install -m 755 -o root -g root goldbot/ops/linux/goldbot-ledger-ingest.sh /usr/local/libexec/goldbot/`, same pinned-SHA pattern as the chart scripts) and then
`echo 'restrict,command="/usr/local/libexec/goldbot/goldbot-ledger-ingest.sh" <pubkey>' | sudo -u goldbot tee -a /var/lib/goldbot/.ssh/authorized_keys`.
Run: `GOLDBOT_LEDGER_REMOTE=goldbot@<brain> GOLDBOT_LEDGER_KEY=~/.ssh/goldbot_ledger python -m goldbot.ops.run ledger sync` (the host stays in your shell, never in the repo).
A rejected or unacknowledged batch stays pending and is retried on the next run (exit 1). Backfill once with `ledger import all` (the registry is fetched
from release `research-v1` with `gh`). Remove: delete the key line from the brain's `authorized_keys`.
