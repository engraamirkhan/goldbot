# 0004 Starting account £1,000, a £50 daily loss limit, and the £50-a-day north star

2026-10-10 · program director, with the product-owner and performance-analyst views · decision delegated by the
owner.

> **Amended 2026-10-10.** The first version of this ADR read the owner's "£50" as the account size. That was
> wrong. The owner corrected it: *"the broker details I gave you, it should be able to know the balance, which
> should be 1000 GBP; 50 GBP is the daily loss limit."* The IC Markets demo account is in **GBP** with a **£1,000**
> balance, and the engine reads it from the broker. **£50 is the owner's absolute daily loss limit.** The owner
> then confirmed the profit aim: *"that is the ambitious aim and whole purpose of this trading system, to make 50
> or more a day, but less if the conditions are not suitable; usually there are days in the week where the market
> can provide opportunities."* So £50+ a day is recorded as the **north-star goal** and varies by day (section 6).
> It is not a daily quota. Every number below is redone for £1,000. The superseded £50-account analysis is
> summarised in section 8.

## Owner summary
- **North star:** £50 or more on good days, less or nothing on poor ones. We chase it by being selective and
  letting winners run. Bigger bets are never the route. As a monthly *average* it becomes realistic at about
  £21k–£53k of equity.
- **Your account:** £1,000 GBP on the IC Markets demo, read live from the broker (balance and equity, never from
  config).
- **Daily loss limit:** never more than £50 lost in a day. The tighter design caps apply first: 2% of the account
  (£20 at £1,000) and 1.5% across all accounts (£15). In practice, two full losing trades stop new entries for the
  day; the worst day is about £17. The £50 limit only binds once the account passes £2,500. Exits are never blocked.
- **We found a sizing bug (HIGH):** the risk check treats US-dollar amounts as pounds. It errs on the safe side
  (trades are about 25% smaller than intended), but because of it the gate refuses the only trade that fits
  £1,000. Fixing it comes first.
- **What can trade at £1,000:** only 15-minute strategies, at the smallest gold size (0.01 lot, about £8 of risk,
  0.8%). That also needs the small-account rule (BACKLOG 25) and a Raw-spread account. 1h needs about £1,650, 4h
  about £3,300 and daily about £7,900.
- **Honest expectation:** no strategy has passed our tests yet. If one does, 1–4% a month (£10–£40 a month at
  £1,000) is a realistic range. A £50 day (5%, about 6 risk units at the smallest size) is a rare best day from a
  big trend or news move. It cannot be the average at this size without near-certain ruin.
- **No martingale, no grids, no catch-up bets.** Progress is reported monthly: average £/day, % return, best days,
  opportunity days caught, and the largest drawdown.

## Context
Rails from `docs/DESIGN.md` (Sizing, Hard limits) and `config/settings.yaml` `risk:`:
- Risk per trade 0.5% (`risk_per_trade: 0.005`), 0.1% tiny-live, multiplier 0.25–1.5, hard clamp 1%
  (`goldbot/risk/gate.py:35`, clamp `:246-247`). Stop = 1.5 × ATR(14) of the decision timeframe.
- A trade is skipped (`min_lot_exceeds_risk`) when the minimum lot's realised risk exceeds 1.2 × target risk
  (`gate.py:254-256`).
- Margin: max(broker `order_calc_margin`, notional / 20); margin level after the order ≥ 300% (`gate.py:158-182`,
  `:258-263`). Combined notional ≤ 30% × 20 × equity = 6× equity (`:264-270`).
- Caps: daily 2% and weekly 5% per account (`gate.py:215-220`). The supervisor applies 1.5% / 4% on combined equity
  (`goldbot/risk/supervisor.py:18-19`, `:54-65`). Drawdown 8% sizes down and 12% halts (`gate.py:139-150`).
  Values are at `config/settings.yaml:45-48`.
- Decision timeframes are 15m, 1h and 4h. 1d is research-only and 1w is context only. No strategy is net positive
  after costs; tsmom has +0.06 R gross and is negative net.

