# Growth sprint, 2026-11-01: the doubling goal, measured honestly

Owner (research director, 2026-11-01). Owner goal 2026-11-01: "double the capital per week no matter what the capital
is ... find the strategy, strategies, invent ... start proving it on demo accounts from tonight's open". This supersedes
the research pause. Demo only. Live stays off, RiskGate is still the only order path, and no real-money cap moves.

## 1. The growth math (computed; script and seed below)

Model: one trade returns e R net of costs with sd 1 R. Risk a fraction f of equity per 1 R. Log growth per trade is
about f*e - f^2/2. Kelly f* = e, growth per trade e^2/2, weekly growth exp(N e^2/2) - 1.

**Net edge needed for +100%/week at full Kelly** (e* = sqrt(2 ln2 / N), risk per trade f* = e*):

| Trades/week N | 25 | 50 | 100 | 250 | 500 |
|---|---|---|---|---|---|
| Net edge e* (R/trade) | 0.235 | 0.167 | 0.118 | 0.074 | 0.053 |
| Risk per trade f* | 23.5% | 16.7% | 11.8% | 7.4% | 5.3% |

At half Kelly the same edge gives +68% a week for any N (exp(0.75 ln2)). **Weekly growth at full Kelly** for
e = 0.02 / 0.05 / 0.10 R: N=50 gives 1.0% / 6.4% / 28%; N=250 gives 5.1% / 37% / 249%.

**Where we are.** About 3,002 trials, 0 survivors, gross edge about 0 (scorecard-2026-10.md; the H-16, H-17, H-83,
H-84 and H-B screens in this folder are all FAIL). Measured round trip is ICM $0.16-0.27/oz and Vantage $0.16-0.45/oz.
I re-derived this from raw Dukascopy 1m data: the median 15m ATR(14) for 2025-07-01..2025-09-30 is $3.83, so at a
1 x ATR15 stop the cost is **0.042-0.071 R**. Net edge is therefore about -0.05 R, and **Kelly says risk 0**. The only
sizing that "grows" a negative edge is no trade.

**Zero gross edge at the risk that makes doubling possible.** Monte Carlo, 50,000 paths per cell, seed 20261101. A path
stops at 2x (doubled) or at 0.5x (halved, the practical ruin for this goal). Cost is 0.03 R, the optimistic end.

| Payoff | N | f=5% double / halve | f=10% | f=20% |
|---|---|---|---|---|
| +-1 R | 50 | 1.8% / 8.9% | 15.0% / 49.4% | 27.0% / 71.9% |
| +-1 R | 250 | 13.6% / 65.7% | 23.5% / 76.3% | 27.2% / 72.8% |
| 3 R target, 1 R stop | 50 | 12.4% / 36.0% | 25.2% / 69.4% | 31.0% / 69.0% |
| 3 R target, 1 R stop | 250 | 25.4% / 73.4% | 26.8% / 73.2% | 31.0% / 69.0% |

At f = 1% the chance of doubling is 0.0-0.4% in every cell. A theorem bounds all of this. With zero edge, equity is a
martingale under any sizing, pyramiding or payoff shape, so P(reach 2x before 0.5x) <= 1/3. Costs push it below 1/3
(simulated 27-31%). **Doubling a week with no edge is a 1-in-3-or-worse coin flip that halves the account two times in
three.** The median week ends at 0.40-0.49x. Convexity (pyramids, 3 R targets) changes the shape of the payoff, not
its mean. Only edge changes the mean.

**Under today's demo caps** (RiskGate 1% per trade, design hard limit; icm-demo daily £220 = 2%, weekly 6%, kill switch
12%; config/accounts.yaml): doubling needs +100 R net in a week, which is impossible at our trade counts. The demo can
still measure e and payoff shape, and R is scale-free. So the read applies a **virtual ledger** at f = 5/10/20% to the
realised R sequence. That answers "would it have doubled" without loosening RiskGate (section 3).

## 2. Candidates for convex growth, ranked by expected information

Each candidate gets one look on 2010-2017 (explore window), today, locally. Rules are frozen here before any data is
seen. **K += 7** (3,002 -> 3,009); at that K the program-wide deflated bar is t of about 4.0.

**Pass bar (each variant):** net mean R at 1.5 x measured ICM cost, with release-minute slippage at 3 x spread for C1,
must have a day-bootstrap 90% lower bound > 0. Its difference from a matched random-entry twin (same days, session, exit
and pyramid mechanics) must have a lower bound > 0, Holm-adjusted over the 7 variants. At least 5 of 8 years must be
positive. Losers are reported next to winners. A pass goes to one confirm on 2018-2025-09. The holdout stays sealed.

1. **C1 news straddle (NFP, CPI, FOMC; 2 variants: OCO at +-0.5 and +-1.0 x pre-release ATR15, set at T-1 min).** The
   filled side keeps the opposite side as its stop and trails 1 x ATR15. Mechanism: a scheduled information shock.
   FOMC and NFP days carry +0.40 / +0.37 log points more range than RV20 predicts (t 3.0 / 2.7; knowledge-base
   gold-specific.md:117, grade B). Prices adjust within minutes (Cai, Cheung, Wong 2001, peer-reviewed). A spot CFD
   charges no volatility premium, so the straddle pays only spread and slippage. Falsified if whipsaw (both sides hit)
   exceeds 45%, or by the bar. About 290 events. Highest information per trial, but rare live (one NFP this week, on
   11-06). Note that H-16 retired tier-1 events as a range-forecast input: that is not the same claim as persistence
   after the shock.
