# First deploy to the demo account

SRE, 2026-10-11. For Aamir. Target: **Mon 12 Oct 2026, 09:00-10:30 UTC** (10:00-11:30 London).
Fallback: **Tue 13 Oct, 09:00-10:30 UTC**.

What you get at the end:
* goldbot running 24/7 on the demo account `icm-demo`;
* Telegram alerts when something breaks;
* the dashboard with your owner account and authenticator;
* the Approve button wired up for the day a strategy qualifies.

**Expect no trade cards yet.** No strategy has passed its tests (research: no edge after costs), and goldbot never
suggests a trade without one. This deploy proves the plumbing. The shadow book paper-trades every candidate in the
meantime.

## Rules for this whole document

* `<like-this>` is a placeholder. Type your real value only into your own Terminal, or into a goldbot prompt that
  hides it. Never put it in a chat, an issue, a commit or a note in the repository. This covers MT5 login, passwords,
  tokens, IP addresses, Telegram id and email.
* Commit ids (`<SHA>`) and file hashes (`<HASH>`) are public. They are safe to share.
* "Mac" means the Terminal app on your Mac. "brain" means Mac Terminal after `ssh ubuntu@<brain-ip>`.
* Run every long command inside `tmux` (`tmux new -s boot`). If the connection drops, reconnect and run
  `tmux attach -t boot`. The install keeps going and logs to `/root/goldbot-bootstrap.log`.
* Demo only. Live needs the phase gate, `unlock_live` and a typed phrase. None of that is in this document.

## Which case are you in? (2 min)

On the Mac: `ssh ubuntu@<brain-ip> cat /etc/goldbot/role`

| You see | Case | Follow |
| --- | --- | --- |
| no VM yet, or `No such file or directory` | **A: fresh install** | this document |
| `brain` | **B: update** | [2026-10-batch.md](2026-10-batch.md) |

Nothing in the repository, the issues or the workflow runs shows that a VM was ever set up. Expect case A.

## Two ways to run MT5 (both tried on day 1)

The brain (Oracle Ampere A1, Linux) runs everything except MT5. MT5 runs on a small "bridge" box. Both options
are free and both are unproven at 1 GB of memory, so day 1 sets up both. Keep whichever stays stable for 24 h.

| | **A: Windows (Azure free VM)** | **B: Wine (Oracle micro)** |
| --- | --- | --- |
| Machine | Azure B2ats v2: 2 vCPU, 1 GiB RAM, free for 12 months | Oracle E2.1.Micro: 1/8 OCPU, 1 GB RAM, free for good |
| MT5 | native Windows, as MetaQuotes supports it | under Wine (less proven) |
| Link to brain | Tailscale (free) + the brain's SSH tunnel | Oracle private network + the brain's SSH tunnel |
| Install | `vps_bootstrap.ps1 -BridgeOnly` (about 25 min) | `mt5_bootstrap.sh` (30-45 min on 1/8 OCPU) |
| You log in to MT5 with | Remote Desktop (Windows App on the Mac) | Screen Sharing over SSH (VNC) |
| Risk | Windows in 1 GiB: a 4-6 GB pagefile is set | Wine in 1 GB: 4 GB swap is set |

The brain connects to **A first**. B soaks on its own. If A fails at any step, point the brain at B (step 6B).

**Paid fallback (your decision, about £10-15 a month):** a 2-4 GB Windows VPS running the full Windows install
unchanged (`vps_bootstrap.ps1` without `-BridgeOnly`, RUNBOOK sections 1-2). It needs no brain. Use it if both
free bridges fail their 24 h soak.

---

## Part 1: before the session (about 60 min, any time before)

None of this touches the market.

**P1. Oracle (browser, 20 min)**
1. Sign up at oracle.com/cloud/free. Choose home region **UK South (London)**. You cannot change it later. Turn on
   MFA.
2. Billing -> **Upgrade to Pay As You Go**. Always Free stays free. Without PAYG, Oracle can reclaim a quiet VM.
   A VM counts as idle when CPU, network and memory all stay under 20% for 7 days, and the brain is quiet.
3. Billing -> Budgets -> a budget of **1** with an email alert.
4. Mac, once: `ssh-keygen -t ed25519` (press Enter at each question).

