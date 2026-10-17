# Post-deploy audit: 2026-10-17 (both servers on `main` 01e70a7)

Finance-auditor, 2026-10-17, read at `8a592a4`. Standing rule: an audit after every deploy (`README.md`, "Post-deploy
audit trigger"). This replaces the planned `audit-2026-10-18-deploy.md`: the deploy came a day early.

**Deployed.** The MT5 box went live at 02:41 UTC and the brain at 05:23 UTC, both on `01e70a7` (PR #84, merged
2026-10-16 21:27 UTC). The engine reads the live demo feed with **entries blocked**: `account_class_unknown`. The engine
was off during the deploy, so Friday's classifier run saw only 238 London/NY ticks, below the 1,000 it needs. The next
read is Fri 23 Oct, so the first smoke order is Mon 26 Oct at the earliest.

**Sample.** No trades on `icm-demo` (n = 0): no closed trades, fills, approvals or swaps. Every trading figure below is
nil or an assumption. **No broker access from here**, so nothing here is tied out to the broker (s.5).

## 1. Bad news first

1. **PR #85 is not merged.** The brief said "merged PRs #84/#85". On the GitHub API at 2026-10-17, #85 is **OPEN**, not
   merged. The servers run #84 only. #85 changes `goldbot/risk/gate.py` (+182/-60) and adds `goldbot/ops/smoke.py`
   (the bridge smoke check). Its rule-compliance review therefore still lies ahead, and it needs **its own
   post-deploy audit** when it ships. This audit covers `01e70a7` only. (F1)
2. **The day-0 rule-compliance checks RC-1 to RC-9 cannot be computed from here.** They need the engine's state,
   journal, store and the broker. With no trades they would only confirm that the records exist and parse, and nobody
   has confirmed that yet. (F2)
3. **Some money costs are unrecorded.** The repo has no figure for Claude spend this month. Session notes outside the
   repo record a spend limit being hit and a switch to a different model's credits. The budget excludes the owner's
   Claude subscription by design, but that boundary assumes the owner is not also buying extra usage. (F6)

## 2. Rule compliance (code and config as deployed; behaviour not observable here)

| Check | Result | Evidence | Severity | Owner |
|---|---|---|---|---|
| Demo only, no live | **Pass (config)** | `config/accounts.yaml` at 01e70a7: `icm-demo` `mode: demo`, `enabled: true`. `icm-live` `enabled: false` ("unlocked by the paper -> tiny-live gate"). `vantage-demo` disabled. No `state/phase_state.json` in the repo, and none would be expected. The live phase record lives on the server, so this repo cannot check it | Info | sre confirms phase state on #34 |
| RiskGate untouched as the only path | **Pass (code review of the diff)** | `git diff c28ed08 01e70a7 -- goldbot/risk/gate.py` (+74/-44 in that range) is a refactor only. `account_reasons()` now holds the intent-free refusals, and `check()` starts from them. `daily_cap`, `weekly_cap`, `account_class_unknown`, `open_risk_unknown` and `invalid_state` are unchanged in logic. No limit constant changed. Trading-safety approved it in #84 | Info | - |
| No limit loosened | **Pass** | `config/settings.yaml` 1236e5f (early Oct) vs 01e70a7: `risk_per_trade` 0.005, `daily_cap` 0.02, `weekly_cap` 0.05, supervisor 0.015/0.04 unchanged. Additions tighten only: `daily_loss_limit_abs: 50` on `icm-demo` (ADR 0004), and heat and projection added but off. `config/` is untouched in #84 | Info | - |
| Independent lot ceiling (BACKLOG 66) | **Pass, deployed** | `goldbot/execution/bridge.py` at 01e70a7: `MAX_NOTIONAL_EQUITY_MULT = 6.0`. An order taking notional above 6x equity is refused (10006). Entries only; closes are never subject to it | Info | - |
| BACKLOG 66 status text | **Stale** | The fragment still says "merged on the integration branch … next: release PR", but the ceiling is on `main` through #84 | Low | release-manager |
| Classifier fix deployed | **Pass** | e98eec8 ("stored unknown adopts a known class") is an ancestor of 01e70a7. Without it, the thin run would block entries until Fri 30 Oct, not 23 Oct (`docs/handoff.d/2026-10-16-classifier-unknown-not-sticky.md`) | Info | - |
| Entries refused while the class is unknown | **Pass by design, not observed** | Unknown class is a fail-closed refusal in `account_reasons()`. Exits never call the gate. No order and no trade has happened, so no position, stop or exit needs reconciling | Info | - |
| RC-1 to RC-9 on the running system (F2) | **Not computable here** | These checks need engine state, the `decisions`/`fills` store, approvals, and the broker's deal history. This sandbox has none of them | Medium | sre: post the counts (proposals 0, orders 0, fills 0, `closed_records_lost` 0, `entry_blocks` = `account_class_unknown`, daily allowance published) on #34 by Mon 19 Oct 22:00 UTC, figures only |
| PR #85 status (F1) | **Finding** | #85 is OPEN. It changes the gate and adds the smoke path | Medium | release-manager; finance-auditor audits within 48 h of its deploy |

## 3. Costs, month to date (1-17 Oct 2026, GBP)

| Line | Amount | Basis | Free period ends | Not verified |
|---|---|---|---|---|
| Brain: Oracle A1 Flex (Always Free) | £0 | `budget-2026-10.md` s.2 | never | Whether the Oracle account is on Pay As You Go (OD-3). Whether a budget alert and a free-shapes-only quota are set (P7, due Fri 23 Oct) |
| MT5 box: Oracle E2.1.Micro under Wine (Always Free) | £0 | same | never | The Wine soak on 1 GB has only just started (scenario D in the budget: £8-15 a month if it fails) |
| Boot volumes, egress | £0 | same | never | not re-read today |
| OCI Object Storage (restic backups) | £0 | free to 20 GB | never | **Bucket size not read.** BACKLOG 67 (15 GB alert) is not built yet, so nothing is measuring usage against the 20 GB limit (F4) |
| GitHub Actions | £0 | Public repo on standard runners: not billed (CFO correction, `budget-2026-10.md` s.2, read 2026-10-17) | n/a | The cause of the 2026-10-06 stop is still unexplained (release-manager, due Fri 23 Oct) |
| Staff agents (Anthropic API, `agents.monthly_cap_usd` 40) | £0.00 | $0.00 on the health page 2026-10-16, as reported to the CFO | n/a | Not read from `agent_spend` by this audit (it lives on the server) |
| Claude sessions running the program | **excluded / not recorded** | Outside the books by the budget's definition. No amount in the repo (F6) | n/a | amount unknown |
| Market data (Dukascopy, FRED, calendars), Telegram, Healthchecks, Tailscale | £0 | free plans | never | not re-read today |
| Trading costs (spread, commission, swap, slippage) | £0, n = 0 | no fills | n/a | - |
| **Total cash, month to date** | **£0 recorded** | | **no trial or credit in use**, so the 60-day rule raises nothing | |

**Break-even** is unchanged from the budget: scenario A is £0 a month. The only hurdle is a gross edge above
0.057 R a trade at the 15m minimum lot.

## 4. The lost Friday classifier read: cost as time

| Item | Plan | Now | Delay |
|---|---|---|---|
| First known account class | Fri 16 Oct run (if the engine had been up for the London/NY session) | Fri 23 Oct run | **7 days** |
| Earliest first smoke order | Mon 19 Oct | Mon 26 Oct | **5 trading days** |
| Money | - | **£0.** The account is demo, no cash is at risk, and the ledger has no line for it | - |
| Knock-on time | - | Every gate clock that counts demo trades starts a week later: CE-1 and the first cost table (day 7), `costs-v1` (50 fills), and the paper gate's 150 trades. At the budget's assumed ~20 trades a month, that is about 5 trades of sample. Tiny-live was already no earlier than Dec 2027 (`capital-plan.md` s.4), so a week does not move it materially | - |
| Risk carried by the new date | - | The 26 Oct smoke falls in the **US-EU DST gap week (26-30 Oct)**, which is exactly the week the `server_clock` check must first confirm `Europe/Athens` (`config/accounts.yaml` comment). A clock error that week would misdate the risk day that the £50 limit counts | Medium (F5) |

If Fri 23 Oct is also thin (fewer than 1,000 London/NY ticks, for example an engine restart during the session), the
class stays unknown and the delay grows by another week. One thin Unknown run does not demote a known class, and two
in a row are needed.

## 5. What could not be reconciled

- **Broker balance (RC-9):** no statement export and no broker access. The opening balance is £1,000 per ADR 0004, as
  reported, not seen. With n = 0 trades, balance minus opening should be £0 plus any demo interest or adjustment. Still
  open.
- **Position ids (RC-8):** n = 0, so nothing is due. The reconciliation job (GAP-08) has not been seen running.
- **Agent spend, Object Storage size, Oracle billing state:** all live on the servers or in owner consoles; none was
  read.

## 6. Learning record (this week)

| Decision | Evidence | Finance reading |
|---|---|---|
| **No gate-1 rerun** (research director, 2026-10-17) | `docs/research/gate1-rerun-decision-2026-10-17.md`: 0 of 20 registry rows pass. The best row, #16 tsmom 1h, has 33 model-filtered trades against 500 required and is −0.079 R net, t −3.50. The Q4 budget is 20/20 | **Agreed.** The rerun would cost a trial and raise K to 21 for no information. Cash cost £0 either way. The hit rate stays 0 of 20 (95% upper bound about 15%) |
| **Holiday list cme-metals-v3 adopted** (PD + PO on BACKLOG 45, 2026-10-16) | `holiday-list-measurement-2026-10.md`, quant ruling "SAFE TO ADOPT". Only change: 2 Jan becomes NORMAL and is removed | **Decided, not shipped.** BACKLOG 70 is parked until 45's final (due 2026-10-30), and `risk.holidays.enabled` stays off. **£ effect this month: nil**: it cannot move money until the window is switched on, which is the owner's call after BACKLOG 46 |
| Learning metrics LE-1 to LE-4 | n = 0 trades | Not computable. First read in the November audit, if the demo trades |

## 7. Findings summary

| ID | Finding | Severity | Owner | Due |
|---|---|---|---|---|
| F1 | PR #85 (gate + smoke) is open, not merged. Needs its own post-deploy audit | Medium | release-manager | at its deploy (+48 h audit) |
| F2 | Day-0 RC-1 to RC-9 not computable off-server | Medium | sre | Mon 19 Oct 22:00 UTC |
| F3 | BACKLOG 66 status text is stale (it is on `main`) | Low | release-manager | Fri 23 Oct |
| F4 | Object Storage usage unmeasured; BACKLOG 67 not built while backups run | Medium | sre | Fri 23 Oct (already due) |
| F5 | First smoke falls in the DST gap week, before `server_clock` has confirmed the timezone | Medium | sre / program-director | before Mon 26 Oct |
| F6 | Claude spend running the program is unrecorded | Low (policy) | program-director, then owner | Fri 23 Oct |

## 8. Recommendations (ranked)

### Program director

| # | Recommendation | Expected £ effect or risk avoided | Measurement | Due |
|---|---|---|---|---|
| P1 | **Protect the Fri 23 Oct classifier read.** No engine restart or deploy during London/NY (07:00-21:00 UTC) that day. Put a deploy freeze on the RUNBOOK calendar | £0 cash. Saves a further 7 days of delay per missed read | Classifier run stores a known class with ≥ 1,000 London/NY ticks (count posted on #34) | Fri 23 Oct |
| P2 | **Day-0 RC evidence on #34** (F2): proposal, order, fill and closed-record counts, `entry_blocks`, the daily allowance and `closed_records_lost`. Figures only | Risk: a broken money path found before the first order, not after | All fields present, posted | Mon 19 Oct 22:00 UTC |
| P3 | **Run `server_clock` before the first smoke** (F5). If the gap-week reading disagrees with `Europe/Athens`, hold the smoke until it is fixed | Risk: a misdated risk day under the £50 limit | Check result posted before the first order on 26 Oct | Mon 26 Oct, before 07:00 UTC |
| P4 | **Build BACKLOG 67 before the nightly backups grow** (F4), and confirm the P7 Oracle budget alert and free-shapes quota | Keeps £0. Avoids an unseen PAYG charge or a failing backup | Size reading posted on #34; alert configured | Fri 23 Oct |
| P5 | **Route PR #85 to its own post-deploy audit** (F1) and correct BACKLOG 66's status (F3) | Risk: an unaudited gate change on the money path | finance-auditor audit filed within 48 h of #85's deploy | at deploy |
| P6 | **Decide whether Claude spend is counted** (F6). If the owner buys usage beyond the subscription, record the monthly amount (no account details) in the ledger | Makes break-even honest. If counted, it probably dominates every other line | One line in `ledger-2026-10.md` (or an explicit "excluded" ruling) | owner ruling by Fri 23 Oct; ledger due Wed 4 Nov |

### Research director

| # | Recommendation | Expected £ effect | Measurement | Due |
|---|---|---|---|---|
| R1 | **Hold the gate-1 position.** No rerun until a row qualifies under the decision note's rule. Spend the Q1 2027 open slots where the next demo trade can come from (15m), each stating its break-even bar (+0.076 R net at 20 trades, budget R1) | Saves a trial and K inflation. £0 cash | Every Q1 pre-registration states its bar | Q1 freeze |
| R2 | **Use the delay week.** Have the performance-analyst set up CE-1, CE-3 and PR-7 on the demo store now, so the first fills produce numbers on day 7, not day 14 | A 0.10 $/oz error in the slippage prior is about £1.50 a month at 20 trades; finding it a week earlier is worth little in £ but a week in time | CE-1 table exists by Sun 1 Nov (day 7 after the first entries) | Fri 23 Oct |
| R3 | **Land BACKLOG 45's final by 30 Oct**, so that v3 ships with its sha and BACKLOG 46 can follow. The window stays off until the owner rules | Nil until switched on. Removes a needless block on 2 Jan | 70 merged with the final sha | Fri 30 Oct |

## Owner decisions (options, not decided here)

- **Claude spend in the books (F6):** (a) keep it excluded as a personal tool; (b) count spend above the base
  subscription; (c) count all of it. **Recommendation: (b).** It is the only part that varies with the program's
  activity.
- No spending, hosting or live decision is due from this deploy.

## Not verified / left out

Broker balance, engine state, store tables, agent spend, Object Storage size and Oracle billing mode were not read (no
server or broker access from this sandbox). Vendor free limits are taken from `budget-2026-10.md`, not re-read. No code,
settings, limits or trials were changed.