### Assumptions (stated, not measured)
| Input | Value | Source |
| --- | --- | --- |
| Account | **£1,000 GBP**, IC Markets SC demo | owner; the engine reads balance and equity from MT5 `account_info` at runtime |
| GBPUSD | 1.33, so £1,000 ≈ **$1,330** | currency-converter.org.uk 1.32–1.324 (3–4 Oct 2026); 30rates 1.35 (10 Sep 2026) |
| XAUUSD | **$4,150/oz** | RoboForex, 28 Sep 2026 (range 3,920–4,500) |
| Contract | 100 oz per lot, min lot 0.01, step 0.01 | design defaults; to be measured (BACKLOG 27) |
| ATR(14), 1d | **~$70** (range $50–110) | **estimate** from ~22% annualised vol; not measured from `data-v1` (BACKLOG 22) |
| ATR, other timeframes | √time from 1d over 23 h: 4h ÷ 2.4, 1h ÷ 4.8, 15m ÷ 9.6, 1w × √5 | **estimate**; slightly optimistic |

Every equity threshold scales linearly with ATR and the gold price, and inversely with GBPUSD.

## 1. Code audit: balance, currency and daily caps (read-only)

| # | Question | Finding | Severity |
| --- | --- | --- | --- |
| A1 | Is equity read from the broker? | **Yes.** `MT5Broker.account()` maps `mt5.account_info()` equity, balance, margin and currency (`goldbot/execution/mt5_adapter.py:203-207`). The engine copies equity, margin, balance high-water mark and the day and week start into `AccountState` on every tick (`goldbot/engine/runner.py:1064-1076`, also `:1293`, `:1316`). No balance or equity exists in `config/` (`config/accounts.yaml` holds no balance). Only `PaperBroker` has a configured equity ($10,000 USD, `goldbot/execution/paper.py:21`, `:53`), for paper mode. | OK |
| A2 | Do sizing, caps and stages use that equity? | **Yes.** Sizing uses `st.equity` (`gate.py:249`, `:254`), the daily and weekly caps use `day_start_equity` and `week_start_equity` (`:215-220`), the stages use `balance_closed_hwm` (`:139-150`), and the margin level uses `st.equity` (`:260`). | OK |
| A3 | Is account-currency risk converted from the USD stop? | **No.** `risk_usd = st.equity * risk_frac * mult` (`gate.py:249`) is equity in **GBP**, divided by `stop_distance * contract_oz` in **USD** (`:250`). The realised-risk check does the same (`:254-255`). Nothing uses GBPUSD, `trade_tick_value` or `order_calc_profit`: `SymbolInfo` carries no tick value (`mt5_adapter.py:200`) and the gate has no FX input. On the £1,000 GBP account every trade is sized at 1/1.33 ≈ **0.75× intended** (risk is understated by 25%), and the computed realised risk is **1.33× the true figure**. The error is conservative, but it refuses the only trade that fits £1,000: a 15m min lot is computed as 1.09% (true 0.82%), which is over the 1% exception cap of BACKLOG 25. | **HIGH** (wrong on every trade; blocks 15m at £1,000) |
| A4 | Margin and notional in the same currency? | **No.** The 1:20 figure `notional / 20` is in USD (`gate.py:165`, notional at `:257`). It is compared with the broker margin (GBP, `mt5_adapter.py:242-245`) and with GBP equity (`:260`). The combined-notional cap compares USD notional with 6 × GBP equity (`:268`). Both err strict by 1.33×. | part of A3 |
| A5 | Closed-trade R and return | `pnl` comes from deal `profit` in the deposit currency (GBP) (`runner.py:1544-1545`). `risk = |entry − sl| × lots × contract` is in USD (`:1591`), so `r = pnl / risk` is **understated by 1.33×** (`:1602`), and so is `ret = pnl / (entry × lots × contract)` (`:1599`). Attribution, drift and the phase gate read R. | part of A3 (MEDIUM on its own) |
| A6 | Contract terms passed to the gate? | **No.** `Intent(...)` at `runner.py:576-577` passes no `contract_oz`, `volume_min`, `volume_step`, `volume_max` or `stops_level_points`, so the gate uses its defaults: 100 oz, 0.01, 0.01, 2.0, 0 (`gate.py:106-110`). The first version of this ADR said these were "read from the terminal at runtime"; for the gate that was wrong. They match ICM's usual XAUUSD terms but are not verified. | MEDIUM |
| A7 | Daily loss caps: where, on what? | Account cap 2%: `gate.py:215-218`, `day_loss = 1 − equity / day_start_equity`. Equity includes floating P&L, so the cap is on equity, in the account currency (a ratio, so it is currency-neutral). The day resets at 00:00 UTC (`gate.py:279-288`, via `runner.py:1069`). Supervisor 1.5%: `supervisor.py:54-63` sums each engine's published `equity` and `day_start_equity` (`runner.py:1844`). Both are checked **before** an entry against the loss so far; neither adds the new trade's risk, so a day can end one full loss past the cap. No absolute (£) cap exists. | OK for one GBP account; see A8 |
| A8 | Supervisor with mixed currencies | The supervisor sums equities across engines without conversion (`supervisor.py:54-57`), as does the gate's `other_equity` (`runner.py:1107`). If `vantage-demo` runs in USD next to `icm-demo` in GBP, the combined ratios mix currencies. | MEDIUM (inert while one account runs) |

