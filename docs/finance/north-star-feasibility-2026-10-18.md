# North-star feasibility: can £50+ on an opportunity day be reached at £1,000, and what would it take?

CFO, 2026-10-18. Owner decision memo (recommendations only; no config, limit or sizing was changed).
Frame: ADR 0004, `config/settings.yaml` `risk:` (0.5% per trade, 1% hard clamp, 2% account / 1.5% supervisor daily
caps, 8% size-down, 12% kill), `config/accounts.yaml` (`daily_loss_limit_abs: 50`), `goldbot/risk/gate.py`
(projected daily check at `:574-577`, min-lot exception `:646-651`), research note
`docs/research/candidates/next-2026-10-18.md` (branch `worktree-agent-a066e3fbe20a92547`) and the quant reviewer's
lot-rounding correction. **Sample size behind every trading figure: 0 trades; no strategy has passed the gates
(best measured gross edge +0.061 R, R-06).** Every £ below is a scratch Monte Carlo on stated assumptions, net of
trading costs, before running costs (budget-2026-10). Not a forecast and not a promise of profit.

## Verdict first

| # | Finding |
|---|---|
| 1 | **Not reachable as a regular outcome at £1,000 on the current rails.** At 0.5% risk a trade risks £4.06 (0.02 lot, lot-rounded), so £50 is **12.3 R in a day** (16-17 R if the stop is 3.9 USD and size rounds to 0.01 lot). Four 2 R winners make at most ~£28. Expected £0.20-2.35 a day at 0.05-0.15 R and 1-4 trades. |
| 2 | **Raising risk does not buy it.** 1% risk is allowed by the rails (owner sizing decision) but the 2% daily cap then allows one full loss a day; the mean rises to only £0.31-3.19 while P(8% stage in 3 months) jumps to 40-89%. **2% risk cannot trade at all**: one 0.09-lot trade (£18.3 + £4.8 cost) exceeds the £20 daily limit, and 2% is above the 1% hard clamp. It would need two limits loosened: not recommended, ever. |
| 3 | **The lever is equity.** At **£5,000** a £50 day is a 1% day, about 2 R at 0.5% risk (£24/R); modelled at 0.10 R x 2 trades, ~13% of trading days reach £50, mean ~£3.3/day. £50 *on average* needs **£25k-£100k** (ADR 0004 Table A). |

## Inputs and the minimum lot (c)

| Input | Value | Source |
|---|---|---|
| Contract | 100 oz/lot, min 0.01, step 0.01: **0.01 lot = 1 oz = $1 per $1 move** (£0.75 at GBPUSD 1.33) | `gate.py:182,227` defaults; terminal values per BACKLOG 27 |
| Round-trip cost | 0.71 $/oz (spread ~0.34, commission 0.07, slippage 0.15 x 2) = **£0.53 per 0.01 lot** | `settings.yaml` `costs`, research note s.0 |
| Stop A (research) | 1.5 x ATR15 = **2.7 USD** (mean ATR15 $1.80); cost 0.26 R; £2.03 per 0.01 lot | research note s.0 |
| Stop B (ADR 0004) | 1.5 x ATR15 = **10.9 USD** (ATR estimate at $4,150 gold); cost 0.07 R; £8.20 per 0.01 lot | ADR 0004 s.2 |

**Discrepancy flagged:** the two 15m ATRs differ 4x ($1.80 is a full-history mean, $7.3 a current-price estimate).
Every figure below depends on it; BACKLOG 22 (measured ATR) must settle it before any owner ruling.

**Where 0.01 lot already exceeds the target risk** (stop in USD = target £ x 1.33):

| Rule | £ at £1,000 | 0.01 lot exceeds it above | Consequence |
|---|---|---|---|
| 0.5% design risk | £5.00 | **6.65 USD stop** | below 6.65 the lot rounds down (e.g. 3.9 USD -> 0.01 lot = 0.29%) |
| 1.2 x 0.5% tolerance | £6.00 | 7.98 USD | refused `min_lot_exceeds_risk` unless the exception is on |
| Min-lot exception cap 1% | £10.00 | 13.30 USD | refused; halves to 6.65 USD in the 8% stage |

## (a) Daily outcomes, 3 months (63 trading days, 4,000 paths, seed 7)

Two-outcome model, target 2 R / stop 1 R, win rate p = (1 + c + E)/3, Poisson trades/day, lot-rounded sizing on
current equity, the 1.5% supervisor and projected min(2%, £50) daily checks, 8% size-down (risk halved, clears at 5%),
12% kill. "Cap day" = the daily limit stopped entries (at £1,000 the £50 itself never binds; £15-20 does).

**Option 0/1: 0.5% risk (today), Stop A, 0.02 lot = £4.06/R, £50 = 12.3 R**

