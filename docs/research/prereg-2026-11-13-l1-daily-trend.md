# L1 daily trend with volatility targeting: NOT pre-registered (duplicate of a closed line)

Principal quant strategist, 2026-11-13. Brief: research-director plan 13 Nov, line L1 ("gold plus cross-asset
time-series trend on daily bars with volatility targeting; never run properly here, W1B was killed by the A2
fill-count rule"). Status: **declined at 0 trials, 0 reads, K +0.** No data was read for this card.

## 1. Why there is no run

The brief's premise is wrong. The line was run properly, on P&L, with the daily position engine
(`goldbot/research/position/engine.py`), five days after W1B's kill:

| Item | L1 brief | POS-1 A1 TREND-XASSET (`docs/research/pos1/`, read 2026-11-06) |
|---|---|---|
| Signal | 1-12 month lookback sign | 001 sign(r252) month-end; 002/025/026A mean(sign r21, sign r63, sign r252), band 1/3 |
| Universe | gold first, then the cross-asset set held locally | XAU; U_metals; U_A1 = 13 markets (metals, WTI, 6 indices, 4 FX) |
| Vol targeting | target, estimator, cap | 10 %/yr per market and per book, EWMA span 60 d, 60-day covariance for the book, max leverage 2, 3 x ATR(20) disaster stop |
| Costs | bps + swap | bps per market (amendment A1 table) + financing at DTB3/OECD differentials + markup (gold measured 0.74 %/yr) |
| Windows | LEARN 2010-17 (+2003-05) | EXPLORE = LEARN-OLD 2003-05 + LEARN 2010-17; CONFIRM and TEST-OLD unread |
| Control | matched random | 1,000-draw null per spec (null excess column) |
| Result | — | **0 of 16 reach t >= 2.0.** Book 026A gross +0.535 (gross t 1.70), financing -0.369, net SR 0.09 (net t 0.32). Gold 001 net SR 0.40 on 18 round trips; 015 net SR 0.48 on 10 |
| Verdict | — | Kill rule A1.4 met; "TREND-XASSET is closed 2026-11-06: not re-run, widened, re-parameterised (e.g. with other markups or vol targets) or renamed. Reopening needs new mechanism evidence and a new card." K 16 reads (19 for deflation) |

L1 as briefed differs from POS-1 A1 only in what that verdict forbids: a wider universe (`raw/daily/long/`, 30
markets 2000-2025: adds grains, softs, Brent, gas, UST10y, more FX), another vol target, or new lookbacks. Running
any of them on LEARN would be a second look at a window already read for this family, after seeing gross t 1.70.
Under honest K, that is a forking path, not a test.

Nothing a re-run could change makes LEARN pass:
- **Cheaper swaps cannot rescue it.** Gross t 1.70 is already below the t >= 2 bar. Zero financing still fails.
- **The `raw/daily/long` set is mixed-source continuous futures (Yahoo `=F`).** The roll returns are not what a CFD
  holder earns, so a wider book would also add a data-validity question.
- **Forward evidence is useless by the 27 Nov kill date.** About 10 daily observations against a prior SR of 0.5-1.0
  have power close to 0.

## 2. What it would take to reopen (new card, new evidence)

Any one of these, written up before any read:
- (a) A published, net-of-financing replication of TSMOM on retail CFDs or rolled futures after 2017, showing a
  materially higher book SR than our 2003-05 and 2010-17 gross 0.50.
- (b) Measured broker swap for every leg, plus a mechanism for why the window we have read understates the edge.
- (c) An owner decision to trade the futures, where funding is about T-bill and the 2.5 %/yr CFD markup is gone.
  Instrument choice is owner-only; flagged, not assumed.

The fresh windows would then be TEST-OLD 1d 2006-09 (unread for this family) plus a forward shadow of 026A. Both
need a card frozen first.

## 3. Sizing facts, recorded for any future card

Inputs: XAUUSD at ICM has contract 100, so 0.01 lot = 1 oz. Gold was $4,071 on 2026-11-06, giving about £3,100
notional per 0.01 lot at GBPUSD 1.31. Realised vol was 20.6 %/yr over 60 days and 28.6 %/yr over 252 days.

- **£1,000 live equity.** A 10 %/yr target needs about £485 notional, about 0.16 oz: six times below the minimum lot.
  The minimum lot alone runs 60-90 %/yr vol, a daily sigma of about £39-56. A one-sigma day reaches the £50 daily
  loss cap. **Not implementable live.**
- **£10,000 demo.** The target is about 1.6 oz, so 0.02 lot. The 0.01 step quantises exposure in 50-64 % jumps, so
  the vol target is nominal.