**Verdict:** balance and equity come from MT5, and the caps and stages use them correctly. The currency conversion
is **wrong (HIGH)**: the gate treats USD amounts as GBP. It mis-sizes every trade by about 1.33× on the safe side,
under-reports R by 1.33×, and blocks the 15m minimum-lot trade at £1,000. The fix is **BACKLOG 28, top priority**,
and it must land before or with BACKLOG 25.

## 2. Feasibility at £1,000 ($1,330)

| Timeframe | 1.5 × ATR stop | Min-lot (0.01) risk | True % of £1,000 | Gate computes today (A3) | Today at 0.5% (≤ 0.6%) | With BACKLOG 25 (≤ 1%) and 28 |
| --- | --- | --- | --- | --- | --- | --- |
| 15m | ~$10.90 | £8.20 | **0.82%** | 1.09% | refused | **allowed** (Raw account; 15m is off on Standard except session-open) |
| 1h | ~$21.90 | £16.47 | 1.65% | 2.19% | refused | refused |
| 4h | ~$43.50 | £32.71 | 3.27% | 4.35% | refused | refused |
| 1d | ~$105 | £78.95 | 7.89% | 10.5% | refused | refused (research-only) |
| 1w | ~$236 | £177 | 17.7% | 23.6% | refused | refused (context only) |

Note that BACKLOG 25 **without** 28 still refuses 15m at £1,000 (1.09% > 1%). Both are needed.

- **Margin, 0.01 lot** (notional $4,150 = £3,120): 1:20 gives £156, so the margin level is 641% (≥ 300%). Two
  positions would need £312 (320%).
- **Combined-notional cap:** 6 × £1,000 = £6,000 ($7,980) holds one 0.01 lot ($4,150) but not two ($8,300). **At
  £1,000 only one position can be open at a time**, whatever `max_positions` says.
- **Size-down stage:** in `SIZE_DOWN` the exception cap halves to 0.5%. At £920 (8% down) a 15m min lot is 0.89%,
  so it is refused. Without trades the drawdown cannot recover to the 5% clear level. **At £1,000 the 8% stage is
  in effect a stop**, until a deposit or the re-arm path. Shadow trading continues.

## 3. Daily loss limit: £50 absolute, design percentages first

**Decision.** £50 is the owner's outer daily limit in account currency. The design's tighter percentage caps stay.
The effective daily limit is:

- per account: **min(2% × day-start equity, £50)**. The £50 binds above £2,500.
- combined (supervisor): **min(1.5% × combined day-start equity, £50)**. The £50 binds above about £3,333.

New setting **`risk.daily_loss_limit_abs: 50`** (account currency; `null` = off; validated > 0). RiskGate blocks
**entries only**: exits, stops and closes are never gated. Unlike the percentage caps (checked on the loss so far),
the absolute check is **projected**: an entry is refused (`daily_loss_limit_abs`) when the day's loss so far plus
this trade's full stop risk in account currency would exceed £50. One trade cannot carry the day past £50, except
through gaps or slippage beyond the stop. The supervisor applies the same £50 to the combined loss, which needs
equities in one currency (BACKLOG 28, A8). This change touches `goldbot/risk`, so it needs trading-safety review;
it is BACKLOG 29.