**P2. Oracle VMs (browser, 10 min)**. Compute -> Instances -> Create, both in the same network (the default VCN):
1. **brain**: Ubuntu 24.04, shape Ampere VM.Standard.A1.Flex, **2 OCPU, 12 GB**. That is the whole free allowance
   now; more is billed. Paste `~/.ssh/id_ed25519.pub`.
2. **mt5**: Ubuntu 24.04, shape VM.Standard.E2.1.Micro, same key.
3. On each instance page, note the public IP. For mt5, also note the **private IP**. Keep both in your Mac only.

*If A1 says "out of capacity":* try each availability domain, then retry every hour (PAYG often fixes it). Still none
by Monday 08:00 UTC? Use the **second** Oracle micro as the brain. The bootstrap gives it 4 GB swap. It is slow, but
the brain needs about 1 GB at rest. The Saturday retrain will run slowly in swap.

**P3. Azure (browser, 20 min, option A)**
1. Sign up for the Azure free account. Cost Management -> Budgets -> a budget of **1** with an email alert.
2. Create a virtual machine:
   * image **Windows Server 2025 Datacenter** (it includes `winget`), the `[smalldisk]` variant if offered;
   * size **B2ats v2**; OS disk **Premium SSD, 64 GiB** (the free allowance is two 64 GB P6 disks);
   * region near London;
   * an administrator user name and password (password manager). This account is only for you over Remote Desktop.
     The bridge runs as its own non-admin user, `goldbot-bridge`. The install creates it and asks you to choose its
     password, so save a second password manager entry for it before step 1b;
   * public inbound ports: **RDP only, source = My IP**.
   Re-check each choice against the free-services list as you create it.
3. On the Mac, install **Windows App** (Mac App Store) for Remote Desktop.

**P4. Tailscale (5 min, option A).** Make a free Personal account at tailscale.com and install it on the Mac.

**P5. Telegram (5 min)**
1. Message **@BotFather** -> `/newbot` and keep the token in that chat for now.
2. Open your new bot and press **Start**.
3. Message **@userinfobot** to see your numeric id.

**P6. Get the commit and the hashes (Mac, 5 min).** The release manager names the deploy commit on issue #34, once
PR #74 and the first-deploy fixes are merged. That commit is `<SHA>`.
```bash
cd ~/myproject/goldbot && git fetch -q origin
SHA=<SHA from issue 34>
git branch -r --contains $SHA | grep -qx '  origin/main' && echo on-main
gh api repos/engraamirkhan/goldbot/commits/$SHA/check-runs --jq '.check_runs[] | "\(.name) \(.conclusion)"'
for f in linux/brain_bootstrap.sh linux/mt5_bootstrap.sh vps_bootstrap.ps1; do
  echo "$f $(git show $SHA:goldbot/ops/$f | shasum -a 256 | cut -c1-64)"; done
```
Write down `<SHA>` and the three `<HASH>` values. They are public.

**Check:**
* `on-main` is printed;
* `backend success` and `frontend success` are listed. Without `gh`, open the commit page on GitHub and look for
  the green tick;
* `git show $SHA:goldbot/ops/vps_bootstrap.ps1 | grep -c BridgeOnly` prints more than 0.

**Stop if:** any check fails. You are on the wrong commit; ask for the right one.

**P7. Outside alert (browser, 5 min).** Alerts come from the brain itself, so a dead brain is silent. A free
check on Healthchecks.io fixes that (it emails you when the brain stops pinging it):
1. healthchecks.io -> sign up (free plan) -> **Add Check**: name `goldbot brain`, Schedule **Simple**, Period
   **2 minutes**, Grace **3 minutes**.
