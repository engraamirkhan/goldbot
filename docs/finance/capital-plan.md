# Capital plan: account, north star, growth path and owner decisions

CFO, 2026-10-16. Base: `claude/gifted-goldberg-lcb7pl` at `9f04599`. Frame: ADR 0004 (account, £50 daily loss limit,
north star), `config/settings.yaml` (`risk:`, `gates:`), `goldbot/ops/gates_phase.py`, `goldbot/risk/gate.py`,
the finance-auditor's `budget-2026-10.md` (2026-10-11) and `recommendations-2026-10.md`.
**Sample size behind every trading figure: 0 trades.** Nothing trades yet; every £ below is arithmetic on stated
assumptions, not a measurement. Nothing here promises profit, changes a limit or loosens one. Reviewed quarterly,
before each research freeze (next: before the Q2 2027 freeze), and whenever a phase gate is recorded.

## Verdict first

| # | Finding | Number |
|---|---|---|
| 1 | The north star (£50 on an opportunity day) is **5% of equity in a day, 6.1 R** at £1,000. At the expectancy the gates will accept, the average trading day makes **£0.78-1.56**. A £50 day is 30-60 times the average day: a rare runner day, not a plan | s.3 |
| 2 | At the gates' expectancy (+0.1 to +0.2 R net, 20 trades a month) the honest return is **1.6-3.3% a month, £16-33**, before running costs | s.3 |
| 3 | **Tiny-live cannot trade at £1,000.** Tiny-live risks 0.1% a trade and never uses the minimum-lot exception, so the 15m minimum lot (£8.20) needs **about £6,850** of live equity. As written, the paper -> tiny-live -> full path cannot be walked at £1,000. Owner decision C2 | s.4 |
| 4 | Earliest real money on the current plan: tiny-live **about Dec 2027**, full size **about Mar 2029**, and only if a strategy passes the research gates in Q1 2027 (none has: 0 of 20 Q4 2026 trials) | s.4 |
| 5 | **Leverage 1:5000 on the demo does not change any engine order** (RiskGate sizes by risk and charges margin at 1:20 at least), **but it removes the broker as a backstop** for any order that bypasses RiskGate: the bridge's own ceiling is 3.0 lots, which 1:5000 lets through on £1,000, and a $4.43 move then costs the whole account. Flagged to the finance-auditor and the sre; owner recommendation C5 | s.6 |

## 1. The account

| Item | Value | Source |
|---|---|---|
| Account | **£1,000 GBP, demo**, IC Markets (SC), config key `icm-demo`. Balance and equity are read from MT5 at runtime, never from config | ADR 0004 A1, `config/accounts.yaml` (`login: null`) |
| Account type | **Raw Spread assumed** (spread about $0.25/oz + $7 commission per lot round trip). Not yet confirmed: the sre reads it at the MT5 login (ROADMAP OD-4). On Standard, 15m is off except at the session open and nothing trades at £1,000 | ADR 0004, budget s.3 |
| Hedging | Hedging account (checked at the MT5 login). Positions on both accounts count as one position for every limit (combined-notional cap) | deploy runbook; `gate.py:536-543` |
| Leverage | **1:5000 reported today** (owner note; unverified until BACKLOG 27 records it with `order_calc_margin` for 0.01 lot and the stop-out level, due 2026-10-20). Gold may carry its own symbol margin regardless of the account setting | BACKLOG 27 |
| Real money at risk now | **£0.** Live accounts stay disabled until the paper -> tiny-live gate is recorded in `state/phase_state.json` and the owner runs `unlock_live` with the typed phrase | CLAUDE.md, DESIGN "Demo first" |
| £ per R | **£8.20**: the 15m minimum lot (0.01 lot = 1 oz) with a 1.5 x ATR stop of about $10.90, at GBPUSD 1.33. 0.82% of equity, inside the 1% minimum-lot exception cap | ADR 0004 s.2 |
| What can trade | 15m only, one position at a time (6 x equity notional cap), at most two full losses a day (worst day about £17) | ADR 0004 s.2-3 |

## 2. The north star and the loss limit

- **North star:** £50 or more on days the market offers opportunity, less or nothing when it does not. Counted on the
  opportunity day (22:00-22:00 UTC). It is never a quota and never a reason to size up (ADR 0004 s.6).
