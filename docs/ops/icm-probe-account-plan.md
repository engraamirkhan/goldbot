# Plan: second MT5 terminal + bridge for the `icm-probe` demo account

Status: assessment and plan (SRE, 2026-10-19). Read-only: nothing was installed, started or changed on either server.
Owner approval 2026-10-19: a dedicated second IC Markets MT5 demo account `icm-probe` for demo learning probes
(directors' ruling: probes must not share the real-trading account's equity or limits).
Hosts are named by their SSH alias only (`goldbot-mt5`, `goldbot-brain` in `~/.ssh/goldbot.conf`). This file names no
logins, hosts or IPs.

## 1. Capacity, measured 2026-10-19 13:44 UTC

| | goldbot-mt5 (Wine box) | goldbot-brain |
|---|---|---|
| Shape | x86_64, 2 vCPU shown (Always Free E2.1.Micro: 1/8 OCPU baseline, burstable) | aarch64 A1, 2 OCPU |
| RAM | 954 MB total, 409 MB available, 94 MB free | 11.9 GB total, 9.97 GB available |
| Swap | 4 GB file, **442 MB in use**, swappiness 60, si/so about 0 at rest | none |
| Disk `/` | 48 GB, 33 GB free (MT5 install with history 771 MB) | 48 GB, 43 GB free |
| Load | 0.2 to 0.4 | 0.03 |
| PSI memory | 0.00 (no stalls now) | 0.00 |
| OOM kills in 3 days | 0 | not checked |

Memory of the current MT5 stack on the Wine box (RSS + swapped out):

| Process | RSS | Swapped |
|---|---|---|
| terminal64.exe | 96 MB | 153 MB |
| bridge (Wine Python) | 58 MB | 110 MB |
| wineserver + Wine services (services, winedevice x2, plugplay, explorer, rpcss, lsass, svchost) | about 40 MB | about 66 MB |
| Xvfb | 16 MB | 1 MB |
| Terminal cgroup (systemd) | 146 MB now | **peak 562 MB**, swap peak 356 MB |

Terminal CPU: 1 h 15 min over 21 h, about 6 % of one core on average.

What a second terminal + bridge costs: about 420 MB steady (250 MB terminal + 170 MB bridge), more at its first start
while it downloads history (the first terminal peaked at 562 MB). A separate Wine prefix adds about 100 MB for its own
wineserver and services. That prefix is needed on a shared box, see risk R2.

Verdict:
- **Brain: fits easily.** One more engine (`goldbot-engine@icm-probe`, about 360 MB RSS like `icm-demo`) uses under
  5 % of available RAM. Disk and CPU are not a concern.
- **Wine box: does not fit safely.** Today the box already keeps 442 MB in swap with 409 MB available. A second
  stack would put the combined working set (about 1.3 to 1.5 GB) above physical RAM, so the box would run on swap.
  It would probably start and run, but in a fast market the `icm-demo` terminal and bridge would page. That terminal
  manages the exits of the account that trades. CPU is also tight: two terminals on a 1/8 OCPU baseline burst
  together exactly when the market moves. The probe must not add risk to the trading account's exits.

Recommendation, needing the owner's approval because it is a server deployment:
**Option B, a second Always Free E2.1.Micro VM (`goldbot-mt5-probe`) for the probe terminal.** Always Free allows two
AMD micro VMs. The owner confirms in the Oracle console that the second slot is free. This is the same build as
`goldbot-mt5` (`mt5_bootstrap.sh`) and costs £0. The failure domains are separate: a probe terminal crash, update,
OOM or reboot cannot touch `icm-demo`. No port, permitopen or watchdog change is needed on the existing box.
- Option A (same box, own Wine prefix) is the fallback only if the second micro slot is taken. It needs the code
  changes in section 2.4 and a memory guard (`MemoryHigh=`/`MemoryMax=` and `OOMScoreAdjust=+500` on the probe's
  units, so the kernel kills the probe first). Before going ahead, measure 7 days of the probe stack's RSS + swap and
  of `icm-demo` bridge latency.
- Rejected: the probe terminal on the brain or on another A1 VM. The brain is ARM and MT5 is an x86 Windows binary;
  it would need x86 emulation under Wine, which is unproven, and this project's rule is the simplest design that is
  still right in a year.

## 2. How one account runs today, and what a second one needs

### 2.1 Today: one account, `icm-demo`

- `config/accounts.yaml`: the account entry (`server`, `terminal_path`, `server_tz`, `magic_base`, `mode`,
  `approval_mode`, `risk_overrides`, `cost_probe`), always with `login: null`.
