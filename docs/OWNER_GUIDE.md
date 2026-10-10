# goldbot owner guide

For Aamir. Each step links to its [RUNBOOK](RUNBOOK.md) section.

## 1. What goldbot does for you
- Watches gold (XAUUSD) and **proposes** an entry when a tested strategy fires.
- You tap **Approve** within 90 s; nothing opens without it.
- Stops, targets and exits are **automatic** and never wait for you.
- Researches and evolves strategies inside safety rails it cannot loosen.
- If anything breaks, **Telegram tells you** within about 6 minutes.

## 2. Setup checklist (about 2-3 hours, in order)
"Terminal (vm)": Mac Terminal after `ssh ubuntu@<vm public ip>`. Keep `<values>` private.

| Step | Time | Where | Do | Detail |
|---|---|---|---|---|
| [ ] Oracle account | 30 min | browser, Terminal | Sign up, Pay As You Go, $1 budget alert; `ssh-keygen -t ed25519` | [0.1](RUNBOOK.md#01-oracle-account-browser) |
| [ ] Two VMs: brain, mt5 | 15 min | browser | Compute -> Instances -> Create | [0.2](RUNBOOK.md#02-create-the-two-vms-browser) |
| [ ] MT5 box bootstrap | 20 min | Terminal (mt5) | `sudo bash /tmp/goldbot/goldbot/ops/linux/mt5_bootstrap.sh` | [0.3](RUNBOOK.md#03-the-mt5-box) |
| [ ] One-time MT5 login | 15 min | Terminal, Screen Sharing | `sudo systemctl start goldbot-vnc`; Finder -> Connect to Server -> `vnc://localhost:5900`; tick **Save password** | [0.3](RUNBOOK.md#03-the-mt5-box) |
| [ ] Bridge token | 5 min | Terminal (mt5) | `goldbot-mt5 accounts bridge-serve icm-demo`; `sudo systemctl start goldbot-bridge@icm-demo`. Shown **once** | [0.3](RUNBOOK.md#03-the-mt5-box) |
| [ ] Brain bootstrap | 25 min | Terminal (brain, mt5) | `sudo bash /tmp/goldbot/goldbot/ops/linux/brain_bootstrap.sh`; mt5: `sudo goldbot-mt5-authorize '<key line>'`; brain: `sudo goldbot-tunnel <mt5 private ip>` | [0.4](RUNBOOK.md#04-the-brain) |
| [ ] Guided setup | 15 min | Telegram, Terminal (brain) | `goldbot run setup`: asks for each value in turn, says where to get it, hides secrets. Have ready: MT5 demo login + password, the bridge token, a bot token from @BotFather, your id from @userinfobot, your email. Safe to run again | [0.4](RUNBOOK.md#04-the-brain) |
| [ ] Cloudflare tunnel | 15 min | browser, Terminal (brain) | Route to `http://localhost:8787`; `sudo cloudflared service install <tunnel token>` | [2.7](RUNBOOK.md#27-cloudflare-tunnel-dashboard-from-your-phone-no-vpn) |
| [ ] Preflight | 2 min | Terminal (brain) | `goldbot run preflight`: every ❌ line prints its fix; repeat until it says **READY** | [0.4](RUNBOOK.md#04-the-brain) |
| [ ] Start, health check | 10 min | Terminal (brain) | `sudo systemctl start goldbot-supervisor goldbot-api goldbot-telegram goldbot-news goldbot-scheduler`; `sudo systemctl start goldbot-engine@icm-demo`; `goldbot run health` | [0.4](RUNBOOK.md#04-the-brain) |
| [ ] Dashboard first run | 10 min | Terminal, phone | `journalctl -u goldbot-api \| grep "setup code"` (newest line); enter it, add the authenticator, **save the 10 recovery codes** in your password manager | [2.10](RUNBOOK.md#210-dashboard-first-run-create-the-owner-account) |
| [ ] Backups | 30 min | browser, Terminal (brain) | Oracle console steps in 0.6, then `goldbot run setup` again (step 8) and `goldbot run backup --init` | [0.6](RUNBOOK.md#06-backups-and-restore-brain) |

If the wizard cannot be used, the manual commands are in [RUNBOOK 0.4](RUNBOOK.md#04-the-brain) under "By hand".

## 3. Daily use
- **Approve:** Telegram shows direction, lots, entry, stop, target, probability, spread. Tap **Approve**, or a **Reject** reason. Or the dashboard **Approvals** tab. After 90 s it expires. [More](RUNBOOK.md#approving-or-rejecting-an-entry)
- **`/halt`** stops new entries; open trades keep their stops. Resume on the dashboard: **Overview**, authenticator code, **Re-arm**. [More](RUNBOOK.md#halt-and-re-arm)
- **Deploy:** Telegram *New version ready* -> **[Deploy]**. Never while an entry waits; rolls back by itself if a service fails. [More](RUNBOOK.md#05-daily-use)
- **Health tab:** each check reads ok, warn or fail with a reason, plus drift and drawdown per strategy.

| Alert | Means | Do |
|---|---|---|
| `heartbeat:<service>`, `supervisor` | Service silent; entries blocked | `sudo systemctl restart goldbot-<service>` |
| `bridge:icm-demo` | Brain cannot reach MT5 | Check the mt5 VM; redo the MT5 login if logged out |
| `daily_cap`, `weekly_cap` | 2% / 5% loss limit hit | Review losses ([why](RUNBOOK.md#engines-are-running-but-block-every-entry)) |
| `order_failed` | Broker refused after retries | Nothing, unless repeated |
| `drift` | Model drifted; entries halted | After review: `goldbot run drift-review --clear "<note>"` |
| `stop_rule` | Edge appears gone | Consider `/halt`; ask Claude for options |

## 4. Decisions waiting for you
Batch 1 by 2026-10-31, batch 2 by 2026-12-15 ([queue](ROADMAP.md#5-owner-decision-queue), [rulings A-D](research/preregistration-2027Q1.md#needs-the-owner-before-freezing)).

| Decision | Recommendation |
|---|---|
| Event floor, daily signals (A) | **150 rule-only events: can retire, cannot promote; under 150 = inconclusive** (replaces ROADMAP's 400) |
| 150-paper-trade gate vs slow strategies (~29 trades/year) | Rule now on a variant: 12 months and >= 60 trades, same expectancy test |
| Demo cost-probe orders | **Yes**: 0.01 lot, demo, through RiskGate |
| PROPOSED `gates:` values | Approve as written |
| Charge discovery K_eff to every later trial | **Yes**: can only make results look worse, never better |
| Trial budget (B) | Keep 20 a quarter |
| Paid calendar feed (C) | Not for Q1 |
| Other instruments (D) | Defer until the slow-TSMOM verdict |

## 5. What is honest to expect
**No strategy is profitable yet.** Dates are earliest-possible.

| Milestone | When |
|---|---|
| Running on Oracle demo | ~2026-11-14 - 12-12, after your setup |
| Q1 trial verdicts (may all be negative) | 2027-01-04 - 02-28 |
| A strategy passes the gates | earliest 2027-03; **may not happen** |
| First real money, tiny size | earliest 2027-10, realistically **2028**, only if gates pass |
| Full size | earliest 2028-10 |

## 6. Never do
- Paste passwords, tokens, MT5 logins, IPs or recovery codes into chat, GitHub or a repo file. **The repo is public.**
- Widen risk settings without asking.
- Disable or bypass halts.