2. **C2 pyramided session-range breakout (2 variants: Asia range broken in London; London range broken in NY).** Entry
   on a 15m close beyond the range. Initial stop 1.5 x ATR15. Add one unit at each +1 R, up to 3 adds, and on each add
   move every stop so that open risk stays <= 1 unit. Mechanism: intraday time-series momentum plus stop cascades at
   session extremes (practitioner; Gao, Han, Li, Zhou 2018 JFE intraday momentum is peer-reviewed for equities). A
   pyramid is worth something only if returns are autocorrelated at the add horizon, so the random-entry twin with
   identical adds is the test. H-17's trend-day conditioning failed, but this tests the payoff structure, not the day
   filter. About 3,800 trades. It gives the most trades a week and drives the demo test.
3. **C3 intraday momentum (2 variants: the first 30 min of the NY session predicts the last 60 min; the first 60 min of
   London predicts London close).** One trade a day, about 2,000 per variant. Peer-reviewed for equities, practitioner
   for gold.
4. **C4 compression expansion with an asymmetric target (1 variant: NR7 day, then a break of the prior-day range with a
   4 R target and a 1 R stop).** Mechanism: volatility clustering. Graded lower because H-16 already retired
   compression as a range input. Cheap to run, so it gets a single variant.

Declined for this sprint: equity-curve or martingale sizing (the theorem in section 1 rules it out), and owner-style
manual box trades (gross about 0, scorecard). Folklore patterns from the knowledge base get no slot.

## 3. Demo test from tonight's 22:00 UTC open (owner override of ADR 0061, this experiment only)

- **Account: icm-demo** only. It has the lowest measured cost, and its strategy arms are already refused under ADR 0061,
  so taking it costs the least learning. icm-probe and vantage-demo keep their controls and shadow book unchanged.
- **Arm GS1 = C2 variant pair**, 15m, both sessions, about 10-15 entries a week. If pyramid adds cannot be live by
  21:30, GS1 launches with a single unit and a 3 R target and trail, and adds follow by 11-03 (logged as a protocol
  deviation). C1 is not on demo this week: one event is not a read.
- **Control GS1C:** same account, same size and mechanics (stop distance, adds, trail). Entry time is uniform within the
  same session window on the same days, and direction is a fair coin. It is drawn from a seeded RNG that is logged.
- **Sizing:** 1% of equity per unit, the RiskGate cap. With the add rule, open risk stays <= 1%. The daily 2% and weekly
  6% caps bind. No cap is loosened. **Virtual ledger** at f = 5/10/20%, computed from GS1's and GS1C's realised R.
- **Exemption:** `role: owner_experiment`, for GS1 only, with the expiry 2026-11-27 in code, journalled. GS1C is a
  control role.
- **Pre-registered weekly read (Sat 2026-11-07, by the performance-analyst, posted on #34):** (a) did real equity
  double (expected no, given the caps) and did the virtual ledgers double; (b) max drawdown, real and virtual; (c) GS1
  minus GS1C mean R with a day-bootstrap 90% CI and n; (d) payoff shape: the share of total R from the top 10% of
  trades. One week of about 12 trades cannot separate edge from luck, and the read will say so.
- **Kill rule:** by 2026-11-27, or once GS1 has 40 or more trades, kill GS1 if its excess over GS1C has a 90% lower bound
  <= 0, or if the C2 screen or confirm FAILs. Scale only if screen, confirm and the forward excess all pass. If the C2
  screen FAILs today, GS1 still runs this week on the owner's instruction, labelled "unqualified, screen FAIL".

**Owner-only (escalated, not decided).** The literal goal, doubling the demo book itself, needs 10-20% risk per trade,
above the 1% design hard limit. Options: (A) keep 1% plus the virtual ledger; (B) a demo-only cap raise on icm-demo.
**Recommendation: A.** The R sequence carries the same information. Section 1 shows B halves the account two weeks in
three at zero edge, and B would train habits that are banned for real money.

Reproduce: the growth Monte Carlo is a short numpy script with seed 20261101. The ATR figure comes from
`raw/xauusd_1m_dukascopy_2025.parquet` (bid OHLC, resampled to 15m, ATR(14), 2025-07-01..2025-09-30).

## 4. Lanes (all opus, launched at once, one PR each)

- implementer: GS1/GS1C arms, pyramid adds, `owner_experiment` exemption, deploy to icm-demo by 21:30 UTC.
- trading-safety-reviewer: review of that PR, read-only, before deploy.
- microstructure-researcher C1, strategy-researcher C2, opportunity-researcher C3, exits-researcher C4: one screen
  each, `docs/research/gs-c<n>-screen-2026-11.md`, results by 20:00 UTC.
- performance-analyst: `scripts/growth_sprint_read.py` (matched read plus virtual ledger) for the 11-07 read.
- quant-reviewer: read-only audit of sections 1-3 (math, prereg, K) before the screens report.
