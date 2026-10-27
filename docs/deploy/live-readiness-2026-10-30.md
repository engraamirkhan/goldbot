# Live readiness for real money from 2026-10-30

Owner directive (2026-10-27): "we will trade on live account on 30th october 2026".
Author: program-director, 2026-10-27. Status: **plan, decision owed by the owner by 2026-10-28 18:00 UTC.**
No profit is promised anywhere in this plan.

## 1. Where we stand (verified read-only on 2026-10-27, about 12:20 UTC)

| Fact | Evidence |
|---|---|
| Phase 0. No roadmap gate is recorded: `state/phase_state.json` does not exist on the brain | `run.py gates --no-write` on the brain: "phase 0; gates recorded: none" |
| `paper_to_tiny_live` needs `backtest_to_paper` first, 150 closed paper trades, **182 paper days**, paper expectancy within 50% of a positive backtest, fills within 30%, a chaos drill | same report; goldbot/ops/gates_phase.py; config/settings.yaml `gates:` |
| No evaluated trial passes the research gates: Q4 2026 batch 0 PASS, 0 LEAD (31 stop, 1 inconclusive) | gate report "no evaluated trial"; docs/research batch readout |
| The gate report sees **0 closed demo trades** ("no closed-trade record yet"), although the demo accounts trade hundreds a day | the gate's evidence file `state/closed_trades.jsonl` is not written; the demo record lives in the DBs |
| The live unlock refuses without the recorded gate; the engine never returns a live account without it | goldbot/ops/accounts.py `unlock_live`, `enabled_accounts` |
| Recording a gate does not check its evidence (`record_gate` is recording only) | goldbot/ops/accounts.py `record_gate` docstring |
| With the gate recorded, a live account trades at **0.1% risk per trade** and the minimum-lot exception is **off** in tiny-live | config/settings.yaml `risk_per_trade_tiny_live: 0.001`; commit 636ff6db |
| BACKLOG 29 (absolute daily loss limit) is **implemented** (R34), not open. But `icm-live` and `vantage-live` have **no `daily_loss_limit_abs`** in config/accounts.yaml | goldbot/risk/gate.py projected check; TRACEABILITY R34 |
| `icm-live` keeps `server_tz: Europe/Athens`; icm-demo measured NY+7 on 2026-10-26. 26-30 Oct is the US-EU DST gap week, so on 10-30 the two differ by one hour | config/accounts.yaml comments |
| Live accounts default to `approval_mode: propose` (90 s window); `auto`, exploration and cost probes are refused on any non-demo account | config/accounts.yaml, settings `approval_window_seconds: 90` |
| Backup ok 2026-10-26 22:16 UTC; restore drill ok 2026-10-25 (demo state). No kill switch or chaos drill evidence recorded | state/backup_last.json, state/restore_drill_last.json, gate report `chaos_drill not recorded` |
| No live MT5 box, tunnel or engine unit exists (brain runs icm-demo, icm-probe, vantage-demo only) | `systemctl list-units goldbot*` |

### Sizing arithmetic at today's market (XAUUSD 4,014.84; 0.7508 GBP per USD; 0.01 lot = 1 oz)

Stop = 1.5 x ATR(14) of the decision timeframe (DESIGN Sizing). Measured ATR today: 15m $6.60, 1h $13.07, 4h $25.01, 1d $61.39.

| | 15m | 1h | 4h | 1d |
|---|---|---|---|---|
| Stop risk of 0.01 lot (GBP) | £7.4 | £14.7 | £28.2 | £69.1 |
| Smallest account where 0.01 lot fits the 1% hard maximum (min-lot exception on) | £740 | £1,470 | £2,820 | £6,910 |
| Smallest account where it fits tiny-live 0.1% (x1.2 rounding allowance) | £6,200 | £12,300 | £23,500 | £57,600 |

Margin for 0.01 lot at the FCA 1:20 figure the gate checks is about £151; the gate's 300% margin-level floor then needs
about **£453 of equity** before any risk rule is applied.

**Consequences, stated plainly:**
1. **£20 cannot trade.** 0.01 lot needs about 7.5x the account in margin at 1:20, and a single 15m stop would lose
   about 37% of it. Making £20 work means removing the margin and risk rails, which this plan does not propose
   (ADR 0004: never loosen RiskGate). The owner's "live from £20" goal needs an ADR for a small-account policy
   (CFO + trading-safety), not a config edit; it is not a 10-30 item.
