# Hypothesis portfolio

The strategy researcher keeps this file (`.claude/agents/strategy-researcher.md`, "Selection and evolution") and
updates it every quarter after the evaluator's verdicts. It is seeded 2026-10-10 from
`docs/research/state-of-the-art.md` ("SoA") and `docs/research/indicator-survey.md` ("survey", `#n` = universe row).

**Status values:** proposed / pre-registered / running / passed / failed / retired. *Retired* means do not re-test
without new evidence; a reason is always given.

**Cost yardstick:**
- Measured round-trip cost is about **0.14–0.39 R per trade**. Examples: tsmom 1h, gross +0.061 → net −0.079 (0.14 R);
  intraday_momentum, 0.16–0.23 R; shorter targets cost more.
- Long holds also pay swap, about −60 USD/lot/night for longs (prior). Shorts pay 0.
- A hypothesis is worth a trial only if its plausible gross edge is several times its cost in R. That means a larger
  target, a longer hold, or the short side.

**Budget:** `research.trial_budget_quarter` = 20. Q4 2026 is spent (20/20; registry trials #1–#20). Q1 2027 starts at
registry trial #21. Holdout 2025-10-01..2026-09-30 stays untouched.

## A. Ranked portfolio for Q1 2027

| Rank | ID | Hypothesis | Status | Rationale | Data | Horizon | Expected gross edge vs cost | Trial cost |
|---|---|---|---|---|---|---|---|---|
| 1 | H-01 | **Slow TSMOM**: 1d vol-scaled trend signal (1–12 months), 4h execution, 5–20-day hold, long and short, measured swap | proposed (SoA C1-1) | The only trend horizon with replicated support (MOP 2012, Hurst et al. 2017). The target dwarfs the spread | D | 1d signal, 4h bars | +0.05–0.10 R gross vs about 0.05 R spread at this target size, plus swap on longs (≈0.05–0.10 R over 10 nights). Likely depends on the short side | 1 |
| 2 | H-02 | **Feature discovery**: one pre-registered screen of ~120 bulk point-in-time features (survey §4a) on a 4h triple-barrier label, with fold-internal stability selection and permutation importance | proposed (survey §4b); **tooling ready** (`research_pass.py --discover`, M37/M38; not yet run) | Tests the whole indicator universe for the price of one trial instead of about 35 | D, D+, F, C, E | 4h | Unknown. Continue if out-of-sample AUC ≥ 0.53 (lower bound > 0.50) and the pre-set rule gives net mean R > 0 on ≥ 1,000 events | 1 (later survivor trials use N = registry + K_eff ≈ 35 in the DSR) |
| 3 | H-03 | **Gold VRP**: long when GVZ² − HAR-RV² is high (sign fixed in advance from Nguyen et al. 2017), 5–20-day hold | proposed; trial only if it survives H-02 | Jump/crash-risk premium; documented out of sample for gold at 1–24 months (survey #72) | F (GVZCLS, free) + D | 1d signal, 4h bars | Literature effect at monthly horizon; haircut 50% (McLean–Pontiff). Target ≫ costs; long side pays swap | 1 if it survives |
| 4 | H-04 | **Asia-session drift**: long about 00:00–07:00 UTC, flat before London | proposed (SoA C1-2) | Asian physical/ETF demand vs Western selling; intraday, so no swap | D | 1h | ≤ 0.03% per session vs about 0.01% round trip. Thin: 2–3× costs at best; about 4,000 events | 1 |
| 5 | H-05 | **Stop-cluster levels**: round numbers ($10/$50/$100) and previous day/week extremes. Continuation on a close beyond them, reversal at a touch (Osler) | proposed; trial only if it survives H-02 | Take-profits cluster at round numbers, stops just beyond them (Osler 2003, 2005, FX) | D | 15m–4h | Unknown in gold. Cascades move several ATR, enough to clear 0.2–0.4 R if real | 1 if it survives |
| 6 | H-06 | **Macro-conditioned slow TSMOM**: H-01 gated on 20-day ΔDFII10 and Δbroad USD | proposed (SoA C1-3) | Opportunity cost and USD pricing, but the relation broke in 2022. Reading rule requires 2022–24 positive | D, F | 1d / 4h | Small increment over H-01 | 1 (variant of H-01) |
| 7 | H-07 | **Equity-stress haven**: long gold for 5–15 days after a USA500 drawdown shock or VIX jump | proposed (screen in H-02 first) | Baur & Lucey 2010: haven lasts about 15 days | D+ (USA500), F (VIXCLS) | 1d | Few events (< 1,000 likely). Needs the owner's ruling on the event minimum for daily families | 1 if it survives |
| 8 | H-08 | **Liquidity sweep reversal**: a wick beyond the Asia/London/prior-day extreme with a close back inside, so fade it | proposed (screen in H-02) | Stop cascade exhausted (Osler 2005). An SMC idea with a microstructure reason | D | 15m–1h | Unknown; a short target makes costs 0.3–0.4 R, so it needs a large hit rate | 1 if it survives |
| 9 | H-09 | **COT speculative pressure** contrarian/continuation at 1d | proposed (screen in H-02) | Fan et al. speculative pressure (gross Sharpe 0.55 across futures); mixed for gold | C | 1d | Weekly signal, so few events. Better as a feature than a rule | 0 (feature) |
| 10 | H-10 | **Post-jump behaviour** (Lee–Mykland flag): continuation after news jumps, reversal after no-news jumps | proposed (screen in H-02) | Different mechanisms for the two kinds of jump | D, calendar | 15m–4h | Unknown | 1 if it survives |
| 11 | H-11 | **Post-release continuation** (CPI, NFP, FOMC) with a standardised surprise | proposed (SoA C1-4); blocked on data | Volatility burst several times the spread. No drift documented | Consensus feed (owner decision on source and cost) | 15m–4h | Exploratory | 1 |
| 12 | H-12 | **ETF flow pressure** (GLD Δ1/Δ5): continuation for 1–2 days, reversal at 3–5 days | proposed (feature only) | Price pressure (thesis-level evidence) | E | 1d | Small | 0 (feature) |
| 13 | H-13 | **Volatility-forecast cost filter**: skip any candidate whose HAR-forecast move is < 3× its round trip | proposed (infrastructure) | Costs decide most outcomes | D | all | Raises net R per trade by removing trades that cannot pay their costs. Not alpha | 0 (applied to every family; report net R with and without) |
| 14 | H-14 | **15m scalping set** (owner request): liquidity-sweep fades, stop-cluster/round-number reactions, session-open flow and news-jump continuation on 15m, each gated by expected move ≥ 3× round-trip cost | proposed; screen in a 15m discovery trial first | Microstructure mechanisms (Osler 2003/2005); costs dominate at 15m (0.2–0.4 R), so only cost-gated setups | D | 15m | Must show gross edge several times cost; Q4 15m families showed none | 1 (15m discovery) + survivors |

## B. Retired: do not re-test without new evidence

The governing list is `research.retired_families` in `config/settings.yaml` (family, row id, retired date, reason,
registry trials); this table mirrors it and `tests/test_director.py` fails when they disagree. Editing this table does
not change the research director's allocation: change the settings too. A retired family keeps 1 exploration trial a
quarter and is reinstated only by out-of-sample attribution after its retirement date (`research/director.py`).

| ID | Idea | Status | Trials (registry #, report) | Result | Reason retired |
|---|---|---|---|---|---|
| R-01 | mean_reversion (15m; RSI/Bollinger/VWAP fades) | retired | #3 (#40) | No gross edge; leakage shuffle AUC 0.72 flagged | No cost-surviving evidence for short-horizon reversion in gold (SoA A4) |
| R-02 | session_open breakout (15m) | retired | #1, #7, #11, #13 (#37) | Gross +0.042 R, t 1.31 on 1,498 events. Fails P4 | About 90 events a year; no mechanism without an exchange open |
| R-03 | trend pullback (1h) | retired | #2 (#39) | No gross edge | Small-tick contracts lost short-term trend after 2009 (Kurth et al. 2026) |
| R-04 | range breakout (1h) | retired | #6, #8, #15 (#41) | Gross +0.015 R, t 0.38 | Same reason as R-03 |
| R-05 | intraday_momentum (NY, London) | retired | #18, #19 (#52) | NY −0.006 R (t −0.50); London −0.028 R (t −1.48); net −0.16/−0.26 R | Mechanism (close auction, gamma hedging) absent in spot XAUUSD (SoA A4) |
| R-06 | tsmom at 1h/4h horizons (24–480 bars) | failed; superseded by H-01 | #16, #17 (#51) | Gross +0.061 R (t 2.63) but net −0.079 R; 4h net −0.014 R before swap; design gates fail | Gross edge too small for costs at this horizon. Only the slow version stays alive |
| R-07 | Meta-models over primaries with no gross edge (per-family and pooled_1h) | retired until a primary passes P4 alone | #20 (#53), plus per-family fits | OOF AUC about 0.50 | Meta-labelling filters an edge; it cannot create one (SoA A5) |
| R-08 | Pre-FOMC drift, London-fix front-running, weekday/weekend effects, real-yield sign rules, volatility targeting as alpha | retired from the literature (never trialled) | none | — | Gone after 2015 / venue reformed / fragile / broke in 2022 / no effect in commodities (SoA C3) |
| R-09 | SMC and classic chart tools as standalone rules: FVG, order blocks, BOS/CHoCH, premium/discount, Fibonacci, pivots, Ichimoku, candlesticks, volume profile, Heikin-Ashi | retired as rules; kept as bulk features for H-02 | none | — | No peer-reviewed support net of costs (survey §3). A dedicated trial would cost more than its expected information |

## C. Multiple-testing ledger

| Quarter | Registry trials | Screens inside trials (K_eff) | DSR trial count to use for the next trial |
|---|---|---|---|
| Q4 2026 | #1–#20 | 0 | 20 |
| Q1 2027 (planned) | #21–#33 at most (13 planned + 7 reserve) | H-02: K_eff = features screened when survivors go forward as features, groups screened only for whole-group survivors (recorded as `k_eff` on the discovery row; `registry.n_trials_effective` adds it) | 20 + trials run so far + K_eff for any survivor of H-02 |

## Next generation (after Q1 verdicts)

- Promote any passer to the population as a founder.
- Spawn neighbours from passers only: hold length ±50%, a 1d vs 4h execution grid point, short-only, cost-filter
  on/off.
- Retire failures here with their reason.
- Owner decisions pending:
  - D+ data pulls.
  - Consensus calendar source.
  - P4 event minimum for daily families.
  - Other instruments if H-01 is net ≤ 0.