- **£50 daily loss limit, never loosened:** per account, in account currency, projected at entry
  (`daily_loss_limit_abs: 50`), on the risk day 00:00-24:00 UTC. The tighter caps bind first at £1,000: 2% account
  (£20) and 1.5% supervisor (£15). The £50 binds above £2,500 (account) and about £3,333 (combined). Exits are never
  gated. This plan recommends no change to any of these, at any P&L.

## 3. What the north star implies, honestly

**As a share of equity.** £50 is 5.0% of £1,000 in one day; 6.1 R at £8.20 a R. At the 1% cap it is still 5 R.

**What the gates' expectancy earns.** The paper gate accepts a strategy whose paper expectancy is positive and no more
than 50% below its backtest (`paper_expectancy_within: 0.5`); ADR 0004 s.5 puts a real edge at +0.1 to +0.2 R net.
Expected £ = trades x net R x £8.20:

| Net R per trade | 10 trades / month | 20 trades / month | 40 trades / month |
|---|---|---|---|
| +0.1 R | £8.20 (0.8%) | **£16.40 (1.6%)**, £0.78 a trading day | £32.80 (3.3%) |
| +0.2 R | £16.40 (1.6%) | **£32.80 (3.3%)**, £1.56 a trading day | £65.60 (6.6%) |

40 trades a month is an upper bound at £1,000: one position at a time and entries stop after two losses a day.
These are before running costs (budget s.4) and with no drawdown stop; ADR 0004's Monte Carlo puts the 8%
size-down stage (a de-facto stop at £1,000) at 67% likely within 3 months even at +0.1 R.

**How a £50 day could happen at £1,000.** 6.1 R in a day needs a runner (a trailed winner capturing about one daily
ATR, about $66) or four 2 R winners with at most one loss. At +0.1 R with 2 R targets the win rate is about 37%, so
four wins in a row is about 2% even on a day with four signals, which is itself rare. The frequency of £50 days is
**unmeasured** (BACKLOG 30); the honest prior is "a few times a year at best" at this size.

**£50 on every opportunity day** is not realistic at £1,000. If opportunity days are 4-8 a month (assumption,
unmeasured), £50 on each is £200-400 a month: **20-40% a month, 6-25 times the 1.6-3.3% above.**

**When it becomes realistic**, at a sustained, net, measured return (none exists):

| Target | Monthly £ | Equity at 2% / month | at 3% | at 4% |
|---|---|---|---|---|
| £50 on 6 opportunity days a month | £300 | £15,000 | £10,000 | £7,500 |
| £50 on every trading day (ADR 0004 s.6) | £1,050 | £52,500 | £35,000 | £26,250 |

The route is equity (results plus deposits), never risk per trade.

## 4. Growth path through the phase gates

Thresholds from `config/settings.yaml` `gates:`; "PROPOSED" values await owner sign-off (ROADMAP OD-14, Mon 11-30).
Dates are the earliest possible, assume a strategy survives the Q1 2027 trials, and 20 trades a month.

| Phase | Gate to leave it | Capital | £ at risk per trade | Earliest exit | Status |
|---|---|---|---|---|---|
| 0. Research | backtest -> paper: DSR >= 0.95 (PROPOSED) on the true trial count, positive in 3 separate years, >= 500 backtest trades, live-tick spread model | £0 | £0 | about Apr 2027 | **0 strategies passed** (0/20 Q4 2026 trials; Q1 2027: 13 reserved + 7) |
| 1. Paper (demo, £1,000) | >= 150 paper trades **and** >= 182 days (PROPOSED); expectancy within 50% of backtest; fills within 30%; chaos drill | £0 real (demo) | £0 real (£8.20 demo) | 150 trades at 20/month = 7.5 months: **about Dec 2027** | engine starts on demo Sun 18 Oct; no strategy to trade |
| 2. Tiny live | >= 300 live trades **and** >= 365 days (PROPOSED); expectancy within 40% of paper; drawdown under 1.5 x backtest; brokers within 15% | **see C2: about £6,850 needed** | 0.1% of equity: £6.85 at £6,850, so the 0.01 lot (£8.20) just fits within 1.2 x | 300 trades = 15 months: **about Mar 2029** | infeasible at £1,000 |
| 3. Full size | stop rule: after 18 months paper + live, > 500 pooled trades and lower 90% bound on expectancy < 0 -> stop | owner's choice (C3) | 0.5% design; 0.82% at £1,000 via the exception | n/a | |

