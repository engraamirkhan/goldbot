# Recommendations: October 2026

Finance-auditor, 2026-10-11, base `b8e9b88`. Inputs: `budget-2026-10.md`, `audit-framework.md`, ADR 0004,
`docs/deploy/free-hosting-options.md`, `docs/research/scorecard-2026-10.md`. **No trades exist (n = 0)**, so every £
effect below is an estimate from the budget's assumptions (0.01 lot, £8.20 per R, 0.62 $/oz round trip,
about 20 trades a month once a strategy trades). These are recommendations; the safety rails (RiskGate as the only
path, the 1% cap, the daily limits, the owner's click on entries, exits never gated) are not cost levers and nothing
here touches them.

## A. Program-director: data gaps to close before the first live trade, then cost and hosting

Ranked by risk avoided per unit of work. GAP ids refer to `audit-framework.md` s.5. Engine, risk and execution
changes go through the trading-safety review.

| # | Recommendation | Expected £ effect or risk avoided | Measurement | Owner | When |
|---|---|---|---|---|---|
| P1 | **Record the decision price, spread at entry, initial and final stop, final target and fx on every closed trade** (GAP-02, -03, -06), and give the journal `order` row its proposal id (GAP-12) | Makes the slippage leg of break-even measurable. The 0.30 $/oz slippage prior is half of the assumed round trip (£0.23 of £0.47). If the real figure is double, that is about £4.50 a month at 20 trades, a third of the lean post-trial budget, and it would stay invisible because the approval-time quote hides the 90 s wait | Audit CE-2 and CE-3 computable on the first month's trades; 100% of new records carry the fields | PD (implementer, trading-safety review) | before the first demo trade; at the latest before the paper gate's 150 trades start counting |
| P2 | **Log exit fills** (market closes with the quote at the decision; stop and target fills with their level) into `fills` (GAP-04) | Stops are where slippage concentrates. Each extra 0.10 $/oz on stop exits costs about 0.009 R per 15m trade. Without it CE-4 and CE-7 cannot run, and a net-negative family could look net-positive | CE-4 computable; exit-slippage cells in the cost table reach n >= 50 | PD | before the first demo trade |
| P3 | **Daily read-only reconciliation job**: broker deals (engine magic range) vs closed records vs orders; plus a daily balance snapshot including MT5 balance deals (deposits and withdrawals) (GAP-08, GAP-07) | Avoids a silent missing or double-counted trade corrupting the paper gate (150 trades, expectancy within 50%) and the stop rule. It is also the only way the ledger ties out to the broker | RC-8 and RC-9 pass with zero unexplained differences for a full month | PD (data-engineer or sre) | job before the first demo trade; balance history before the paper gate |
| P4 | **Keep the owner's decision time** on the archived Proposal (GAP-10) and a `money_source` field on closed records, with a health count of records whose money came from the engine's own fills (GAP-05) | Proves "approved within 90 s" for the trading-safety audit; stops gross figures being reported as net | RC-2 fully computable; health shows the fallback count | PD | before the first demo trade |
| P5 | **Daily risk summary** per account (day-start equity, minimum equity, realised loss, cap-hit time) (GAP-11) | Shows that the £50 / 2% / 1.5% caps held on floating losses, not just closed ones | RC-4 and PR-8 computable without approximation | PD | before the paper -> tiny-live gate |
| P6 | **Timeframe, family and model version on the closed record** (GAP-01, GAP-13) | Without them the audit cannot say which change paid off out of sample (LE-2) or attribute by timeframe once 1h joins | PR-4, LE-2 computable | PD | before the first champion promotion |
| P7 | **Hosting guards at sign-up**: budget alerts of a few £ on Azure and Oracle, a calendar reminder for the Azure PAYG upgrade (about 2026-11-11) and the 60-day warning (about 2027-08-13); quota policy limited to free shapes on Oracle | Avoids the Azure trial rolling silently into PAYG billing (about £6-30 a month depending on the size left running) and an Oracle shape outside the free tier | Alerts exist (owner confirms on #34, no identifiers); zero unplanned charges on the first invoices | PD (owner does the sign-up) | at sign-up, 12-13 Oct |
| P8 | **Prove the £0 bridge path during the trial**: run the Oracle E2.1.Micro Wine bridge (candidate B) on demo for a 30-day soak, even if Azure works | If it holds, the post-trial cost falls from £10-15 to £0 a month: about **£120-180 a year saved**, at £1,000 equal to the whole of a 1%-a-month return | 30 days with no unplanned restarts, RSS < 85% RAM, latency logged; the decision is recorded before the 60-day warning | PD (sre) | start within the first month; decide by 2027-08 |
| P9 | **Prefer Tailscale-only dashboard access** over a public Cloudflare hostname unless the owner wants it (decision D3) | Saves £8-12 a year and removes a public endpoint from the attack surface | No domain line in the ledger; dashboard reached over the tailnet | PD (security-reviewer agrees) | at deploy |

## B. Research-director: cost-aware research

| # | Recommendation | Expected £ effect or risk avoided | Measurement | Owner |
|---|---|---|---|---|
| R1 | **Put the break-even bar into every pre-registration**: the net R per trade a strategy needs to cover the lean post-trial budget at its expected trade count (about +0.076 R net at 20 trades a month; gross about +0.13 R at 15m after the 0.057 R cost) | Stops the program from promoting a strategy that clears zero but cannot pay for its own hosting. A strategy at +0.05 R net, 20 trades a month, makes about £8 against £12.50 of costs | Each pre-registration states its trade rate and the implied £ a month at £1,000 vs £12.50; the evaluator reports it | RD (strategy-researcher, evaluator) |
| R2 | **Cost-per-R is the 15m problem: favour fewer, longer-target trades.** The round trip is 0.057 R at 15m and 0.028 R at 1h (ADR 0004 ATR estimates). Runner exits (BACKLOG 31) and an opportunity-day filter (H-16) raise R per trade without adding trades | Halving the trade count at the same gross R per trade saves about £4.70 a month in costs at 20 trades. Each +0.05 R per trade at 20 trades is about £8 a month | Net R per trade and cost per trade (R) by family and timeframe in each monthly audit (LE-1) | RD |
| R3 | **Replace the priors with the measured table as soon as it exists**, and re-run the break-even. Until 50 fills exist, report results at both the prior (0.37 $/oz extra) and a stressed 0.55 $/oz (slippage 0.24) | The Q4 tsmom result moved from +0.06 R gross to negative net on costs alone; a 0.1 $/oz error in the cost assumption is about 0.01 R per 15m trade, enough to flip a marginal family | Every research report names its cost source line (`research_costs`) and shows the stressed result | RD |
| R4 | **Measure the price of the owner's 90 s approval wait** (CE-3) and the value of rejections (PR-7) from the first month, as a performance-analyst study, not a trial | If the decision-to-fill drift is adverse by about 0.10 $/oz, it is about £0.08 a trade, or £1.50 a month; if rejections remove losing trades, they are worth keeping visible to the owner | CE-3 mean and interval at n >= 30; PR-7 replayed R for rejected vs approved | RD (performance-analyst) |
| R5 | **Swap-aware holding rules for any strategy that holds overnight long**: the prior costs about £0.45 a night per 0.01 lot (£1.35 on Wednesday) | A long held over Wednesday's rollover pays about 0.16 R at 15m size. Overnight-hold families must show net-of-swap results | CE-6: no family net positive only before swap | RD |

## C. Owner decisions (options and a recommendation; the owner decides)

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | **Staff-agent API spend** (cap $40, about £30 a month; real cost if an API key is installed) | (a) keep the $40 cap; (b) lower it to about $10 a month until a strategy passes the research gates; (c) off until then | **(b)**. There are no trades for the agents to analyse yet, and at the cap they cost 2-3 times the post-trial VPS and move break-even from +0.08 R to +0.27 R net per trade (budget s.4). Raise it again when the demo has trades to review |
| **D2** | **Hosting after the Azure trial (about Oct 2027)** | (a) pay for a Windows VPS, £10-15 a month; (b) move the bridge to Oracle Wine at £0 if P8's soak passes; (c) run the bridge on the Mac (a stopgap only: it must stay awake) | **Decide by about 2027-08-13 (the 60-day warning), on P8's evidence.** Prefer (b) if the soak passes; otherwise (a), budgeted by the owner as a project cost, because real income before then is about £0 (budget s.1) |
| **D3** | **Dashboard access** | (a) Tailscale-only, £0, no public hostname; (b) Cloudflare Tunnel + Access on a domain, £8-12 a year, unless a domain is already owned | **(a)** unless the owner needs to reach the dashboard from a device that cannot run Tailscale |
| **D4** | **What "covers its costs" means while the system is on demo** | (a) real cover only (live money), which is about £0 before Oct 2027 on the current plan; (b) also track **demo cover**: three consecutive months of demo net P&L at £1,000 at or above the post-trial budget | **(b)**, as the evidence that paying the post-trial hosting is worth it; (a) remains the real test after the live gate |
| **D5** | **Deposit plan** (already open in ADR 0004) | none; £100 or £250 a month (ADR 0004 s.6 table) | No recommendation on whether to deposit. For the record: running costs are a fixed £, so they shrink as a share of equity. At £2,000 the lean post-trial budget is 0.6% a month instead of 1.25% |
| **D6** | **Confirm the demo is a Raw Spread account** (already open in ADR 0004) | Raw (assumed 0.25 $/oz spread + 7 USD per lot commission) or Standard (wider spread, no commission) | **Raw.** On Standard, 15m is disabled except at the session open, so nothing trades at £1,000; the cost assumptions here are Raw |

## D. Next audit

- Day-0 post-deploy audit after the first demo deploy (12-13 Oct): rule-compliance checks RC-1 to RC-8 on whatever
  the deploy produced, plus the baseline list of open GAPs on the running system.
- Day 7: CE-1 (the first measured spread vs the 0.25 $/oz assumption) and the first cost table.
- Early November: `ledger-2026-10.md` (running costs only, £0 expected) and the first monthly audit.
