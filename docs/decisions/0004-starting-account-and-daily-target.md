# 0004 Starting account £50 and the £50-a-day target

2026-10-10 · program director, with the product-owner and performance-analyst views · decision delegated by the
owner ("we will start with 50 GBP, the target should be to make 50 GBP per day, using any of the strategies on 15m,
1h, 4h, daily, weekly").

## Owner summary
- £50 cannot place a single trade under goldbot's safety rules. The smallest gold trade (0.01 lot) with a normal
  stop risks 16% of £50 on 15m and 33% on 1h. One losing trade would trip the 12% emergency stop.
- £50 a day from £50 means doubling the account every day. No strategy, ours or anyone's, does that. Trying would
  lose the £50 within a few trades (about a 99% chance within five).
- What we do: stay on demo (as planned) and keep the £50 for later. Real trading starts only once the account
  reaches about **£850 (15m), £1,650 (1h), £3,300 (4h) and £7,900 (daily)**, and only after the paper gate passes.
- £50 a day becomes a realistic average at roughly **£20,000–£40,000** of equity at our normal 0.5% risk, if a
  strategy proves an edge. None has yet.
- Progress is reported as monthly % return and R (risk units), not £ a day.
- We will never use martingale, grids or bigger bets to catch up.

## Context
The owner wants to start with £50 and make £50 a day on any of 15m, 1h, 4h, daily or weekly. The rails this must
fit, from `docs/DESIGN.md` (Sizing, Hard limits) and `config/settings.yaml` `risk:`:

- Risk per trade 0.5% (`risk_per_trade: 0.005`), 0.1% in tiny-live (`risk_per_trade_tiny_live: 0.001`), multiplier
  0.25–1.5, hard clamp 1% (`goldbot/risk/gate.py:35`, clamp at `:246-247`).
- Stop = 1.5 × ATR(14) of the decision timeframe. Lots = equity × risk × m / (stop × 100 oz), rounded down; the
  trade is skipped with `min_lot_exceeds_risk` when the minimum lot would exceed 1.2× target risk
  (`gate.py:248-256`).
- Margin: the larger of the broker's figure and notional / 20 (`gate.py:159-182`, `leverage_cap: 20.0` at `:48`),
  and margin level after the order ≥ 300% (`:260-264`). Combined notional ≤ 30% × 20 × equity = 6× equity
  (`:49-52`, `:265-271`).
- Daily cap 2%, weekly 5%, drawdown 8% (size down) and 12% (close everything, halt) (`settings.yaml` `risk:`).
- Decision timeframes in the engine are 15m, 1h, 4h. 1d is research-only and 1w is context only
  (`settings.yaml` `timeframes:`, `walkforward:` comment).
- No strategy is net positive after costs. The best gross edge measured is tsmom, +0.06 R, negative net
  (`docs/BACKLOG.md` "Where we stand"). Five families are retired (`settings.yaml` `research.retired_families`).

### Assumptions (stated, not measured)
| Input | Value | Source |
| --- | --- | --- |
| GBPUSD | 1.33, so £50 ≈ **$66.50** | currency-converter.org.uk, 1.32–1.324 on 3–4 Oct 2026; 30rates 1.35 on 10 Sep 2026. Rounded to 1.33 |
| XAUUSD | **$4,150/oz** | RoboForex analysis, 28 Sep 2026 (range 3,920–4,500). No same-day quote found |
| Contract | 100 oz per lot, min lot 0.01 (= 1 oz), step 0.01 | design and `gate.py:106-108` defaults; read from the terminal at runtime |
| IC Markets SC leverage on gold | up to 1:500 headline (FXStreet broker review); comparison sites list 1:200 for gold | **unverified** (the playbook notes the ICM spec page returned 404). Irrelevant to sizing: RiskGate uses the stricter 1:20 |
| ATR(14), 1d | **~$70** (range $50–110) | **estimate**: ~22% annualised vol at $4,150 gives a daily σ of ~$57, and ATR runs ~1.2× σ. Consistent with the playbook's 1.5 × ATR(1d) = $75–170 (`docs/research/xauusd-trader-playbook.md` 4.4). Not measured from `data-v1` (that is BACKLOG 22, G-7) |
| ATR, other timeframes | √time scaling of ATR(1d) over a 23-hour trading day: 4h ÷ 2.4, 1h ÷ 4.8, 15m ÷ 9.6, 1w × √5 | **estimate**; intraday ranges in practice run somewhat above √time, which makes the thresholds below slightly optimistic |

All dollar thresholds below scale linearly with ATR: if volatility is 50% higher, every equity threshold is 50% higher.

## 1. Feasibility at £50 ($66.50)

| Timeframe | ATR(14) | 1.5 × ATR stop | Risk of 0.01 lot | as % of £50 | In the engine today |
| --- | --- | --- | --- | --- | --- |
| 15m | ~$7.30 | ~$10.90 | $10.90 | **16.4%** | decision TF (off on a Standard account, `gate.py` `standard_account_15m`) |
| 1h | ~$14.60 | ~$21.90 | $21.90 | **32.9%** | decision TF |
| 4h | ~$29 | ~$43.50 | $43.50 | **65.4%** | decision TF |
| 1d | ~$70 | ~$105 | $105 | **158%** | research-only |
| 1w | ~$157 | ~$236 | $236 | **354%** | context only |

Commission ($3.50/lot/side, so $0.07 per 0.01-lot round trip) and spread (~$0.15–0.30/oz) are small next to these.

Checked with the G-7 tool (`goldbot/research/min_lot.py`, branch `worktree-agent-a5e86482ae604f08f` commit
`08e654a`, not yet merged; run from an extract of that commit, so no code enters this change):
`python goldbot/research/min_lot.py --price 4150 --stop-distance <10.9|21.9|43.5|105|235.5> --equity 66.5 --risk
<0.01|0.005|0.001>`. It reports "0.01 lot … refused" at $66.50 for every stop (16.39%, 32.93%, 65.41%, 157.89%,
354.14%). Its minimum equities agree with the milestone table in 3.3 (for example 15m: $1,090 at ≤ 1%, $1,817 at
0.5% with the gate's 1.2× tolerance, $9,083 at 0.1%). The tool checks the sizing rule only, not margin or caps.

**Margin for 0.01 lot** (notional 1 oz × $4,150 = $4,150):

| Leverage | Margin | Equity for the 300% level floor |
| --- | --- | --- |
| 1:500 (ICM SC headline, unverified) | $8.30 | $25 |
| 1:200 (reported for gold, unverified) | $20.75 | $62 |
| **1:20 (what RiskGate uses: max(broker, 1:20))** | **$207.50** | **$622 (£468)** |

The combined-notional cap (6× equity) needs equity ≥ $4,150 / 6 = **$692 (£520)** for one 0.01 lot.

**Result at £50:** RiskGate refuses every trade on every timeframe. The order of refusal is `min_lot_exceeds_risk`
(target risk at 0.5% is $0.33, at 0.1% is $0.07, against $10.90+ for the minimum lot), and the margin floor and the
notional cap would refuse it too. This is the gate working. Any minimum-lot loss at £50 (16%+) also exceeds the 2%
daily cap, the 5% weekly cap and the 12% kill switch in one trade.

## 2. What £50 a day requires
£50/day on £50 is **+100% per trading day**. Daily P&L = equity × risk per trade × expectancy (R/trade) × trades
per day, so equity needed = £50 / (risk × E[R] × trades/day).

Plausible trade rates for a filtered specialist: 15m 2/day, 1h 0.6/day (~3/week), 4h 0.2/day (~1/week), 1d
0.05/day (~1/month), 1w 0.01/day (~2–3/year). Plausible net expectancy: 0.1–0.3 R/trade. For scale, nothing
measured so far is above 0 R net.

**Equity needed for an average of £50 per trading day (GBP):**

| Timeframe | Trades/day | 0.5% risk, 0.1 R | 0.5%, 0.2 R | 0.5%, 0.3 R | 1% risk, 0.1 R | 1%, 0.2 R | 1%, 0.3 R |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | 2.0 | 50,000 | 25,000 | 16,667 | 25,000 | 12,500 | 8,333 |
| 1h | 0.6 | 166,667 | 83,333 | 55,556 | 83,333 | 41,667 | 27,778 |
| 4h | 0.2 | 500,000 | 250,000 | 166,667 | 250,000 | 125,000 | 83,333 |
| 1d | 0.05 | 2,000,000 | 1,000,000 | 666,667 | 1,000,000 | 500,000 | 333,333 |
| 1w | 0.01 | 10,000,000 | 5,000,000 | 3,333,333 | 5,000,000 | 2,500,000 | 1,666,667 |
| 15m + 1h | 2.6 | 38,462 | 19,231 | 12,821 | 19,231 | 9,615 | 6,410 |

Slow timeframes cannot carry a daily-income target at any sane account size: their trades are too rare.

**To make £50/day from £50** at 15m (2 trades/day) needs risk per trade of 167% (0.3 R), 250% (0.2 R) or 500%
(0.1 R) of equity: more than the whole account on every trade. 1:500 leverage allows 8 oz (0.08 lot) on $66.50,
whose 15m stop is $88, already 132% of equity.

### Risk of ruin from £50
**With the G-5 tool** (`goldbot/research/ruin.py`, same unmerged commit `08e654a`), parametric 2R win / 1R loss,
10 trades a week (15m), 52 weeks, 10,000 paths, seed 0. Command:
`python goldbot/research/ruin.py --win-rate <p> --avg-win 2 --avg-loss 1 --trades-per-week 10 --risk <r>
[--no-rules]`. The tool sizes as a fixed fraction of current equity, so the 16.4% rows stand for "a 0.01 lot at
£50".

| Risk per trade | Net E[R] (win rate) | Rules | P(12% halt) within 52 weeks | P(lose half) within 52 weeks | Median end equity × start |
| --- | --- | --- | --- | --- | --- |
| 16.4% (0.01 lot at £50) | 0.0 (0.333) | off | 100% | **99.9%** | 0.47 |
| 16.4% | 0.1 (0.367) | off | 100% | **96.9%** | 0.47 |
| 16.4% | 0.2 (0.400) | off | 100% | **78.5%** | 0.48 |
| 16.4% | 0.3 (0.433) | off | 100% | **52.1%** | 0.50 |
| 16.4% | 0.2 | on | 100% (median 0.1 weeks; 2 of 520 trades taken) | 0% | 0.84 |
| 1.0% | 0.1 | on | 87.1% | 0% | 1.10 |
| 1.0% | 0.2 | on | 57.2% | 0% | 1.77 |
| 0.75% | 0.1 / 0.2 | on | 57.8% / 21.2% | 0% | 1.19 / 1.87 |
| 0.5% (design) | 0.1 / 0.2 | on | 16.5% / 2.1% | 0% | 1.22 / 1.62 |

Caveat (from the review of `08e654a`; fixes in progress): after the 8% stage the tool sizes at 0.5× risk, while the
gate does 0.25× (risk halved and the multiplier capped at 0.5). So the "rules on" P(12% halt) figures are
**upper bounds**: the real gate cuts size harder after 8% and trips 12% less often. The 16.4% rows are unaffected
(the first loss already passes 12%). The tool's store mode, which understates the H-01 stop and ignores the
stops-level-plus-spread floor, was not used: every figure here comes from explicit `--stop-distance` values, and at
these stops ($10.90 and up) the floor (stops level plus a spread of at most 45 points = $0.45) does not bind.

With the rules on, the gate stops the £50 account after its first loss (16.4% drawdown in the median path). With the
rules off, even a +0.3 R edge loses half the account within a year in about half the paths.

**Fixed-lot cross-check** (scratch script, not repo code): 20,000 paths, seed 7. Each trade: 2R target, 1R stop, win probability p = (1 + E[R]) / 3. Size fixed at
0.01 lot (it cannot be cut below the minimum). Ruin = equity below one stop (cannot place another trade). RiskGate
is ignored here; with it, the first loss halts trading (12% kill switch).

| Setup | Net E[R] | P(ruin), 3 months | P(ruin), 12 months | P(≥12% drawdown) | Median end equity, 12 months |
| --- | --- | --- | --- | --- | --- |
| 15m, min lot ($10.90 = 16% of £50), 2/day | 0.0 | 71% | 85% | 100% | ~$0 |
| | 0.1 | 49% | 55% | 100% | ~$0 |
| | 0.2 | 31% | 31% | 100% | $1,019 |
| | 0.3 | 18% | 18% | 100% | $1,643 |
| 1h, min lot ($21.90 = 33%), 0.6/day | 0.0 | 72% | 86% | 100% | ~$0 |
| | 0.1 | 62% | 71% | 100% | ~$0 |
| | 0.2 | 50% | 55% | 100% | ~$0 |
| | 0.3 | 40% | 41% | 100% | $767 |

Even with an edge far above anything we have measured (0.3 R net), a min-lot account at £50 has about a 1-in-5
(15m) to 2-in-5 (1h) chance of being wiped out. Chasing the target (≥100% of equity per trade, at p = 0.4) survives
one trade with 40%, five trades with 1.0% and ten trades with 0.01%.

## 3. Decision

### 3.1 Nothing is tradeable at £50; the per-trade risk cap for minimum-lot trades is 1%
- **Cap: realised risk of a minimum-lot trade ≤ 1% of equity** (0.5% while in the 8% size-down stage). Reasons: 1%
  is the design's existing hard clamp (`gate.py:35`), so no rail is loosened; it is the top of the playbook's
  0.25–1% professional range (4.1); two full losses stay within the 2% daily cap; reaching the 12% kill switch
  takes about 12 straight losses (0.6^12 ≈ 0.2% at p = 0.4), against one loss at £50.
- **The cost of 1%, stated plainly:** held at 1% for a year at 10 trades a week, even a +0.2 R edge trips the 12%
  halt in up to 57% of paths (87% at +0.1 R), against up to 2% (17%) at 0.5% (ruin runs in section 2; upper bounds,
  since the tool under-cuts size after the 8% stage). So 1% is a ceiling for
  the first trades on a timeframe, not a level to live at. In practice it does not persist: the minimum lot's
  risk falls as equity grows (at twice the threshold it is 0.5%). The milestone a timeframe is considered
  comfortably tradeable at is the 0.5% column of 3.3. A halt near M1 costs about 12% (~£100) and needs the
  existing re-arm path.
- At £50 every timeframe is 16× to 350× over that cap. **No strategy or timeframe is tradeable at £50**, live or
  tiny-live. The demo runs at a demo balance (3.4).
- **What tiny-live means at small equity:** the 0.1% rate cannot be met by any gold trade below about £6,900
  (15m), £13,700 (1h), £27,300 (4h) or £66,000 (1d). At small equity, tiny-live therefore means **"the smallest
  tradeable size: one minimum lot, realised risk between 0.1% and 1%"**, which is still tiny in money (about £8 at
  £850 on 15m) while measuring real fills.

### 3.2 A minimum-lot exception rule for RiskGate (to implement, BACKLOG 25)
Today a trade is skipped when `lots_raw < volume_min` and the minimum lot's realised risk exceeds 1.2 × `risk_frac`
× m. At tiny-live (0.1%) that skips every trade below the equities above. The rule:

- New setting `risk.min_lot_risk_cap` (0.01; null = today's behaviour exactly).
- Applies **only when `lots_raw < volume_min`**. Then the gate places exactly `volume_min`, never more, if and only
  if `volume_min × stop × contract_oz / equity ≤ min_lot_risk_cap` (halved in `SIZE_DOWN` or combined size-down);
  otherwise it refuses with `min_lot_exceeds_risk` as now. When `lots_raw ≥ volume_min` nothing changes.
- Every other check runs unchanged and after it: margin (max(broker, 1:20), 300% floor), combined notional, daily
  and weekly caps, drawdown stages, positions, blackouts, spread. The exception widens no other limit, and it is not
  a path around RiskGate: it is a branch inside it.
- An allowed exception trade is recorded with reason `min_lot_exception`, its realised risk and the phase rate it
  replaced, so the performance report can separate min-lot trades from rate-sized ones.
- It counts in full toward the open-risk (heat) cap when G-4 lands (BACKLOG 19): near the threshold only one
  position can be open.
- Exits are untouched. Entries still need the owner's confirmation. It applies in demo and tiny-live alike, so
  paper trades on the demo balance behave as live trades would. Live still needs the recorded paper→tiny-live gate,
  `unlock_live` and the typed phrase.

This replaces "account size or tiny-live risk for slow horizons" (playbook section 7, item 1): **H-01 (daily-scale
stop) stays in shadow until equity reaches the 1d milestone.** Decided before H-01's result is known, as the
playbook asked.

### 3.3 Milestone path
Equity at which the minimum lot fits each timeframe, at gold $4,150 and the ATR estimates above (GBP, rounded).
The ladder is recomputed monthly from measured ATR by the sizing-feasibility report (BACKLOG 22).

| Milestone | Equity (1% min-lot cap) | Equity at design 0.5% (min lot within 1.2×, m = 1) | Equity at 0.1% tiny-live rate | Trades |
| --- | --- | --- | --- | --- |
| M0 | £50 | n/a | n/a | none; demo only |
| floor | £520 | | | margin floor (£468) and notional cap (£520): nothing below this, whatever the stop |
| M1 | **£825** | £1,370 | £6,860 | 15m (Raw account only; 15m is off on Standard) |
| M2 | **£1,650** | £2,745 | £13,720 | + 1h |
| M3 | **£3,270** | £5,450 | £27,260 | + 4h |
| M4 | **£7,900** | £13,160 | £65,790 | + 1d (also needs 1d promoted from research-only to a decision timeframe, separate work) |
| M5 | £17,700 | £29,500 | £147,560 | 1w: not in the design; listed for completeness only |
| £50/day | **~£19,000–£38,000** | at 0.5% risk, 0.1–0.2 R net, 15m + 1h (2.6 trades/day) | | ~£12,800 at 0.3 R |

Each milestone is a **permission, not a promise**: a timeframe is traded only if a strategy on it has passed the
research gates and the paper gate. Growth between milestones comes first from the owner's deposits; at 0.5% risk
and 0.2 R net, a 15m + 1h book grows about 5.5% a month, and 0.1 R about 2.7%. A deposit never changes the risk
rate.

### 3.4 Accounts and phases (unchanged rails)
- **Demo now.** The demo account's balance is set to the planned first live milestone (≥ £850 equivalent, or
  £1,650 if no 15m strategy passes), not to £50, so paper results measure what live will do. A £50 demo would only
  log refusals.
- **The £50 is not deployed** until the account reaches M1 and the paper→tiny-live gate is recorded. Live at £50
  would place no orders.
- Paper→tiny-live gate, `unlock_live` plus typed phrase, owner confirmation of each entry and automatic exits are
  all unchanged.

### 3.5 How progress is reported
- **Monthly**: % return on start-of-month equity with deposits and withdrawals removed (time-weighted), net R
  total, R per trade with trade count and 90% interval, maximum drawdown %, and the same by timeframe; plus equity
  against the next milestone.
- **Not reported as a target: £ per day.** A daily £ figure on a small account is noise (one 15m trade at M1 is
  ±£8) and invites chasing.
- The weekly owner digest shows R and drawdown, not £/day.

### 3.6 Deliberately not done
- No martingale, no averaging down, no grid, no adding to losers.
- No raising risk per trade, the multiplier, or the 1% clamp to chase the target; no "catch-up" sizing after losses.
- No using the broker's 1:500 (or 1:200): the 1:20 margin rule and 300% floor stay.
- No loosening the daily/weekly caps, the 8%/12% drawdown stages, the trial budget, the holdout or any gate.
- No trading 1d or 1w through the engine before they are decision timeframes with their own validated models.
- No live trading at £50.

## Consequences
- The owner's £50/day goal is recorded as a long-run outcome at roughly £20k–£40k of equity, not an operating
  target. That is consistent with the design's goal ("a small, durable edge compounded under tight risk").
- The minimum-lot exception changes RiskGate sizing at small equity: it needs trading-safety review, and `null`
  keeps today's behaviour, so it ships inert until reviewed.
- The milestone numbers depend on ATR and the gold price, both estimated here. The first measured sizing-feasibility
  report replaces them; if volatility rises, the milestones rise with it.
- A broker with a 0.001-lot minimum (0.1 oz) would cut every threshold tenfold. That is a new broker, an owner
  decision, and not recommended before a strategy passes the gates.
- Needs the owner: the deposit plan (spending money) and the demo balance to set.

## Not verified / left out
- IC Markets SC leverage on gold, stop-out level and contract terms: unverified (spec page 404; web sources
  conflict). They are read from the terminal at runtime; BACKLOG 27 records them.
- Gold price and GBPUSD are from late-September/early-October 2026 pages, not live quotes. ATR values are estimates,
  not measured from `data-v1`.
- Both Monte Carlos are two-outcome models (2R/1R) on assumed expectancies, without gaps or slippage beyond E[R];
  real outcomes are wider. The G-5/G-7 tools were run from an extract of the unmerged commit `08e654a`
  (`goldbot/research/ruin.py`, `min_lot.py`), not from this branch, and not reviewed here; the fixed-lot cross-check
  is a scratch script. Re-run both from `main` once that branch merges.
- No code, settings, trials, trial budget or holdout were touched.