| Equity | 2% account cap | 1.5% supervisor cap | Effective daily limit | Weekly 5% | 8% stage | 12% halt |
| --- | --- | --- | --- | --- | --- | --- |
| £1,000 | £20 | £15 | **£15** | £50 | £80 | £120 |
| £2,500 | £50 | £37.50 | £37.50 | £125 | £200 | £300 |
| £5,000 | £100 | £75 | **£50** (absolute) | £250 | £400 | £600 |

**Interplay at £1,000, 15m min lot.** A full loss is £8.20 plus costs (commission $0.07 and spread ~$0.20–0.30 per
0.01 lot, about £0.25), so about **£8.45 (0.85%)**.
- After loss 1: day loss 0.85%, below both caps, so entry 2 is allowed.
- After loss 2: £16.90 (1.69%), at or above the supervisor's 1.5%, so **entries stop**. The account cap (2%) alone
  would allow a third entry and a worst day of about £25.
- **Result: at most two full losses a day, a worst day of about £17**, one position at a time, well inside £50.
  Gaps through the stop are the exception.

## 4. Milestone ladder (restated for a £1,000 start)

Equity at which the minimum lot fits each timeframe, at $4,150 gold and the ATR estimates (GBP, rounded).
Recomputed monthly from measured ATR (BACKLOG 22/26).

| Milestone | 1% min-lot cap (BACKLOG 25) | Design 0.5% (within 1.2×) | Trades | Status at £1,000 |
| --- | --- | --- | --- | --- |
| floor | £520 | | margin floor and notional cap | passed |
| **M1** | **£825** | £1,370 | 15m (Raw account) | **reached, once BACKLOG 28 + 25 land** |
| M2 | £1,650 | £2,745 | + 1h | +65% away |
| M3 | £3,270 | £5,450 | + 4h | ×3.3 |
| M4 | £7,900 | £13,160 | + 1d (also needs 1d promoted to a decision timeframe) | ×7.9 |

Each milestone is a **permission, not a promise**: a timeframe trades only when a strategy on it has passed the
research gates and the paper gate. Above £1,370 the 15m min lot fits the design's 0.5% rule without the exception.

## 5. Realistic expectations at £1,000

Scratch Monte Carlo (not repo code; 20,000 paths, seed 7). 15m only, fixed 0.01 lot (£8.20 per R), 2R target / 1R
stop, win rate p = (1 + E[R]) / 3, up to 2 entries a day, the 1.5% daily stop, 21 days a month, 12 months, no
deposits.

| Net E[R] | Mean monthly return | Median equity, 12 months (p10 / p90) | P(12% halt), 3 / 12 months | P(8% stage = de-facto stop), 3 / 12 months |
| --- | --- | --- | --- | --- |
| 0.0 (no edge, today) | 0% | £959 (£885 / £1,172) | 57% / 96% | 86% / 99.8% |
| 0.1 | +1.6% (~£16) | £1,115 (£902 / £1,713) | 31% / 69% | 67% / 95% |
| 0.2 | +4.1% (~£41) | £1,763 (£959 / £2,156) | 14% / 30% | 46% / 74% |

- The base case today is **E[R] ≤ 0**: no strategy is net positive. So the demo's job is to measure, not to earn.
- With a real edge of 0.1–0.2 R, **1–4% a month** is the honest range at £1,000. The minimum-lot granularity makes
  drawdown stops likely within a year; deposits help most, because they lower the min-lot risk %.
- The 12% column ignores the 8% stage; the last column treats the 8% stage as the stop it effectively is at this
  size (section 2). Two-outcome model; no gaps or slippage beyond E[R].

## 6. North-star goal: £50 or more a day, on the days the market offers it

The owner's ambition is the purpose of the system: **£50 or more on a good day, less or nothing when conditions
are poor.** We record it as the north star. It is not a daily quota, and the system never sizes up to reach it.

**What a £50 day is at £1,000.** 5% of equity: about **6.1 R** at the 15m min lot (£8.20 per R), or 5 R at the 1%
cap. One 0.01 lot makes £50 on a ~$66 move in its favour, which is about one full daily ATR. Such a day comes from a
**runner**: a trend or news day (volatility expansion, a London–New York overlap breakout, a CPI/NFP/FOMC move)
where the entry is early and the exit trails. A selective system with an edge might see such days **a few times a
month at best**. That frequency is unmeasured; BACKLOG 30 measures it from `data-v1`.