2. **Tiny-live as designed trades nothing at £1,000.** At 0.1% the gate refuses every 0.01 lot on every timeframe.
3. **At £1,000, only 15m entries fit**, and only with the minimum-lot exception at the 1% cap, which tiny-live forbids today.

## 2. The strategy question (honest answer)

**No strategy meets the live gates on 10-30, and none can by the design's own rule before about May 2027**
(182 paper days after a `backtest_to_paper` that is itself not yet earned). There is no champion: 0 trials passed,
the exploration learner has labelled nothing yet (exploration_models 0; fix in progress), and the shadow book has 14
closed trades. The demo accounts since the 10-25 reset are about flat (icm-demo -0.2% on the week, icm-probe +0.3%,
vantage-demo +0.5%); that is noise, not evidence, and a large part of it is exploration that is never traded live.

So whatever trades live on 10-30 trades **without evidence of a positive expectancy after costs**. Its expected
value should be treated as zero to negative. A real-money start on 10-30 can only honestly be a **paid execution
pilot**: it tests the real-money plumbing (fills and slippage against the model, the one-click confirmation, exits
under a live broker, reconciliation, the GBP ledger), it does not test or prove a strategy.

### Options (the decision is the owner's; CFO and research-director recommendations due 10-28 12:00 UTC)

