# ICM depth host (`icm-depth` on `goldbot-mt5-depth`): build plan

SRE, 2026-10-27. Source: docs/research/volume-500-plan-2026-10-26.md section 2, lever C. A second free IC Markets
demo account on its own Wine MT5 box takes icm-demo's 15m-1d families, so icm-demo can become a pure volume host
(500 fills a day). The build copies the vantage-demo box (`goldbot-mt5-vantage`, 2026-10-26) and RUNBOOK 0.3b
"Adding an account box" (steps B4-B11). Only the account id, the SSH alias and the brain's local port are new.

**Status: STOPPED before provisioning. The real cost is above the approved ~£3.6/month (section 1).** Nothing has been
created in OCI. The account entry is on this branch, `enabled: false`, with no arms. Spending is the owner's decision.

## 1. Shape and cost (measured 2026-10-27, Oracle GBP list prices, 730 h a month)

The evidence was read from the Mac with read-only OCI CLI calls: `compute instance list`, `bv boot-volume list`,
`usage-api request-summarized-usages` for 25-26 Oct, and the public price list
`apexapps.oracle.com/pls/apex/cetools/api/v1/products/?currencyCode=GBP`.

| Fact | Value |
|---|---|
| `goldbot-mt5-vantage` shape | **VM.Standard.E5.Flex**, 1 OCPU, baseline 1/8, 2 GB, 50 GB boot. It is not E4.Flex. |
| Its billed usage on 26 Oct | E5 OCPU 1.84 OCPU-h and E5 memory 29.5 GB-h over about 14.7 h, so only the 1/8 baseline OCPU is billed and memory is billed in full. |
| Its real monthly cost | 0.125 x £0.023961 x 730 + 2 x £0.0015974 x 730 = **£4.52**, against the approved £3.57 (+£0.95/month). The finance-auditor should record this. |
| Boot volumes today | 50 + 50 + 47 + 50 = **197 GB of the 200 GB Always Free block storage**. Every boot volume counts (the "Block Volume - Free" SKU was 147 GB on 25 Oct and 197 GB from 26 Oct). |
| Block storage over the free 200 GB | £0.02036685/GB-month + 10 VPU x £0.00135779 = £0.0339/GB-month |

A new box needs at least a 47 GB boot volume (the image minimum, as on `goldbot-mt5-probe`). Storage then reaches
244 GB, so **44 GB is billable**.

| Option | Compute | Storage | **Total / month** |
|---|---|---|---|
| **A (recommended). Own box: VM.Standard.E4.Flex, 1 OCPU, baseline 1/8, 2 GB, 47 GB boot, VPU 10** | £3.57 | £1.49 | **£5.07** (about £61 a year) |
| A-fallback. The same on E5.Flex, used only if E4 has no capacity (E4.Flex is offered in AD-1 and AD-3 today) | £4.52 | £1.49 | £6.01 |
| B. E4.Flex with 1 GB | £2.70 | £1.49 | £4.19 |
| C. A second terminal on `goldbot-mt5-vantage`, its memory 2 GB -> 3 GB | +£1.17 | £0 | +£1.17 |

Why A:
- **B** costs £0.88 less, but the 1 GB micros already swap (`goldbot-mt5-probe`: 952 MB RAM, 423 MB of swap in use,
  measured 2026-10-27). A Wine terminal that pages during a fast market delays exits. 2 GB measured on the vantage box:
  711 MB used, 1.2 GB available, terminal RSS 135 MB.
- **C** is the cheapest, but it has three costs:
  - It puts two brokers' accounts in one failure domain: a box loss stops both learning hosts.
  - It needs a root-run change that does not exist yet: a second bridge on the box at 127.0.0.1:8766 and a second
    `permitopen` in `goldbot-mt5-authorize`, which needs a security review. The H4 `vantage-swing` lane already plans
    to use that box's spare capacity.
  - The resize reboots the vantage box.
- A keeps the rule "one MT5 box per account" (docs/ops/icm-probe-account-plan.md Option B) and needs no new code.