- Secrets. On the MT5 box they live in `/opt/goldbot/state/.secrets.json`, a file store because Wine Python has no
  keyring; the state directory is 0700 mt5. Keys: `mt5-login-icm-demo`, `mt5-bridge-listen-icm-demo` (127.0.0.1:8765)
  and `mt5-bridge-token-icm-demo`. On the brain, `/opt/goldbot/state/.secrets.json` (0600 goldbot) holds
  `mt5-login-icm-demo`, `mt5-bridge-url-icm-demo` (http://127.0.0.1:8765) and `mt5-bridge-token-icm-demo`. **No MT5
  password is stored on either server.** The terminal keeps its own saved login (password typed once over VNC), and
  the bridge attaches with `login=None` (`goldbot/ops/services/bridge.py`).
- The bridge checks the terminal's logged-in account on every RPC against the stored login, the server, demo vs real
  and hedging (`accounts.verify_terminal_account`). A terminal on the wrong account is refused.
- Units on the Wine box: `goldbot-xvfb` (display :99), `goldbot-mt5-terminal@<acct>` (the only launcher;
  `ExecStart` hardcodes `C:\MT5\ICMarkets`, and the "one terminal ever" guard matches any terminal64.exe),
  `goldbot-bridge@<acct>` (waits for any terminal64.exe), and `goldbot-bridge-watchdog@<acct>.timer` (port 8765
  hardcoded in the `.service`). On the brain: `goldbot-bridge-tunnel.service` (one `ssh -N -L
  127.0.0.1:8765:127.0.0.1:8765 mt5@${MT5_HOST}`, with `MT5_HOST` in `/etc/goldbot/bridge.env`) and
  `goldbot-engine@<acct>`.
- The tunnel key on the box is restricted to `permitopen="127.0.0.1:8765"` (`goldbot-mt5-authorize` in
  `mt5_bootstrap.sh`).
- Single-account hardcoding: `mt5_bootstrap.sh` (enables `...@icm-demo`), `mt5-box-upgrade.sh` (`ACCOUNT=icm-demo`,
  `PORT=8765`, `C:\MT5\ICMarkets\Config`), `mt5/mt5-gold-profile.sh` (`MT5_DIR` defaults to `ICMarkets`; it can be
  overridden), and `goldbot-chart-capture.sh` (screenshots the whole display :99).

### 2.2 Prerequisites, in this order (blocking)

1. **Wine box upgrade, Sat 24 Oct (already planned, RUNBOOK 0.5, `mt5-box-upgrade.sh`).** Measured today: the box
   is on `dbd5fa2` (17 Oct). The `goldbot-mt5-terminal@.service` template is **not installed**. The terminal runs in
   a hand-made transient unit `goldbot-mt5-terminal-check` with no `Restart=`, and the installed `goldbot-bridge@`
   has no `Requires=` on the terminal. So a crashed terminal stays down until someone restarts it by hand, and a
   reboot does not start it at all. The watchdog's terminal restart targets a unit that does not exist. This is the
   top reliability risk today, independent of the probe. The new box must be built from the same upgraded unit set.
2. **Limits separation (architect + trading-safety, not SRE; must land before any probe order).** The supervisor
   sums the equity and day/week start equity of every engine (`goldbot/risk/supervisor.py:190`), and the runner adds
   `other_equity` (`goldbot/engine/runner.py:1952`). An `icm-probe` engine would therefore share the combined caps
   with `icm-demo`, which is exactly what the directors' ruling forbids. In addition:
   (a) if the probe account opens in USD while `icm-demo` is GBP, the supervisor halts entries on **every** account
   (`mixed_account_currencies`, fail closed);
   (b) `supervisor_ceilings` falls back to the default caps unless every enabled account is demo with the owner's
   ceilings, so an `icm-probe` entry without `risk_overrides` would tighten `icm-demo` without anyone noticing.
   The ADR 0037 cost-probe logic also lives inside the `icm-demo` engine today, so moving probes to their own account
   needs an engine change and an ADR 0037 amendment. Recommendation: exclude `icm-probe` from the combined caps, give
   it its own daily cap, open the account in **GBP** (hedging, Raw) and keep `enabled: false` until that lands.
3. Owner opens the `icm-probe` demo account at IC Markets: GBP, Raw Trading, hedging, `ICMarketsSC-Demo`. This is a
   sign-up, so it is the owner's step.

### 2.3 Steps with Option B (new micro VM `goldbot-mt5-probe`)

| # | Step | Who |
|---|---|---|
| B1 | Approve Option B and create the E2.1.Micro (Ubuntu 24.04, same VCN/subnet as the brain, no public ingress beyond SSH). Claude can do it if the OCI CLI is signed in on the Mac. | **Owner** (server deployment decision; console or CLI login) |
| B2 | Add `Host goldbot-mt5-probe` to `~/.ssh/goldbot.conf` (alias only in the repo) | Claude |
| B3 | Code PR (section 2.5): parameterise `mt5_bootstrap.sh` and `mt5-box-upgrade.sh` by `ACCOUNT`, add a templated tunnel `goldbot-bridge-tunnel@.service`, add the `icm-probe` entry to `accounts.yaml` (`enabled: false`) | Claude, via the lanes and CI |
| B4 | `sudo bash mt5_bootstrap.sh` on `goldbot-mt5-probe` with `ACCOUNT=icm-probe`: Wine, Python, MT5 at `C:\MT5\ICMarkets` (so `terminal_path` matches the yaml), units, swap, apport off | Claude, after the owner approves the deploy |
| B5 | Owner stores the login once on the brain: `python -m goldbot.ops.accounts add icm-probe`. That command also stores the MT5 password, which nothing on the brain uses. The smaller alternative stores only the login: `goldbot accounts set mt5-login-icm-probe`. | **Owner** (secret entry, keyring/file store, never in chat) |
| B6 | Copy the login to the probe box without showing it: `goldbot accounts export mt5-login-icm-probe \| ssh goldbot-mt5-probe goldbot-mt5 accounts set mt5-login-icm-probe --stdin` (`export` refuses a terminal stdout) | Claude |
| B7 | `systemctl start goldbot-mt5-terminal@icm-probe`, then the owner logs in **once** over VNC: File > Login to Trade Account, IC Markets Raw Trading Ltd, server `ICMarketsSC-Demo`, "Save password". The installer has no IC Markets servers (incident 2026-10-16-mt5-login-generic-installer), so the account is added via File > Open an Account. | **Owner** (password typed only into the terminal) |
| B8 | `bridge-serve icm-probe` on the probe box (listen `127.0.0.1:8765`, new token), then move the token with `export \| ssh goldbot-brain ... set mt5-bridge-token-icm-probe --stdin` and set `mt5-bridge-url-icm-probe` = `http://127.0.0.1:8766` on the brain | Claude |
| B9 | On the brain, authorise the brain's tunnel key on the probe box (`goldbot-mt5-authorize`, permitopen 8765, unchanged) and write `/etc/goldbot/bridge-icm-probe.env` (`MT5_HOST`, `LOCAL_PORT=8766`, root-owned 0644) | Claude (root, reviewed by security) |
| B10 | Enable `goldbot-bridge@icm-probe` and `goldbot-bridge-watchdog@icm-probe.timer` on the probe box, and `goldbot-bridge-tunnel@icm-probe` on the brain. Check `/health` through the tunnel and `preflight icm-probe` (bridge_server_tz, verify_terminal_account ok, hedging) | Claude |
| B11 | Cold-restart test of the probe box (reboot: terminal, bridge and tunnel come back with no hands), recorded in the deploy note | Claude |
| B12 | After prerequisite 2 has landed: set `enabled: true` and start `goldbot-engine@icm-probe` | Claude, with the PD/PO ruling recorded |

Port plan: each MT5 box keeps the bridge on its own 127.0.0.1:8765. The brain's local ends are 8765 (`icm-demo`,
unchanged) and **8766** (`icm-probe`). Nothing else on the brain listens on 8766; checked with `ss` before B9.