**Why it cannot be the average at £1,000.** £50 a day on average is about **+100% a month**. Reaching it means
risking well beyond 1% per trade, or many trades a day. The £50-account ruin results (section 8) show where that
goes: even a +0.3 R edge loses half the account within a year in about half the paths. At the rails' 1% cap, a 0.2
R edge earns about £41 a month, not a day.

**When it becomes a realistic monthly average.** £50 a trading day ≈ **£1,050 a month**:

| Sustained monthly return | Equity for £50/day average |
| --- | --- |
| 2% | ~£52,500 |
| 3% | ~£35,000 |
| 5% (top-decile, rarely sustained) | ~£21,000 |

At 0.5% risk and 0.1–0.2 R on 15m + 1h (2.6 trades a day), the per-trade arithmetic gives ~£19k–£38k (table A).

**Illustrative compounding from £1,000.** These are **scenarios, not forecasts**. They assume a proven edge
(none exists yet), no drawdown halts, and deposits at month end.

| Monthly return | Deposit / month | 1 year | 2 years | 3 years | 5 years | Reaches £50/day average |
| --- | --- | --- | --- | --- | --- | --- |
| 2% | £0 | £1,268 | £1,608 | £2,040 | £3,281 | ~16.8 years |
| 2% | £100 | £2,609 | £4,651 | £7,239 | £14,686 | ~9.6 years |
| 2% | £250 | £4,621 | £9,214 | £15,038 | £31,794 | ~6.7 years |
| 3% | £0 | £1,426 | £2,033 | £2,898 | £5,892 | ~10.1 years |
| 3% | £100 | £2,845 | £5,475 | £9,226 | £22,197 | ~6.2 years |
| 3% | £250 | £4,974 | £10,639 | £18,717 | £46,655 | ~4.3 years |
| 5% | £0 | £1,796 | £3,225 | £5,792 | £18,679 | ~5.2 years |
| 5% | £100 | £3,388 | £7,675 | £15,375 | £54,038 | ~3.5 years |
| 5% | £250 | £5,775 | £14,351 | £29,751 | £107,075 | ~2.6 years |

**How the system pursues it, inside the rails.**
1. **Be selective:** trade more on opportunity days and stand aside on poor ones. Opportunity-day detection
   classifies the day by news, volatility expansion and session overlap (BACKLOG 30, a hypothesis through the trial
   registry).
2. **Let winners run:** scale-out and trailing exits (BACKLOG 6 is in review; runner-exit variants are BACKLOG 31,
   through the registry). Exits stay automatic and are never gated.
3. **Size by conviction, within the cap:** the existing size multiplier (0.25–1.5) scales risk with the model's
   edge, clamped at 1%. There is no other lever.
4. **Compound with equity:** risk is a fraction of broker equity, so £ per R grows as the account grows.
5. **Climb the ladder:** each milestone adds a timeframe (section 4), and so more opportunity days.

**KPIs (monthly report, BACKLOG 26):**
- monthly average £/day and % return (time-weighted, deposits excluded);
- distribution of daily P&L: count of days at £50+ and the best-day histogram in R and £;
- % of opportunity days captured: days the classifier flagged, and days with a daily range ≥ 1.5 × ATR(1d), on
  which the system made ≥ 1 R;
- maximum drawdown %, and days lost to the daily cap.

A daily £ figure is never shown as a target to hit, and a missed £50 day never triggers catch-up sizing.

## 7. Decisions
1. **Currency-correct sizing first (BACKLOG 28, HIGH, top priority).** Risk, margin and notional are converted to
   the account currency using MT5 `order_calc_profit` (loss at the stop for the lots), falling back to
   `trade_tick_value / trade_tick_size`. If neither is available on a non-USD account, entries **fail closed**
   (`fx_conversion_unavailable`). Closed-trade R uses the same currency. Trading-safety review.
2. **Minimum-lot exception (BACKLOG 25), raised to second.** Unchanged rule: exactly `volume_min` if its realised
   risk ≤ `risk.min_lot_risk_cap` (1%; half in size-down), every other check after it, `null` = today's behaviour.
   Nothing trades at £1,000 without it.