2. Integrations: email is on already. Optional: add **Telegram** there (Healthchecks.io's own bot, not yours).
3. Copy the check's **ping URL** (`https://hc-ping.com/...`) into your password manager. It is a secret: never paste
   it into a chat, a command line, an issue or the repository. You store it on the brain in step 8b.

Have ready: `<mt5-login>` and the MT5 demo password (typed only into the MT5 terminal), the bot token, your Telegram id, your email, the
Healthchecks.io ping URL (password manager), an authenticator app on your phone.

---

## Part 2: the session (60-90 min, market open)

### 1. Start both installs (10 min of typing, then wait)

**1a. brain** (Mac: `ssh ubuntu@<brain-ip>`, then `tmux new -s boot`):
```bash
curl -fsSLo brain_bootstrap.sh https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/linux/brain_bootstrap.sh
echo "<HASH-brain>  brain_bootstrap.sh" | sha256sum -c - && sudo bash brain_bootstrap.sh <SHA>
```
**Expect:** `brain_bootstrap.sh: OK`, then about 25 min of output. The last lines show a key starting `ssh-ed25519`
and then `brain installed`. Copy the `ssh-ed25519 ...` line; it is a public key and safe to copy.

**1b. option A, Windows.** In Windows App, connect to `<azure-ip>`. Open **PowerShell as Administrator**:
```powershell
cd $env:USERPROFILE
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/vps_bootstrap.ps1 -OutFile vps_bootstrap.ps1
(Get-FileHash .\vps_bootstrap.ps1 -Algorithm SHA256).Hash -eq "<HASH-vps>".ToUpper()
```
**Expect:** `True`. **Stop if** it prints `False`.
```powershell
powershell -ExecutionPolicy Bypass -File .\vps_bootstrap.ps1 -BridgeOnly -Ref <SHA>
```
It asks you once to choose the `goldbot-bridge` password; use the one from your password manager. **Expect** about
25 min. The SSH server is started; the bridge service is not. Near the end you see
`check: goldbot-bridge runs the bridge's Python`, then `Bridge box installed`.

Possible warning: `SSH is limited to Tailscale addresses ... not yet bound to the Tailscale adapter`. That is fine;
step 3A binds it.

**Stop if** the script stops with `goldbot-bridge cannot run ...`. The bridge user cannot read Python or the
folder; report it before going on. Then **restart Windows** (Start -> Power -> Restart) so the pagefile takes
effect, and reconnect.

**1c. option B, Oracle micro** (Mac: `ssh ubuntu@<mt5-ip>`, `tmux new -s boot`):
```bash
curl -fsSLo mt5_bootstrap.sh https://raw.githubusercontent.com/engraamirkhan/goldbot/<SHA>/goldbot/ops/linux/mt5_bootstrap.sh
echo "<HASH-mt5>  mt5_bootstrap.sh" | sha256sum -c - && sudo bash mt5_bootstrap.sh <SHA>
```
**Expect** 30-45 min, ending `MT5 box installed`.

**1d. No core files (brain, and the MT5 box for option B).** A watchdog kill must not write the engine's memory,
which holds tokens, to disk. Run `cat /proc/sys/kernel/core_pattern`. **Expect** `|/bin/false`. The bootstrap log
shows the earlier value on its `core_pattern was:` line. On the brain, also run `cat /etc/goldbot/rollback-floor`.
**Expect** `<SHA>`. **Stop if** core_pattern names apport or systemd-coredump. Run
`sudo sysctl -p /etc/sysctl.d/60-goldbot-no-core.conf` and check again before any secret goes in.

**Stop and roll back:** nothing is running yet. If an install fails, read the end of `/root/goldbot-bootstrap.log`
(Windows: `C:\ProgramData\goldbot-bootstrap.log`), then delete the VM and recreate it. A fresh VM is faster than
repairing one.

### 2. Log in to MT5 once (5 min per option)

**A (Windows, in Remote Desktop).** Start the terminal **as the bridge user**, so the saved login belongs to the
account the service runs as. In PowerShell:
`runas /user:goldbot-bridge C:\MT5\ICMarkets\terminal64.exe`. It asks for the `goldbot-bridge` password.
**Never start `terminal64.exe` as the administrator** (no double-click, no "Run as administrator"): the bridge
user can write that folder (MT5 updates itself), so only ever start it with `runas` as above. Then:
1. File -> Login to Trade Account: `<mt5-login>`, password, server **ICMarketsSC-Demo**, tick **Save password**.
2. Check that `XAUUSD` is in Market Watch.
3. Tools -> Options -> Expert Advisors -> tick **Allow algorithmic trading**.
4. Check the account is **hedging**. The terminal's title bar (or Navigator -> the account) says `Hedge`. goldbot
   refuses a netting account. If it says `Netting`, open a new MT5 demo account in the IC Markets client area with
   account type **hedging** (server ICMarketsSC-Demo), log in with that one instead, and use its login from here on.
5. Then **File -> Exit**. The bridge service starts its own copy. Two copies would fight over the same folder.

**B (Oracle micro):**
1. On mt5: `sudo systemctl start goldbot-vnc`.
2. Mac, new window: `ssh -L 5900:localhost:5900 ubuntu@<mt5-ip>`.
3. Finder -> Go -> Connect to Server -> `vnc://localhost:5900`.
4. If no terminal shows, start it on mt5: `sudo -u mt5 -H env DISPLAY=:99 wine 'C:\MT5\ICMarkets\terminal64.exe' &`
5. Do the same steps as A (login, Market Watch, algorithmic trading, **Hedge**), then exit the terminal.
6. On mt5: `sudo systemctl stop goldbot-vnc`. Close the tunnel window.

**Stop if:** the login fails or the terminal will not open. That option is out for today; carry on with the other
one.

### 3. Link the brain to the bridge (10 min)

**A (Windows over Tailscale)**
1. Windows, PowerShell as Administrator: `tailscale up --unattended` (it keeps running when you sign out of Remote
   Desktop). Sign in to your Tailscale account in the browser it opens.
2. brain: install Tailscale from its signed package source, then sign in:
   ```bash
   curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
   curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list
   sudo apt-get update && sudo apt-get install -y tailscale && sudo tailscale up
   ```
3. In the Tailscale admin page, note the Windows box's `100.x.y.z` address. That is `<windows-tailscale-ip>`.
4. Windows, PowerShell as Administrator, from `C:\goldbot`:
   `.\goldbot\ops\vps_bridge_authorize.ps1 '<the ssh-ed25519 line from 1a>'`
   **Expect:** `authorised (forwarding to 127.0.0.1:8765 only)`.
5. Windows, Administrator PowerShell: `Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -InterfaceAlias Tailscale`
   binds SSH to the Tailscale adapter. Harmless if the install already did it.
6. brain: `sudo goldbot-tunnel <windows-tailscale-ip>`

**B (Oracle micro)**
1. mt5: `sudo goldbot-mt5-authorize '<the ssh-ed25519 line from 1a>'`
2. brain: `sudo goldbot-tunnel <mt5-private-ip>`. Do this only when B becomes the one in use (step 6B).

**Expect:** `systemctl is-active goldbot-bridge-tunnel` prints `active` after about 10 s. It shows `activating`
while the bridge is not started yet; that is fine.

### 4. Bridge token, then setup straight away (10 min)

Do these two back to back. The token is shown once; type it straight into setup.

1. Bridge box, start the bridge with a new token.
   * **A**: in PowerShell, run as the bridge user (asks for its password). A new window opens:
     ```powershell
     runas /user:goldbot-bridge "cmd /k cd /d C:\goldbot && .venv\Scripts\python -m goldbot.ops.accounts bridge-serve icm-demo"
     ```
     Then, in the **Administrator** PowerShell: `nssm restart goldbot-bridge-icm-demo`.
   * **B**: in a fresh `tmux new -s token`, run `goldbot-mt5 accounts bridge-serve icm-demo`, then
     `sudo systemctl restart goldbot-bridge@icm-demo`.

   It asks for `<mt5-login>`, then the listen address: type `127.0.0.1:8765`. It prints the token once.
2. brain: `goldbot run setup`. Answer each step:

   | Step | Answer |
   | --- | --- |
   | 1 | `<mt5-login>`; at the MT5 password prompt press **Enter** (the brain never needs it: the terminal keeps its own saved login) |
   | 2 | press Enter for `http://127.0.0.1:8765`, then paste the token |
   | 3 | the bot token |
   | 4 | your Telegram id |
   | 5 | your email |
   | 6, 7, 8 | press Enter to skip (backups are set up tomorrow, Part 4) |

3. Remove the token from every screen:
   * **A**: close the `runas` window;
   * **B**: `tmux kill-session -t token`, which also discards its scrollback;
   * a plain window: `clear`, then close it.

   Then delete the bot-token message in @BotFather's chat.

### 5. Preflight (5 min)

brain: `goldbot run preflight`

**Expect:** each line ✅, ⚠️ or ❌. Fix each ❌ with the command it prints, then run preflight again until the last
line says **READY**.

Warnings that are normal today:
* `backups` (set up tomorrow);
* `server_clock` (only when the market is quiet).

**Stop if** `terminal_account` stays ❌ with "margin mode is netting" (or "unknown"). The account is not a hedging
account: open a hedging demo, as in step 2 point 4. If it says "unknown", the bridge box runs older code than the
brain; both must be on `<SHA>`.

**Stop if** `terminal_account` stays ❌ with "another account", "server" or "real". The terminal is logged in to the
wrong account. Redo step 2. Never work around this check: it is what stops a live account being traded as demo.

### 6. Start goldbot (5 min)

brain:
```bash
sudo systemctl start goldbot-supervisor goldbot-api goldbot-telegram goldbot-news goldbot-scheduler
sleep 15 && sudo systemctl start goldbot-engine@icm-demo
sleep 60 && goldbot run health
journalctl -u goldbot-engine@icm-demo -n 20 --no-pager
```

Seed the news calendar now. Otherwise the tier-1 blackout has no calendar until the scheduler's 06:10 UTC run
tomorrow. The scheduler only waits for the next slot after its first start:
```bash
cd /opt/goldbot && sudo -u goldbot -H env PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring .venv/bin/python -c "
import pandas as pd; from goldbot.config import load_settings; from goldbot.data.store import Store
from goldbot.data.econ_calendar import parse_ff_week, fetch_ff_week
s = load_settings(); df = parse_ff_week(fetch_ff_week(), pd.Timestamp.now('UTC'), s.risk.blackout.events)
print(Store(s.data_root).append('calendar_events', df, source='forexfactory'), 'calendar events stored')"
```
**Expect:** a number above 0, then `calendar events stored`.

**Expect:**
* the engine log says `engine icm-demo started (RemoteBroker), models: []`;
* health shows no FAIL lines, apart from the ones listed below;
* Telegram sends a message with a dashboard setup code.

**6W. Engine watchdog acceptance (brain, 8 min).** Systemd restarts an engine whose loop sticks. Prove it once, now,
while the demo account holds no goldbot position:
```bash
systemctl show -p ActiveState,WatchdogUSec,StatusText goldbot-engine@icm-demo
goldbot run health | grep engine_loop
```
**Expect:** `ActiveState=active`, `WatchdogUSec=5min` and `StatusText=engine loop running`, then an
`OK` line for `engine_loop:icm-demo` ("loop turning").

Drill: freeze the loop and watch systemd restart it.
```bash
sudo kill -STOP "$(systemctl show -p MainPID --value goldbot-engine@icm-demo)"; date -u
journalctl -u goldbot-engine@icm-demo -f --no-pager      # Ctrl-C once the engine says "started" again
```
**Expect**, within about 6 min of the `date` line:
* Telegram: `FAIL engine_loop:icm-demo: engine loop silent ...`, may appear after 3-5 min (the health pass runs every 5 min, so it can be missed: the journal lines and `goldbot run health` are the pass criteria);
* journal: `Watchdog timeout`, a Python stack (`Fatal Python error: Aborted`), then the restart;
* `engine icm-demo started (RemoteBroker)` (the start reconciled with the broker), and no ERROR line after it;
* `goldbot run health`: `engine_loop:icm-demo` ok again, and Telegram says `recovered: engine_loop:icm-demo`.

**Stop if** nothing restarts after 8 min. Run `sudo kill -CONT <pid>` and report it.

**Fallback, if there is no time for the drill:** deploy with the watchdog off. The loop heartbeat and the
`engine_loop` health alert stay on.
```bash
sudo mkdir -p /etc/systemd/system/goldbot-engine@.service.d
printf '[Service]\nWatchdogSec=0\n' | sudo tee /etc/systemd/system/goldbot-engine@.service.d/no-watchdog.conf
sudo systemctl daemon-reload && sudo systemctl restart goldbot-engine@icm-demo
```
A stuck loop is then alerted but not restarted: restart it by hand (RUNBOOK "Engine loop stuck, and the watchdogs").
Run the drill at the next quiet moment, then delete `no-watchdog.conf`, run `daemon-reload` and restart the engine.

Normal for now (warnings):
* `backup_age`, `restore_drill`;
* "no measured swap";
* `secrets`: optional keys missing (Anthropic key, GitHub token: add later with `goldbot run setup`);
* `alerts` (in the first minute);
* `deadman` (until step 8b);
* no proposals.

**Check both boxes run the same code:**
* brain: `sudo -u goldbot git -C /opt/goldbot rev-parse HEAD`
* B: `sudo -u mt5 git -C /opt/goldbot rev-parse HEAD`
* A, in the Administrator PowerShell: `git -c safe.directory=C:/goldbot -C C:\goldbot rev-parse HEAD`

Each must print `<SHA>`.

**Check the bridge user can run its Python (option A).** Administrator PowerShell, asks for the `goldbot-bridge`
password:
```powershell
runas /user:goldbot-bridge "cmd /k C:\goldbot\.venv\Scripts\python.exe -c print(1)"
```
The new window must show `1`. Close it.

**Check the bridge box is locked down (option A, 2 min):**
* Windows, Administrator PowerShell: `icacls C:\goldbot`. It must list no `Authenticated Users` and no
  `BUILTIN\Users` entry; only SYSTEM, Administrators and `goldbot-bridge` (read).
* brain: the tunnel key must not run commands. This must **fail**, with no user name printed:
  ```bash
  sudo -u goldbot ssh -o BatchMode=yes -i /var/lib/goldbot/.ssh/id_ed25519 mt5@<windows-tailscale-ip> whoami
  ```
  For option B, use `mt5@<mt5-private-ip>`.

**Stop if** either check fails: stop the bridge (`nssm stop goldbot-bridge-icm-demo`) and report it.

**6B. Switch the brain to option B** (only if A failed):
1. `sudo goldbot-tunnel <mt5-private-ip>`. This restarts the running tunnel onto B.
2. `goldbot run setup`: step 2 only, with the token from B's `bridge-serve` (run in `tmux new -s token`, as in step
   4).
