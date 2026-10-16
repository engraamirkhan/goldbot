# Tonight's demo deploy, driven by Claude (Fri 16 Oct 2026)

SRE, 2026-10-16. For Aamir. Release **bc030d3** (on `main`, CI green). Oracle only: the brain runs on A1, and the
MT5 bridge runs under Wine on an E2.1.Micro (first-deploy.md option B), linked over Oracle's private network. No
Azure and no Tailscale tonight. Azure option A comes later as an update.

How it works: Claude runs every command from the Mac, and you approve each one at Claude Code's prompt. You type
every secret yourself into a hidden prompt, in a Terminal window attached to a tmux session that Claude opened. No
secret passes through Claude, a command line, the repo or a log.

This uses the existing scripts at bc030d3. No new tooling. The full design for Claude-run deploys (Keychain, narrow
permission rules, Azure) is separate future work.

**Clock.** Start about 16:05 UTC (17:05 London). Gold closes at **21:00 UTC (22:00 London)** and reopens Sun
22:00 UTC. Steps marked **[ticks]** need the market open.

## Rules

* `goldbot-brain` and `goldbot-mt5` are SSH aliases that the provisioning step writes into `~/.ssh/config`
  (`Include ~/.ssh/goldbot.conf`). Claude uses the aliases. IP addresses stay in that file and are never printed into
  the chat.
* Claude never runs `tmux capture-pane`, `cat state/.secrets.json`, `cat config/settings.local.yaml` or `security
  find-*` against these boxes or the Mac. You may refuse any prompt that does.
* Hidden prompts are typed in the **Terminal app**, not with `!` in Claude Code. A `!` command has no usable
  terminal for a hidden prompt, and its output goes into Claude's context.
* Demo only. Nothing here touches `unlock-live`, RiskGate or any limit.

## 1. Owner steps (about 75-85 min in total)

| # | When | Step | Min |
| --- | --- | --- | --- |
| O1 | now | **Oracle sign-up** (oracle.com/cloud/free). Home region **UK South (London)**, which cannot be changed later. Turn on **MFA**. Billing -> Budgets -> a budget of **1** with an email alert. | 15 |
| O2 | now | **PAYG upgrade**: Billing -> Upgrade to Pay As You Go. Approval can take minutes or up to a day. It is **not** needed to create Always Free VMs, so do not wait for it. It helps with "out of capacity" on A1 and with idle reclamation. | 3 |
| O3 | now | **Mac CLI** (Terminal app): `brew install oci-cli`, then `oci setup config`. Enter your user OCID, tenancy OCID (Profile menu) and region `uk-london-1`. Let it generate the key, with **no passphrase**. Console -> Profile -> **API keys** -> Add API key -> paste `~/.oci/oci_api_key_public.pem`. Check: `oci iam region list --output table` prints a table. Also check that `~/.ssh/id_ed25519.pub` exists (if not: `ssh-keygen -t ed25519`). | 13 |
| O4 | now | **Telegram**: @BotFather -> `/newbot`. Keep the token in that chat for now. Open your new bot and press **Start**. Message @userinfobot to see your numeric id. Keep both in your password manager. | 5 |
| O5 | now | **Healthchecks.io**: sign up, Add Check `goldbot brain`, Simple, period **2 min**, grace **3 min**. Copy the ping URL into your password manager. It is a secret. | 5 |
| O6 | session | **MT5 first login** over VNC (section 3.3). You need the demo login and password. | 10 |
| O7 | session | **Secrets** into hidden prompts (section 3.4): bridge-serve on the MT5 box, `goldbot run setup` on the brain, then the ping URL. Then delete the token message in BotFather. | 15 |
| O8 | session | **Dashboard**: setup code, email, password, **scan the authenticator QR**, save the 10 recovery codes (section 4). | 10 |

What cannot be automated, and why:
* **O1-O2**: identity, card, CAPTCHA and MFA must be you.
* **O3**: you create the API key, so you also hold the power to revoke it.
* **O4**: Telegram has no API for creating a bot, and a bot cannot message you until you press Start.
* **O6-O8**: the secrets and your authenticator are yours alone; Claude must never see them.