| Net E (R/trade) | Trades/day | Mean £/day | p10 / p90 day | P(£50 day) | Cap days | P(8%) 3 mo | P(12%) 3 mo |
|---|---|---|---|---|---|---|---|
| 0.05 | 1 / 2 / 4 | 0.20 / 0.38 / 0.71 | -5 / +7 to -15 / +16 | 0 / 0 / 0.02% | 0-7% | 8 / 22 / 43% | 0 / 1 / 7% |
| 0.10 | 1 / 2 / 4 | 0.40 / 0.78 / 1.51 | same | 0 / 0 / 0.04% | 0-6% | 5 / 14 / 28% | 0 / 0 / 2% |
| 0.15 | 1 / 2 / 4 | 0.60 / 1.19 / 2.35 | same | 0 / 0 / 0.09% | 0-6% | 3 / 8 / 17% | 0 / 0 / 1% |

Runner exits (5 R target) raise P(£50 day) to 0.6-2.4% of days but P(8%) to 51-80% and P(12%) to 8-27%.
Stop B: 0.01 lot needs the min-lot exception (0.82%); mean £0.31-2.66, P(£50 day) 0.03-2.5%, P(8%) 31-84%, and the 8%
stage then refuses every trade (de-facto stop, so P(12%) = 0).

**Option 3: 1% risk (owner sizing decision, inside the 1% clamp), Stop A, 0.04 lot = £8.12/R, £50 = 6.2 R**

| Net E | Trades/day | Mean £/day | P(£50 day) | Cap days | P(8%) 3 mo | P(12%) 3 mo |
|---|---|---|---|---|---|---|
| 0.05 | 1 / 2 / 4 | 0.31 / 0.53 / 0.86 | 0.3 / 1.0 / 2.9% | 12 / 26 / 43% | 53 / 75 / 89% | 8 / 21 / 39% |
| 0.10 | 1 / 2 / 4 | 0.65 / 1.13 / 1.93 | 0.3 / 1.4 / 4.0% | 12 / 26 / 43% | 47 / 68 / 83% | 5 / 14 / 26% |
| 0.15 | 1 / 2 / 4 | 1.03 / 1.83 / 3.19 | 0.4 / 1.8 / 5.4% | 12 / 27 / 44% | 40 / 60 / 75% | 3 / 9 / 18% |

**2% risk (needs the 1% clamp and the 2% daily cap loosened; shown only to close the question):** with the daily
caps removed but £50 kept, mean £0.47-3.72/day, P(£50 day) 2-8%, **P(12% kill in 3 months) 53-95%**.

## (b) Account size

| Balance | £50 as % | R at 0.5% (lot-rounded) | Effective daily limit | Modelled (0.10 R, 2/day, 0.5%) |
|---|---|---|---|---|
| £1,000 | 5% | 12.3 R | £15 (supervisor 1.5%) | £0.78/day, P(£50 day) ~0 |
| **£5,000** | **1%** | **2.0 R** (0.12 lot, £24.4/R) | £50 absolute: one full loss/day | £3.3/day, P(£50 day) ~13%, P(8%) 16%, P(12%) 0.2% |
| £25k-£100k | 0.05-0.2% | — | £50 absolute | £50/day *average* at 0.10-0.20 R, 2 trades (ADR 0004 Table A) |

## (d) Options for the owner (OWNER-ONLY; queued by the program-director)

| Option | £ cost / capital at risk | £ effect (modelled) | Downside | Break-even / reverse if | CFO view |
|---|---|---|---|---|---|
| **1. Stay £1,000 at 0.5%, demo; north star = rare runner day** | £0 extra | £0.2-2.4/day if an edge exists; £50 days ~never | No north-star days; months of near-zero £ | Running costs must stay ~£0 (budget); reverse when a strategy passes paper and BACKLOG 22 gives the ATR | **Recommended now** |
| **2. Grow equity to ~£5,000 by deposit, only after the paper -> tiny-live gate and 3 months net-positive paper** | +£4,000 deposit (owner's money at risk) | £50 becomes a 2 R day; ~£3/day mean, ~1 day in 8 at £50+ *if* 0.10 R holds | Same % drawdown in bigger £ (8% = £400, 12% = £600) | Only on recorded gates; cut back if live net R < half of paper or 8% stage hits | **Recommended as the growth path, conditional** |
| **3. Raise risk to 1% (sizing)** | £0, but 2x £ at risk per trade | Mean +£0.1-0.8/day; £50 days 0.3-5% | P(8%) 40-89%, P(12%) 3-39% in 3 months | Never on a streak; would need E >= 0.15 R measured | **Not recommended**. 2% is ruled out (loosens limits) |

No option loosens a limit. Owner ruling requested by **2026-11-30** (OD-14 sitting); Option 2's deposit is decided
only when the paper gate is recorded. What would change this view: a measured ATR15 near $7 (Stop B rows apply),
or a strategy with >= 0.15 R net out of sample over >= 1,000 trades.

Not verified: ATR15, GBPUSD 1.33, contract terms (BACKLOG 27); two-outcome model without gaps; opportunity-day
frequency unmeasured (BACKLOG 30). I am not a tax adviser; deposits have no tax effect modelled here.