3. `sudo systemctl restart goldbot-engine@icm-demo`, then `goldbot run preflight`.

**Roll back (any time):**
```bash
sudo systemctl stop goldbot-engine@icm-demo goldbot-scheduler goldbot-api goldbot-telegram goldbot-news goldbot-supervisor
```
The demo account has no goldbot positions, so stopping changes nothing at the broker. Start again with the block
above.

### 7. Dashboard owner account (10 min)

1. Mac, a new window (keep it open while you use the dashboard): `ssh -L 8787:127.0.0.1:8787 ubuntu@<brain-ip>`
2. Browser: `http://localhost:8787`.
3. Use the setup code from Telegram. Or, on the brain:
   `journalctl -u goldbot-api --no-pager | grep "setup code" | tail -1`. Always use the newest code.
4. Enter your email (the same as setup step 5) and a password of 12 or more characters from your password manager.
5. Scan the authenticator QR with your phone.
6. Copy the **10 recovery codes** into your password manager now. They are shown once.

**Expect:** you are signed in and the dashboard shows the account, health and the loss budget. The public address
(Cloudflare Tunnel plus Access) is a follow-up; the SSH window above is enough for now.

### 8. Rehearse an alert (8 min)

brain: `sudo systemctl stop goldbot-news`. Wait.

