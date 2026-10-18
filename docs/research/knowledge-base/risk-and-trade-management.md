# Risk and trade management

Part of the [knowledge base](README.md). Format, grades and markers are defined there.

**Scope.** Fixed-fractional risk per trade, minimum reward:risk, partial profit-taking and break-even stops, never
widening a stop, entering only on a closed candle, not chasing missed entries, and scenario planning ("no level, no
trade"). goldbot's own rules are in RiskGate (`goldbot/risk/gate.py`), ADR 0004 (account size and limits) and the
trading contract (owner confirms entries; exits are automatic and never gated). None of these are research variables:
nothing here proposes loosening a limit.

## Summary

- **The course's risk rules are mostly the same as goldbot's** (at most 1% per trade, size from the stop distance,
  stop never widened). They protect capital; they do not create an edge.
- **Reward:risk ratios do not create expectancy.** On a driftless random walk, a 2:1 target is hit about one time in
  three, and the expected gross R is zero for any ratio. Expectancy comes from the entry's edge; costs come from the
  stop's size.
- **Stops add value only when returns have momentum**, and tight stops lose to trading costs (Kaminski & Lo 2014; Lo &
  Remorov 2017). Our own tight-stop study confirms this for gold at 15m.
- **Partial exits and break-even stops change the shape of the results, not their mean**, unless the price process
  has structure the exit exploits. They are exit variants to test on a passer, not on a signal with no edge.

## Claims

| # | Claim | Course baseline (paraphrased) | External evidence | Grade | Our data |
|---|---|---|---|---|---|
| K1 | Risk a fixed small fraction (at most 1%) per trade; size = risk ÷ stop distance | Six rules of risk: 1% maximum, size from the stop | Fixed-fractional sizing bounds the probability of ruin; growth-optimal (Kelly) sizing needs a known edge and is far above 1% only when the edge is large (Kelly 1956, *Bell System Technical Journal* *(unverified)*; Thorp 2006, *Handbook of Asset and Liability Management* *(unverified)*). Retail CFD losses are common: 74-89% of accounts lose (ESMA 2018, https://www.esma.europa.eu/node/84933; regulatory data) | **A** (mathematics) | **implemented:** RiskGate (0.5% default; 1% cap; £50 daily limit, ADR 0004). At £1,000, 0.01 lot with a 1.5 × ATR15 stop is about £9, so the minimum lot already uses most of the budget (`atr-measured-2026-10-18.md`) |
| K2 | Take only trades with at least 2:1 reward:risk to the first target | Skip anything under 2:1 to the next level | On a driftless random walk with barriers at +T and −S, P(target first) = S/(S+T), so expected gross R is 0 for any ratio (gambler's-ruin result; any probability text). Costs are charged per trade and scale with 1/S, not with T. A ratio filter can help only if the target is a level price is drawn to (support-resistance file, R11) | **A** (mathematics) | **study:** tight-stop study: gross R flat across stop widths; cost ≈ 0.71 $/oz ÷ stop $ |
| K3 | Take most of the position (about 80%) at the first target, move the stop to break-even, let the rest run | 80% at TP1, stop to break-even, 20% runner to TP2 | Under a martingale no exit rule changes expected value (optional stopping); a break-even stop raises the hit rate and cuts the average win. Runner exits can add value when trends persist (slow trend, `state-of-the-art.md` A1). No peer-reviewed test of the 80/20 split found | **A** that exits alone add nothing without an edge; **D** for the split | **queued:** runner-exit research H-17 V1 / BACKLOG 31; exit variants are tested only on passers ("Next generation" in `hypotheses.md`) |
| K4 | Never widen a stop; only move it to break-even or trail it | Stops only ever tighten | People hold losers too long and sell winners too soon (disposition effect: Odean 1998, *JF* 53(5), https://faculty.haas.berkeley.edu/odean/papers/disposition/disposition.html; US brokerage accounts). Stop-loss rules add value only when returns have momentum; under a random walk they lower expected return (Kaminski & Lo 2014, *J. Financial Markets* 18, 234-254, https://dspace.mit.edu/handle/1721.1/114876; daily index futures, gross) | **A** (behavioural) for the discipline; **B** for when stops help | **implemented:** server-side stop at entry; exits never gated; no stop widening in the engine |
| K5 | Tight stops hit by noise are the main cause of losses; a better entry with a smaller stop fixes it | Lower-timeframe entries give smaller stops | Tight stop-loss rules trail buy-and-hold mainly because of trading costs; outperformance needs high serial correlation (Lo & Remorov 2017, *J. Financial Markets* *(venue unverified)*, https://dspace.mit.edu/handle/1721.1/107017; US stocks 1964-2014, 0.2% cost) | **B** | **study:** tight-stop study: about 40% of tight-stop trades stopped within 15 minutes vs 7% at baseline; net R −0.45 vs −0.11; "the binding constraint is gross edge, not stop width" |
| K6 | Enter only on a closed candle (wicks do not count) | Every entry on the 1h close; enter on the next candle's open | No market evidence needed: it removes look-ahead and intrabar ambiguity. Our backtests and live engine already act only on closed bars | **A** (method) | **implemented:** bar-close detection by clock; `asof_join` on `available_utc`; lookahead tests |
| K7 | Do not chase a missed entry | A missed entry is gone | No study found. It mainly controls slippage and the cost of entering further from the stop | **D** | **implemented** in effect: signals expire; the owner's 90 s confirmation window |
| K8 | Plan scenarios in advance: only act at the pre-planned level; no level, no trade | Map A→C / B→D in advance and wait | Pre-commitment reduces discretionary bias; the research analogue is pre-registration (Arnott, Harvey, Markowitz 2019, *JFDS* *(unverified)*) | **C** | **implemented:** pre-session brief (06:30 UTC); pre-registered trials |
| K9 | Avoid trading close to FOMC, NFP and CPI | Mostly ignore news, but stay out around the big three releases | Intraday gold volatility is dominated by US releases, and prices adjust within minutes (Cai, Cheung, Wong 2001, *J. Futures Markets* 21(3), https://ideas.repec.org/a/wly/jfutmk/v21y2001i3p257-278.html; COMEX 1994-97). Spreads widen and stops slip in the release minute | **A** for volatility (not direction) | **implemented:** −15/+30 min tier-1 blackout. Tier-2 releases have neither blackout nor feature (playbook 1.4) |
| K10 | Losing streaks invite revenge trading; stop after a run of losses | (Implied by "discipline, no emotion") | Traders take more risk after morning losses (Coval & Shumway 2005, *JF* 60(1), https://ideas.repec.org/a/bla/jfinan/v60y2005i1p1-34.html; CBOT) | **B** | **gap:** no per-agent loss-streak throttle (playbook G-6) |

## What this means for goldbot

1. **No risk rule in the notes conflicts with RiskGate**, and none should be relaxed to "make a trade fit".
2. **Reward:risk and exits are not where the edge is.** Spend no trial on exit variants of a signal with no gross
   edge; test exits only on a passer.
3. **Smaller stops make the cost problem worse.** The break-even gross edge is about 0.71 $/oz ÷ stop $: 0.11 R at
   the £5-risk cap ($6.65 stop), 0.17 R at $4.25, 0.36 R at $2 (tight-stop study).

## Open checks

- Verify Kelly 1956 / Thorp 2006 references and the Lo & Remorov venue.
