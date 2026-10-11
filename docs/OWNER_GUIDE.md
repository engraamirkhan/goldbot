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
- **Morning digest:** at 06:45 UTC Telegram sends one message: all good or what needs attention, yesterday's trades, risk, and decisions waiting for you. [More](RUNBOOK.md#05-daily-use)
- **Approve:** Telegram shows direction, lots, entry, stop, target, probability, spread. Tap **Approve**, or a **Reject** reason. Or the dashboard **Approvals** tab. After 90 s it expires. [More](RUNBOOK.md#approving-or-rejecting-an-entry)
- **`/halt`** stops new entries; open trades keep their stops. Resume on the dashboard: **Overview**, authenticator code, **Re-arm**. [More](RUNBOOK.md#halt-and-re-arm)
- **Deploy:** Telegram *New version ready* -> **[Deploy]**. Never while an entry waits; rolls back by itself if a service fails. [More](RUNBOOK.md#05-daily-use)
- **Entry status (top of Overview):** one block per account answers "can it take new trades right now?".
  **OPEN** (green): yes. **PARTLY OPEN** (amber): yes, except the strategies named (e.g. "trend-g0 paused after 3
  losses in a row, until 14:30 your time"). **PAUSED** (amber): no, and it clears by itself (market closed, news
  blackout, today's loss limit reached); it says when. **BLOCKED** (red): no, and something needs you; the reason
  is tagged **Needs you**, says what to do, and links the Runbook section (e.g. "Broker clock differs from the
  expected time zone", Runbook: Server clock). Times are in your phone's time zone; hold or hover for UTC. Exits
  always keep running; if the engine itself has gone silent while the market is open, the block says since when and
  that your open trades still have their stop and target at the broker (a silent engine while the market is closed is
  normal and shows as "Market closed"). If the dashboard can't reach the server, a banner says since when and every
  badge turns grey **UNKNOWN**: what you see is the last report. The Approvals tab's safety strip uses the same reasons.
  [Spec](ux/entry-status.md)
- **Health tab:** each check reads ok, warn or fail with a reason, plus drift and drawdown per strategy.
- **Your account (Overview):** once the engine is connected to the broker, each account card shows the **balance** and
  **equity** in the account's currency (equity includes open trades), today's change since 00:00 UTC, the margin level,
  the **loss budget** (how much more can be lost before new entries stop, and which limit decides it: today's or
  this week's, this account's or all accounts together) and the
  **next milestone** (the timeframes your balance can trade now and the balance the next one needs, with a progress
  bar). The Approvals tab shows the loss budget too. "Pending broker terms" means the engine has not read the
  broker's currency conversion yet, so no amount is guessed. A milestone allows a timeframe; a strategy still has to
  pass its tests before it trades there.
- **Monthly report:** `goldbot run monthly-report` (or `--month 2026-10`) prints the month's time-weighted return
  (deposits are not counted as profit), R, largest drawdown, average per trading day, best days and milestone
  progress. The morning digest carries a one-line month so far. It never sets a daily amount to reach.

| Alert | Means | Do |
|---|---|---|
| `heartbeat:<service>`, `supervisor` | Service silent; entries blocked | `sudo systemctl restart goldbot-<service>` |
| `bridge:icm-demo` | Brain cannot reach MT5 | Check the mt5 VM; redo the MT5 login if logged out |
| `daily_cap`, `weekly_cap` | 2% / 5% loss limit hit | Review losses ([why](RUNBOOK.md#engines-are-running-but-block-every-entry)) |
| `order_failed` | Broker refused after retries | Nothing, unless repeated |
| `drift` | Model drifted; entries halted | After review: `goldbot run drift-review --clear "<note>"` |
| `stop_rule` | Edge appears gone | Consider `/halt`; ask Claude for options |
| `model_watch` | A new model's first trades: either a tripwire alarm (badly broken model, the previous one is restored) or `cannot_alarm` (too few trades to judge yet) | Nothing; slow decay is caught by the quarterly `drift` check |

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
| Per-agent throttle thresholds (BACKLOG 21, built, off) | Turn on at 3 full losses / 4 bars cooldown / 3 entries a day. The caps are **per decision, per account** (each account counts its own; checked at every entry). After the cooldown the count stays: one more full loss blocks again, until a real shadow win (target or >= 0.25R) of the live model |
| Tier-2 news blackout (BACKLOG 18, built, off) | No new entries from 5 min before to 15 min after a tier-2 US release (PPI, retail sales, ISM, GDP, JOLTS, jobless claims, FOMC minutes, Fed chair, any other high-impact USD item in the Forex Factory calendar). If the calendar is missing, entries stop (`calendar_unavailable`). Recommend: on before tiny-live, after the alert path and the holiday review (BACKLOG 45, 46) |
| Reopen window (BACKLOG 17, built, off) | No new entries for 10 min after each daily reopen and for 30 min after the Monday open (the playbook proposed 15 min daily). Read from the broker's session hours. Recommend: on before tiny-live, after the alert path and the holiday review (BACKLOG 45, 46) |
| Holiday days (BACKLOG 17, built, off) | No new entries on the days in `config/holidays_cme_metals.yaml` (US federal holidays as a proxy, not measured: CME metals closed or short days, not Columbus or Veterans Day; Good Friday, Easter Monday, the day after Thanksgiving, 24/26/31 Dec, 1/2 Jan; 2026-2028). If the list runs out, entries stop until it is extended; health warns 90 days ahead. Recommend: on before tiny-live, after the alert path and the holiday review (BACKLOG 45, 46) |

How to turn these on: on the server, add them to `config/settings.local.yaml`, for example
`risk: { blackout: { tier2: { enabled: true } }, reopen: { enabled: true }, holidays: { enabled: true } }`, then
restart the engines. They only stop **new entries**; exits and stops always run. Refusals show in the decisions journal
as `tier2_blackout`, `reopen_window`, `holiday_thin` or `calendar_unavailable`.
Before adopting any of them, ask for the replay (BACKLOG 46): how many entries each window would have removed from
the shadow book and the backtest candidates, and whether the removed trades did worse than the kept ones, by family
and timeframe. A window that removes good trades costs money. Health tells you on Telegram if a window has stopped
all entries for more than 30 minutes while the market is open (`entry_windows:` checks), if the calendar has not been
fetched for 36 hours, and 90 days before the holiday list runs out.

## 4a. Share your own trading record (private)
The research team turns how you trade by hand into hypotheses it can test. Your MT5 report holds your account
number and name, so it **never goes into the repo or GitHub**: it stays in a private folder on your Mac.

1. In the MT5 desktop terminal open the **History** tab (Toolbox, `Ctrl+T`), right-click and pick the period
   (**All History** or the last 3-6 months).
2. Right-click again -> **Report** -> **HTML** (or **Open XML (MS Office Excel 2007)** for `.xlsx`).
3. Save it in `~/Personal project/trade-history/` (create the folder; it is outside the repo).
4. Run `python -m goldbot.ops.run owner-history` from the repo (add `--server-tz <zone>` if your broker is not on
   Athens time, `--start-balance 50` if the report has no deposit row). It reads the newest report in that folder and
   writes `owner_history_*.md`, `.json` and `.trades.csv` next to it; the console prints a short summary.

Only the account currency is read from the report header. The "hypothesis seeds" at the end are patterns in your own
past trades, not proof of an edge: the research director pre-registers any that are worth a trial. The tool refuses to
write into any git folder, and a commit check rejects a report file. **Do not paste amounts into the repo** (or
GitHub issues): share the seeds, which use R, percentages and counts only.

## 5. What is honest to expect
**No strategy is profitable yet.** Dates are earliest-possible.

| Milestone | When |
|---|---|
| Running on Oracle demo | ~2026-11-14 - 12-12, after your setup |
| Q1 trial verdicts (may all be negative) | 2027-01-04 - 02-28 |
| A strategy passes the gates | earliest 2027-03; **may not happen** |
| First real money, tiny size | earliest 2027-10, realistically **2028**, only if gates pass |
| Full size | earliest 2028-10 |

**Account, loss limit and goal ([ADR 0004](decisions/0004-starting-account-and-daily-target.md)):** your IC Markets demo is £1,000 GBP, and goldbot reads the balance from the broker. You never lose more than £50 in a day. The tighter design caps apply first (about £15–£20 at £1,000: two losing trades stop new entries for the day), and exits are never blocked. The goal is £50 or more on good days and less on poor ones. As a monthly *average* it is realistic at about £21k–£53k. At £1,000 only 15-minute strategies can trade, at the smallest size (about £8 of risk). That needs two fixes first: a currency bug in sizing and the small-account rule. 1h opens at about £1,650, 4h at £3,300 and daily at £7,900. Progress is reported monthly: average £/day, % return, best days and drawdown.

## 6. Never do
- Paste passwords, tokens, MT5 logins, IPs or recovery codes into chat, GitHub or a repo file. **The repo is public.**
- Widen risk settings without asking.
- Disable or bypass halts.