**Expect within 6 minutes:** a Telegram message naming `heartbeat:news` as failing. Then run
`sudo systemctl start goldbot-news`. **Expect** a "recovered" message within about 2 minutes.

**Stop if** no message arrives in 8 minutes. Alerts are not working. Check `journalctl -u goldbot-telegram -n 50`
and leave goldbot stopped until alerts work.

### 8b. Outside alert (7 min)

brain: `goldbot accounts set deadman-url`, then paste the ping URL from P7 at the hidden prompt.

**Expect within 2 minutes:** the check shows **up** on healthchecks.io, and `goldbot run health | grep deadman` says
ok.

Rehearse it: `sudo systemctl stop goldbot-telegram`. **Expect within 6 minutes:** an email (and a Telegram message, if
you added it in P7) from Healthchecks.io saying `goldbot brain` is **down**. Then `sudo systemctl start
goldbot-telegram`. **Expect** an **up** notice within about 2 minutes.

**Stop if** the check never turns up: `journalctl -u goldbot-telegram | grep "dead man"` says why (RUNBOOK section 6
"Outside alert"). goldbot may keep running on demo, but fix it before going live.

### 9. Write down (2 min, in your own notes, not the repository)

* The time each step finished.
* brain: `free -m`.
* B: `free -m` and `ps -o rss,cmd -u mt5 | sort -n | tail -3`.
* A: Task Manager -> Memory.