**Why tiny-live is infeasible at £1,000.** Tiny-live risk is 0.1% (`risk_per_trade_tiny_live: 0.001`) and the
minimum-lot exception is never applied in tiny-live (ADR 0004, BACKLOG 25). A trade is skipped when the minimum lot's
risk exceeds 1.2 x target. 15m: £8.20 / (0.1% x 1.2) = **£6,833**; 1h: £16.47 / 0.0012 = **£13,725**. Below that,
every tiny-live proposal is refused `min_lot_exceeds_risk`, so the tiny-live gate's 300 trades can never accrue.

**Evidence that would justify adding capital** (recommendation, never automatic): the stage's gate recorded, plus
at least 6 months of out-of-sample net results after every cost (trading and running) with the lower 90% bound on
expectancy above zero, and drawdown inside 1.5 x backtest. Never on a streak.

**Evidence that would justify cutting back or stopping:** the 8% size-down stage or the 12% halt; the stop rule;
3 consecutive months net negative after all costs at full size (recommend returning to tiny-live size); a finance
audit finding that the ledger does not tie out to the broker (no capital change until it does).

## 5. Recommended risk appetite (for the owner to confirm; no limit changes)

| Dimension | Recommendation | At £1,000 | Rail it sits inside |
|---|---|---|---|
| Max drawdown tolerated | the existing stages: 8% sizes down, 12% halts | £80 / £120 | `drawdown_stage1/2` |
| Worst day | two full losses, then the 1.5% supervisor cap | about £17 | 2% / 1.5% / £50 |
| Losing trades | expect about 63% of trades to lose at +0.1 R with 2 R targets (37% win rate); reported monthly, never acted on by sizing | n/a | none: reporting only |
| Per-trade risk | the 15m minimum lot, 0.82%; no increase | £8.20 | 1% clamp, `min_lot_risk_cap` <= 0.01 |
| Concentration | one position at a time, one instrument | 1 x 0.01 lot | 6 x equity notional cap |

## 6. Leverage review: 1:5000 on the demo

**Engine orders: no money-path exposure.**

| Check | Evidence | Effect of 1:5000 |
|---|---|---|
| Lots come from risk, not margin | `gate.py:510-529`: lots = equity x risk x multiplier / (stop x contract x fx), clamped at 1% | none |
| Margin is charged at 1:20 at least | `gate.py:365-384, 531`: margin = max(broker `order_calc_margin`, notional / 20); margin level after the order >= 300% | none: the broker's smaller figure never wins |
| Combined notional <= 6 x equity | `gate.py:540-543` (`0.30 x leverage_cap 20`) | none |
| Lots per order <= 2.0 | `runner.py:1032` caps `volume_max` at Intent's 2.0 | none |

**Residual exposure: the broker is no longer a backstop.**