**Owner decision needed:** approve about £5.10/month for option A. The alternative is option C at +£1.17/month, built
after the multi-bridge lane. If neither is approved, icm-demo stays a depth host at no more than about 300 fills a day,
and the shortfall is reported daily (the volume plan's stated fallback).

## 2. Provisioning (after the approval)

`scripts/deploy/oci_provision.sh` cannot build this box. Its Always Free guard refuses every shape except
E2.1.Micro and A1.Flex (`check_shape`), so the vantage box was launched with a direct `oci compute instance launch`.
That launch should not be repeated by hand. A small follow-up, given below as "prov-paid", adds
`--paid-mt5 <name> --approved-gbp <n>` to the script. It would:
- refuse unless the estimate (compute plus storage above 200 GB, from the price list) is at or below `--approved-gbp`;
- reuse the instance by display name, so it never launches a second billed copy;
- use the same VCN, subnet `goldbot-public` and security list `goldbot-sl`;
- write a `Host goldbot-mt5-depth` block to `~/.ssh/goldbot.conf`.

The security list needs no change: SSH from the subnet CIDR already carries the brain's tunnel, and the bridge port
is never opened.

Launch values: display name `goldbot-mt5-depth`, `VM.Standard.E4.Flex`, `--shape-config
'{"ocpus":1,"memoryInGBs":2,"baselineOcpuUtilization":"BASELINE_1_8"}'`, the latest Canonical Ubuntu 24.04 x86_64
image (not Minimal), `--boot-volume-size-in-gbs 47`, public IP, `~/.ssh/id_ed25519.pub`. Check the console's cost
estimate before launch (memory "vantage-vm-spend").

## 3. Bootstrap and account (RUNBOOK 0.3b, with `--account icm-depth`)

| Step | What | Who |
|---|---|---|
| P0 | `config/accounts.yaml` entry `icm-depth` on `main` (`enabled: false`, `login: null`). The bridge serves only an account it knows. | Claude (this branch) |
| B4 | `sudo bash mt5_bootstrap.sh <SHA> --account icm-depth` inside `tmux new -s boot`, with the hash checked. Terminal at `C:\MT5\ICMarkets\terminal64.exe`. | Claude |
| A1 | Open the GBP Raw, hedging, `ICMarketsSC-Demo` demo. A sign-up with the owner's identity details is the owner's step. If the owner has already given those details for this, Claude does it in the terminal over VNC (File > Open an Account). | Owner or Claude |
| B5/B6 | Login number: on the brain with `goldbot accounts set mt5-login-icm-depth` (hidden prompt), then piped to the box with `export` and `set --stdin`, as in B6. It never appears on a screen or in the repo. | Claude, through the prompt |
| B7 | `sudo systemctl start goldbot-mt5-terminal@icm-depth`. The password goes only into the box keyring, through the documented hidden prompt (`goldbot-mt5 accounts add icm-depth`). Then `goldbot-mt5 login icm-depth`, which prints ok or failed. VNC only if that fails. | Claude (terminal login, standing rule) |
| B8 | Bridge listen `127.0.0.1:8765` on the box; the token is made on the Mac and piped to both ends; `mt5-bridge-url-icm-depth` = `http://127.0.0.1:8768` | Claude |
| B9 | On the brain: `ss -ltn 'sport = :8768'` shows only the header line. Authorise the tunnel key on the box. Write `/etc/goldbot/bridge-icm-depth.env` (root 0644, `MT5_HOST=<private ip>`, `LOCAL_PORT=8768`). The private IP is typed on the brain only. | Claude (root; security review) |
| B10 | `goldbot-bridge@icm-depth` on the box; `sudo systemctl enable --now goldbot-bridge-tunnel@icm-depth` on the brain; `/health` on the box and through 8768; `goldbot run preflight --account icm-depth` | Claude |
| B11 | Cold restart of the box: within 5 min, with no hands, xvfb, terminal and bridge are active, there is exactly one `terminal64.exe`, and `/health` is ok through 8768. Times go in section 7. | Claude |

The brain's local ports are 8765 (icm-demo, `goldbot-bridge-tunnel.service`), 8766 (icm-probe), 8767 (vantage-demo,
read from `/etc/goldbot/bridge-*.env` on 2026-10-27) and **8768 (icm-depth)**. The tunnel template refuses 8765 for
this account.

