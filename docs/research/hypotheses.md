# Hypothesis portfolio

The strategy researcher keeps this file (`.claude/agents/strategy-researcher.md`, "Selection and evolution") and
updates it every quarter after the evaluator's verdicts. It is seeded 2026-10-10 from
`docs/research/state-of-the-art.md` ("SoA") and `docs/research/indicator-survey.md` ("survey", `#n` = universe row).

**Status values:** proposed / pre-registered / running / passed / failed / retired. *Retired* means do not re-test
without new evidence; a reason is always given.

**Cost yardstick:**
- *Corrected 2026-10-18 (`costs-binding-2026-10-18.md`).* No broker cost has been measured yet. Every figure here is
  a **prior**: the Dukascopy bar spread + 0.37 $/oz + the swap prior. The charged round trip has been about
  **0.14–0.39 R per trade over 2010–2025**. Examples: tsmom 1h, gross +0.061 → net −0.079 (0.14 R);
  intraday_momentum, 0.16–0.23 R; shorter targets cost more. At today's ATR the same priors are 3–5 times smaller in
  R. For a 1h 1.5 × ATR stop it is about 0.04 R, against 0.17 R in 2010–2019.
- Long holds also pay swap, about −60 USD/lot/night for longs (prior). Shorts pay 0.
- A hypothesis is worth a trial only if its plausible gross edge is several times its cost in R. That means a larger
  target, a longer hold, or the short side.