| | Option | What trades | Rails | Cost / risk |
|---|---|---|---|---|
| **A** | **Delay real money** until `paper_to_tiny_live` is earned | the first strategy that passes | design as written | £0 at risk; live not before about May 2027; plumbing stays untested on real money |
| **B** | **Execution pilot** under a written, owner-signed waiver ADR (not a fake gate record) | owner-confirmed proposals only, from one named 15m rule family (research director names it by 10-28 12:00 from demo forward stats; the owner's box break + close + MFI is the default candidate), 0.01 lot, one position at a time | 1% per trade via the min-lot exception, `daily_loss_limit_abs` £20 (2% of £1,000), weekly £50, **pilot stop at £100 cumulative loss**, flat before the Friday close, no exploration, no probes, exits automatic | a budgeted loss of up to £100 (CFO books it as a cost of learning); every rail unchanged except the min-lot exception and 0.1% tiny-live rate on this one account |
| **C** | **B, but first real order Mon 2026-11-02 08:00 UTC** | as B | as B | as B, plus: the DST gap week is over (server clock risk gone), BACKLOG 135 gets two more days of review, and the first live session is not a Friday with a weekend gap |

Not offered: recording `backtest_to_paper` and `paper_to_tiny_live` with text evidence. `record_gate` would accept
it, but it falsifies the phase record that every later gate reads, it would still trade nothing (0.1% rule), and the
design says "a gate that is not met extends the phase rather than being waived".

**Program-director recommendation: Option C.** Everything is ready on 10-30 (account funded, live box logged in,
drills passed, engine running live in read-only mode with entries off), and the first owner-confirmed real-money entry
goes out on Monday 11-02. If the owner holds to 10-30, Option B with the 10-29 18:00 UTC go/no-go below; any red
item moves the date to 11-02 without further discussion.

## 3. Checklist (each item: accountable role, effort, by 10-30 08:00 UTC?, acceptance test, owner part)

"Owner part" lists only what legally or physically needs the owner. Everything else is Claude-run.

| # | Item | Accountable | Effort | By 10-30 08:00? | Acceptance test | Owner part |
|---|---|---|---|---|---|---|
| L1 | **Owner decision A / B / C** and the signed pilot waiver ADR (scope, rails, £100 pilot stop, written stop rule) | program-director (ADR drafted by cfo + trading-safety-reviewer, architect checks the mechanism) | S | yes if decided by 10-28 18:00 | ADR merged; the owner's decision quoted on #34 | one reply choosing A/B/C; sign the ADR |
| L2 | **BACKLOG 135 close-priority bridge** | implementer; reviews trading-safety, sre, security | M (1.5-2 days incl. reviews and a bridge protocol bump on every box) | **tight**: code 10-28, reviews 10-29 12:00, deployed to demo boxes 10-29 14:00 | 135 acceptance (4): with an order holding the terminal 30 s, an engine-side stop close reaches the terminal within 5 s; never re-sent; demo run 4 h clean | none |
| L3 | **Pilot mechanism**: a recorded owner waiver that `unlock_live` and `enabled_accounts` accept for one named account only, the min-lot exception at 1% for that account, max 1 position, the £100 cumulative pilot stop as a supervisor HALT | implementer; reviews trading-safety, code, security | M | tight (same window as L2, separate files: goldbot/ops/accounts.py, goldbot/risk/supervisor.py, config) | tests: without the waiver live stays refused; with it only the named account; tiny-live 0.1% unchanged for everything else; pilot stop halts entries, never exits | none (the waiver record is written from the signed ADR) |
| L4 | **Live account config**: `icm-live` `daily_loss_limit_abs: 20`, `server_tz` set from a measurement on the live server (NY+7 expected), `approval_mode: propose`, `dynamic_limits: false`, only the named family enabled | implementer; trading-safety review | S | yes | config loads; health `server_clock:icm-live` OK; a gate grid test shows a £7.4 entry allowed at £1,000 and a second refused after a £15 loss | none |
| L5 | **Live broker account**: IC Markets live (we have its cost and commission data; one broker first, Vantage later). Which legal entity the owner's account sits with, KYC done, funded | owner (legal), cfo checks the entity, leverage and costs | owner-dependent | only if already open and verified | the terminal reads the account currency GBP, balance as funded, `trade_mode` real | **open/verify the account (KYC); fund it with about £1,000, not £20** (section 1) |
| L6 | **Live MT5 box**: its own Wine MT5 host and bridge (one box per account), tunnel unit, engine unit `goldbot-engine@icm-live`, not started | sre | M (half a day; the vantage-demo build is the template) | yes, if the host spend is approved 10-28 | bridge health OK from the brain; terminal logged in, reading live; no engine running | approve the host spend (about £3.6 a month, as vantage-demo) |
| L7 | **Keyring login** for icm-live on the live box | sre prepares a one-line prompt command | S | yes | `accounts` health check finds the login and password; nothing in any file or log | **type the login and password into the keyring prompt** (secret entry; never in chat) |
| L8 | **Real-money approval UX**: BACKLOG 103 (card says REAL MONEY and uses the proposal's own window, HIGH) and 101 (Approve above the fold on the phone) | ui-ux-designer then implementer; reviews code, trading-safety | S + S | 103 yes, 101 likely | screenshot of a real-money card on the phone; a click at 91 s is refused | be reachable for the 90 s confirmations during pilot hours |
| L9 | **BACKLOG 104**: exits, stop changes and the kill switch check the terminal account | implementer; trading-safety | S | yes | test: a kill switch against the wrong terminal account refuses and alerts | none |
| L10 | **Kill switch drill** on the live box with no position, then on demo with a position | sre; trading-safety witnesses | S | yes | /halt stops entries in under 10 s; flatten closes a demo position in under 5 s with an order in flight (needs L2); recorded with `run.py gate-evidence` | none |
| L11 | **Chaos drill** (bridge down, tunnel down, brain restart, stale tick) on demo with the L2 build | sre | S | yes | each fault: entries refused, exits still served or broker stop holds, alert within 60 s; recorded | none |
| L12 | **Backup and restore drill for live state** (engine, orders, risk, approvals for icm-live included in the snapshot) | sre | S | yes | restore on a scratch path shows the icm-live files and DB rows; restore_drill_last ok after 10-29 | none |
| L13 | **Monitoring and alerts for real money**: health checks for icm-live, Telegram real-money pushes (fills, exits, refusals, HALT), fixed owner address for the dashboard | sre; ui-ux for the message copy | S | yes | an injected fault on icm-live reaches Telegram; the dashboard opens at its fixed address from outside the servers and the owner can approve | confirm the alert arrived on the phone once |
| L14 | **Gate evidence plumbing**: the gate report counts the demo record (closed_trades file or DB reader) | data-engineer; quant-reviewer | S-M | no (not needed for B/C; needed for A) | `run.py gates` shows the demo closed-trade count | none |
| L15 | **Exploration learner labels nothing** (fix in progress) | ml-researcher, implementer; quant-reviewer | M | no; **not a live blocker** (exploration never runs on real money), it blocks the evidence for the strategy question | exploration_models rows grow after a day | none |
| L16 | **Finance**: the live account in the GBP ledger, the pilot budget, daily reconciliation to the broker statement | finance-auditor, cfo | S | yes | ledger row for the deposit; day-1 reconciliation within £0.01 | none |
| L17 | **Strategy nomination for the pilot** (one 15m family, its demo forward record, why it is the least bad) | research-director with the evaluator; quant-reviewer | S | yes (by 10-28 12:00) | a one-page note with the family, its demo n, mean R after costs and CI; labelled "no evidence of edge" | none |
| L18 | **Go/no-go review** 10-29 18:00 UTC: every item above green or explicitly not needed | program-director chairs; trading-safety, sre, security, cfo, research-director | S | yes | the signed checklist on #34 | none |
| L19 | **Unlock** | owner | S | yes | `enabled_accounts` returns icm-live; the engine starts with entries proposing | **type `ENABLE LIVE icm-live`** (the phrase; Claude runs the rest) |

## 4. GO / NO-GO criteria (10-29 18:00 UTC review; first order 10-30 08:00 UTC for B, 11-02 08:00 UTC for C)

GO only if **all** hold:
1. The owner chose B or C and signed the waiver ADR; the CFO's pilot budget and the research director's family note are on #34.
2. BACKLOG 135 deployed on every bridge, its 5 s close test passed, 4 h of demo trading on it without a tick error.
3. Pilot mechanism (L3) and live config (L4) merged on green CI; the server clock measured on the live server.
4. The live account is funded at no less than £750 (recommended £1,000), GBP, read by the terminal; login in the keyring.
5. Kill switch, chaos and restore drills passed and recorded in the last 48 h.
6. A real-money approval card seen on the owner's phone; alerts for icm-live reach Telegram.
7. Health green for every account (reconcile FAILs on demo cleared or explained) and no open HIGH/CRITICAL review.

NO-GO on any one of: a failed drill, 135 not deployed, the account under £750, an unsigned waiver, any secret or
account id found in the repo, or the owner unreachable for confirmations on the day. A NO-GO moves the first order to
the next weekday 08:00 UTC that passes, reported on #34 the same evening.

Pilot exit rules (B and C): stop on the £100 cumulative loss, any single loss above £10, any exit later than 5 s
after its stop in the engine log, or any reconciliation break; the pilot ends after 30 closed trades or 4 weeks with
a written readout (performance-analyst, finance-auditor). Scaling up, a second broker, other timeframes or auto
entries need the real gates.

## 5. Schedule (UTC)

| When | What | Who |
|---|---|---|
| 10-27 18:00 | this plan and the batched decision on #34 | program-director |
| 10-28 12:00 | CFO budget and recommendation; research director family note; trading-safety view on the waiver | cfo, research-director, trading-safety-reviewer |
| 10-28 18:00 | **owner decision A / B / C, host spend, funding started** | owner |
| 10-28 | L2, L3, L4, L8, L9 built in parallel worktrees; L6 live box built | implementers, ui-ux, sre |
| 10-29 12:00 | reviews closed; release to demo boxes; 4 h soak starts | release-manager, sre |
| 10-29 | keyring entry, drills L10-L13, finance L16 | owner (keyring), sre, finance-auditor |
| 10-29 18:00 | **go/no-go** | program-director and reviewers |
| 10-30 07:00 | owner types the unlock phrase (B) | owner |
| 10-30 08:00 | B: engine on, first proposal when the family signals; flat by 20:00 | engine, owner confirms |
| 11-02 08:00 | C: the same, first real-money order | engine, owner confirms |

## 6. Risks (top, for the roadmap register)

| Risk | Likelihood | Impact | Owner | Mitigation |
|---|---|---|---|---|
| Trading real money with no evidenced edge | certain under B/C | money (bounded) | cfo | pilot framed as plumbing; £100 stop; 0.01 lot; owner confirms each entry |
| BACKLOG 135 not ready or not reviewed by 10-29 | medium | exits up to about 55 s late on real money | implementer, trading-safety | hard GO item; Option C buys two days |
| Server clock wrong in the DST gap week | medium on 10-30 | risk day and session windows off by an hour | sre | measure on the live server; Option C avoids it |
| Owner not reachable within 90 s | medium | missed entries only (exits automatic) | program-director | pilot hours agreed with the owner; Telegram push |
| Friday start with a weekend gap | medium under B | a gap through the stop | trading-safety | flat before the Friday close |
| Live account entity and leverage differ from the 1:20 the gate assumes | low | sizing or margin surprise | cfo | the gate checks the broker's own margin figure and the 1:20 figure, whichever is stricter |