## 2. Provisioning (VCN, security list, instances): owned by another agent

See that agent's section. Two requirements from this plan:
* **SSH (22) must be allowed from the subnet's own CIDR**, as well as from your IP. The brain's bridge tunnel is SSH
  from the brain to the MT5 box over the private network (`goldbot-tunnel <mt5 private ip>`).
* **No rule for 8765.** The bridge listens on 127.0.0.1 only and is reached through that tunnel.

## 3. Bootstraps, prompts and secrets

### 3.0 Release check (Claude, Mac, 1 min)
This is first-deploy.md P6, from your own clone (off-box, so the hashes are trustworthy):
```bash
cd ~/myproject/goldbot && git fetch -q origin
SHA=bc030d3aceb89ab7f146672705d6ebe6360e8258
git merge-base --is-ancestor $SHA origin/main && echo on-main
gh api repos/engraamirkhan/goldbot/commits/$SHA/check-runs --jq '.check_runs[] | "\(.name) \(.conclusion)"'
for f in brain_bootstrap.sh mt5_bootstrap.sh; do echo "$f $(git show $SHA:goldbot/ops/linux/$f | shasum -a 256 | cut -c1-64)"; done
```
**Stop if** `on-main` is missing, or `backend` or `frontend` is not `success`.

### 3.1 What the scripts ask

| Script / command | Interactive prompts | Secret? | Who types |
| --- | --- | --- | --- |
| `brain_bootstrap.sh` | **none** (apt is noninteractive; `ssh-keygen -N ''`; uv and npm need no input) | - | nobody: Claude runs it detached |
| `mt5_bootstrap.sh` | **none** in the script. Risk: `wineboot -i` can open a Wine Mono/Gecko install dialog on the invisible display and wait (not verified) | - | nobody (see 3.2 if it stalls) |
| `goldbot-mt5 accounts bridge-serve icm-demo` (MT5 box) | MT5 login number (visible); listen address (visible, `127.0.0.1:8765`); then **prints the bridge token once** | login: identifier; token: **secret, on screen** | you |
| `goldbot run setup` (brain) | 1 MT5 login (visible), MT5 password (hidden: press **Enter**, the brain does not need it); 2 bridge URL (Enter), **bridge token (hidden)**; 3 **bot token (hidden)**; 4 Telegram id (visible); 5 email (visible); 6-8 Enter to skip | yes | you |
| `goldbot accounts set deadman-url` (brain) | **ping URL (hidden)** | yes | you |
| MT5 terminal login (VNC) | login, **password**, server | yes | you |

**Can a secret go over stdin from the Keychain instead?** Not with these prompts:
* `goldbot run setup` refuses to run without a terminal (`setup_wizard.py` `main`).
* `accounts._prompt` raises when stdin is not a terminal.
* Python's `getpass` flushes any input typed ahead (`TCSAFLUSH`), so piping into `ssh -tt` is unreliable.

A stdin path is possible at bc030d3 by calling `goldbot.ops.accounts.set_secret` from `python -c`, with the value
piped from `security find-generic-password -w`. But that puts the secret within Claude's reach, and it bypasses the
wizard's checks. **Tonight: you type every secret.**

### 3.2 Run both bootstraps (Claude, detached, about 25 min for the brain and 30-45 min for the MT5 box)
Claude starts each in a detached tmux session on the box. A dropped connection does not stop it, and the script also
logs to `/root/goldbot-bootstrap.log`. `<HASH-…>` is the value from 3.0.
```bash
ssh goldbot-mt5 "tmux new -d -s boot 'curl -fsSLo mt5_bootstrap.sh https://raw.githubusercontent.com/engraamirkhan/goldbot/$SHA/goldbot/ops/linux/mt5_bootstrap.sh && echo \"<HASH-mt5>  mt5_bootstrap.sh\" | sha256sum -c - && sudo bash mt5_bootstrap.sh $SHA; exec bash'"
ssh goldbot-brain "tmux new -d -s boot 'curl -fsSLo brain_bootstrap.sh https://raw.githubusercontent.com/engraamirkhan/goldbot/$SHA/goldbot/ops/linux/brain_bootstrap.sh && echo \"<HASH-brain>  brain_bootstrap.sh\" | sha256sum -c - && sudo bash brain_bootstrap.sh $SHA; exec bash'"
# progress (no secrets in either log at this stage)
ssh goldbot-mt5 'sudo tail -n 5 /root/goldbot-bootstrap.log'; ssh goldbot-brain 'sudo tail -n 5 /root/goldbot-bootstrap.log'
```
**Expect:** `MT5 box installed` and `brain installed`.