### 2.4 Option A only (same box), additional changes

- A second Wine prefix `/home/mt5/.wine-icm-probe`. Without it, the wineserver lives in the cgroup of whichever
  terminal started first, so restarting `icm-demo`'s terminal unit would kill the probe terminal and both bridges,
  and the reverse.
- `goldbot-mt5-terminal@.service` and `goldbot-bridge@.service`: take `WINEPREFIX` and the install directory from
  `/etc/goldbot/mt5-%i.env` (root-owned). Scope the "one terminal ever" guard and the bridge's wait to that
  directory's terminal64.exe, not to any terminal of user mt5.
- `goldbot-bridge-watchdog@.service`: read the port from the env file instead of the hardcoded 8765.
- `goldbot-mt5-authorize`: `permitopen` for 8765 and 8766. The tunnel forwards 8766 to the box's 8766.
- `mt5-box-upgrade.sh` "exactly one terminal64.exe" becomes "one per enabled account". The chart capture must pick
  `icm-demo`'s window, or use a second Xvfb display `:98` for the probe.
- Memory guard on the probe units (see section 1) and a box-level `MemAvailable` health check.

### 2.5 Code changes either way (one PR, lanes: implementer, test-engineer, code-reviewer, security-reviewer, sre)

- `goldbot-bridge-tunnel@.service` templated per account. Keep `goldbot-bridge-tunnel.service` as `icm-demo` until
  the next attended window, so nothing on the trading path moves now.
