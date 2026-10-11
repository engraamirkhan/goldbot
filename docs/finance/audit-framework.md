# Audit framework: the checks for each monthly and post-deploy audit

Finance-auditor, 2026-10-11, base `b8e9b88`. These checks are the contract for every `audit-YYYY-MM.md`. Each check
has a data source, a method, a pass/fail threshold and an owner, and says whether **the data the system records
today** is enough to compute it. Code read for this: `goldbot/ops/gates_phase.py` (`ClosedTrade`,
`append_closed_trade`), `goldbot/engine/runner.py` (`_execute`, `_on_decision`, `_try_close`, `_exit_deals`,
`_record_closed`, `flush_journal`, `_write_state`), `goldbot/telegram/bus.py` and `approvals.py` (`Proposal`,
`BusDecision`), `goldbot/execution/costs.py`, `goldbot/execution/audit.py`, `goldbot/engine/shadow.py`,
`goldbot/ops/monthly_report.py`.

## Rules for every check

- **Net or it does not count.** Profit is the broker's net (`profit + commission + fee + swap`, which
  `_exit_deals` produces). A record without broker money (`commission` or `swap` is None: the engine's own-fills
  fallback) is **gross of commission and swap**. It is counted apart and never summed into "net".
- **Sample size on every figure.** Cells with fewer than 30 trades are reported as "noise" (the same floor as
  `attribution.min_trades`). Means carry a 90% Student-t interval.
- **Not computable is not a pass.** A check blocked by a GAP is reported as "not computable (GAP-nn)".
- **Severity:** HIGH = money lost or a safety rule not shown to hold; MEDIUM = a number that cannot be trusted or a
  leak above £5 a month; LOW = reporting quality.
- Owners: **PD** program-director (engineering, hosting), **RD** research-director (research, trials),
  **TS** trading-safety-reviewer (confirms rule-compliance findings), **FA** finance-auditor (runs the check).
  FA runs every check; the owner named is who acts on a failure.

Legend for "today": **Yes** = computable from what is recorded; **Partial** = computable with a join, a
reconstruction or an approximation (stated); **No** = a GAP blocks it.

## 1. Profitability

| ID | Check | Source | Method | Pass / fail | Owner | Today |
|---|---|---|---|---|---|---|
| PR-1 | Net P&L for the month, £ and R, against the running costs | `closed_trades_*.jsonl`; budget | Sum `pnl` where `pnl_currency` = account currency and commission and swap are present; R = sum of `r`; subtract the month's running costs (ledger) | Pass: net after running costs >= 0. Reported with n and a 90% interval; below 30 trades it is "noise" whatever the sign | RD | **Partial**: GAP-05 (fallback records gross), GAP-18 (pre-BACKLOG-28 R understated) |
| PR-2 | Net expectancy per trade and per trading day | closed trades | Mean `r` and mean `pnl`; per day = net / Mon-Fri days elapsed (as `monthly_report`) | Pass: 90% lower bound > 0 once n >= 30; otherwise "noise" | RD | **Yes** (with the PR-1 caveats) |
| PR-3 | By **family** | closed trades | Family = `agent_id` prefix before `-g<gen>-` (`AgentIdentity.agent_id`) | A family whose net R is below 0 with n >= 30 and an upper 90% bound < 0 is a finding | RD | **Partial**: derived from a naming convention, not a field (GAP-01) |
| PR-4 | By **timeframe** | closed trades + agent registry | Look up the agent's timeframe from the registry / specialist config | Same as PR-3 | RD | **No** for retired or mutated agents: no timeframe on the record (GAP-01) |
| PR-5 | By **session** (Asia, London, New York) | closed trades `entry_utc` + `SessionTable` | Label each entry with `DEFAULT_SESSIONS.session_label` (DST-aware) | Same as PR-3 | RD | **Yes** |
| PR-6 | By **day type** (opportunity day vs normal) | closed trades + bars via `asof_join` on `available_utc`; `features/day_type.py` | Classify each entry day point-in-time; also the ex-post test "range >= 1.5 x ATR(1d)" (ADR 0004 KPI) | Report capture: share of opportunity days with >= 1 R. No pass bar until H-16 sets one | RD | **Partial**: recomputed, not recorded; the classifier (H-16) does not exist yet (GAP-16) |
| PR-7 | **Owner approvals vs rejections**: did rejections save or cost money? | `approvals/done/*.json` (outcome, entry, stop, target, side, lots) + bars | Approved: the real trade. Rejected and expired: replay entry, stop and target (and the exit policy) on 1m bars from the decision time, with the 0.62 $/oz round trip; compare mean R | Finding when rejected trades' replayed mean R exceeds approved trades' by more than 0.1 R with n >= 30 each (rejections cost money), or the reverse (rejections add value: report it) | RD (owner informed) | **Partial**: replay needed, no recorded counterfactual (GAP-09); decision time lost (GAP-10); auto-mode trades have no Proposal file (journal `executed:auto` only) |
| PR-8 | Drawdown against the limits | closed trades; engine state | Max drawdown of the closed-trade return index (as `monthly_report`) vs the 8% and 12% stages | Fail: closed drawdown beyond 8% without the stage engaging | TS | **Partial**: closed trades only; floating drawdown is not kept (GAP-11) |
| PR-9 | North-star capture | closed trades | Days at £50+, best-day histogram, average £/day (ADR 0004 s.6). Descriptive: never a target | Reported, no pass/fail | RD | **Yes** |