## 4. The engine unit

`goldbot-engine@.service` and `goldbot-bridge-tunnel@.service` are account templates and serve `icm-depth` unchanged.
No new unit file is needed (tests/test_icm_depth_account.py pins this). **`goldbot-engine@icm-depth` is not enabled
until the account is.** `goldbot.ops.services.engine` gates only live accounts on `enabled`, so a demo engine of a
disabled account would start if its unit were enabled (risk R1). The brain has room for it: 12 GB, 2.8 GB used, and
the three engines run at 436-485 MB RSS each (measured 2026-10-27).

The arm move (not part of this change; it waits for the trading-safety and quant reviews) does the following in one
config change:
- moves icm-demo's 15m-1d families to `icm-depth` under their own registry rows or a re-host amendment (the quant's
  call);
- sets `daily_loss_limit_abs`, `risk_overrides`, `approval_mode: auto`, `probe_only: true`, `max_positions` and
  `exploration`, as on icm-demo;
- sets `enabled: true`, enables the engine unit, and adds `icm-depth` to `goldbot/telegram/labs.py` LABS/ROLE (with
  the ui-ux-designer).

## 5. Health, alerts and backups

- **Detection.** Health builds its account list from `accounts.enabled_accounts()`. When the account is enabled, the
  checks `bridge:icm-depth`, `terminal:icm-depth`, `bridge_compat:icm-depth`, `server_clock:icm-depth`, engine
  heartbeat and `tick_errors` apply with no code change, and so do the Telegram alert path and the deadman. While
  disabled, the box is watched only by its own `goldbot-bridge-watchdog@icm-depth.timer`. That is acceptable, because
  nothing trades through it.
- **SLOs**, the same as the other learning hosts:
  - engine heartbeat fresh while the market is open;
  - an alert within 6 min of a silent bridge or engine;
  - a box rebuild in under 1 h (B4 to B11).
- **Backups.** The engine state lives on the brain (`state/`) and is covered by the brain's backups, encrypted and
  off-host, so nothing new is needed. The box holds only the terminal's `Config/`, backed up on the box itself
  (`~mt5/config-backup`) the same way as on the other boxes. A lost box is rebuilt, not restored: B4 to B11, with the
  login and password entered again through the prompts.

## 6. Rollback

| Stage | Rollback |
|---|---|
| Config on main, box not built | Remove the `icm-depth` block. Nothing runs. |
| Box built, account disabled | `sudo systemctl disable --now goldbot-bridge-tunnel@icm-depth` on the brain and delete `/etc/goldbot/bridge-icm-depth.env`. `oci compute instance terminate --preserve-boot-volume false` for `goldbot-mt5-depth`. Billing stops at termination. |
| After the arm move | Revert the config commit: the arms go back to icm-demo, `icm-depth` `enabled: false`. `systemctl disable --now goldbot-engine@icm-depth`. Restart `goldbot-engine@icm-demo`. Open demo positions close on their own stops, or are closed before the switch (the RUNBOOK's "flat first" rule). |

## 7. Risks

| # | Risk | Impact | Prevention |
|---|---|---|---|
| R1 | A demo engine starts for a disabled account if its unit is enabled | Medium: a disabled `icm-depth` with no arms and probe_only off could take live-agent entries on demo | Unit stays disabled (section 4). Proposed fix for the trading-safety lane: the engine refuses to start when the account is not `enabled` (`refuse_start`), with a test. |
| R2 | The approved figure was based on E4 pricing, but the vantage box runs E5 | Low (£0.95/month), but money moved without being recorded | Report it to the CFO and finance-auditor. prov-paid checks the estimate against the approval before any launch. |
| R3 | The free storage pool is full (197/200 GB) | Every further box adds about £1.5/month of storage | Stated in section 1. Any new paid resource needs a fresh owner approval. |
| R4 | E4 capacity is out in the region | The launch waits, or falls back to E5 (+£0.94/month) | Fallback only within the approved amount. |

## 8. Record

| When | What | Result |
|---|---|---|
| 2026-10-27 | Read-only OCI inventory, usage and price list; brain ports and RSS; box memory | Section 1, section 3 and section 4 |
| | Owner approval of the real cost | pending |