**Budget:** `research.trial_budget_quarter` = 20. Q4 2026 is spent (20/20; registry trials #1–#20). Q1 2027 starts at
registry trial #21. Holdout 2025-10-01..2026-09-30 stays untouched.

## A. Ranked portfolio for Q1 2027

**Rank** (here and in A2) is the unified north-star rank across H-01..H-23, set by the research director on
2026-10-10 (`research-plan.md` §3–4; reasons in A3). Q1 2027's running order is fixed by `preregistration-2027Q1.md`
and does not follow this column.

| Rank | ID | Hypothesis | Status | Rationale | Data | Horizon | Expected gross edge vs cost | Trial cost |
|---|---|---|---|---|---|---|---|---|
| 7 | H-01 | **Slow TSMOM**: 1d vol-scaled trend signal (1–12 months), 4h execution, 5–20-day hold, long and short, measured swap | queued in the Q1 pre-registration (AT RISK until the 2026-11-13 recount); **retire-only**: its lower bound (152) is below the 1,000 pass floor, so it can read at best inconclusive and no H-01 follow-up can arise (2026-10-17). **Run 2026-10-18 as batch B10, trial #48: inconclusive, held** (294 events, gross +0.166 R at t 2.04, rule-only net +0.002 R). No re-run unless a pre-registered power gain is written first | The only trend horizon with replicated support (MOP 2012, Hurst et al. 2017). The target dwarfs the spread | D | 1d signal, 4h bars | +0.05–0.10 R gross vs about 0.05 R spread at this target size, plus swap on longs (≈0.05–0.10 R over 10 nights). Likely depends on the short side | 1 |
| 6 | H-02 | **Feature discovery**: one pre-registered screen of ~120 bulk point-in-time features (survey §4a) on a 4h triple-barrier label, with fold-internal stability selection and permutation importance | proposed (survey §4b); **tooling ready** (`research_pass.py --discover`, M37/M38; not yet run) | Tests the whole indicator universe for the price of one trial instead of about 35 | D, D+, F, C, E | 4h | Unknown. Continue if out-of-sample AUC ≥ 0.53 (lower bound > 0.50) and the pre-set rule gives net mean R > 0 on ≥ 1,000 events | 1 (later survivor trials use N = registry + K_eff ≈ 35 in the DSR) |
| 14 | H-03 | **Gold VRP**: long when GVZ² − HAR-RV² is high (sign fixed in advance from Nguyen et al. 2017), 5–20-day hold | proposed; trial only if it survives H-02 | Jump/crash-risk premium; documented out of sample for gold at 1–24 months (survey #72) | F (GVZCLS, free) + D | 1d signal, 4h bars | Literature effect at monthly horizon; haircut 50% (McLean–Pontiff). Target ≫ costs; long side pays swap | 1 if it survives |
| 9 | H-04 | **Asia-session drift**: long about 00:00–07:00 UTC, flat before London | pre-registered (SoA C1-2); implemented as `asia_drift`, active (not in `research.retired_families`), not a founder until it passes. **Run 2026-10-18 as batch B08, trial #44: stop** (screen pass, gross +0.043 R at t 3.09 on 4,025 events; rule-only net -0.093 R, so costs decide it). O-7/O-8/O-9 therefore lapse by their own condition. Before that verdict the row read: if it reads **continue**, the Q1 open budget funds O-7 (H-24), O-8 (model research) and O-9 (H-C) (2026-10-17) | Asian physical/ETF demand vs Western selling; intraday, so no swap | D | 1h | ≤ 0.03% per session vs about 0.01% round trip. Thin: 2–3× costs at best; about 4,000 events | 1 |
| 12 | H-05 | **Stop-cluster levels**: round numbers ($10/$50/$100) and previous day/week extremes. Continuation on a close beyond them, reversal at a touch (Osler) | proposed; trial only if it survives H-02 | Take-profits cluster at round numbers, stops just beyond them (Osler 2003, 2005, FX) | D | 15m–4h | Unknown in gold. Cascades move several ATR, enough to clear 0.2–0.4 R if real | 1 if it survives |
| 10 | H-06 | **Macro-conditioned slow TSMOM**: H-01 gated on 20-day ΔDFII10 and Δbroad USD | proposed (SoA C1-3) | Opportunity cost and USD pricing, but the relation broke in 2022. Reading rule requires 2022–24 positive | D, F | 1d / 4h | Small increment over H-01 | 1 (variant of H-01) |
| 18 | H-07 | **Equity-stress haven**: long gold for 5–15 days after a USA500 drawdown shock or VIX jump | proposed (screen in H-02 first) | Baur & Lucey 2010: haven lasts about 15 days | D+ (USA500), F (VIXCLS) | 1d | Few events (< 1,000 likely). Needs the owner's ruling on the event minimum for daily families | 1 if it survives |
| 11 | H-08 | **Liquidity sweep reversal**: a wick beyond the Asia/London/prior-day extreme with a close back inside, so fade it | proposed (screen in H-02) | Stop cascade exhausted (Osler 2005). An SMC idea with a microstructure reason | D | 15m–1h | Unknown; a short target makes costs 0.3–0.4 R, so it needs a large hit rate | 1 if it survives |
| 20 | H-09 | **COT speculative pressure** contrarian/continuation at 1d | proposed (screen in H-02); **data available** (release `positioning-v1`, opt-in `--positioning` features `pos_cot_*`), pending the data-positioning workflow's first run | Fan et al. speculative pressure (gross Sharpe 0.55 across futures); mixed for gold | C | 1d | Weekly signal, so few events. Better as a feature than a rule | 0 (feature) |
| 13 | H-10 | **Post-jump behaviour** (Lee–Mykland flag): continuation after news jumps, reversal after no-news jumps | proposed (screen in H-02) | Different mechanisms for the two kinds of jump | D, calendar | 15m–4h | Unknown | 1 if it survives |
| 19 | H-11 | **Post-release continuation** (CPI, NFP, FOMC) with a standardised surprise | proposed (SoA C1-4); blocked on data | Volatility burst several times the spread. No drift documented | Consensus feed (owner decision on source and cost) | 15m–4h | Exploratory | 1 |
| 21 | H-12 | **ETF flow pressure** (GLD Δ1/Δ5): continuation for 1–2 days, reversal at 3–5 days | proposed (feature only); **data available** (`pos_gld_tonnes_chg5_pct`/`chg20_pct`, GLD archive via data-positioning), pending the workflow's first run; GLD source may be blocked to scripts (reported, not scraped) | Price pressure (thesis-level evidence) | E | 1d | Small | 0 (feature) |
| 3 | H-13 | **Volatility-forecast cost filter**: skip any candidate whose HAR-forecast move is < 3× its round trip | proposed (infrastructure); built in roadmap W1-5 from Mon 2026-10-19 (strategy-researcher, due 2026-10-30) | Costs decide most outcomes | D | all | Raises net R per trade by removing trades that cannot pay their costs. Not alpha | 0 (applied to every family; report net R with and without) |
| 5 | H-14 | **15m scalping set** (owner request): liquidity-sweep fades, stop-cluster/round-number reactions, session-open flow and news-jump continuation on 15m, each gated by expected move ≥ 3× round-trip cost | **pre-registered for the Q1 2027 open budget** (row O-4 + survivors O-5/O-6, `preregistration-2027Q1.md` "Q1 open-budget round", 2026-10-17); runs in Q1 only if E-2, BACKLOG 31 and `calendar-v1` land by 2027-02-12, else stays in the Q2 draft | Microstructure mechanisms (Osler 2003/2005); costs dominate at 15m (0.2–0.4 R), so only cost-gated setups | D | 15m | Must show gross edge several times cost; Q4 15m families showed none | 1 (15m discovery) + survivors |
| 17 | H-15 | **Liquidation rebound** (playbook C-1): after a day when gold falls *with* the S&P 500 and the VIX jumps (dash for cash), long gold 5–15 days. Distinct from H-07, which assumes haven buying | proposed; feature-first (playbook D-8 into H-02) | Forced cross-asset deleveraging sells gold, and it usually recovers (2008, Mar 2020). Mar 2026 is in the holdout and is not usable as evidence | D, F (VIXCLS, SP500) | 1d | Unknown; few events (< 1,000, owner ruling on the daily floor) | 0 until it survives H-02 |
| 24 | H-24 | **West-hours drift (short)**: short at the New York open (08:30 New York; entry pinned 2026-10-17 at the 09:00 New York 1h bar close, estimand 09:00–16:00, amendment under Q1 O-7), flat at 16:00 New York, stop 1.5 ATR(1h), every trading day. The Western leg of the mechanism H-04 tests in Asia | proposed (2026-10-11, session-coverage review A4); **conditional on H-04 continue**; pre-registered as Q1 open-budget row O-7 (2026-10-17), else funded from Q2's Q1-follow-up reserve; no trial before H-04's verdict | Same thesis as H-04: Asian physical/ETF demand vs Western selling (SoA B2). The 2026 press figures fall in the holdout and are not evidence | D | 1h | As thin as H-04 (≤ 0.03% per session vs a round trip of about 0.025–0.03%). Short side, intraday: no swap. About 4,000 events. 1h stop, so it needs M2 (£1,650) | 1 if H-04 continues |

Folded into existing rows by `xauusd-trader-playbook.md` (no new trials):
- $100 round-number step: H-05.
- Dovish-FOMC continuation (Awartani et al. 2024): H-11.
- Seasonal demand windows: H-02 features.
- LBMA auction and 08:30 ET window scalps: H-14.
- Exit variants: an H-01 survivor only.
- BTC as a gold signal: not to be added (unstable relation, no mechanism).

## A2. Opportunity-day and runner-exit candidates (Q2 2027)

Added 2026-10-10 from `docs/research/opportunity-days.md` ("OD"), answering the owner's north-star (£50+ on
opportunity days, less or nothing on poor days; account £1,000). The thesis: **range is predictable, direction is
not.** A range forecast pays only by (a) skipping quiet days, where fixed USD costs are a larger share of R, and (b)
letting a correctly-signed trade run on a trend day. It cannot create an entry edge.
- **Feature-first.** The score's inputs are an opt-in bulk feature family (BACKLOG 30a), kept out of the frozen Q1
  H-02 universe so its configuration and K_eff are unchanged.
- **No Q1 2027 trial is reserved.** *Amended 2026-10-17:* H-16 and H-17 are pre-registered against Q1's open budget
  (rows O-1 to O-3 of `preregistration-2027Q1.md`), conditional on their inputs landing in time; otherwise they stay
  in Q2.
- Research window: about 3,940 trading days (2010-01 .. 2025-09). The holdout is untouched.

| Rank | ID | Hypothesis | Status | Rationale | Data | Horizon | Expected gross edge vs cost | Trial cost |
|---|---|---|---|---|---|---|---|---|
| 1 | H-16 | **Opportunity score = day-range forecast.** In-fold HAR on stub-free daily ranges, plus GVZ ÷ YZ20, tier-1 count today, prior-day range ÷ ATR20(1d), NR4/NR7/inside-day, weekend gap ÷ ATR(1d). Refreshed at London+60 and NY+60 with the early-session noise ratio. No direction terms | **Screen FAILED 2026-10-30** (A3, 2010-2017, frozen at `1d25461d`): rho gain +0.20 passed, quintile ratio 1.44 < 1.5 failed. GVZ, events and compression retired as range inputs; day factor HAR alone; O-1 trial cancelled; K +1 (`opportunity-days-screen-2026-11.md`). Was: pre-registered for Q1 2027 open budget (row O-1) | Volatility persistence (Corsi 2009, R); GVZ adds to HAR (Luo et al. 2016; Wei et al. 2020, FRL, Chinese futures); release days are high-volatility days (Cai et al. 2001; Elder et al. 2012) (OD 1.1, 1.2, 1.8) | D, F (GVZCLS, `asof_join`; D−2 at the London open), calendar | daily, refreshed intraday | Not a return trial. **Reading rule:** continue if out-of-sample Spearman ρ(score, realised range ÷ ATR20) ≥ the HAR-only baseline + 0.05, AND the top-quintile mean range ≥ 1.5× the bottom quintile. Fail: the day factor uses HAR alone, and GVZ, events and compression are retired as range inputs | 1 (counted for honesty, though it tests range, not returns) |
| 2 | H-19 | **Stand-aside overlay:** every Q2 trial reports net R by score band (low / mid / high) and the effect of skipping the low band | proposed (**Q2 2027**, overlay) | Cost in R falls as ATR rises (SoA A7). Lundström 2013 WP: ORB returns ≈ 150–200 bp/day apart across volatility states (S) | D | all | Not alpha. It raises net R per trade by skipping trades whose range cannot pay costs | 0 (as H-13) |
| 4 | H-17 | **Trend-day continuation with a runner.** On days with `opp_score` in the top half (H-16's frozen model), at each 15m close between London+60 min and NY+120 min (DST-aware local times): if \|close − day open\| exceeds the noise boundary (14-day mean \|move from the open\| at the same time of day × 1.0), enter that way, at most one trade a day. Stop 1.5 ATR(15m). Exits: **V0** target 3 ATR vs **V1** 50% at 2 ATR, chandelier 2.5 ATR armed at 1.5 ATR, cap 10 ATR, flat 16:30 New York | **Screen FAILED 2026-10-30** (A3, 2010-2017, frozen at `60c4c802`): 671 high-half events, V0 gross -0.003 R (t -0.05), net -0.35 R, 0 of 6 net-positive years, weekly Welch high-low -0.11 R (t -1.24); V1 not read (Q-1). O-2/O-3 cancelled; retired as **R-17**; K +2 (`h17-screen-2026-11.md`). Was: bet 2 of 2026-11-02; pre-registered for the **Q1 2027 open budget** (rows O-2/O-3, 2026-10-17) if E-3, BACKLOG 31, `calendar-v1` and >= 1,000 strict events exist by 2027-02-12, else **Q2 2027**; overlap with R-02/R-05 ruled (Q-1) | Zarattini, Aziz, Barbon 2024: noise-area trend days with trailing and clock exits, SPY net Sharpe 1.33 (S). Kaminski & Lo 2014: trailing exits pay only with serial correlation (S). New vs R-05: ex-ante range conditioning and a runner exit | D | 15m | Unknown. Unconditional NY momentum was −0.006 R gross (R-05). Cost 0.15–0.25 R historically on 15m, lower on high-ATR days. Events ≈ 1,000–1,500 (*estimate*; count from events only before freezing). **Reading rule:** continue only if all hold: gross t ≥ 2 on ≥ 1,000 events; V1 net mean R > 0; ≥ 3 positive net years incl. 2021 or 2022; V1 net ≥ V0 net; the low-score half (shadow) has lower gross R than the high half. If the conditioning makes no difference, the families stay retired. £/day metrics reported, not in the rule | 2 (V0, V1) |
| 8 | H-18 | **Post-blackout event continuation.** At +30 min after NFP, CPI, FOMC or PCE (the end of the gate's blackout), enter in the direction of the release-to-now move if \|move\| > 1.0 ATR(15m). Runner V1, flat 16:30 New York | proposed (**Q2 2027 only if** an event floor is ruled or tier-2 data exists) | Event days carry the volatility (R). FOMC adjustment lasts beyond 5 min (Awartani et al. 2024, S). Post-announcement effects in gold futures (Sobti, S). Distinct from H-11: needs no consensus surprise | D, calendar | 15m–1h | Unknown. About 44 events/yr, so **~690 events, below the 1,000 floor**: inconclusive by design unless the owner rules a floor or BACKLOG 18/D-4 adds tier-2 events. Reading rule as H-17 (gross t ≥ 2, net > 0, ≥ 3 positive years) | 1 |
| 15 | H-20 | **Compression → expansion** (NR4/NR7, inside day, ATR percentile) adds range information beyond HAR | proposed (feature inside H-16) | Practitioner only (Crabel 1990); no peer-reviewed test found (P/F) | D | daily | Measured as part of H-16's incremental ρ; no separate rule | 0 |
| 16 | H-21 | **Early-session noise ratio** (range since the session open ÷ its 14-day average at the same time) predicts the rest-of-day range and flags trend days | proposed (feature in the H-16 refreshes and H-17 entries) | Early-interval RV predicts late RV in gold futures (FRL 2023, S); SPY detector (S) | D | 15m–1h | No separate rule | 0 |
| 23 | H-22 | **COT extremes predict volatility** | proposed (feature only; lowest) | Vendor claims only (F); weekly | C | daily | — | 0 |
| 22 | H-23 | **Overnight USD / real-yield shock predicts the day's range** | proposed; blocked on intraday USD (D+, owner) | Contemporaneous relation R; predictive W | D+ | daily | — | 0 until data |

**Q2 2027 pre-registration candidates:** H-16 (1), H-17 (2), H-19 (0), and H-18 (1) only if its floor is ruled. That
is 3–4 trials. When they run, each adds to the DSR trial count in section C. The ledger below is unchanged today
(nothing has run).

The Q2 draft (`preregistration-2027Q2-draft.md`, not frozen) plans H-16 (1), H-17 V0/V1 (2) and the H-14 15m
discovery (1), with H-14 survivors (≤ 2) and H-18 (1) conditional, 6 slots for Q1 follow-ups and 7 in reserve. It
conditions H-17 on H-16's HAR-only baseline rather than H-16's full model, so H-17's configuration does not depend on
H-16's verdict (the full-score version is a Q3 neighbour).

## A3. North-star ranking: changes and reasons (research director, 2026-10-10)

Ranking criteria, in order: can the trades be taken at the current milestone (M1: 15m only at £1,000, ADR 0004 §4);
evidence grade; plausible gross edge against cost; information value for other items; trial cost including K_eff.

| ID | Old rank | New rank | Reason |
|---|---|---|---|
| H-16 | A2 #1 | 1 | Range is the one forecastable property of gold's day (R); it enables stand-aside and runner exits for every 15m idea and serves two KPIs (opportunity days, drawdown) without an entry edge |
| H-19 | A2 #2 | 2 | Zero-trial overlay; fixed USD costs make quiet days the most expensive in R |
| H-13 | 13 | 3 | Zero-trial cost filter on every trial; costs decide most 15m outcomes. Was ranked low only because it is infrastructure |
| H-17 | A2 #3 | 4 | Only Q2 idea that can trade at £1,000 and targets the £50-day mechanism (trend day caught early, held). Low probability (15m record negative; R-02/R-05 overlap) |
| H-14 | 14 | 5 | 15m is the only tradeable timeframe at £1,000; one discovery trial screens every 15m mechanism. Survivor unit = group keeps its K_eff small |
| H-02 | 2 | 6 | Highest information value (gates H-03/05/07/08/10) but 4h trades need M3 (£3,270) |
| H-01 | 1 | 7 | Best literature support, unchanged as Q1's lead trial, but shadow-only until M4 (~£7,900; ADR 0004 decision 7), so no KPI contribution at the current equity |
| H-18 | A2 #4 | 8 | Event days are opportunity days and it is 15m; held back by the event floor (~690 events before its filter) and the missing historical calendar (E-1) |
| H-04 | 4 | 9 | 1h needs M2 (£1,650); a live entry at ~01:00 London needs the owner's click; thin edge vs costs |
| H-06 | 6 | 10 | Conditional on H-01; same M4 constraint |
| H-08 | 8 | 11 | Has a microstructure reason (Osler) and a 15m form; screened inside H-14 at 15m |
| H-05 | 5 | 12 | As H-08; moved below it because round-number effects are FX evidence only |
| H-10 | 10 | 13 | 15m form screened inside H-14; no gold evidence either way |
| H-03 | 3 | 14 | Monthly-horizon premium; daily execution needs M4 |
| H-20 | A2 #5 | 15 | Feature inside H-16; practitioner evidence only |
| H-21 | A2 #6 | 16 | Feature inside H-16/H-17 mechanics |
| H-15 | 15 | 17 | Daily, few events; needs D-8 and ruling A |
| H-07 | 7 | 18 | Daily, few events; ruling A |
| H-11 | 11 | 19 | Blocked on a consensus feed; no documented drift |
| H-09 | 9 | 20 | Feature only, weekly |
| H-12 | 12 | 21 | Feature only |
| H-23 | A2 #8 | 22 | Blocked on intraday USD data |
| H-22 | A2 #7 | 23 | Folklore grade; feature only |

## A4. Session coverage (research director, 2026-10-11)

The owner asked whether all three daily sessions are covered. Session clocks (`goldbot/data/calendar.py`, DST per
date): **Tokyo** 08:00–15:00 Tokyo = 23:00–06:00 UTC all year. **London** 08:00–16:30 London = 08:00–16:30 UTC in
winter, 07:00–15:30 UTC in summer. **New York** 08:30–16:00 New York = 13:30–21:00 UTC in winter, 12:30–20:00 UTC in
summer. "Trades" means the hypothesis enters or holds a position in that session. "Uses" means it reads that session
only as an input.

**Live and shadow: nothing.** No agent has passed the gates, so no champion or challenger proposes trades, and the
shadow book is empty (scorecard section 1). Every row below is queued or proposed.

| ID | State | TF | Entry window / holding | Tokyo | London | New York | Tradeable at £1,000 (M1)? |
|---|---|---|---|---|---|---|---|
| H-02 (+ survivor slots H-03/05/07/08/10) | queued Q1 | 4h | any 4h bar; 12-bar (48 h) label | trades | trades | trades | no (M3) |
| H-01, H-06 | queued Q1 | 1d signal, 4h execution | first 4h bar after the daily settlement; held up to ~28 days | trades (held) | trades (held) | trades (held) | no (M4) |
| tsmom 4h continuity (C4H) | queued Q1 | 4h | any 4h bar; 48 h | trades | trades | trades | no (M3) |
| H-04 Asia drift | queued Q1 | 1h | long 00:00–07:00 UTC, flat before London | **trades** (09:00 Tokyo to the close, plus the hour before London) | — | — | no (M2, and the owner's click at ~01:00 UK) |
| H-13 overlay | Q1 and Q2 | all | every trial | as the trial | as the trial | as the trial | n/a (0 trials) |
| H-16 opportunity score | Q2 draft | daily | forecast at 06:30 UTC of the 08:00 London .. 16:30 New York range | uses (overnight range inputs) | forecast target | forecast target | selector, no trades |
| H-19 overlay | Q2 draft | all | every Q2 trial | as the trial | as the trial | as the trial | n/a |
| H-17 trend day | Q2 draft | 15m | entries 09:00 London .. 10:30 New York; flat 16:30 New York | — | **trades** | **trades** | yes (M1) |
| H-14 15m discovery (+ ≤ 2 survivors) | Q2 draft | 15m | candidates 08:00 London .. 16:00 New York | uses (Asia extremes in the sweep group) | **trades** | **trades** | yes (M1) |
| H-18 event continuation | Q2 draft, conditional | 15m | release + 30 min (08:30 ET releases, FOMC 14:00 ET); flat 16:30 New York | — | — | **trades** | yes (M1) |
| H-24 West-hours drift | proposed, conditional | 1h | short 09:00–16:00 New York (entry pinned 2026-10-17 at the 09:00 New York bar close, after the tier-1 blackout) | — | — | **trades** | no (M2) |
| H-11 | blocked (consensus feed) | 15m–4h | after US releases | — | — | trades | yes (M1) |
| H-03, H-07, H-15 | proposed (daily) | 1d | multi-day | held | held | held | no (M4) |
| H-05, H-08, H-10 | at 15m inside H-14; at 4h inside H-02 | 15m / 4h | as H-14 / H-02 | 4h only | trades | trades | 15m form yes |
| H-09, H-12, H-20, H-21, H-22, H-23 | features only | — | — | — | — | — | n/a |

**Coverage.** London and New York are covered at 15m, the only timeframe £1,000 can trade, by three Q2 ideas
(H-17, H-14, H-18) and one selector (H-16). Tokyo is covered by one queued idea, H-04, at 1h (not tradeable until
M2), and by the multi-day holds, none tradeable at £1,000.

**Gaps and decisions** (against the budget: Q1 is fixed; Q2 has 20 with 7 free and 6 Q1-follow-up slots):

| Gap | Pre-register within the budget? | Reason |
|---|---|---|
| **G-1. Tokyo at 15m: nothing** | **No, recorded only.** Revisit if H-04 continues (a 15m execution of Asia drift becomes a Q3 neighbour), if the owner's export shows his edge in Tokyo hours (`owner-method-study.md`), or if auto mode is enabled on the demo (BACKLOG 15) | (1) Entries need the owner's click within 90 s, and 23:00–06:00 UTC is midnight to 07:00 in the UK. Live would trade a small, self-selected subset of research events. (2) Spreads are widest and the range smallest in Asian hours (playbook 2.2), so fixed USD costs are the largest share of R. That is the opposite of the opportunity-day thesis. (3) The one Asian-hours mechanism with any evidence (physical and ETF demand) is H-04 already. Asian extremes still feed London trades through H-14's sweep group |
| **G-2. The Western leg of the Asia/West mechanism** | **Yes, conditionally: H-24**, added to Q2's Q1-follow-up reserve, live only if H-04 reads continue (1 trial). Not run unconditionally | One mechanism predicts both legs. A null Asian leg removes most of the prior for the Western leg, so spending a trial before H-04's verdict buys little. H-24 is distinct from R-05 (intraday momentum: a signal from the morning move) and R-08 (London-fix front-running): it is a clock rule with a demand-flow reason |
| G-3. 06:00–07:00/08:00 UTC (between Tokyo and London), and 16:00–17:00 New York | No | Thin hours with no mechanism in the literature reviewed. The daily reopen window is a risk rule (BACKLOG 17), not an edge |
| G-4. H-16 forecasts the London–New York range only | No | Nothing trades in Tokyo at M1, so a Tokyo range forecast has no user. Revisit with G-1 |

## A5. Holiday-list hypotheses (BACKLOG 45; research director, 2026-10-17)

From `holiday-list-measurement-2026-10.md` (quant ruling 2026-10-16). These rules test risk rules, not alpha. Their full
rules are in the note, and the Q1 rows are in `preregistration-2027Q1.md`, "Q1 open-budget round".

| ID | Hypothesis | Status | Data | Trial cost | Power / expected result |
|---|---|---|---|---|---|
| H-A | The Dukascopy holiday-thinness proxy holds on the broker: pooled spr/ATR on THIN days >= 1.25× controls | pre-registered (forward, 2026-10-18 .. 2027-04-30); **at risk**: no tick-recorder job yet, and BACKLOG 1 is late | broker ticks (demo) | 0 (diagnostic) | 5 THIN dates; needs >= 4 clean, so losing Thanksgiving plus one more is INCONCLUSIVE |
| H-B | Candidates entered on THIN days earn >= 0.05 R less gross than matched controls | pre-registered; read per family after its verdict | frozen Q1 candidate sets + the 2010-2025 instance list | 0 (diagnostic) | H-01 ~6 THIN candidates (INCONCLUSIVE by construction); H-04 ~110 |
| H-C | Asia-session entries on US early-close holidays are not worse than on controls (margin −0.10 R) | pre-registered as Q1 row O-9; **only if H-04 continues** (tightened 2026-10-17 from "not retired"); **0-trial diagnostic, only outcome "the whole-day block stays"** (quant finding 7, 2026-10-17); a trial again only at >= 50% power | H-04 candidates + instance list | 0 | ~97 entries, se ~0.12 R, **~13% power**: INCONCLUSIVE expected |

## A6. Style lanes: candidate lists of 2026-10-17 (research director)

The owner's directive of 2026-10-17 starts the continuous program (`program-continuous.md`). The three style lanes
filed their first candidate lists the same day. None ran a trial or read an outcome. What each asks for, as adopted:

| Lane | File | Q1 2027 trials asked | Asks adopted | Q2 2027 and later |
|---|---|---|---|---|
| Scalping (15m) | `candidates/scalping-2026-10-17.md` | **O-4 only** (H-14, already in the open round) + O-5/O-6 survivors; no new row | **Withdrawal rule** written into O-4's row: if D-1 gives a median round trip above 0.35 R at every admissible gate floor (1×, 1.5×, 2×, each ≥ 55,000 candidate bars), O-4 is withdrawn before it runs (0 trials, 0 to K). F-1 group map amended (`ctx_<tf>` groups, families `liquidity` and `activity`, `context_groups: per_timeframe` pinned; 14 groups on `f-079deacc61`, about 16 with the new families). C-1..C-10 are groups inside O-4 or next-generation exits, not separate trials | C-8 cost-aware exits only from an O-4 survivor |
| Intraday (1h/4h) | `candidates/intraday-2026-10-17.md` | **0** | H-24 (O-7) entry pinned at the **09:00 New York** 1h bar close, estimand 09:00–16:00 New York, withdrawn before the run if D3's median cost exceeds 0.10 R (amendment under O-7). O-8 inputs pinned (IC-2). `macro_spx_dd20` masked in folds without full SP500 coverage (amendment under H-02). London→New York transition is covered by R-05 | **IC-5** Asia→London transition continuation: **1 conditional Q2 trial**, Q2 draft follow-up item 7, from the Q2 free reserve only by a written note; withdrawn if neither H-04 nor H-24 continues, if D2's count is < 1,000, or if it agrees with `tsmom_z_24` on > 70% of events. IC-4 (vol-regime tsmom 4h) forward shadow only. IC-6 ≤ 2 Q2 follow-ups of a surviving H-02 cluster |
| Next three (2026-10-18) | `candidates/next-2026-10-18.md` | **0** | **N1** `tod_periodicity` adopted as an O-4 group (K_eff +1, opt-in family, dropped if not coded by 2026-12-11). **N2** edge-decay exit queued for Q2. **N3** H-17 V2 breadth gate parked on the owner's D+ ruling | N2: 1 conditional Q2 trial (after O-5/O-6 pass, Q2 free reserve, written note). N3: 1 conditional Q2 trial if the owner approves the pull and H-17 V0 passes P4 |
| Swing (1d/1w) | `candidates/swing-2026-10-17.md` | **0** (H-01 is the lane's Q1 trial) | No swing trade can be placed at £1,000, and as the rails stand none at any equity while ATR(1d) > ~$43 at a 1.5 × ATR stop: **ADR 0004 §4 amended 2026-10-17** (M4 conditional on volatility; 1d research and shadow only; no limit loosened). Gate 1 is unreachable for holds ≥ 8 days on 2010-2025, so swing yields **context features and retirements, not a book** | **SW-0** 1d swing feature discovery (groups SW-1..SW-6, SW-8, SW-9; `survivor_unit: group`, K_eff ≈ 16): **1 trial from the Q2 free reserve, conditional**, else Q3. **Forward-shadow rule:** a surviving swing group's forward shadow only decides whether it stays as a context input or is retired. It is never a promotion route (25-40 trades a year) |
| Small-account follow-ups (2026-10-18) | `prereg-h25-precision-stop-2026-10-18.md`, `feature-groups-s1-s2-2026-10-18.md` | **0 in Q1** (H-25 asks 1 in 2026Q4: override 55 -> 56, research director to rule; fallback 1 unallocated Q1 trial) | **H-25 pre-registered 2026-10-18** (not dispatched): 15m breakout parent, limit at the broken level, stop 0.6 x ATR15 (the cost bound c* = 0.15 R at today's 0.09 ATR15 round trip), target 2R, barrier exits; in-sample charged 0.15 R + swap because the live S-1 gate passes 0.21% of 2010-2025Q3 bars; K_eff +19 (tight-stop looks); prior P(continue) ~5%. **S-1/S-2:** already in `f-079deacc61` (`em_atr_cost`, `atr14`, `spread_atr` in `volatility`/`microstructure`; `hour_utc`, `session_id` in `calendar`): K_eff +0, no new group; `atr_regime` considered and not recommended; cost-driven-AUC risk raised for O-4 (mid-label AUC diagnostic proposed) | A STOP on H-25 withdraws O-11 and H-26 before they are written (0 trials). A CONTINUE allows forward shadow under the live gate (kill read at 100 fills, pass read at 300 = 1 trial) |

**Status of these IDs.** IC-1..IC-9, SW-0..SW-9 and C-1..C-10 are recorded here as **proposed**. Proposed rows are not
tests and add nothing to K. K grows only when a registry row runs (or a discovery records its `k_eff`).

## A7. Owner's chart indicators, replicated (strategy researcher, 2026-10-18)

Owner request, 2026-10-18: backtest the three chart indicators the owner trades from. They are rebuilt clean-room from
their pulled outputs as the opt-in feature family `smc_replica` (`goldbot/features/smc_replica.py`). Every observable
plot matches exactly on 15m/1h/4h/1D, except the box indicator's break shapes (99.86%). The match table and the
pre-registration are in `prereg-tv-replica-2026-10-18.md`. These rows are tested **as an exception to R-09**, which
was retired from the literature with no trial run. The reason for the exception is the owner's directive, not new
evidence of an edge, so the prior stays low. Both TVR-1 and TVR-2 failing closes the SMC topic.

| ID | Hypothesis | Status | Trial cost |
|---|---|---|---|
| H-TVR1 | 15m: internal CHoCH in the swing-trend direction with a same-side FVG in the last 3 bars; stop 1.0 / target 2.0 ATR15, 32 bars | **pre-registered** 2026-10-18; funded 2026-10-18 (research director, B4a: 2026Q4 override 53 → 55 under the owner's standing directive); **withdrawn at 0 trials** (B4/B5, `prereg-tv-replica-2026-10-18.md` s.0): 441 events, below the P4 floor; the earlier "target dispatch 2026-10-22" is void (corrected 2026-10-19, `rollout-plan-2026-10-19.md` action 1) | 0 (was 1) |
| H-TVR2 | 15m: swing BOS continuation (50-bar leg pivots); stop 1.0 / target 2.0 ATR15, 32 bars | **withdrawn at 0 trials** (B4/B5): 1,196 events, below the floor; no reworded rule from the counts (B4b); corrected 2026-10-19 | 0 (was 1) |
| H-TVR3 | H-TVR1 on 1h (stop 1.0 ATR1h, 24 bars; needs M2 sizing) | proposed, **conditional** on H-TVR1 PASS or LEAD | 1 if triggered |
| D-TVR | Add the `smc_replica` columns to H-02 as one group (amendment before H-02 runs) | **O-4 only**: the group is in O-4's 15m map (7ee992e; quant sign-off due 2026-10-25). The research director ruled on 2026-10-18 that H-02 runs without it, on `f-51ac5ebabb`: the replica is not on `main` and no run input enables it (`preregistration-2026Q4-pullforward.md` 2.4a) | 0 (K_eff +1, O-4) |
| D-TVR | Add the `smc_replica` columns as one group to O-4 (H-14, 15m) and H-02 (4h), before TVR dispatch; no `structure` columns duplicated | **adopted** by the research director 2026-10-18 (B4a); amendment text + quant sign-off due 2026-10-25 | 0 (K_eff +1 per run, +2) |

**Tested universe (features, no trial).** These are opt-in `smc_replica` columns and reach a model only through
D-TVR or a pre-registered row: `bos/choch_{bull,bear}_{internal,swing}`, `trend_swing`, `trend_internal`, `eqh`,
`eql`, `fvg_bull`, `fvg_bear`, `mfi`, `mfi_os_cross`, `mfi_ob_cross`, `range_f1_dist_atr`, `range_f2_dist_atr`,
`sr_break_up`, `sr_break_dn`, `candle_class`, `ma_stack_sma_ema`.

**Not trialled, by decision:** money-flow cross rules (R-01 reversion, or no mechanism) and box-break rules (R-04
breakout). The order blocks cannot be replicated: they are off in the owner's settings (data request D-3).

## A8. Cost-aware proposals (strategy-researcher, 2026-10-18)

Sources: `costs-binding-2026-10-18.md` (evidence) and `preregistration-costs-2026-10-18.md` (rules). The key finding
is about gate 3 at K = 53, which needs a per-trade t of about 3.94. No known signal clears it **even at zero
cost**:

| Signal | Net R needed for t 3.94 |
|---|---|
| H-04 | +0.055 |
| tsmom 1h #36 | +0.136 |
| tsmom 1h #16 | +0.091 |

So cost work fixes the net sign, but a pass needs a larger gross t: more events or bigger edges.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-28 (CB-T1) | tsmom 1h cost-amortised: `min_score` 1.0, stop 2.0, target 4.0, `max_bars` 96 (`7f60182a4559`). The cheapest 1h market-order config today (0.030 R, scenario A) | pre-registered; not dispatched; needs 1 slot (2027Q1 free reserve, or the owner raises Q4 to 54) | 1 | batch §2 at the K of the verdict (≥ 54). FAIL closes the tsmom 1h exit variants (R-06 reconfirmed) |
| H-29 (CB-T2) | Limit entry at the signal price on H-28's signal | pre-registered, **conditional** on H-28 LEAD/PASS + CB-D2 "build" + tooling | 1 if its conditions hold | adopt only if net per signal ≥ H-28's |
| H-30 (CB-D2) | Limit-fill economics on the broker's recorded ticks (forward shadow candidates) | pre-registered diagnostic; starts when the tick record has 20 days | 0 | ≥ 0.25 $/oz saving per candidate → build H-29 tooling; < 0.10 → retire limit entry |
| CB-0 / CB-D1 | `costs-v1` build plus GBP fixes K1-K3; forward-cost quantities reported on new trials | proposed (engineering) / pre-registered (reporting) | 0 | measurement only |

Known low value, recorded as **not proposed** so they are not re-proposed:
- an H-04 re-test with new stops or holds (cannot pass at any cost);
- tight-spread-hours trials (worth 0.03–0.05 R at 15m, under 0.005 R at 1h);
- cost-filtered entry as a research-window trial (about 70 events at today's cost level);
- further 15m wide-target variants of R-01, R-02 and R-05.

## A9. Next generation after the batch (strategy-researcher, 2026-10-18)

Source: `next-gen-2026-10-18.md`. Event counts were computed by counting only (no outcome). The gates were chosen
after the published side tables were seen, so each row is charged in full and carries controls that can only
downgrade the verdict. P(PASS) is 1-2% by construction: t ≈ 3.97 needs a net annual Sharpe of about 1.0 on gold
alone. All rows are shadow-only at £1,000.

| ID | Hypothesis | Status | Events (count only) | Trials | Reading |
|---|---|---|---:|---|---|
| H-32 | 1h tsmom (#16 defaults) + H-06 macro gate (`macro_gate: true`, `2af4a90fa439`) | **pre-registered**, not dispatched, unfunded | 1,686 (188 in 2024-25) | 1 | batch §2 at K ≥ 55; plus G1 (net ≥ #16's −0.079 R + 0.03 R), G2 (2022-24 net > 0) and the filter lift (kept vs rejected gross, Welch t ≥ 1). FAIL retires the macro gate on 1h-4h tsmom |
| H-31 | 1h tsmom (#16 defaults) with side = sign(d1 close − SMA200) (`trend_align: d1_sma200`, `7a677f1dab2e`) | **pre-registered**, not dispatched; needs the key implemented (lookahead test) and funding | 2,527 (345) | 1 | batch §2 at K ≥ 55; plus the filter lift and a drift control on the long side (at most LEAD if long ≤ the drift baseline). FAIL retires daily-regime alignment as a 1h-4h tsmom filter |
| H-33 | Owner's structure (`trend_swing`) as a filter on momentum | **routed into H-02** (D-TVR group); 0 trials | 2,709 at 1h (reference only) | 0 | The group survives H-02: one survivor-slot trial (4h, same rule as H-31). It does not: R-09 extended to structure-as-filter |

Not proposed, so they are not re-proposed without new evidence:
- long-only 1h/4h trend (side picked from seen tables; mostly drift beta, per Huang et al. 2020);
- 4h regime-aligned tsmom (about 1,190 events, PASS needs about +0.30 R gross);
- any 1d trend (≤ 454 events);
- volatility-scaled exposure (R-08);
- cost-amortising exit variants beyond H-28 (worth about 0.02 R under the constant swap prior).

## B. Retired: do not re-test without new evidence

The governing list is `research.retired_families` in `config/settings.yaml` (family, row id, retired date, reason,
registry trials); this table mirrors it and `tests/test_director.py` fails when they disagree. Editing this table does
not change the research director's allocation: change the settings too. The retired families share 1 exploration trial
a quarter (to the one with the best post-retirement evidence, else by rotation) and each is reinstated only by out-of-sample attribution after its retirement date (`research/director.py`).

| ID | Idea | Status | Trials (registry #, report) | Result | Reason retired |
|---|---|---|---|---|---|
| R-01 | mean_reversion (15m; RSI/Bollinger/VWAP fades) | retired | #3, #5, #10, #12 (#40) | No gross edge; leakage shuffle AUC 0.72 flagged | No cost-surviving evidence for short-horizon reversion in gold (SoA A4) |
| R-02 | session_open breakout (15m) | retired | #1, #7, #11, #13 (#37) | Gross +0.042 R, t 1.31 on 1,498 events. Fails P4 | About 90 events a year; no mechanism without an exchange open |
| R-03 | trend pullback (1h) | retired | #2, #4, #9, #14 (#39) | No gross edge | Small-tick contracts lost short-term trend after 2009 (Kurth et al. 2026) |
| R-04 | range breakout (1h) | retired | #6, #8, #15 (#41) | Gross +0.015 R, t 0.38 | Same reason as R-03 |
| R-05 | intraday_momentum (NY, London) | retired | #18, #19 (#52) | NY −0.006 R (t −0.50); London −0.028 R (t −1.48); net −0.16/−0.26 R | Mechanism (close auction, gamma hedging) absent in spot XAUUSD (SoA A4) |
| R-06 | tsmom at 1h/4h horizons (24–480 bars) | failed; superseded by H-01 | #16, #17 (#51) | Gross +0.061 R (t 2.63) but net −0.079 R; 4h net −0.014 R before swap; design gates fail | Gross edge too small for costs at this horizon. Only the slow version stays alive |
| R-07 | Meta-models over primaries with no gross edge (per-family and pooled_1h) | retired until a primary passes P4 alone | #20 (#53), plus per-family fits | OOF AUC about 0.50 | Meta-labelling filters an edge; it cannot create one (SoA A5) |
| R-08 | Pre-FOMC drift, London-fix front-running, weekday/weekend effects, real-yield sign rules, volatility targeting as alpha | retired from the literature (never trialled) | none | — | Gone after 2015 / venue reformed / fragile / broke in 2022 / no effect in commodities (SoA C3) |
| R-09 | SMC and classic chart tools as standalone rules: FVG, order blocks, BOS/CHoCH, premium/discount, Fibonacci, pivots, Ichimoku, candlesticks, volume profile, Heikin-Ashi | retired as rules; kept as bulk features for H-02 | none | — | No peer-reviewed support net of costs (survey §3). A dedicated trial would cost more than its expected information |
| R-10 | Re-scoring R-06 tsmom 1h/4h (and further tsmom 1d loop mutations in 2026Q4) under cheaper measured costs | retired 2026-10-19 (live review s.4; rollout plan R4) | none new | — | Gross t 2.4-2.6 cannot clear the DSR bar at t about 4.1; every loop trial raises the bar for H-48/H-50 |
| R-11 | 1h and 4h box rules (`box_break_close` 1h/4h) as standalone trials | **corrected 2026-10-19** (`prereg-2026-10-19-h50-followup.md` s.2): 4h stands, 1h superseded by H-53 | none | Was 907 / 319, counted with the slot held for the full time limit; the pipeline's one-at-a-time (slot freed at the label exit) gives about 1,625 (1h) / 436 (4h) | 4h stays below the P4 floor (parity and cost monitor only). 1h clears the floor. That is feasibility, not evidence of edge. Its standalone trial H-53 is a post-STOP decision charged as the 2027Q1 retired-family exploration trial |
| R-12 | Reading any shadow trade opened before the history-gap fix (2026-10-19 13:49 UTC) as evidence | rule (live review s.1.1, H-52) | none | — | Decision frames had a two-week hole after each engine restart; such trades are quarantined (`history_gap`) in the roster/promotion code |
| R-13 | EQH/EQL as magnets (KB-5; `eq_liquidity_run` stage 2, H-49) | retired 2026-10-19 by its pre-registered stage-1 rule | none (stage 1 is a 0-trial diagnostic, K_eff +1) | Cross-rate lift vs matched single pivots -0.3 pts [-2.8, +2.5] (2010-2017, 1,092 approaches) and +0.8 pts [-1.9, +3.6] (2018-2025-09, 987); bar was ≥ 5 pts with CI > 0 in both (`kb5-stage1.json`) | Equal highs/lows are no more likely to be traded through than single pivots in XAUUSD 15m |
| R-14 | sweep_absorb (15m shallow-sweep fade; H-48, KB-2) | retired 2026-10-19 by its pre-registered rule (STOP) | #88 | Gross -0.028 R, t -1.17 on 3,210; net -0.552 R (n 3,247); long and short both negative; 0 net-positive years | No reclaim-fade edge before costs; the H-08 15m form and the cascade arm close with it; no neighbour tests. Its roster agent stays a demo shadow monitor with no promotion route (ADR 0040) |
| R-15 | box_break_close (owner's box break-and-close + MFI, 15m frozen roster rule; H-50) | retired 2026-10-19 by its pre-registered rule (STOP) | #89 | Gross +0.030 R, t 1.61 on 5,684; net -0.297 R (n 5,835); long -0.290 R, short -0.305 R; 0 net-positive years; lot fit 97.4%, -£1.25 per trade at £1,000 | No gross edge at P4 and deeply negative net. H-51a/b lapse. The four box agents keep running on demo as parity/cost monitors with no promotion route; **deviation from the rule's text: probes keep mirroring them** (owner's demo-for-learning directive; ADR 0040). H-53 (1h) is the 2027Q1 retired-family exploration trial, decided after this STOP on feasibility (not edge) information. If it reads STOP: no further box_break_close trial without a new mechanism or measured costs below half the prior |
| R-16 | Macro-direction rules at 4h/1d (H-84): -sign of the 20-day change in DFII10 or in the broad dollar, H-01 slow TSMOM gated by both (H-06), and long in the top third of the GVZ^2 - HAR-RV^2 variance premium, on H-01 barriers | retired 2026-10-30 by its pre-registered rule (`prereg-2026-11-03-h84-macro.md`, `d4b8499d`) | none (2010-2017 screen, K +4) | n 221 / 215 / 93 / 113; gross -0.055 / +0.045 / +0.125 / +0.077 R (t -0.59 / 0.47 / 0.84 / 0.62); net -0.223 / -0.112 / -0.054 / -0.218 R; 1-3 of 8 years positive (`h84-macro-screen-2026-11.md`) | Gross t < 1 on every variant, so the verdict holds without swap. The real-yield rule has the wrong sign even before 2022 (confirms R-08). Macro returns only as gate primitives in discovery v2, counted in K_v2 |
| R-17 | Trend-day continuation on HAR-high days (H-17 V0 fixed 3 ATR and V1 runner; 15m first noise-boundary breach 09:00 London..10:30 NY) | retired 2026-10-30 by its pre-registered screen rule (A3, fail is final, Q-1) | none (2010-2017 screen, K +2) | 671 events: V0 gross -0.003 R (t -0.05), net -0.352 R, 0 of 6 net-positive years; high half below the low half (weekly Welch -0.11 R, t -1.24); V1 not read | Ex-ante range does not select direction at 15m; third read of the R-02/R-05 mechanism with no gross edge. Re-test only via the R-02/R-05 reinstatement path |
| R-18 | Discovery grammar v2 (H-85): bounded G2 search over the v1 vocabulary plus 13 gates (HAR band, NR4/NR7, tier-1 event day, real-yield and dollar 20-day change sign, GVZ tercile), the 4h-return side and 5 exits (time, 1R/2R/3R, break-even trail), 1h/4h/1d | retired 2026-10-31 by its pre-registered rule (`prereg-2026-11-02-discovery-v2.md`, `513f088c`) | none (explore search, K +1,704 declared; 864 specs ledgered) | 0 survivors; best fitness below the search null on all three (0.985 vs 1.430, 0.325 vs 0.903, -0.123 vs 0.881); floor passers' median own net R -0.17 / -0.16 / -0.24 R at measured costs (`discovery-v2-screen-2026-11.md`) | Automated generation freezes 2026-11-20 (A4) until the Q1 SoA refresh; the gates stay as PIT features for hand-written pre-registrations. A new generation needs a new information source (COT, ETF flows, order flow, author calls), not more search over bar and macro inputs |
| R-19 | Positioning and flows as 1-week directional rules on XAUUSD (H-86..H-88): COT managed-money net % OI decile extremes (contrarian), -sign(weekly MM change) in its top third, sign(GLD 5-day tonnes change) in its top third; 1R = 1.5 ATR(1d) stop, 5-decision-day time exit | retired 2026-11-01 by its pre-registered rule (`prereg-positioning-2026-11-01.md`, `f7fc8755`) | none (screen, K +3; holdout sealed) | gross t 0.16 / 0.29 / 0.19; net -0.08 / -0.05 / -0.06 R at cor-2026-10-31.2 + swap prior; swap-free net <= +0.012 R; none beats its year- and side-matched random-entry q95 (`positioning-screen-2026-11.md`) | Columns stay as PIT features/gates for a model, counted there; other windows, thresholds, holds or the opposite signs are re-tests and need new evidence and a new id |
| R-RS | Retail ICT / supply-demand setups as stand-alone rules on XAUUSD (RS-1 silver bullet NY AM 5m, RS-2 1h supply/demand first retest, RS-3 15m inverse FVG), limit entries, 2 R | retired 2026-11-01 by the pre-registered rule (`prereg-2026-11-01-retail-setups.md`, `9fc69477`; code `3779c53b`) | none (screen, K +3; holdout sealed) | net -0.05 / -0.28 / -0.10 R (90% LB -0.15 / -0.41 / -0.13), gross +0.08 / -0.09 / +0.01 R, 4 / 0 / 0 of 8 years positive; only RS-1 beats its random twin (Bonferroni-3 p 0.068) and that is the twin's adverse limit-fill selection (`retail-setups-screen-2026-11.md`) | No edge before or after cost; other kill-zone windows, gap floors, R multiples or zone definitions are re-tests and need new evidence. With E-1 (OB), E-2 (FVG magnet), #88 (sweep), G-7 (round numbers), R-01 (VWAP), the divergence closures and R-15 (box + MFI), the retail SMC/price-action list is closed as rules |
| R-20 | Meta-label filters (extends R-07): a fixed LightGBM regressor on 40 PIT columns (H-62 intraday base, COT/GLD positioning, tier-1 calendar) ranking I0 random-schedule decisions taken both ways (ML-M1) or I2B box-break candidates (ML-M2), top decile per year | retired 2026-11-01 by its pre-registered rule (`prereg-ml-meta-label-2026-11-01.md`, `1eac5cb6`) | none (2010-2017 screen, K +2; holdout sealed) | M1 top decile net +0.016 R (LB -0.034), forward -0.015 R, though +0.11 R over side-matched random selection (p 0.0005) and gross +0.146 R; M2 net -0.513 R (`meta-label-screen-2026-11.md`) | Ranking skill (~0.10 R) is real but mostly swap/cost-in-R selection (89% short) and smaller than the I0 bracket's ~0.13 R cost. Reopen only with a new information source (order flow, options surface, author calls), not a new algorithm, column subset or geometry. ML-M1c (1h, K +1) also failed 2026-11-01: net +0.032 R, LB -0.048, 4/8 years |

## C. Multiple-testing ledger

| Quarter | Registry trials | Screens inside trials (K_eff) | DSR trial count to use for the next trial |
|---|---|---|---|
| Q4 2026 | #1–#20 | 0 | 20 |
| Q4 2026, owner-directed batch (2026-10-18; **dispatched**, B01-B04 = #21-#35 run; B01-B03 12/12 FAIL, R-02/R-05 retirement reconfirmed 2026-10-18; readout and registry defect D-1 in `batch-2026-10-18-readout.md`) | #21–#53 if all 33 run (2026Q4 budget override 53, settings) | 0 | **53** (gate-1 t ≈ 3.94). H-04 and H-01 move here from Q1, so the Q1 reservation becomes 6 and the end-of-Q1 projection is about 116 (t ≈ 4.2) |

| *Superseded 2026-10-20 (evaluator M3): the batch was dispatched; see the row above and the closed-batch row below.* Q4 2026, owner-directed batch (2026-10-18; planned, **not dispatched**; `batch-2026-10-18.md`) | #21–#53 if all 33 run (needs the 2026Q4 budget raised to 53: owner) | 0 | **53** (gate-1 t ≈ 3.94). H-04 and H-01 move here from Q1, so the Q1 reservation becomes 6 and the end-of-Q1 projection is about 116 (t ≈ 4.2) |
| Q4 2026 batch, **closed** 2026-10-18 about 07:30 UTC (`research-v1`) | #21–#52 recorded, each with its verdict: 31 stop and 1 inconclusive (#48 H-01, held); 0 PASS, 0 LEAD; B13 withdrawn to fund H-02 (`batch-2026-10-18-readout.md` section 6) | 0 | **52 now; 53 when H-02 runs** (it is #53, the last 2026Q4 slot). H-02's K_eff (about 10 groups, no `smc_replica`) then adds to every later trial's DSR |
| Q4 2026 batch, status 2026-10-18 about 07:10 UTC (`research-v1` snapshot; superseded by the row above) | #21–#47 recorded, the batch still running | 0 | 47 now, 53 when the batch and H-02 finish. Cost proposals (A7): +1 if H-28 runs (≥ 54), +1 more only if H-29's conditions hold. CB-0/CB-D1/CB-D2 add 0 |
| Q1 2027 (planned) | #21–#40 at most (8 reserved: 4 queued + 4 contingent H-02 survivor slots; 12 open under the E-5 cap, allocated 2026-10-17 as 9 conditional trial rows (O-1..O-10 without O-9, now a 0-trial diagnostic) + 3 free reserve; corrected 2026-10-17 to settings) | H-02: K_eff = features screened when survivors go forward as features, groups screened only for whole-group survivors (recorded as `k_eff` on the discovery row; `registry.n_trials_effective` adds it) | 20 + trials run so far + K_eff for any survivor of H-02. Projection, end of Q1: up to 40 registry rows + H-02 K_eff (about 10: **H-02 moved to `survivor_unit: group`, quant ruling Q-H02U 2026-10-17**; it was about 300 at the feature unit) + H-14 K_eff (about 12) ≈ 60; the gate-1 DSR then needs a model-filtered t of about 4.0 (was about 4.6). *Updated 2026-10-17:* H-14's K_eff is about 16 under the O-4 group-map amendment (14 groups verified on `f-079deacc61`, plus `liquidity` and `activity`), so the adopted end-of-Q1 case is about 66 (t ≈ 4.03, still 4.0). *Updated 2026-10-18 (research director):* N1 `tod_periodicity` adopted as one more O-4 group, so H-14 about 17 and the end-of-Q1 case about 67 (t still about 4.0); no registry trial |
| Owner-indicator replica (2026-10-18; `prereg-tv-replica-2026-10-18.md`; **withdrawn at 0 trials** 2026-10-18, B4/B5; corrected 2026-10-19) | 0 (H-TVR1, H-TVR2 withdrawn below the event floors; H-TVR3 lapses) | D-TVR adopted: K_eff +1 in O-4, +1 in H-02 | Registry `n_trials_effective` at the verdict date, **never below 55** |
| Q2 2027 (draft) | #41–#60 at most | SW-0 if it runs: K_eff ≈ 16 (about 8 groups × 2 sides, `survivor_unit: group`); H-14 survivors and Q1 follow-ups add 1 each | **About 77 + the other Q2 trials** (the swing lane's projection: 60 + SW-0's row + its K_eff ≈ 16). On the updated Q1 base of about 66 the same sum is about 83 + the other Q2 trials. Either way the gate-1 bar is a t of about 4.1. Nothing in the lane lists adds to K until a row runs |
| Q4 2026, H-25 (pre-registered 2026-10-18, **not dispatched**; `prereg-h25-precision-stop-2026-10-18.md`) | +1 (#56 in budget order; needs the override 55 -> 56) | `k_eff: 19` on its row (tight-stop study's 19 entry variants) | **Registry `n_trials_effective` at the verdict, never below 75** (about 85 once H-02's K_eff is recorded); gate-1 t about 4.1 |
| Next generation (2026-10-18; `next-gen-2026-10-18.md`; pre-registered, **not dispatched**, unfunded) | +2 (H-31, H-32) if funded | H-33: 0 (inside H-02's D-TVR K_eff) | Registry `n_trials_effective` at the verdict, **never below 55** (t ≈ 3.97; ≈ 4.09 once H-25's K_eff is recorded) |
| KB pre-registrations (2026-10-18; `prereg-kb-2026-10-18.md`; pre-registered, **not dispatched**, unfunded: needs owner override of the Q4 budget) | +2 (H-48, H-49) if funded; D-KB1 0 | D-KB1 +1 on H-25's row; H-49 stage 1 +1 | Registry `n_trials_effective` at the verdict, **never below 90** (gate-1 t ≈ 3.97; about 4.0 with the two trials and their K_eff) |
| Live review (2026-10-19; `prereg-2026-10-19-live-review.md`; `rollout-plan-2026-10-19.md` R1-R4) | +1 (H-50, funded by R1: H-49's stage-2 slot reassigned permanently); +2 (H-51a/b) only if H-50 reads CONTINUE/LEAD, from the Q1 2027 open budget; H-52 0. H-49 stage 2, if stage 1 passes, needs its own slot (owner: 55 → 56, or the first Q1 open slot) | H-49 stage 1 +1 | Registry `n_trials_effective` at the verdict, **never below 108** (gate-1 DSR t about 4.1) |
| H-48/H-50 verdicts and H-53 (2026-10-19; `prereg-2026-10-19-h50-followup.md`) | #88 H-48 STOP, #89 H-50 STOP; 2026Q4 **55 of 55 used**. H-51a/b lapse (0). +1 (H-53, `box_break_close` 1h) in 2027Q1 only, as that quarter's shared retired-family exploration trial (2026Q4's was already used by the batch) | 0 | Registry `n_trials_effective` **110** now; H-53 at its verdict **never below 111** (gate-1 DSR t about 4.1) |
| Cycle-1 recount (research director, 2026-10-20; evaluator audit s.1, ml-leakage-audit F5) | 2026Q4 closed **55/55**; #88 H-48 STOP (R-14) and #89 H-50 STOP (R-15) recorded here, registry verdict rows owed (evaluator via release-manager, 2026-10-23). 2027Q1: **6 reserved** (H-02, C4H + 4 slots; H-01 #48 and H-04 #44 ran in Q4), 14 open. H-28 withdrawn; H-25 lapsed in Q4 (may bid for a Q1 free-reserve slot by a dated note); H-49 stage 2 void (stage 1 retired R-13) | H-49 stage 1's 0-trial look, K_eff +1, has no registry row | **K = 111** = 89 trial rows + 21 (#69 `k_eff`) + 1 (H-49 stage 1). A floor until BACKLOG 107 counts non-discovery `k_eff` and looks. H-53 at its verdict **never below 112** (BACKLOG 107 merged before H-53 dispatch, otherwise the verdict row records `k_used: 112`; D-KB1's +1 enters with H-25's row, not here); O-2/O-3 never below 113 (V1u K_eff +1, `prereg-exits-2027Q1.md`) |
| High-volume learning program (research director, 2026-10-25; `high-volume-learning-2026-10-25.md`; proposed, not frozen) | 0 (H-60, H-61, H-62 are 0-trial); graduates H-63+ at 1 each from 2027Q1 open slots, 4 earmarked | H-60: K_eff + about 24 arms (clustered by return correlation), once | K about 116 -> about 140 after H-60; one-sided Bonferroni t 3.33 -> 3.38; gate-1 DSR about 4.1+ |
| A3 history screens (opportunity researcher, 2026-10-30; `opportunity-days-screen-2026-11.md`, `h17-screen-2026-11.md`) | 0 registry trials. O-1 (H-16) and O-2/O-3 (H-17) **cancelled** by their screens: 3 Q1 open slots freed (reallocation: research director) | H-16 screen +1, H-17 screen +2 (V0, V1) | Add 3 to every later K; registry untouched |
| Discovery grammar v2 (strategy researcher, 2026-10-31; `prereg-2026-11-02-discovery-v2.md`, code `2ccbc8e5`) | 0 registry trials, 0 budget slots | **K_v2 = 1,704 declared before the run** (3 x (288 search + 20 x 14 neighbours)); null specs in K_null only | **Program K 1,264 -> 2,968** (declared figure; the ledger holds 864 v2 specs, no neighbours were evaluated). Result 2026-10-31: 0 survivors (R-18) |
| Positioning and flows (macro researcher, 2026-11-01; `prereg-positioning-2026-11-01.md` `f7fc8755`, code `af2eea16`; `positioning-screen-2026-11.md`) | 0 registry trials (no survivor, no confirm; holdout sealed) | K +3 (H-86..H-88, one look each) | **K 2,971** |
| Growth sprint C3 screen (opportunity researcher, 2026-11-01; `growth-sprint-2026-11-01.md` §2, `gs-c3-screen-2026-11.md`, rule frozen at `2b8acfbd`) | 0 registry trials, 0 budget slots | **K +2** (V1 NY first 30 min -> last 60 min; V2 London first 60 min -> London close), part of the sprint's K +7 | Program K +2 (3,002 -> 3,004 for C3; 3,009 once C1/C2/C4 report). Result 2026-11-01, 2010-2017: **both FAIL** (V1 net -0.095 R, 0/8 years; V2 net -0.008 R, LB -0.109, 3/8 years, excess p 0.0086 > 0.05/7). R-05 shape closed a third time; no confirm |
| Opportunity signals screen (opportunity researcher, 2026-11-01; `prereg-opportunity-signals-2026-11-01.md` `d4dba0f3`; `opportunity-signals-screen-2026-11.md`; A24) | 0 registry trials, 0 budget slots | **K +3** (OS-SA, OS-SB, OS-SD, one look each); declined lines (c), (b-ii) +0 | Program K 3,014 -> **3,017**. All three FAIL; no confirm |
| Tick-proxy microstructure screen (microstructure researcher, 2026-11-01; `prereg-2026-11-01-micro-tick.md` `8472864a`; `micro-tick-screen-2026-11.md`; A25) | 0 registry trials, 0 budget slots | **K +3** (MT-VAC, MT-TIM, MT-SRR, one look each) | Program K 3,017 -> **3,020**. All three FAIL; no confirm |
| Retail setups screen (strategy researcher, 2026-11-01; `prereg-2026-11-01-retail-setups.md` `9fc69477`, code `3779c53b`; `retail-setups-screen-2026-11.md`; A26) | 0 registry trials, 0 budget slots | **K +3** (RS-1..RS-3, one look each); 7 requested setups already closed were not re-run (+0) | Program K 3,020 (A25) -> **3,023**. All three FAIL; no confirm |
| Meta-label screens (ML researcher, 2026-11-01; `prereg-ml-meta-label-2026-11-01.md` `1eac5cb6`, `prereg-ml-meta-label-m1c-2026-11-01.md` `fba52087`; `meta-label-screen-2026-11.md`; A27) | 0 registry trials, 0 budget slots | **K +3** (ML-M1, ML-M2, ML-M1c, one look each) | Program K 3,023 (A26) -> **3,026**. All three FAIL (R-20); no confirm |

## Next generation (after Q1 verdicts)

- Promote any passer to the population as a founder.
- Spawn neighbours from passers only: hold length ±50%, a 1d vs 4h execution grid point, short-only, cost-filter
  on/off.
- Retire failures here with their reason.
- Owner rulings (owner approved the research director's recommendations, 2026-10-11):
  - P4 event minimum for daily families: **150, retire-only** (`research.screen_min_events_daily: 150`). It governs
    H-01 and H-06 now, and H-15, H-07 and H-03 if they reach a daily trial.
  - Consensus calendar source: **none**. H-11 stays blocked.
  - Other instruments: **out of scope**, even if H-01 is net ≤ 0.
  - Trial budget **20** a quarter; the conservative K_eff default is **confirmed**.
  - `costs-v1` published by 2026-12-11 if the demo terminal runs, otherwise "priors accepted" on 2026-12-14.
  - Owner's trade history: complete export, analysed locally, only the summary (R, %, counts, K) shared.
- Owner decisions still pending:
  - D+ data pulls.
  - Event-family floor for H-18 and projected percentage daily caps (`opportunity-days.md` section 6).

## A10. Owner-style multi-timeframe confluence (strategy researcher, 2026-10-18)

Source: `owner-style-mtf-2026-10-18.md`. Owner, 2026-10-18: he used the three replicated chart indicators across
15m, 1h and 4h together. Written before any count of these rules; counts only are added later (its section 10). The
TVR withdrawal ruling (B4b: no reworded TVR rule from its counts) is answered in that file's section 0 and needs the
research director's ruling before dispatch. Prior low; P(PASS) about 1-2%.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-34 | `smc_mtf` `omtf_c`, 15m: 4h and 1h swing trend = side, 15m internal CHoCH with the trend, pullback into the 1h discount/premium half, same-side 15m FVG; structure stop beyond the 4-hour pullback extreme (0.75-1.5 ATR15, else skipped), 2R target inside the 1h 50-bar range, 32 bars. Count ladder L0-L3 drops gap, discount, 1h in turn; first rung with ≥ 1,500 candidates runs | **withdrawn before dispatch** 2026-10-18 by its own count ladder: 0 / 7 / 11 / 16 candidates at L0-L3 (< 1,500). The structure stop is a median 3.3 ATR15, so only 34 of 8,385 CHoCHs fit the 1.5 × ATR15 lot cap. No reworded rule (B4b) | 0 (was 1) | batch §2 rules 1-6 at K ≥ 77, plus a context-lift check (vs the bare 15m trigger) that can only downgrade. Both H-34 and H-35 FAIL closes the SMC rule topic, multi-timeframe included |
| H-35 | `smc_mtf` `omtf_r`, 15m: 4h trend = side, a 1h box break against the side in the last 4 hours with 1h MFI at the extreme (≤ 20 / ≥ 80), then a 15m internal CHoCH back with the side (liquidity sweep and reclaim); same exits. Ladder L0-L2 drops MFI, then 4h. Withdrawn if > 50% of its candidates are H-34's | **withdrawn before dispatch** 2026-10-18: 0 / 9 / 18 candidates at L0-L2 (< 1,500), same stop-cap cause | 0 (was 1) | as H-34 |
| H-36 | The owner's own setup, inferred from his MT5 export mapped to the replica state at entry on 15m/1h/4h (`owner-replica-map`, lift vs base rate at Bonferroni 5%/K_map; his P&L per state is never computed) | proposed, **conditional** on his export and ≥ 30 mapped XAUUSD trades; holdout-dated trades flagged `holdout_informed` | 1 + K_map | written as its own pre-registration after the map |
| D-OMTF | 1h and 4h replica columns as one group `ctx_smc` in O-4's 15m map (H-14), beside D-TVR | proposed; research director + quant sign-off before O-4 runs | 0 (K_eff +1, O-4) | group survival only |

Ledger (to be moved into section C by the integrator): **0 registry trials** (H-34/H-35 withdrawn by count before
dispatch); +1 + K_map if H-36 runs; D-OMTF K_eff +1 inside O-4 if adopted. Holdout untouched (counts end
2025-09-30). Sizing fact recorded so it is not re-derived: a structure stop on a 15m CHoCH (median 3.3 ATR15, about
$27/oz today) is about 2% of £1,000 at 0.01 lot, so structure-stop 15m rules are not placeable at £1,000 under the
rails; a 1.0 × ATR15 stop version is TVR-like and closed by B4b.

## A11. Box and money-flow families from the owner's course notes (strategy researcher, 2026-10-18)

Source: `box-mfi-families-2026-10-18.md`. Owner, 2026-10-18: his notes on the box (`sr_channels`), money-flow (`mfi`)
and structure (`smc`) indicators and a price-action course are "baselines, not rules to obey". Each family runs one
count-selected rung (first rung with ≥ 1,500 candidates on 2010..2025-09; else withdrawn, 0 trials). Written before
any count. A7/B4b answered in that file's section 0; research-director ruling needed before dispatch. Prior low
(P(PASS) about 1-2% each). Later the same day the owner's price-action course notes (5-7) added the daily rule, the
map/trigger hierarchy and H-42..H-46, all written before any count. **Counts (2026-10-18, counts only, `box-mfi-families-2026-10-18.md` section 9): only H-37 reaches 1,500, at its last rung; 0 trials used; every ladder is re-counted once when the 2003-2009 data is in `data-v1`.** Pre-count ranking: H-45, H-46, H-47, H-37, H-42, H-44, H-39,
H-38, H-43, H-41.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-37 | `box_break`: 1h box body-close ≥ 0.2 box heights beyond the far edge, 1h and 15m money flow from the opposite extreme, 15m break same way, pullback to the farther of the broken box and 15m EMA20, stop beyond the box, target next 1h box (≥ 2R), not in the golden zone. Ladder: pool 4h/1h, 70/30, drop golden zone, drop 15m, drop MFI | **selected rung L6** (2,327 kept on 2010..2025-09: the bare box break, no MFI/15m/golden-zone/daily rule); dispatch deferred to the post-extension recount and blocker K-T; expected FAIL on rule 2 by cost (median cost 0.85 R/trade 2010-2025, 0.15 R today); stop median £2.9 today, 88% fit 0.01 lot | 1 | batch §2 at K ≥ 80 + baseline lift (vs the bare box break). FAIL retires box-break rules (R-10) |
| H-38 | `box_rip`: 1h touch of a box without a close through, opposite candle closes back outside, 1h or 4h money flow at the extreme, stop beyond the wick, target next box (≥ 2R), not in the golden zone. Ladder: pool 4h, 70/30, drop golden zone, drop MFI | below 1,500 at every rung (L0 54 … L6 1,303); re-counted once after the 2003-2009 extension (projection ≈ 1,890 at L6), withdrawn if still below; 0 trials | 1 | as H-37; FAIL retires box-rejection rules (R-11) |
| H-39 | `box_retest` (price-action note): 1h close beyond a box, first retest from the other side within 24 bars rejected, stop beyond the retest wick, target next box (≥ 2R). Ladder: pool 4h, double window | below 1,500 (L0 391 … L3 1,437); re-count after the extension (projection ≈ 2,080 at L3); 0 trials | 1 | batch §2 at K ≥ 80. FAIL retires retest rules (R-12) |
| H-40 | `box_filter_discovery`: one discovery trial over the candidates of every family that runs (H-45, H-46, H-37..H-39, H-42..H-44) with the pre-registered flags (1h trend-with, major level, volume veto / break-with-volume, depth 0.3, reward 3, day bias, premium/discount, engulfing, box-only anchor, fib-50 confluence, pin-at-EMA20, pin-retest, pin-then-inside, EMA8/21, the trend-level-signal confluence count, daily-with for H-46, London/NY session gate) plus the `mfi_signals` group; fold-internal stability selection; ≤ 2 survivors go forward | **FAIL** (run 2026-10-18): no flag group survived fold-internal selection; pooled OOF AUC 0.503 (null). K is now 90. All box/price-action flags (incl. R-GATE, S-GATE, D-MFI `mfi_signals`) are **retired as filters** on the 2010..2025-09 candidates; a flag returns only with new evidence. Source: owner/director run summary, trial record in the registry | 1 (K_eff 21) used, 0 survivors | survivor = selected in ≥ 60% of training folds and Welch t ≥ 2 inside them |
| H-41 | `box_break_15m`: H-37 on 15m boxes alone (owner: "15m boxes = scalping") | proposed, conditional on H-37 PASS/LEAD | 1 if triggered | as H-37 |
| H-45 | `trend_level_signal` master family (course checklists Long A / Short A): 4h HH+HL and above 4h EMA10/20, daily agrees, 1h bar touches a key level (box, EMA20, 50% level, broken-level retest) and closes as a signal candle (hammer, engulfing, piercing, star, false break, wick off the line), money flow turning from < 40 / rolling from ≥ 60, not mid-range; stop beyond wick and zone; entry next 1h open; 80% at TP1 (next box, ≥ 2R), breakeven, runner to TP2. Ladder: + course B setups, drop MFI, drop daily. Sub-families with > 50% overlap fold into it | below 1,500 at every rung (L0 49, L1 155, L2 250, L3 514; confluence ≥ 3/4/6: 49/44/18); re-count after the extension, withdrawn if still below; 0 trials. Use: `box_pa` feature group in O-4/H-02 | 1 | batch §2 at K ≥ 85 + baseline lift |
| H-46 | `liquidity_sweep` (gold note): tested 1h box swept, close back through, swing-structure break, rejected retest; stop above the sweep high + max(0.25 ATR, 2 spreads); TP1 next box (≥ 2R), TP2 the new extreme. Ladder: pool 4h boxes, drop "tested", double windows | below 1,500 (16 / 21 / 116 / 108); stop above the sweep high median $18.5 today (4% fit 0.01 lot at £1,000); 0 trials | 1 | as H-45 |
| H-47 | `sweep_displacement` (owner pre-entry checks): daily/4h bias, London/NY session gate, sweep of the previous session high/low or 1h equal highs/lows, then a displacement bar (body ≥ 1.5 ATR) away from it; entry at its 15m close, stop above the sweep high + buffer, target the opposite liquidity (≥ 2R). Ladder: pool 1h, displacement 1.0, drop bias. M5/M1 execution out of scope | below 1,500 (13 / 19 / 54 / 320); displacement stop median $22 today (0% fit at £1,000); 0 trials | 1 | as H-45 |
| R-GATE | Regime filters on every family (owner note 13): chop (two-sided 1h sweeps in 24 bars), stretch (> 3 ATR from 1h EMA50 in the trade direction), 4h ATR expansion > 1.3× its 14-bar mean (skip: ×2/3 size is impossible at 0.01 lot; CFO candidate tighten rule), news blackout (already applied). H-40 flags + read-only removed-share / kept-vs-removed contrasts | pre-registered | 0 (K_eff +3 in H-40) | survives only through H-40 |
| S-GATE | London/NY session hard gate on every family: H-40 flag + a read-only inside-vs-outside contrast per verdict | pre-registered | 0 (K_eff +1 in H-40) | survives only through H-40 |
| H-42 | `pin_bar`: 1h pin (wick ≥ 65%, body ≤ 25%) through a key level and closed back, after a retrace, not extended, with the daily trend; stop beyond the wick, next box (≥ 2R). Ladder: pool 4h, counter-trend large pins at major levels, body 33%, drop daily | below 1,500 (223 … L4 743); stop median £8.9 today (14% fit default gate); 0 trials | 1 | as H-37 |
| H-43 | `inside_bar`: 4h inside bar at a level with the daily trend, buy/sell-stop beyond the mother bar (3 bars), stop beyond the mother (or a preceding pin). Ladder: pool daily, drop level, drop daily | below 1,500 (fills 28 / 30 / 94 / 238 of 1,150-12,844 setups); mother-bar stop median $31 today (0% fit); K-S still open; 0 trials | 1 | as H-37 |
| H-44 | `fakey`: 4h inside bar false-broken against the daily trend with 4h EMA8/21 aligned, close back inside; stop beyond the false-break wick, next box (≥ 2R). Ladder: pool 1h, counter-trend large fakeys at levels (not on EMA support), drop EMA, drop daily | below 1,500 (90 … L4 1,206); re-count after the extension (projection ≈ 1,750 at L4); 0 trials | 1 | as H-37 |
| D-MFI | `mfi_signals` (regime, extreme circle, double circle, 50-cross, 40/60 pullback hold, divergence) on 15m/1h/4h as one group in O-4 | proposed; quant sign-off with D-TVR | 0 (K_eff +1) | group survival only; no standalone money-flow rule (owner: "never a signal alone"; R-01) |

Not run, with reason (do not re-propose without new evidence): reward floor 1.5 (worse target per cost); a
money-flow-only rule (R-01); break depth read as penetration *into* the box (that is a touch, not a break; flagged
to the indicator lane, section 3.1 of the source file).

## A12. Knowledge-base hypotheses KB-1/KB-2/KB-5 (strategy researcher, 2026-10-18)

Source: `prereg-kb-2026-10-18.md`, ranked from `knowledge-base/README.md` KB-1..KB-7 after H-40 FAIL and the
price-action claims check. Counts only (2010-01 .. 2025-09, holdout not loaded); no outcome read. Honest prior:
P(PASS) about 1% each; chosen for information per trial and because both fit a 0.01 lot.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-48 (KB-2) | `sweep_absorb`, 15m: first pierce of the previous-day or previous-session extreme, shallow (< 0.5 ATR15) and closed back inside within 2 bars, London/NY; fade at the reclaim close; stop = sweep extreme + 0.25 ATR15 (0.6-1.0 ATR15 kept), 2R, 16 bars | **STOP 2026-10-19** (trial #88, run 39279358684): gross -0.028 R, t -1.17 on 3,210; net -0.552 R; retired as R-14 | 1 | batch §2 at K ≥ 90, P4 gross t ≥ 2 on ≥ 1,000; cascade arm (deep, close beyond) not tested: overlaps R-04/B03 breakouts |
| H-49 (KB-5) | `eq_liquidity_run`, 15m: approach (0.3-1.0 ATR15) to an unbroken equal high/low aged 4-96 bars, trade toward it; target level + 0.25 ATR15, stop 0.75 ATR15, 16 bars. Stage 1 (0 trials) is the KB-5 magnet base rate vs single pivots | **failed** stage 1 (2026-10-19, `kb5-stage1.json`): cross-rate lift -0.3 pts [95% CI -2.8, +2.5] in 2010-2017 and +0.8 pts [-1.9, +3.6] in 2018-2025-09, below the 5-point bar in both; stage 2 not run (its slot went to H-50 under R1) | 0 (K_eff +1) | as H-48; stage 1 fail retires "EQH/EQL as magnets" |
| D-KB1 (KB-1) | Retest selection diagnostic on the H-37 L6 breaks (2,327), retest N = 3 (1h) fixed | pre-registered, 0 trials, K_eff +1 on H-25 | 0 | never-retested share of forward move ≥ 70% lowers H-25's prior; no new trial |

KB-3 (activity_tod), KB-4, KB-6, KB-7 stay features/exits inside O-4, exit library, H-17 and H-16; H-40's failure
does not retire them (different candidate sets), but KB-3's volume-veto half is now read as "no flag signal on boxes".

## A13. Live review and rollout (strategy researcher, 2026-10-19)

Source: `prereg-2026-10-19-live-review.md` (H-50..H-52) and `rollout-plan-2026-10-19.md` (rulings R1-R4). Committed
before any H-50 dispatch and before any clean shadow outcome was read; git history is the pre-registration proof.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-50 | `box_break_close` 15m `{}`, the exact frozen roster rule (BREAK_DEPTH 0.2; MFI(12) 30/70 turning; swing trend not against; not in the golden zone; stop 1.5 ATR14, target 3.0 ATR, 96 bars) as a research trial on 2010-2025; 2,404 one-at-a-time events | **STOP 2026-10-19** (trial #89, run 39284389371): gross +0.030 R, t 1.61 on 5,684 (the pre-registered 2,404 was a time-limit-block count; the trial is the frozen rule, `prereg-2026-10-19-h50-followup.md` s.2); net -0.297 R; retired as R-15 | 1 | prereg s.2 H-50 at K ≥ 108; STOP (stated prior 97%) moves the 4 box agents to parity monitors |
| H-51a | H-50 with the money-flow extreme removed (MFI ablation) | **lapsed 2026-10-19** (H-50 STOP), 0 trials | 1 (conditional) | MFI stays only if H-50's gross mean R exceeds the ablation's by Welch t ≥ 2 |
| H-51b | H-50 on 30m (`{"timeframe": "30m"}`), 1,444 events | **lapsed 2026-10-19** (H-50 STOP), 0 trials | 1 (conditional) | as H-50 |
| H-52 | Admissible forward evidence: quarantine before the 2026-10-19 13:49 UTC fix, daily parity ≥ 95% (bar, side) both ways, restart counter, probe costs at ≥ 30 fills | pre-registered, 0 trials; quarantine marker in the roster/promotion code; parity job and restart counter open (due 10-23) | 0 | parity below 95% on a day quarantines the agent's day; costs read at ≥ 30 filled probes |
| H-53 | `box_break_close` 1h `{"timeframe": "1h"}`, the exact frozen roster rule (as H-50, `HOLD_BARS` 48), 2010-01..2025-09, about 1,625 one-at-a-time events (pipeline convention) | **pre-registered 2026-10-19** (`prereg-2026-10-19-h50-followup.md` s.3), not dispatched; post-STOP feasibility decision, charged as the 2027Q1 retired-family exploration trial (one of the Q1 open trials, `research-plan.md` 11.5; H-51a/b lapsed at 0); to be written as a queued row before 2027-01-01 (evaluator M2). H-37 and H-41 fold into its verdict (2026-10-20): a STOP retires them, a CONTINUE does not reopen them | 1 | as H-50 at K ≥ 111 (recount 2026-10-20: read at K ≥ 112), P4 t also day-clustered; stated prior P(CONTINUE) about 3%; STOP extends R-15 to every box timeframe |
| H-54 | Forward-only opportunity log: fixed equal-weight score s = (x1 + x5 + x22)/3, no fit, scored 06:30 UTC and outcome 22:20 UTC into insert-only hash-chained tables; GVZ, event and compression inputs logged but sealed until O-1 is recorded; no backfill, no bar before 2026-10-01 | **proposed 2026-10-20** (`prereg-opportunity-forward-2026-10.md`, branch `research/opportunity-forward-prereg`, awaiting quant review); jobs and loader owed as backlog items (product-owner) | 0 (K 0) | one read at n ≥ 120 days (about 2027-05-28) by its continue/fail/inconclusive rules; forward check of H-16, never an entry rule |


## A14. High-volume learning program (research director, 2026-10-25)

Source: `high-volume-learning-2026-10-25.md`. Proposed; the rules freeze when the pre-registration files merge, before
any exploration fill of an arm is read. Exploration rows are tagged `exploration` and are never promotion evidence.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-60 | Historical arm priors: about 24 frozen arms (scalp 1m/5m, intraday 15m/1h, swing 4h/1d, random-bracket controls) on data-v1 2015-01..2025-09-30, per-era measured costs; outputs fire rate, gross/net R, day-clustered t, per-year, parity | proposed 2026-10-25, prereg owed | 0 (K_eff + arms, once) | no verdict, no retirement, no promotion: priors for H-61 only |
| H-61 | Exploration program on the probe-only demo host `icm-probe` (no new accounts): 0.01 lot at own stops, ADR 0043 pots (scalp £50, intraday £48, swing £40), per-arm £ caps and cumulative-R stops, Thompson allocation with 5% floor / 30% cap, propensity logging | proposed 2026-10-25, prereg owed; engine E0-E10 owed | 0 | none for promotion; an arm is read once at its 300th admissible trade (>= 20 days), graduates at P(mean > 0) > 1 - 0.05/m (Bonferroni) and parity, else retires; random controls never graduate; retired-family neighbours (R-06/R-10, R-07, R-14, R-15) need the evaluator's re-entry record before the read; graduation only earns a new pre-registration (H-63+), whose forward n >= 60 is fixed-sample from the `icm-demo` roster shadow agent |
| H-62 | Online meta-labeller per style: <= 40 frozen features, weekly retrain at >= 1,500 new labels, session-day blocked cross-fitting with purge/embargo, strict-forward scoring, champion/challenger on 2 unseen weeks | proposed 2026-10-25, prereg owed | 0 | a model reaches icm-demo only through its own promotion pre-registration |

## A15. Time-structure programme (research director, 2026-10-26)

Sources: `time-structure-2026-10-26.md` (synthesis of slices A-H) and `prereg-2026-10-26-time-structure.md`. Discovery
half 2010-2017 only. The programme took about 2,460 raw looks, charged 187 effective (G at 51 axes; K ≈ 332, 337 with the 5 arms). Every row's "closed" means
**this version, tested this way, did not beat random after cost**.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-70 | F-1: 15m band break (KC/BB/DC20 union) traded with the break when hurst_128 is in its low tercile and against it when high; 4-bar time exit, stop 1.0 ATR15 | **pre-registered**; arm F1A + control F1C (needs the 2026Q4 override 69 -> 74); O-11 confirmation (2027Q1, conditional) | 1 (O-11) + arm registrations | prereg 2.3 |
| H-71 | G-1: first 1h close ≥ 0.1 ATR beyond a prior-period level in Asia hours, stop 1.0 ATR back through the level, fixed 2R, 32 bars | **pre-registered**; arm G1A + random-level control G1C; O-12 (conditional) | 1 (O-12) + arms | prereg 3.3 |
| H-72 | Friday long, Thursday 19:00 to Friday 16:30 New York, 1.0 ATR4h stop | **pre-registered, forward-only** (2018+ seen by slice B); arm WFRI | 1 at its 170-Friday read (26 Fridays is a safety check only) | prereg 4 |
| H-73 | Time-structure range feature group (≤ 12 columns: FOMC/NFP days, pit window, wavelet energy, rv ratio, range used, displacement, tick z) | proposed; spec by the opportunity-researcher | 0 (K_eff +1) | drop rule prereg 5 |
| H-74 | Slow-trend swing group: 30-day-extreme first touch (C-1), 4h/1d band state (F-3), 30/60/90 windows | proposed for SW-0 (Q2) | 0 here | SW-0's rule |
| H-75 | D-1 Asia trend persistence 1h | held: H's rebuild null; reconcile by 2026-11-13 | 0 | new prereg if reconciled |
| H-76 | A-1 failed Asia-range fade 15m | **closed 2026-10-26** (evaluator condition 8): sign flipped on an independent rebuild, holdout seen by slice A, 48 events a year | 0 | — |
| H-77 | D-2 CMF20 1h | held: redundancy with the MA regime unchecked | 0 | ρ > 0.7 closes it |
| H-78 | Pre-specified book read on arm fills against sign-randomised nulls | proposed (daily_review) | 0 | descriptive |
| H-79 | Allocator against a shuffled-weight null and a journal replay | proposed (backlog 111) | 0 | backlog acceptance |

Retired by this programme (0 trials, form tested): Fibonacci retracement/extension/time zones, wavelet/Ehlers/
Goertzel/EMD direction, Hurst as an external gate, 30/60/90-day momentum/reversal/range position, turn of month,
expiry, FOMC/NFP/CPI direction, pre-FOMC drift, festival and month seasonality, oscillator fades and divergences at
15m-1h, ADX gate, OBV, session-VWAP fades, value-area 80% rule, order-block retest, FVG magnet, sweep reversal, key-level
retests and fades, session opens as levels, 15m ORB (24 variants), TTM squeeze, unconditional band fades and breaks,
LBMA/COMEX/Tokyo anchor reactions.

## A16. Adaptive exits by timeframe, lot size and running P&L (trade management researcher, 2026-10-26)

Source: `exits-adaptive-targets-2026-10-26.md` (2010-2017 discovery only) and `prereg-exits-adaptive-2026-10-26.md`.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-80 | Box break-and-close as ONE family on 15m/30m/1h/4h (frozen roster entry, 1.5 ATR_tf stop, 0.01 lot): RUNNER exit (BE at +1 R, 2.5 ATR_tf trail after +1 R, no target; real arms BXR15/30/1H/4H) vs FIXED 2 R (virtual BXF*) vs first box at >= 2 R (virtual BXT*), paired on the same fills, pooled with a timeframe stratum. Discovery: runner - fixed +0.039 R (t 2.4; 30m carries it), first-box - fixed -0.016 R (all 4 TF negative), nearest box inside 2 R on 97-98% of breaks, no exit net-positive (measured cost at today's ATR: fixed -0.045, runner -0.021 R) | **R2 FAILED 2026-10-30** (2018-01..2025-09, n 5,355: runner - fixed gross -0.018 R, t -1.12, 2 of 4 TF; E0/E4/E8 net -0.199/-0.288/-0.206 R; STRUCT closed; `prereg-exits-adaptive-2026-10-26.md` s.8). BXR* retired under A2; 1 registry trial (local-only, to sync as 2026Q4). R-15 stands and extends to 15m-4h box exits | arms 0 (K_eff +1); confirmation read R2 on 2018-01..2025-09: 1 recommended | R1 forward: kill only, no adoption before 3,000 fills; R2: adopt runner if pooled paired t >= 2 (Holm, 2 comparisons), 3 of 4 TF same sign, net >= fixed; close next-box targets if pooled diff <= 0 |

## A17. Scalp-volume families 1m/5m (microstructure researcher, 2026-10-27)

Source: `prereg-2026-10-27-scalp-volume.md` (2010-2017 event counts and null-bracket SD only; no return read).

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-81 | Ten 1m/5m exploration families at fixed-$ brackets with cost/R ≤ 0.08 per broker (ICM $3 / $4.50, Vantage $7), each with a shared per-account random control: Z1 tick-surge continuation, Z2 run-exhaustion fade, Z3 round-number break, Z4 round-number touch rejection, Z5 tick-confirmed prior-hour break, Z6 NR4 inside-bar break, Z7 same-half-hour periodicity (Heston-Korajczyk-Sadka 2010), Z8 volume absorption fade, Z9 1h-trend 5m reclaim, Z10 three-horizon momentum; plus Vantage S-twins VS0-VS5B (blocked until the Vantage cost UB is ≤ $0.24) | **running on demo; S5/Z4/Z6/Z7 to retire on A5 sign-off (draft, 11-04)**: H-82 replay of the live entry code on 2010-2017 bounds gross at +/-0.011 R on 27,851-168,705 entries an arm, net -0.04..-0.13 R at every exit. Z1/Z2/Z3/Z5/Z9/Z10 under A2 (11-13). Was: pre-registered, 25 ids on the 2026Q4 override 118 -> 143 | 0 (exploration rows; K +25 by the precedent) | prereg section 4: one read per family at the 300th admissible fill, continue / stop (-30 R) / futility (UB < +0.03 R); never promotion evidence |

## A18. Exits on the scalp arms' own entries (trade management researcher, 2026-10-30)

Source: `prereg-2026-10-30-hb-exits.md` (bet 2b of `research-plan.md`; the plan's "H-B", renumbered: H-B is the THIN-days row).

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-82 | Wider targets (1 / 1.5 / 2 / 3 R) or a time-only exit at the arm's own hold, on the fixed entries of S5A/S5B, Z4D/Z4P, Z6D, Z7D/Z7P, beat the arm's current target by more than the same exits gain on random-side entries within +/-60 min (difference-in-differences of net R, day-block bootstrap), 2010-2017 only | **failed 2026-10-30** (screen `hb-exits-screen-2026-11.md`: all 5 variants FAIL, best pooled DiD +0.0009 R, 90% CI -0.0002..+0.0019; retired, do not re-test on fixed-$ scalp entries without new evidence) | 0 registry trials; K +5 (one per variant) | PASS per variant: pooled DiD >= +0.05 R, 90% lower bound > 0, Bonferroni-5 bound > 0, family and year consistency; no PASS closes plan line 2's wider-target part for these entries; a PASS earns only a 2018-2025-09 confirmation |

## A19. When-to-trade map on random entries (microstructure researcher, 2026-10-30)

Source: `prereg-2026-10-30-when-map.md` (research plan bet 3; tests the performance-analyst's H-A trend filter).

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-83 | Random entries (S0 geometry: $3 stop, 1 R, 30 min, both sides every 10 min) pay net of the worse measured broker cost ($0.23/oz + thin-hour spread add-on) in some of 24 cells of session (Asia, London, NY overlap, NY late) x prior-day range / ATR20 tercile x side vs the trailing 4h return; and "with" beats "against" by >= 0.05 R (H-A) | **failed** 2026-10-30 (0 of 24 cells; H-A d -0.008 R; `when-map-screen-2026-11.md`); timing-only line at the S0 clock retired | 1 screen, K +25 | cell passes on Holm-adjusted day-bootstrap lower bound > 0; plan line 3 retired if no cell has net > 0, day-clustered t >= 3 and >= 6 of 8 years positive; H-A passes on mean d >= 0.05 R with 90 % LB > 0 |

## A20. Macro-driven 4h/1d direction (research director, 2026-10-30 cycle 2)

Source: `research-plan.md` line 1, bet 1. The macro-researcher writes the pre-registration (variants, PIT inputs,
costs + swap, reading rule) and freezes it by commit before any 2010-2017 outcome is computed.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-84 | On a 1d signal executed on 4h bars with 5-20-day holds, <= 4 variants: 20-day change in 10y real yield (DFII10, short when rising), 20-day change in broad USD (short when rising), H-01 slow TSMOM gated by both (H-06), and the GVZ-RV variance premium (H-03), all `asof_join` on `available_utc` from `macro-v1` | **failed** 2026-10-30, 0 of 4 (prereg `d4b8499d`; `h84-macro-screen-2026-11.md`): gross t -0.59 / 0.47 / 0.84 / 0.62, net -0.22 / -0.11 / -0.05 / -0.22 R, Holm p 1.00 for each; plan line 1 retired (R-16); no confirm trial | screen K +4; a survivor's confirm on 2018-2025-09 is 1 registry trial each | Per variant: gross t >= 2, net > 0 at the worse measured cost + swap, >= 5 of 8 years positive, else retired. Disclosed: H-01 (trial #48) has seen 2010-2025-09 unconditioned; the gates are the new part. The 2022 real-rate break falls in the confirm window and is the falsification test. 1d stops are not sizable on the live pilot: demo/shadow only |

## A21. Discovery grammar v2 (strategy researcher, 2026-10-31; research-director bet 1, week 2026-11-02)

Source: `prereg-amendment-2026-10-30-kill-rules.md` A4; pre-registration `prereg-2026-11-02-discovery-v2.md` (code
`2ccbc8e5`), ADR 0058 amendment 2026-11-02. One explore run (2010-2017) each on 1h, 4h, 1d.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-85 | A bounded G2 search over v1's vocabulary plus 13 when-to-trade gates (HAR range band, NR4/NR7, tier-1 event day, real-yield and dollar 20-day change sign, GVZ tercile), the r4h side and 5 exits (time, 1R/2R/3R, break-even trail) finds a 1h/4h/1d rule with own net mean R >= +0.05 R at measured costs that beats its own search null, the shift null and the plateau test | **failed** 2026-10-31, 0 survivors on 1h/4h/1d (prereg `513f088c`, code `2ccbc8e5`; `discovery-v2-screen-2026-11.md`): best fitness 0.985 / 0.325 / -0.123 vs null bars 1.430 / 0.903 / 0.881; batch SPA p 0.044 / 0.479 / 0.845, PBO 0.35 / 0.62 / 0.51. Retired R-18; generation freezes 2026-11-20 | 0 registry trials; K +1,704 | 0 survivors on all three: generation freezes 2026-11-20 (A4); >= 1: generation continues, confirm only at DSR >= 0.95 at K 2,968 and with a slot |

## A22. Positioning and flows at 1d decisions / 1-week holds (macro researcher, 2026-11-01)

Source: R-18 ("a new generation needs a new information source: COT, ETF flows"). Pre-registration
`prereg-positioning-2026-11-01.md`, frozen by commit before any outcome; screen 2010-2017, matched random-entry control,
one look each; holdout sealed unless a screen passes. Closes the rule forms of H-09 and H-12 (their feature columns stay).

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-86 | P1_EXT: managed-money net % OI in its 156-report bottom decile -> long, top decile -> short, weekly decision, 1-week hold, 1R = 1.5 ATR(1d) stop | **failed** 2026-11-01 (prereg `f7fc8755`, code `af2eea16`; `positioning-screen-2026-11.md`): n 114, gross +0.015 R (t 0.16), net -0.081 R, Holm p 1.00, below matched-control q95 (-0.055); retired R-19 | screen K +1; confirm 1 registry trial if the screen passes | prereg s.5: n >= 50, Holm p < 0.05 net, gross t >= 2, >= 5/8 years, net > 0 at 1.5x, net >= +0.05 R, net > matched-control q95 |
| H-87 | P2_FLOW: -sign(weekly change of MM net % OI) when the change is in the top third of its 52-report range (Kang, Rouwenhorst & Tang 2020 liquidity-provision sign), 1-week hold | **failed** 2026-11-01: n 135, gross +0.023 R (t 0.29), net -0.051 R, Holm p 1.00, control q95 +0.050; retired R-19 | as H-86 | as H-86 |
| H-88 | P3_ETF: sign(5-day % change in GLD tonnes) when in the top third of its 252-day range (flow momentum), daily decision, one at a time, 1-week hold | **failed** 2026-11-01: n 214, gross +0.014 R (t 0.19), net -0.060 R, Holm p 1.00, control q95 +0.104; retired R-19 | as H-86 | as H-86 |

## A23. Event-minute drift (research director memo 2026-11-01; `decision-2026-11-01-next-lines.md` line 1)

Source: director's explore of 14 US calendar event types on 2010-2017 (K 30 recounted: 14 types x 2 directions +
the 5m window + the 60m hold). Pre-registration `prereg-h89-fomc-2026-11-01.md`, code `a5f413b0`.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| H-89 | After a scheduled FOMC statement (14:00 New York), sign(mid t+5m - mid t-1m) continues to t+60m: entry t+6m close at ask/bid, exit t+60m at bid/ask, net of $0.08 commission + $0.30 slippage. Explore 2010-2017: n 64, +$3.11 net at entry t+6m (t 3.21), 8/8 years; placebo Wednesdays -$0.08 | **failed 2026-11-01, retired final** (`h89-fomc-confirm-2026-11.md`): 2018-01..2025-09 n 62, mean net -$1.23, t -0.82, 3 of 8 years positive, 50% positive; top-5 removed -$2.74. Do not re-test post-FOMC continuation on another window, entry minute, horizon or subset without a new mechanism or data source | 1 registry trial (local sidecar, sync owed via PR #243); explore K 30 + confirm K 1 -> ledger ~3,002 | PASS only if mean net > 0 with t >= 2, >= 5 of 8 years positive, share > 0 >= 55%; any leg fails -> retired |
## A22. Growth sprint screens C1, C2, C4 (exits researcher, 2026-11-01)

Source: `growth-sprint-2026-11-01.md` section 2 (PR #251, K += 7 for C1-C4); operational details `prereg-growth-2026-11-01.md`. IDs carry the GS- prefix so parallel lanes cannot collide on H numbers.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| GS-C1/C2/C4 | Convex payoffs on 2010-2017 at 1.5 x measured ICM cost: C1 news straddle at NFP/CPI/FOMC (k 0.5, 1.0 x ATR15), C2 pyramided session-range breakout (Asia->London, London->NY; 1.5 x ATR15 stop, 3 adds at +1R, open risk <= 1 unit), C4 NR7 prior-day-range break with a 4 R target | **failed** 2026-11-01, 0 of 5 (prereg `fd02d0de`; `growth-screens-2026-11.md`): net mean R C2a -0.36, C2b -0.03, C4 +0.05, C1a +0.09 (whipsaw 64%), C1b -0.42; none beats its random twin at Bonferroni-7. Retired as specified except C1b, whose sign depends on 1m intrabar order (optimistic bracket +0.14 R, LB +0.01): tick re-measurement proposed, K +1 if slotted | 0 registry trials; K +5 of the director's +7, plus K +5 for the disclosed intrabar bracket (program K 3,014 with C3) | per variant: net mean 90% LB > 0, beats a 4-twin random-entry control with identical mechanics at Bonferroni-7, >= 5 of 8 years positive; else FAIL and retired as specified |

## A24. Opportunity signals screen (opportunity researcher, 2026-11-01)

Source: orchestrator brief 2026-11-01 (lines a-d). Pre-registration `prereg-opportunity-signals-2026-11-01.md`; lines (c) month/expiry and (b-ii) London-open gap declined as re-tests (K +0).

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| OS-SA/SB/SD | SA: fade a >= 3-sigma (clock-matched) 60-min move outside tier-1/2 news to the window VWAP, structure stop, 240 min; SB: fade the Sunday-reopen gap (>= 0.20 ATR20d at +30 min) to Friday's close by 07:00 UTC Monday; SD: 1h Donchian-20 trend in the top RV5 tercile, Bollinger(20,2) fade to SMA20 in the bottom tercile | **failed** 2026-11-01, 0 of 3 (prereg `d4dba0f3`; `opportunity-signals-screen-2026-11.md`): SA n 2,582 gross +0.093 R but twin +0.148 R, net -0.197 R, 0/8 years; SB n 90 (COUNT), net -0.016 R; SD n 1,895 net -0.126 R, 3/8 years, reversion leg gross -0.079 R. Retired as specified; no neighbouring thresholds without a new mechanism | 0 registry trials; K +3 (program K 3,014 -> 3,017) | per variant: day-bootstrap LB90 net mean R > 0, real-minus-twin Bonferroni-3 p < 0.10, >= 5 of 8 years, n >= 100 |

## A25. Tick-proxy microstructure at 1m (microstructure researcher, 2026-11-01)

Source: research-director brief 2026-11-01 (tick-level mechanisms not yet tried). Pre-registration
`prereg-2026-11-01-micro-tick.md`; screen 2010-2017, matched random twins, one look each, Holm over 3.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| MT-VAC/TIM/SRR | VAC: fade a fast 3-min move (>= 3 ATR1m) made while the minute's mean spread is >= 1.5 x its median (liquidity vacuum); TIM: 5-min continuation of a tick-volume-weighted 1m sign imbalance (abs I5 >= 0.6, activity >= 1.5 x median); SRR: fade a tick-burst first pierce of the prior-day high/low closed back inside within 5 min. 1.5 x ICM cost of record plus spread-state surcharge, fill at t+1 open | **failed** 2026-11-01, 0 of 3 (prereg `8472864a`; `micro-tick-screen-2026-11.md`): net mean R VAC -0.43 (gross +0.19, t 3.6; cost 0.62 R), TIM -0.44 (gross +0.005 on 28,054), SRR -0.29 (gross +0.07); none beats its twins at Holm-3; 1/0/0 of 8 years. Retired as specified at 1m resolution, SRR with R-14 | K +3 (explore screen, 0 registry trials; program K 3,017 -> 3,020) | growth-sprint section 2 bar over 3 variants; FAIL retires each as specified at 1m resolution (SRR with R-14) |

## A26. Retail gold setups (strategy researcher, 2026-11-01)

Source: owner 2026-11-01 "find strategies, setups, signals to add more". Pre-registration
`prereg-2026-11-01-retail-setups.md` (`9fc69477`), code `3779c53b`, report `retail-setups-screen-2026-11.md`. Seven of
the nine requested setups were already closed (FVG magnet E-2, order-block retest E-1, Asia sweep into London #88/R-14,
round-number magnet G-7, VWAP-band fades R-01, RSI/MFI divergence, box break + close + MFI R-15) and were not re-run.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| RS-1 | ICT silver bullet: first 5m FVG (>= 0.1 ATR15) completing 10:00-10:55 New York after the 09:30+ span swept the 00:00-09:25 ET high/low; limit at the near edge to 11:00, stop beyond the first two bars + 0.1 ATR15, 2 R, flat 12:00 ET | **failed** 2026-11-01: n 346, gross +0.08 R, net -0.05 R (LB -0.15), 4/8 years; vs twin +0.085 R/day, Bonferroni-3 p 0.068; longs +0.10 / shorts -0.22 R. Retired (R-RS) | 0 registry; K +1 | prereg s.3: n >= 200, net LB > 0, twin p x 3 < 0.10, >= 5/8 years |
| RS-2 | Supply/demand first retest, 1h: 1-3 base bars (body <= 50% of range, range <= ATR1h), departure body >= 1.5 ATR1h beyond the base; limit at the proximal edge, live 120 h, cancelled by a close beyond distal; stop distal + 0.1 ATR1h, 2 R, 48 h | **failed** 2026-11-01: n 323, gross -0.09 R, net -0.28 R (LB -0.41), 0/8 years; vs twin p 0.49 (Bonferroni-3). Retired (R-RS) | 0 registry; K +1 | as RS-1 |
| RS-3 | Inverse FVG, 15m: an FVG (>= 0.25 ATR15) closed through its far edge within 32 bars (setup 07:00-20:00 UTC) flips; limit at that edge for 16 bars, stop beyond the failed move + 0.1 ATR15, 2 R, 8 h | **failed** 2026-11-01: n 3,564, gross +0.01 R, net -0.10 R (LB -0.13), 0/8 years; vs twin p 0.15 (Bonferroni-3). Retired (R-RS) | 0 registry; K +1 | as RS-1 |

## A27. Meta-label filter over existing entry generators (ML researcher, 2026-11-01)

Source: owner direction relayed 2026-11-01 (learned filter over the high-volume generators). Admissible against R-07
only on the new information in `prereg-ml-meta-label-2026-11-01.md` s.0 (positioning + calendar PIT columns, both-sided
unbiased rows, net-R label at `cor-2026-10-31.2`). Screen 2010-2017, one look per model, holdout sealed. IDs carry the
ML- prefix so parallel lanes cannot collide on H numbers. Numbered A24 in the frozen pre-registration; renumbered
A27 at merge (A24-A26 landed first).

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| ML-M1 | A fixed LightGBM regressor on 40 PIT features (H-62 intraday base + COT/GLD + tier-1 calendar), trained on net R of every I0 decision taken both ways (15m, 1.5 ATR stop, 2 R, 96 bars), selects a top decile with positive net edge | **failed** 2026-11-01 (prereg `1eac5cb6`, code `ec68e028`; `meta-label-screen-2026-11.md`): top decile n 4,190, net +0.016 R (90% LB -0.034), gross +0.146 R, beats year- and side-matched random selection (-0.095, p 0.0005), 5/8 years, forward 2012-2017 net -0.015 R; 89% short (swap and cost-in-R selection); retired R-20 | K +1; 0 registry trials | top-decile net mean 90% LB > 0, beats a year- and side-matched random selection at p <= 0.025, >= 5/8 years, forward leg > 0; else FAIL -> R-20 |
| ML-M2 | The same model class over I2B `box_break_close` candidates (15m, the rule's structural stop, 2 R, 96 bars; amendment 1) | **failed** 2026-11-01: top decile n 313, net -0.513 R (LB -0.655), 1/8 years, forward -0.584 R; pool net -0.794 R (stop median $0.61, costs 0.5-0.75 R); retired R-20 | K +1; 0 registry trials | as ML-M1 |

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| ML-M1c | The ML-M1 model, features (h1_adx14 -> h4_adx14) and hyperparameters on 1h decisions (I0 schedule both ways, 1.5 x ATR14(1h) stop, 2 R, 24 x 1h), where costs are <= ~0.03 R; data-snooped from ML-M1 (director slot 2026-11-01) | **failed** 2026-11-01 (prereg `fba52087`, code `9f519d5d`; `meta-label-screen-2026-11.md` M1c): top decile n 4,192, net +0.032 R (90% LB -0.048), beats matched random (p 0.0005), forward +0.077 R, long +0.053 / short +0.024, but only 4/8 years (positive 2012-2015 only; positioning columns act as regime labels). Confirm not run; joins R-20 | K +1; 0 registry trials | M1's bar with control p <= 0.05, plus long and short top-decile net each >= 0 (n >= 30 each); pass -> one 2018-01..2025-09 confirm, else R-20 |

## A28. Cross-asset lead-lag, 1m (macro researcher, 2026-11-01)

Prereg `prereg-2026-11-01-cross-asset.md`; results `cross-asset-screen-2026-11.md`.

| ID | Hypothesis | Status | Trials | Reading |
|---|---|---|---|---|
| XA-X1/X2/X3 | Gold lags silver (X1), the USD (X2: USDJPY up, EURUSD down) or the three jointly (X3) on 1m: when the lead's 5m move implies a gold move >= 2 residual sd and gold has done at most half of it, trade gold the implied way, 15m time exit, 1.5 x ATR15 stop, 1.5 x ICM cost of record | **failed** 2026-11-01, 0 of 3 (prereg `f2daf9b1` + amendments `a1876e41`, `4851c5f1`; `cross-asset-screen-2026-11.md`): net mean R X1 -0.097, X2 -0.103, X3 -0.081, 0/8 years positive each; X3 beats its random twin by +0.019 R (gross +$0.03/oz, Bonf-3 p 0.004), about 1/8 of the cost. Retired as a standalone rule; returns only with tick data and a round trip under ~$0.05/oz | K +3 (explore, no registry trials; ~3,023 on the brief's base of 3,020; parallel lanes add their own) | per variant: net mean R > 0 and beats a 4-twin random-entry control, each day-bootstrap Bonferroni-3 p < 0.10, >= 5 of 8 years positive; all FAIL -> 1-15m cross-asset lead-lag on 1m bars retired |