**If the MT5 bootstrap stalls** at the Wine step for more than 10 min with no new log line, look at the display:
start VNC as in 3.3, and close any Wine dialog with **Cancel**.

Then Claude checks:
* `cat /proc/sys/kernel/core_pattern` prints `|/bin/false` on both boxes;
* `cat /etc/goldbot/rollback-floor` on the brain prints the SHA;
* `git rev-parse HEAD` as the service user (`sudo -u goldbot` on the brain, `sudo -u mt5` on the MT5 box) prints the
  SHA on both.

Claude reads the brain's tunnel public key and authorises it on the MT5 box. It is a public key, so it is safe to
handle:
```bash
KEY=$(ssh goldbot-brain 'sudo cat /var/lib/goldbot/.ssh/id_ed25519.pub')
ssh goldbot-mt5 "sudo goldbot-mt5-authorize '$KEY'"        # expect: authorised (forwarding to 127.0.0.1:8765 only)
```

### 3.3 MT5 first login under Wine (you, over VNC, 10 min)
Claude:
```bash
ssh goldbot-mt5 'sudo systemctl start goldbot-vnc'
ssh -f -N -L 5900:localhost:5900 goldbot-mt5            # background tunnel on the Mac
ssh goldbot-mt5 "pgrep -f terminal64 >/dev/null || sudo -u mt5 -H env DISPLAY=:99 WINEDEBUG=-all nohup wine 'C:\\MT5\\ICMarkets\\terminal64.exe' >/dev/null 2>&1 &"
```
You: Finder -> Go -> Connect to Server -> `vnc://localhost:5900`. Then, in the MT5 window:
1. File -> Login to Trade Account: your login, your password, server **ICMarketsSC-Demo**, tick **Save password**.
2. Check that **XAUUSD** is in Market Watch.
3. Tools -> Options -> Expert Advisors -> tick **Allow algorithmic trading**.
4. Check the account says **Hedge**. If it says Netting, open a hedging demo in the IC Markets client area and use
   that account instead.
5. **File -> Exit**.

Claude then: `ssh goldbot-mt5 'sudo systemctl stop goldbot-vnc'`, and closes the tunnel
(`pkill -f 'L 5900:localhost:5900'`). The VNC server has no password and listens on the box's localhost only, so it
stays off except for this step.

**Fallback if VNC fails:** MT5's start config (`terminal64.exe /config:<ini>`, `[Common]` `Login`, `Password`,
`Server`; MetaQuotes "Platform Start" help) could do the first login. The ini would hold the password in a file, so
it needs a reviewed procedure: you type it via `read -s` into an mt5-only 0600 file, it is shredded afterwards, and
the result is checked with preflight. **Not for tonight.** If VNC fails, stop at this point (section 5).

### 3.4 Secrets (you type them; about 15 min)
Claude opens two sessions. Each runs one command and then a shell:
```bash
ssh goldbot-mt5  "tmux new -d -s token 'goldbot-mt5 accounts bridge-serve icm-demo; exec bash'"
ssh goldbot-brain "tmux new -d -s setup 'goldbot run setup; exec bash'"
```
You, in the **Terminal app**:
1. `ssh -t goldbot-mt5 tmux attach -t token`.
   * Type the MT5 login, then `127.0.0.1:8765`.
   * Copy the token it prints into your clipboard.
   * Detach with `Ctrl-b d`.
2. `ssh -t goldbot-brain tmux attach -t setup`.
   * Answer as in the table in 3.1, and paste the token at step 2.
   * Detach with `Ctrl-b d` when it says `Setup finished`.
