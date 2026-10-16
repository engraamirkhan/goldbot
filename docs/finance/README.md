# Finance: the books, the budget and the audits

Owner of this folder: the `finance-auditor` agent (`.claude/agents/finance-auditor.md`). It recommends; it never
changes code, settings, limits or trials. The research-director and program-director act on its recommendations;
the owner decides anything that spends money or changes risk.

**Status 2026-10-16:** nothing trades yet. The first demo deploy is in progress (it slipped from the 12-13 Oct plan);
the engine start is planned for **Sun 18 Oct 2026 22:02 UTC, on demo**. The day-0 rule-compliance audit is due within
24 h of the engine start, by **Mon 19 Oct 2026 22:02 UTC**. Finance D6 (Raw Spread account) is still to be confirmed
by the owner at the MT5 login (ROADMAP OD-4). The baseline from 2026-10-11 is `budget-2026-10.md`,
`audit-framework.md` and `recommendations-2026-10.md`; the first ledger and audit come after the deploy (see
"Post-deploy trigger").

**Stale item:** ROADMAP OD-10 ("commit the uncommitted docs in the main checkout", which lists the finance docs) is
stale for this folder: the finance docs were committed on 2026-10-11 in `f7b0cdc`. Nothing in `docs/finance/` is
waiting to be committed.

## What lives here

| File | Cadence | What it holds |
|---|---|---|
| `budget-YYYY-MM.md` | when a cost, trial or plan changes, and at least every quarter | Running costs now and after each free trial ends, trading-cost assumptions, break-even in £ and R, trial-end dates, the 60-day warning list |
| `ledger-YYYY-MM.md` | monthly, from the first month with a closed trade | Gross P&L, each trading cost on its own line (spread, commission, swap, slippage against the decision price), net P&L, each running cost, and the tie-out to the broker balance |
| `audit-YYYY-MM.md` | monthly, plus one after every deploy (`audit-YYYY-MM-DD-deploy.md`) | The checks in `audit-framework.md`, each with its result, evidence, sample size, severity and owner |
| `recommendations-YYYY-MM.md` | monthly | A ranked list for the program-director and research-director, each item with its expected £ effect or the risk it avoids, a measurement and an owner, plus owner decisions with options |
| `capital-plan.md` | quarterly, before each research freeze, and when a phase gate is recorded (CFO) | The account, the north star in % of equity, the growth path through the phase gates, the risk appetite, the leverage review and the owner-only capital recommendations |
| `audit-framework.md` | changes only through review | The check catalogue: source, method, pass/fail threshold, owner, and whether today's data can compute it (GAP list) |

## Sources (read only)

| Source | Where | What it gives the books |
|---|---|---|
| Closed-trade record | `state/closed_trades_<account>.jsonl` (`goldbot/ops/gates_phase.py` `ClosedTrade`, written by `engine/runner.py` `_record_closed`) | One line per closed position: net `pnl` in account currency, `commission`, `swap`, `r`, `ret`, lots, prices, exit reason, MAE/MFE, `pnl_currency` |
| Trade journal | store table `decisions` (`runner.flush_journal`) | Every proposal, gate refusal, order, close, trail, scale-out, kill switch and reconcile action, keyed by proposal id |
| Fills | store table `fills` (`runner._execute`) | Entry fills: requested vs filled price, the input to the slippage table |
| Approvals | `approvals/done/<proposal_id>.json` (`telegram/bus.py`, `Proposal`) | Decision price (`entry`), stop, target, card risk, spread at the decision, outcome (approved, rejected, expired), gate refusal at approval |
| Engine state | the engine's published state (`runner._write_state`) | Latest broker balance, equity, day-start equity, daily allowance, stage, `closed_records_lost`, foreign positions |
| Cost tables | `state/costs_<account>.json` (`execution/costs.py` `CostTable`), release `costs-v1` | Spread by session (median, p90), slippage by session and order type, commission, swap; whether each is measured or a prior |
| Execution audit | `execution/audit.py` | Slippage drift and spread widening against the cost table |
| Shadow book | `state/shadow_<version>.json` (`engine/shadow.py`) | Paper outcomes per model version, for shadow vs live |
| Trial registry, scorecards | `state/research_registry.jsonl`, `docs/research/` | Which change was promoted when, and its pre-registered expectation |
| Agent spend | aux.db `agent_spend` (mirrored to `state/agent_spend.json`) | Staff-agent API spend per month in USD |
| Hosting and services | `docs/deploy/free-hosting-options.md`, provider invoices the owner keeps | Running costs and trial end dates (amounts only; invoices are never committed) |
| Broker statement | an export the owner keeps locally | The balance tie-out. Never committed (a pre-commit hook blocks MT5 reports) |

**Public repo rule.** Nothing written here may contain an MT5 login, server or host, a password, a token, a Telegram
id or an e-mail address. `Proposal.decided_via` holds a Telegram id or a dashboard e-mail, so the audits count
decisions by channel ("telegram", "dashboard") and never quote the field. Accounts are named by their config key
(for example `icm-demo`). Balances are reported as figures in the account currency, without any identifier.

## Monthly cadence

The first Sunday after month end, after the research director's `monthly_research` job (08:00 UTC):

1. **Collect.** Read the sources above for the closed month. Count records, unreadable lines and
   `closed_records_lost`, and the records without broker money (`commission` or `swap` = None).
2. **Ledger.** Build `ledger-YYYY-MM.md` in GBP: gross, each cost line, net, running costs. Tie out to the broker:
   opening balance + net closed P&L + deposits - withdrawals = closing balance. Any difference above £0.01 per trade
   or £1 in total is a finding.
3. **Audit.** Run every check in `audit-framework.md` that the data supports. Write each result with its sample
   size and uncertainty. A check that cannot run because of a GAP is reported as "not computable", with the GAP id,
   never as a pass.
4. **Budget.** Re-read the free-tier pages that the hosting doc names. Update the trial-end list and apply the 60-day
   rule (`budget-2026-10.md` section 5).
5. **Recommend.** Write the ranked list. Bad news first.
6. **Report.** Post a short summary (net £, net R, trades, top finding, owner decisions due) on issue #34 for the
   program-director. Figures only, no identifiers.

## Post-deploy audit trigger

An audit runs after **every deploy** that changes the engine, risk, execution, approvals, costs or the bridge
(release-manager posts the merge on #34; the program-director routes it):

- **Day 0, within 24 h:** the rule-compliance block only (RC-1 to RC-8), on whatever the deploy produced. It checks
  that the deploy did not break the money path: proposals reach the owner, every order has an approved proposal or
  auto mode, the daily allowance is published, stops are on the broker, position ids reconcile, the cost job ran.
  With no trades it confirms that the records exist and parse.
- **Day 7:** costs and execution (CE-1 to CE-4) on the first week of ticks and fills: is the measured spread close
  to the prior?
- **First full month:** the full monthly audit.

The **first demo deploy (in progress; engine start planned for Sun 18 Oct 2026 22:02 UTC)** gets this trigger. Its
day-0 audit is due by **Mon 19 Oct 2026 22:02 UTC** (`audit-2026-10-18-deploy.md`); if the engine start slips, the
due date is the new start + 24 h. It is also the baseline: it records which GAPs from `audit-framework.md` are still
open on the running system.

Unscheduled audit: any day whose net loss exceeds the effective daily limit, any `stop_rule` or `closed_trade_record_lost`
health FAIL, a broker balance that does not tie out, or a running cost that changes.