3. **Absolute daily loss limit (BACKLOG 29):** `risk.daily_loss_limit_abs: 50`, projected, entries only.
4. **Demo runs at the real £1,000 GBP balance.** The demo-balance question is answered.
5. **North star recorded (section 6),** pursued through selectivity, runners, conviction sizing within the cap,
   compounding and the ladder. Hypotheses go through the trial registry. **No trials are spent and the budget is
   untouched** by this ADR.
6. **Reporting:** monthly £/day average, %, R, best days, opportunity capture, drawdown (BACKLOG 26).
7. **H-01 (daily-scale stop) stays in shadow until M4 (~£7,900).**
8. **Deliberately not done:** no martingale, averaging down, grids or adding to losers. No raising risk, the
   multiplier or the 1% clamp, and no catch-up sizing. No use of 1:500 leverage. No loosening of the caps, stages,
   trial budget, holdout or gates. Live only after the recorded paper→tiny-live gate, `unlock_live` and the typed
   phrase. The owner confirms each entry and exits are automatic.

## Consequences
- Until BACKLOG 28 lands, demo trades on the GBP account are about 25% smaller than designed and their R is
  understated. Demo results from before the fix are flagged, not mixed with later ones.
- At £1,000 the system is a one-position, 15m-only, at most two losses a day book. Deposits move it up the ladder
  faster than returns.
- The £50 absolute limit is inert below £2,500. It becomes the owner's hard ceiling as the account grows.
- The numbers depend on estimated ATR, gold price and GBPUSD. Measured values (BACKLOG 22, 27) replace them.

**Needs the owner:** the deposit plan (optional; the table in section 6 shows its effect). Also confirm the demo
is a **Raw Spread** account: on Standard, 15m is disabled except session-open, and nothing else trades at £1,000.
The classifier reports the class once the terminal runs (BACKLOG 1).

## 8. Superseded: the £50-account analysis (first version)
At £50 (~$66.50) RiskGate refused every trade on every timeframe: a 0.01 lot risked 16.4% (15m) to 354% (1w), the
1:20 margin floor needed ~£470, and the notional cap ~£520. With rules off, a min-lot account at £50 lost half its
equity within a year in 52–99.9% of paths (E[R] 0.3 down to 0; G-5 tool on unmerged commit `08e654a`). Chasing
£50/day from £50 (≥ 100% of equity per trade) survived five trades in about 1% of paths. Those results still show
why the north star cannot be forced through position size.

**Table A. Equity needed for an average of £50 per trading day (GBP),** equity = £50 / (risk × E[R] ×
trades/day):

| Timeframe | Trades/day | 0.5% risk, 0.1 R | 0.5%, 0.2 R | 0.5%, 0.3 R | 1% risk, 0.1 R | 1%, 0.2 R | 1%, 0.3 R |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15m | 2.0 | 50,000 | 25,000 | 16,667 | 25,000 | 12,500 | 8,333 |
| 1h | 0.6 | 166,667 | 83,333 | 55,556 | 83,333 | 41,667 | 27,778 |
| 4h | 0.2 | 500,000 | 250,000 | 166,667 | 250,000 | 125,000 | 83,333 |
| 1d | 0.05 | 2,000,000 | 1,000,000 | 666,667 | 1,000,000 | 500,000 | 333,333 |
| 15m + 1h | 2.6 | 38,462 | 19,231 | 12,821 | 19,231 | 9,615 | 6,410 |

At £1,000, 15m at 2 trades a day and 1% risk would need **+2.5 R per trade** for £50 a day. No documented
systematic gold edge comes close.

## Not verified / left out
- The code audit is by reading only. No test was written to demonstrate A3–A8; BACKLOG 28's tests will.
- ICM SC leverage, contract terms and stop-out level are unverified (BACKLOG 27). Whether the demo is Raw or
  Standard is unknown.
- Gold price, GBPUSD and ATR are estimates. Both Monte Carlos are scratch scripts with two-outcome models.
- The frequency of £50 days is unmeasured (BACKLOG 30).
- No code, settings, trials, trial budget or holdout were touched.