3. Claude (no secret in either command):
   * `ssh goldbot-mt5 'tmux kill-session -t token; sudo systemctl restart goldbot-bridge@icm-demo'`. Killing the
     session also discards its scrollback, which held the token.
   * `ssh goldbot-brain 'tmux kill-session -t setup'`.
   * Then `sudo goldbot-tunnel <mt5 private ip>` on the brain, with the address read from `~/.ssh/goldbot.conf` by
     the command itself, never typed into the chat.
4. Delete the bot-token message in BotFather's chat, and clear your clipboard.

The outside alert goes in later (section 4): Claude opens `tmux new -d -s dm 'goldbot accounts set deadman-url; exec bash'`,
you attach and paste the ping URL at the hidden prompt, and Claude kills the session.

## 4. Preflight, start, health, alert rehearsal, outside alert: market-closed behaviour owned by another agent

The steps follow first-deploy.md sections 5-8b unchanged, with Claude running each command:
1. `goldbot run preflight` until **READY**.
2. Start the services.
3. Seed the calendar.
4. 6W watchdog drill, or its no-watchdog fallback.
5. Dashboard (O8: Claude opens `ssh -f -N -L 8787:127.0.0.1:8787 goldbot-brain`, and you open
   `http://localhost:8787`).
6. Alert rehearsal (stop and start `goldbot-news`).
7. Outside alert (`deadman-url`, then stop and start `goldbot-telegram`).

The other agent's section says which of these need **[ticks]** and how preflight and health behave after 21:00 UTC.

## 5. Stop and rollback points

| Point | Stop if | Roll back |
| --- | --- | --- |
| 3.0 release check | not on main, or CI not green | nothing has been created |
| A1 creation | out of capacity after the retry loop | use a second E2.1.Micro as the brain (the bootstrap adds 4 GB swap; dashboard build and retrain are slow) |
| 3.2 bootstrap | the hash check fails, or the script stops with an error (read the end of `/root/goldbot-bootstrap.log`) | terminate that instance and create it again; a fresh VM is faster than repairing one |
| 3.2 core_pattern | it shows apport or systemd-coredump | `sudo sysctl -p /etc/sysctl.d/60-goldbot-no-core.conf` and check again **before any secret goes in** |
| 3.3 MT5 login | the login fails, the terminal will not open, or the account is Netting | stop for tonight; nothing runs yet, and no secret is on the brain |
| 3.4 secrets | a token was pasted into the wrong place (chat, a visible prompt) | treat it as leaked: BotFather `/revoke` for the bot token; a new `bridge-serve` for the bridge token; then run setup again |
| preflight | `terminal_account` ❌ "another account", "server" or "real" | never work around it: redo 3.3 |
| after start | an engine or the bridge crash-loops, or alerts do not arrive within 8 min | `sudo systemctl stop goldbot-engine@icm-demo goldbot-scheduler goldbot-api goldbot-telegram goldbot-news goldbot-supervisor` on the brain. The demo holds no goldbot position, so nothing changes at the broker |
| 21:00 UTC | the market closes before the **[ticks]** checks | leave the services running (entries are blocked anyway) and finish the tick checks at Sun 22:00 UTC or Mon morning |

Once the services are stopped, they start again with the block in first-deploy.md section 6. A failed
`goldbot-bridge@icm-demo` on the MT5 box changes nothing while the engine is stopped.

## Risks for tonight

1. **Time.** Oracle account activation, plus a possible A1 retry, plus 45 min of Wine install on 1/8 OCPU. If the
   MT5 box is not logged in by about 19:30 UTC, the **[ticks]** checks slip to Sunday night.
2. **Wine first boot may stall** on a Mono/Gecko dialog (3.2). This is not verified on 1 GB.
3. **Security list.** Without SSH from the subnet CIDR, the tunnel never comes up. `goldbot-bridge-tunnel` then
   stays `activating`.
4. **Micro brain** (if there is no A1): `npm run build` in 1 GB plus swap can take 20-40 min.
5. **The token is on screen** in the attached tmux session. That is safe only because Claude never captures the
   pane, and the session is killed straight after.