## 2. Rule compliance

| ID | Check | Source | Method | Pass / fail | Owner | Today |
|---|---|---|---|---|---|---|
| RC-1 | **Every order went through RiskGate** | journal (`order`, `proposed`, `executed:auto`, `gate_at_approval:*`); code | Code: `broker.place_order` has one caller, `runner._execute`, reached only after `gate.check` (at decision and again at approval). Data: every journal `order` row has a proposal id with a `proposed` or `executed:auto` row before it and no `gate:` refusal for that id | Fail on any order without a gate-allowed decision | TS | **Partial**: the `order` row carries no proposal id, so it is matched by agent and time; the `fills` row carries `client_order_id` (GAP-12) |
| RC-2 | **Every entry had the owner's click** (or eligible auto mode) | `approvals/done/*.json`; journal; engine state `approval_mode` | Each filled position's `client_order_id` = proposal id. In propose mode the proposal's outcome must be APPROVED; otherwise there must be an `executed:auto` row while auto mode was allowed (not within 30 days of a re-arm, not after the kill switch) | Fail on any position without an approval or a valid auto-mode decision | TS | **Partial**: the approval time is discarded (GAP-10), so "approved within 90 s" cannot be shown; the auto-mode history is reconstructed from journal rows (`mode_propose`, `rearm`) |
| RC-3 | **Risk per trade within the cap** | approvals (`risk_amount`, `risk_fraction`, `min_lot_exception`); closed trades (`lots`, `entry_price`, `r`); `equity_before` | Realised risk = abs(entry - initial stop) x lots x 100 x fx / equity_before | Fail if any trade > 1.0% (the hard clamp), or > 0.5% x multiplier without the min-lot exception, beyond the 2% fx tolerance (ADR 0009) | TS | **Partial**: initial stop and fx are not on the record (GAP-02, GAP-06). Back-solved from `r` and `pnl` when both exist |
| RC-4 | **Daily £50 limit and percentage caps held** | closed trades by UTC risk day; journal `gate:` reasons; engine state | Per risk day: realised loss vs min(2% x day-start equity, £50) per account and the supervisor's 1.5%; entries after the cap was reached must be refused | Fail: an entry filled after the effective limit was reached, or a day's loss exceeded the limit by more than one trade's stop risk (gaps excepted, listed) | TS | **Partial**: day-start equity and the cap-hit time are not kept per day (GAP-11) |
| RC-5 | **Kill switch worked** | journal `mode_propose` (reason `kill_switch`), `kill_switch_close`, `/halt` control; health log | For each halt: time to the first refused entry, and every open position closed or kept on server stops as designed | Fail: any entry after a halt; any kill-switch close unconfirmed for more than 5 minutes | TS | **Yes**, once a halt happens; otherwise run as a drill (the chaos drill in gate evidence) |
| RC-6 | **Exits automatic** | closed trades `exit_reason`; journal close rows | Every exit reason is an engine or broker reason (stop, target, time_exit, hard_flat, trail_close, kill_switch_close, engine_stop_close, stop_out). Any `broker_close` without a deal reason = a manual close | Fail on any manual close, or any exit that waited on an approval | TS | **Yes** (a manual close shows as `broker_close`) |
| RC-7 | **No stop widened** | `state` orders (`initial_sl`); journal `trail`, `weekend_tighten`, `reinstate_stops`, `orphan_stop` rows | Per position, the sequence of stop levels from `initial_sl` must move only towards the price (up for a long, down for a short) | Fail on any loosening | TS | **Partial**: the rows log the new stop only, and the final stop is not on the closed record (GAP-03). Changes made outside the engine (in the terminal) are not seen |
| RC-8 | **Position ids reconcile with the broker** | closed trades; orders state; MT5 deal history (magic range); `closed_records_lost` | Every engine position (magic in the account's range) in the broker's deals has exactly one closed record or is still open; no closed record without broker deals | Fail on any position missing either side, or `closed_records_lost` > 0 | PD | **No**: there is no reconciliation job and broker history is not exported (GAP-08) |
| RC-9 | **Broker balance ties out** | engine state (latest balance only); owner's statement export | Opening balance + net closed P&L + deposits - withdrawals = closing balance | Fail: difference > £1 a month or > £0.01 per trade | PD | **No**: no balance history, deposits and withdrawals not captured (GAP-07) |

## 3. Costs and execution

| ID | Check | Source | Method | Pass / fail | Owner | Today |
|---|---|---|---|---|---|---|
| CE-1 | Realised **spread** vs model, by session | store `ticks`; `costs_<account>.json`; prior 0.25 $/oz | Median and p90 by session (`spread_table`) vs the prior and the previous table | Finding: median > 1.5 x the model (`SPREAD_WIDENING`), or the London/NY median > 0.30 $/oz on a Raw account | RD | **Yes** (from ticks), once the terminal runs |
| CE-2 | Realised **entry slippage** vs model | store `fills` (requested, filled, side) | `execution_audit`: mean by session against the cost table / 0.15 prior, with the 2-SE drift flag | Finding: mean > model by > 2 SE and >= 0.05 $/oz; n >= 50 for a measured cell | RD | **Yes**, but measured from the **approval-time quote**, not the decision price (GAP-02) |
| CE-3 | **Decision-to-fill cost** (the price of the 90 s approval wait) | `approvals/done` `entry` (decision price at the bar close) + `fills` requested and filled | side x (filled - decision price), $/oz and R, approved trades only | Finding: mean adverse move > 0.10 $/oz or > 0.02 R (n >= 30) | RD / PD | **Partial**: a join by proposal id; auto-mode decisions keep the decision price only as the fill's `requested` (GAP-02) |
| CE-4 | Realised **exit slippage** (stops above all) | closed trades `exit_price`, `exit_reason`; final stop and target | For stop and target exits: side x (level - exit fill); for market closes: against the quote at the decision | Finding: mean stop slippage > 0.15 $/oz (the prior) with n >= 30 | RD | **No**: no exit fills in `fills`, no final stop on the record, no quote at the close decision (GAP-03, GAP-04) |
| CE-5 | **Commission** vs model | closed trades `commission`; `BrokerTerms` | Commission per lot round trip vs 7 USD (2 x 3.5) | Finding: > 10% above the model | RD | **Partial**: in account currency without the fx used (GAP-06); None on fallback records (GAP-05); the `fills.commission` column is always NaN |
| CE-6 | **Swap** paid vs prior | closed trades `swap`; `BrokerTerms` swap rates | Sum by side; nights held from entry and exit and the server rollover | Finding: swap > 10% of gross P&L for a family, or a family that is net positive only before swap | RD | **Partial**: as CE-5 |
| CE-7 | **Costs that turn a gross edge into a net loss** | closed trades; CE-1 to CE-6 | Gross R (before every cost) vs net R by family and session | Finding: any cell with gross R > 0 and net R < 0 (n >= 30) | RD | **No** until spread and exit slippage per trade exist (GAP-03, GAP-04). Gross = net + commission + swap is computable; spread is not separable |

## 4. Learning

| ID | Check | Source | Method | Pass / fail | Owner | Today |
|---|---|---|---|---|---|---|
| LE-1 | **Month-on-month trend** | the last 3-6 ledgers and audits | Net R per trade, hit rate, cost per trade (R), owner approval rate, opportunity-day capture, each with n | Finding: two months of decline in net R per trade with n >= 30 each; no trend claimed below that | RD | **Yes** from the second month (with the PR-1 caveats) |
| LE-2 | **Out-of-sample payoff of each promoted change** | trial registry (promotion date, pre-registered expectation); closed trades and shadow trades after the date | Net R of trades made by the promoted version after promotion vs its pre-registered expectation and the version it replaced | Finding: lower 90% bound below the expectation at n >= 50, or net R < the predecessor's | RD | **No**: no model version on the closed record (GAP-13); approximated by `agent_id` and date |
| LE-3 | **Shadow vs live divergence** | `state/shadow_<version>.json`; closed trades; proposal id (account, agent, bar close) | Pair each live trade with the shadow trade of the same agent and signal bar; compare entry, exit and R; divergence = live R - shadow R | Finding: mean divergence < -0.1 R (n >= 30), or more than 10% of live trades without a shadow twin | RD | **Partial**: the pair is rebuilt from the proposal id (it encodes the bar close as a timestamp); no version on the live record (GAP-13). Shadow stops fill at the stop on gaps (optimistic, documented) |
| LE-4 | Did the owner's approval rate change, and with what effect? | approvals; PR-7 | Approval, rejection and expiry rates by month, with PR-7's replayed value | Reported | RD | **Partial** (as PR-7) |

## 5. GAP list: what today's records cannot support

Ranked by how much they block the audit before the first live trade. "Close by" is advice to the program-director;
the fix is theirs, and any change to `engine/`, `risk/` or `execution/` needs trading-safety review.

| GAP | What is missing | Where (code) | Blocks | Close by |
|---|---|---|---|---|
| **GAP-02** | **Decision price and spread on the closed record.** The decision price is only in the Proposal file (`entry`, `spread_points` at the decision); `fills.requested` is the **approval-time** quote in propose mode (`_on_decision` passes the tick at approval), so the slippage table understates the true cost of the 90 s wait. No spread at entry or exit on any record | `ClosedTrade`; `runner._execute` (`requested=price`); `Proposal.entry` | CE-2, CE-3, RC-3, ledger "slippage vs the decision price" | before the first demo trade |
| **GAP-03** | **Final stop and target on the closed record**, and the stop history. Trail rows log only the new stop. `initial_sl` is in the orders state, not on `ClosedTrade` (R implies it only when fx is known) | `ClosedTrade`; `runner` `trail` rows | RC-7, CE-4, RC-3 | before the first demo trade |
| **GAP-04** | **Exit fills are not logged** in `fills`: `_try_close` journals the fill price but no requested quote; broker stop and target fills are not compared with their level | `runner._try_close`, `_sweep_closed` | CE-4, CE-7. Exit slippage is assumed equal to entry slippage (0.15 $/oz), which is untested and most likely optimistic for stops | before the first demo trade |
| **GAP-08** | **No position reconciliation with the broker.** Nothing compares broker deal history (by magic range) with the closed records and orders | none | RC-8, ledger tie-out | before the first demo trade (a daily read-only job) |
| **GAP-07** | **No broker balance history**, and deposits or withdrawals (MT5 balance deals) are not captured. The engine state holds the latest balance only | `runner._write_state` | RC-9, the ledger's tie-out, time-weighted returns | before the paper -> tiny-live gate |
| **GAP-05** | Closed records from the **own-fills fallback** have `commission` and `swap` = None, and their `pnl` is **gross** of both; `fills.commission` is always NaN | `_record_closed` fallback branch; `_execute` | PR-1, CE-5, CE-6. Overstated net | add a `money_source` field (broker or engine) and a count in health |
| **GAP-10** | **The owner's decision time is discarded**: `BusDecision.ts` lives in `approvals/decisions/`, which `archive` deletes; `Proposal` has `created` only | `telegram/bus.py` `archive` | RC-2 (within 90 s), CE-3 attribution, approval latency | keep `decided_ts` on the archived Proposal |
| **GAP-06** | **No fx rate on the record.** Risk is in USD and P&L in GBP; the fx used for R is not stored, so cost lines cannot be restated in either currency | `_record_closed` (`fx`) | RC-3, CE-5, CE-6, GBP ledger lines | before the first demo trade |
| **GAP-01** | **No timeframe or family field** on the closed record; family comes from the `agent_id` naming convention, timeframe from a registry lookup that fails for retired agents | `ClosedTrade` | PR-3, PR-4 | before the first demo trade |
| **GAP-13** | **No model version** (champion version or registry trial) on the closed record or the fills | `ClosedTrade` | LE-2, LE-3 | before the first promotion |
| **GAP-11** | **No daily risk summary**: day-start equity, minimum equity (floating) and the time a cap was hit are not kept per day; the state file is overwritten | `runner._write_state` | RC-4, PR-8 | before the paper -> tiny-live gate |
| **GAP-09** | **No counterfactual for rejected or expired proposals**: their outcome must be replayed from bars; auto-mode decisions have no Proposal file | `bus.archive`; shadow book is per version, not per proposal | PR-7, LE-4 | replay script (research side) is enough; no engine change needed |
| **GAP-12** | The journal `order` row has **no proposal id** (the fills row has `client_order_id`) | `runner._execute` | RC-1 matching | small; with GAP-02 |
| **GAP-16** | **Day type** is not recorded at entry (the H-16 classifier does not exist yet) | `features/day_type.py` | PR-6 | with H-16 |
| **GAP-18** | Records before BACKLOG 28 have `pnl_currency` = None and R understated by about 1.33x on the GBP account | `ClosedTrade` | PR-1, PR-2 | none needed: report them apart (`monthly_report` already flags them) |
| **GAP-19** | **Running costs are not machine-recorded**: hosting bills and the domain come from the owner's invoices; only agent spend is in aux.db | none | the ledger's running-cost lines | manual, monthly; amounts only |

What is already good enough: one durable, fsynced, de-duplicated record per position (`append_closed_trade`); net
P&L from the broker's deals with commission, fee and swap when the deals are readable; MAE and MFE; the journal of
every decision and refusal; spread from the terminal's own ticks; entry fills with requested vs filled; the
measured-or-prior flag on every published cost component; agent spend in aux.db.