These are the first real memory figures on these boxes.

---

## Part 3: the first-trade check (when the first trade card ever arrives)

**Is the "position ticket fix" in your commit?** On the Mac:
`git -C ~/myproject/goldbot cat-file -e <SHA>:tests/test_mt5_position_ticket.py && echo included`. It covers a known
bug: the engine stored the deal number where the position number belongs, so its own trade looked like a stranger's
("orphan"). **Without `included`, tap Reject on every trade card** until an update brings it.

After the fix is installed, check the first approved trade within 5 minutes:
1. MT5 on your phone (log in to the same demo account): Trade tab -> the position's **ticket** number.
2. brain:
   ```bash
   sudo -u goldbot python3 -c "import json;d=json.load(open('/opt/goldbot/state/orders_icm-demo.json'));print({k:v.get('agent_id') for k,v in d['open'].items()})"
   ```
3. **Pass:** exactly one entry, its number equals the MT5 ticket, and the name is a strategy, not `orphan`.
4. **Fail:** two entries, a different number, or `orphan`. Send `/halt` in Telegram (new entries stop; exits keep
   running and the broker keeps the stop and target), then report it.

Do not trade this demo account by hand: goldbot's loss limits count every trade on it.

## Part 4: the next 24 hours

| When (UTC) | Look at | Good looks like |
| --- | --- | --- |
| through the day | Telegram | no FAIL alert; if one comes, it names the check, and `goldbot run health` says why |
| through the day | healthchecks.io | `goldbot brain` stays up; a DOWN email: RUNBOOK section 6 "Outside alert" |
| 06:10 next day | `goldbot run health` -> `scheduler` | `calendar_archive` ran (the news blackout needs it) |
| 06:45 next day | Telegram digest | arrives, status ✅, or ⚠️ only for backups/swap |
| +24 h | `systemctl show -p NRestarts goldbot-engine@icm-demo goldbot-bridge-tunnel` | 0, or a few during setup; not growing |
| +24 h | bridge restarts: B `systemctl show -p NRestarts goldbot-bridge@icm-demo`; A `C:\goldbot\logs\goldbot-bridge-icm-demo.err` | not growing |
| +24 h | memory, as in step 9 | swap/pagefile in use but steady; no "Out of memory" in `journalctl -k` (Linux) |
| +24 h | Oracle and Azure email, Azure Cost Management | no idle notice, cost 0 |
| +24 h | `goldbot run health` -> `server_clock:icm-demo`, `bridge_compat:icm-demo` | ok |
| day 2 (30 min) | backups: RUNBOOK 0.6, then `goldbot run backup --init`, `goldbot run backup`, `goldbot run restore-drill` | `backup_age` ok; without a backup it fails after 72 h |
| +24 h | pick the bridge | the option with no restarts and steady memory stays; stop and delete the other VM |

**Weekly:** the Sunday 10:00 UTC restore drill passes; no Oracle "idle" email (the VM stops, not deletes, if
reclaimed: start it again and tell the SRE).

## Updates later: the MT5 box always goes first

From bridge version 4, a newer brain refuses to start against an older bridge, because it cannot see the hedging
mode. So every update goes:
1. The bridge box first: option A `.\goldbot\ops\vps_update.ps1 -Ref <SHA> -BridgeOnly` (Administrator
   PowerShell, in `C:\goldbot`); option B `sudo goldbot-deploy <SHA>` on mt5.
2. Then the brain: Telegram **Deploy**, or `sudo goldbot-deploy <SHA>`.

After both, `goldbot run preflight | grep bridge_compat` must be ✅.

## Follow-ups (not in this deploy)

1. Position ticket fix, open-risk heat cap, API request validation: separate review lanes.
2. Cloudflare Tunnel plus Access for the dashboard from your phone (needs a domain on Cloudflare).
3. Tailscale for option B too, replacing the Oracle-private link (one link design for both options).
4. A main-loop heartbeat for the engine. Today a hung engine counts as stale only after 45 min of open market (a
   crashed one restarts within 10 s).