- `mt5_bootstrap.sh` and `mt5-box-upgrade.sh`: `ACCOUNT` as an argument, defaulting to `icm-demo`. Tests in
  `tests/test_mt5_box_upgrade_sh.py`, shellcheck clean.
- `accounts.yaml` `icm-probe`: `broker: icm`, `mode: demo`, `server: ICMarketsSC-Demo`, `login: null`,
  `terminal_path: C:\MT5\ICMarkets\terminal64.exe`, `server_tz: Europe/Athens`, `magic_base: 260500`,
  `approval_mode: auto`, `risk_overrides` as `icm-demo`, `enabled: false`.
- Health: confirm that the per-account checks (bridge, heartbeat, server_clock, auth keys) iterate over enabled
  accounts and pick up `icm-probe` with no code change; add a test that proves it. Add a host check for
  `goldbot-mt5-probe` (disk, MemAvailable, swap, time sync) like the existing box has.
- RUNBOOK: "Probe box" section covering terminal down, re-login over VNC, tunnel, restore of the terminal Config.

## 3. Risks, ranked

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | The probe engine shares the supervisor's combined caps with `icm-demo`, or halts all entries (USD account, or missing ceilings) | High: a probe loss blocks real-trading entries, or every account halts | Prerequisite 2: exclude it from the combined caps, open the account in GBP, set `risk_overrides`, keep `enabled: false` until reviewed |
| R2 | Memory pressure on the 1 GB Wine box if co-hosted (Option A): swap paging of the `icm-demo` terminal in fast markets, OOM | High: late exit management on the trading account | Option B. If A: own prefix, `MemoryMax` + `OOMScoreAdjust` on the probe, MemAvailable/PSI health check, 7-day measurement first |
| R3 | The Wine box today has no restart path for the terminal (transient unit, template not installed) | High, independent of the probe | Sat 24 Oct attended upgrade (already planned). The probe box is built from the upgraded units from day one |
| R4 | MT5 auto-update: each terminal updates itself on its own restart, so the two can run different builds. A build may break under the pinned Wine (incident 2026-10-16-mt5-debugger-found-under-wine) or the bridge protocol | Medium: one terminal down after a restart | Separate boxes isolate it. `/health` reports the build; add a check "terminal build differs from the other box / last known good" (WARN). Never restart both terminals in the same window |
| R5 | Bridge port conflict on the brain (two tunnels on 8765) | Medium: the probe engine talks to `icm-demo`'s bridge | The bridge's `verify_terminal_account` refuses every call when the login differs, so it fails closed. Distinct local port 8766, checked with `ss` before enabling |
| R6 | Backups: the probe terminal's saved login and servers (`Config/`) exist only on the box. The `icm-demo` Config backup is on-host only (`~mt5/config-backup`) | Low-medium: a lost box means the owner logs in over VNC again (a 5-minute owner step) | Rebuild is scripted. Add the encrypted off-host copy of both terminals' `Config/` to restic, since it holds a saved credential (the security reviewer decides whether it may leave the box). Brain state, including `aux.db cost_probes`, is already in restic |
| R7 | Oracle can reclaim idle Always Free instances (low CPU, network and memory over 7 days) | Low-medium | Same exposure as the current box. The deadman alert covers a vanished box. The rebuild is scripted (restore rehearsal: bootstrap + one owner VNC login) |
| R8 | The owner step B5 stores an unused MT5 password on the brain (plaintext file store) | Low | Prefer `accounts set mt5-login-icm-probe` (login only) |

Minor findings from this read (no action taken):
- `goldbot-deploy-manual.service` has sat in `failed` on the Wine box since 16 Oct (CI not passed on that commit).
  Clear it with `systemctl reset-failed` in the 24 Oct window, so a new failed unit is not hidden behind it.
- The comment in `goldbot-engine@.service` says "1 GB brain". The brain is A1 with 12 GB and no swap. Fix it in the
  same PR.

## 4. Dates and owner asks

- 24 Oct (Sat), attended: Wine box upgrade (prerequisite 1).
- Owner, any time: approve Option B (or A) and create the micro VM (B1); open the GBP demo account (prereq 3); B5 and
  B7 once the box is built.
- Claude: the PR in section 2.5 and the prerequisite 2 lane hand-off to architect + trading-safety, both this week.
  The probe box build follows approval of B1, the day it is approved.