| Path | At 1:20-1:30 | At 1:5000 |
|---|---|---|
| Margin for 1 lot ($415,000 notional, about £312,000) | £15,600 / £10,400 | **£62** |
| Largest position the broker accepts on £1,000 | about 0.06-0.09 lots | **about 16 lots** |
| An order of 3.0 lots (the bridge's only ceiling, `bridge.py:104, 266`) that bypasses RiskGate (a bug, a misuse of the bridge token, a manual order) | refused by the broker for margin | **accepted**: a $10 move costs about £2,256; **$4.43 against it wipes £1,000** (15m ATR is about $7) |
| Broker stop-out | close to the engine's own margin floor | only near zero equity; a gap can go past it (negative-balance protection at IC Markets (SC) is unverified) |

So 1:5000 does not raise the risk of any trade RiskGate approves; it raises the **size of the worst accident** from
about £50 to the whole account, because RiskGate becomes the only line between a bad order and the account.

**Flags (bad news first; routed by the program-director today):**

| To | Flag | Ask | Due |
|---|---|---|---|
| **sre** | BACKLOG 27 must record the **account leverage, the XAUUSD margin for 0.01 lot and the stop-out level** from the terminal (it already lists them). Confirm whether 1:5000 applies to gold or gold has its own margin | file on the demo box; ADR 0004 assumptions updated | 2026-10-20 (BACKLOG 27 due date) |
| **sre** (with trading-safety review, architect plan) | The bridge ceiling is a fixed 3.0 lots. An **equity-scaled bridge check** (refuse an order whose notional exceeds the 6 x equity cap, or whose lots exceed what 1:20 margin allows) would restore an independent backstop that does not depend on the broker's leverage. A code change in the money path: a recommendation, not decided here | architect plan + trading-safety review if taken up | backlog item proposed by the product-owner before the paper gate |
| **finance-auditor** | Add a monthly audit check: **account leverage recorded**, largest order (lots, notional / equity) vs the gate's limits, and any position without an approved proposal (already RC-2). Report "not computable" until BACKLOG 27 lands | row in `audit-framework.md` | November audit (Wed 2026-11-04) |

## 7. Owner-only recommendations (queued by the program-director; the owner decides)

| ID | Decision | Options with numbers | CFO recommendation | Reverses it | Deadline |
|---|---|---|---|---|---|
| **C1** | Real money before the paper -> tiny-live gate | (a) none: £0 at risk; (b) any live deposit now: no strategy, expected value <= £0 after costs | **(a)**, the current rule | a recorded gate | standing |
| **C2** | **Tiny-live is infeasible at £1,000** (s.4) | (a) fund the live account to about **£6,850** at tiny-live start: £8.20 a trade (the same £ as today's demo), effective daily limit £50 (the absolute limit binds), 8% stage £548, 12% halt £822; (b) keep £1,000 and the rail: tiny-live never trades, the system never goes live; (c) amend tiny-live for the minimum lot at £1,000 (0.82% a trade, worst day about £17, 12% halt £120): **a loosening of the 0.1% tiny-live rail**, needing an ADR and trading-safety review; listed so the owner sees it, **not recommended by the CFO** | **No deposit now. Rule on (a) or (b) before the backtest -> paper gate is recorded** (earliest about Apr 2027), on paper evidence. Until then (b) is the default and costs nothing | a strategy passing backtest -> paper | before the backtest -> paper gate; reviewed at the Q2 2027 freeze |
| **C3** | Deposits (ADR 0004 D5) | £0, £100 or £250 a month (ADR 0004 s.6 table) | **None to the demo** (demo money is not real). Live deposits sized by what the next gate needs (C2), never by the north star | the tiny-live gate | at the paper gate |
| **C4** | Risk appetite (s.5) | confirm, or ask for a tighter one | **Confirm the existing rails** as the appetite; nothing looser | none | with OD-14, Mon 2026-11-30 |
| **C5** | **Broker account leverage** | (a) keep 1:5000; (b) the lowest IC Markets offers that is at least 1:20 (1:20 still leaves a 0.01 lot at a 641% margin level) | **(b) on the live account at opening, and on the demo if the client area allows it.** It tightens, never loosens: the broker caps an accidental position at about 0.06-0.09 lots (a $10 move costs about £48-72) instead of about 16 lots. Demo evidence is unaffected because sizing does not depend on leverage | BACKLOG 27 shows gold already carries its own low symbol leverage | demo: at the next terminal login; live: at account opening |

## 8. Tax and records readiness (UK, GBP; the CFO is not a tax adviser)

| Item | Matters from | Note |
|---|---|---|
| Keep every closed trade, cost and deposit in GBP | the first live trade | the finance-auditor's ledger already does this; broker statements stay with the owner, never in the repo |
| CFD gains and losses are normally within Capital Gains Tax for an individual (spread betting is treated differently) | the first live tax year | the owner should confirm with HMRC guidance or an adviser before going live; the annual exempt amount and rates change, check the current figures |
| Running costs (hosting, data, agents) | if the owner treats the activity as a trade | an adviser question; the ledger keeps them in GBP either way |

## Not verified / left out

- Leverage 1:5000 is the owner's note; the symbol margin, stop-out level and negative-balance protection are not
  read from the terminal yet (BACKLOG 27). The bridge and gate citations are from reading the code, not a test.
- Gold $4,150/oz, GBPUSD 1.33 and ATR are ADR 0004's estimates. The number of opportunity days a month is assumed.
- No code, settings, limits, trials or gates were changed. No account identifiers are in this file.
