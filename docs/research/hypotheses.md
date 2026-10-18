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

**Rank** (here and in A2) is the unified north-star rank across H-01..H-23, set by the research director on
2026-10-10 (`research-plan.md` §3–4; reasons in A3). Q1 2027's running order is fixed by `preregistration-2027Q1.md`
and does not follow this column.

| Rank | ID | Hypothesis | Status | Rationale | Data | Horizon | Expected gross edge vs cost | Trial cost |
|---|---|---|---|---|---|---|---|---|
| 7 | H-01 | **Slow TSMOM**: 1d vol-scaled trend signal (1–12 months), 4h execution, 5–20-day hold, long and short, measured swap | queued in the Q1 pre-registration (AT RISK until the 2026-11-13 recount); **retire-only**: its lower bound (152) is below the 1,000 pass floor, so it can read at best inconclusive and no H-01 follow-up can arise (2026-10-17) | The only trend horizon with replicated support (MOP 2012, Hurst et al. 2017). The target dwarfs the spread | D | 1d signal, 4h bars | +0.05–0.10 R gross vs about 0.05 R spread at this target size, plus swap on longs (≈0.05–0.10 R over 10 nights). Likely depends on the short side | 1 |
| 6 | H-02 | **Feature discovery**: one pre-registered screen of ~120 bulk point-in-time features (survey §4a) on a 4h triple-barrier label, with fold-internal stability selection and permutation importance | proposed (survey §4b); **tooling ready** (`research_pass.py --discover`, M37/M38; not yet run) | Tests the whole indicator universe for the price of one trial instead of about 35 | D, D+, F, C, E | 4h | Unknown. Continue if out-of-sample AUC ≥ 0.53 (lower bound > 0.50) and the pre-set rule gives net mean R > 0 on ≥ 1,000 events | 1 (later survivor trials use N = registry + K_eff ≈ 35 in the DSR) |
| 14 | H-03 | **Gold VRP**: long when GVZ² − HAR-RV² is high (sign fixed in advance from Nguyen et al. 2017), 5–20-day hold | proposed; trial only if it survives H-02 | Jump/crash-risk premium; documented out of sample for gold at 1–24 months (survey #72) | F (GVZCLS, free) + D | 1d signal, 4h bars | Literature effect at monthly horizon; haircut 50% (McLean–Pontiff). Target ≫ costs; long side pays swap | 1 if it survives |
| 9 | H-04 | **Asia-session drift**: long about 00:00–07:00 UTC, flat before London | pre-registered (SoA C1-2); implemented as `asia_drift`, active (not in `research.retired_families`), not a founder until it passes; no trial run. If it reads **continue**, the Q1 open budget funds O-7 (H-24), O-8 (model research) and O-9 (H-C) (2026-10-17) | Asian physical/ETF demand vs Western selling; intraday, so no swap | D | 1h | ≤ 0.03% per session vs about 0.01% round trip. Thin: 2–3× costs at best; about 4,000 events | 1 |
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
| 1 | H-16 | **Opportunity score = day-range forecast.** In-fold HAR on stub-free daily ranges, plus GVZ ÷ YZ20, tier-1 count today, prior-day range ÷ ATR20(1d), NR4/NR7/inside-day, weekend gap ÷ ATR(1d). Refreshed at London+60 and NY+60 with the early-session noise ratio. No direction terms | pre-registered for the **Q1 2027 open budget** (row O-1, 2026-10-17) if `calendar-v1` and its counts exist by 2027-01-29, else **Q2 2027**; BACKLOG 30a and E-4 merged | Volatility persistence (Corsi 2009, R); GVZ adds to HAR (Luo et al. 2016; Wei et al. 2020, FRL, Chinese futures); release days are high-volatility days (Cai et al. 2001; Elder et al. 2012) (OD 1.1, 1.2, 1.8) | D, F (GVZCLS, `asof_join`; D−2 at the London open), calendar | daily, refreshed intraday | Not a return trial. **Reading rule:** continue if out-of-sample Spearman ρ(score, realised range ÷ ATR20) ≥ the HAR-only baseline + 0.05, AND the top-quintile mean range ≥ 1.5× the bottom quintile. Fail: the day factor uses HAR alone, and GVZ, events and compression are retired as range inputs | 1 (counted for honesty, though it tests range, not returns) |
| 2 | H-19 | **Stand-aside overlay:** every Q2 trial reports net R by score band (low / mid / high) and the effect of skipping the low band | proposed (**Q2 2027**, overlay) | Cost in R falls as ATR rises (SoA A7). Lundström 2013 WP: ORB returns ≈ 150–200 bp/day apart across volatility states (S) | D | all | Not alpha. It raises net R per trade by skipping trades whose range cannot pay costs | 0 (as H-13) |
| 4 | H-17 | **Trend-day continuation with a runner.** On days with `opp_score` in the top half (H-16's frozen model), at each 15m close between London+60 min and NY+120 min (DST-aware local times): if \|close − day open\| exceeds the noise boundary (14-day mean \|move from the open\| at the same time of day × 1.0), enter that way, at most one trade a day. Stop 1.5 ATR(15m). Exits: **V0** target 3 ATR vs **V1** 50% at 2 ATR, chandelier 2.5 ATR armed at 1.5 ATR, cap 10 ATR, flat 16:30 New York | pre-registered for the **Q1 2027 open budget** (rows O-2/O-3, 2026-10-17) if E-3, BACKLOG 31, `calendar-v1` and >= 1,000 strict events exist by 2027-02-12, else **Q2 2027**; overlap with R-02/R-05 ruled (Q-1) | Zarattini, Aziz, Barbon 2024: noise-area trend days with trailing and clock exits, SPY net Sharpe 1.33 (S). Kaminski & Lo 2014: trailing exits pay only with serial correlation (S). New vs R-05: ex-ante range conditioning and a runner exit | D | 15m | Unknown. Unconditional NY momentum was −0.006 R gross (R-05). Cost 0.15–0.25 R historically on 15m, lower on high-ATR days. Events ≈ 1,000–1,500 (*estimate*; count from events only before freezing). **Reading rule:** continue only if all hold: gross t ≥ 2 on ≥ 1,000 events; V1 net mean R > 0; ≥ 3 positive net years incl. 2021 or 2022; V1 net ≥ V0 net; the low-score half (shadow) has lower gross R than the high half. If the conditioning makes no difference, the families stay retired. £/day metrics reported, not in the rule | 2 (V0, V1) |
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
| H-TVR1 | 15m: internal CHoCH in the swing-trend direction with a same-side FVG in the last 3 bars; stop 1.0 / target 2.0 ATR15, 32 bars | **pre-registered** 2026-10-18; blocked on the `smc_structure` specialist (implementer) and on funding (owner: 2026Q4 53 → 55, or director: Q1 free reserve 2 of 3) | 1 |
| H-TVR2 | 15m: swing BOS continuation (50-bar leg pivots); stop 1.0 / target 2.0 ATR15, 32 bars | **pre-registered** 2026-10-18; same blockers | 1 |
| H-TVR3 | H-TVR1 on 1h (stop 1.0 ATR1h, 24 bars; needs M2 sizing) | proposed, **conditional** on H-TVR1 PASS or LEAD | 1 if triggered |
| D-TVR | Add the `smc_replica` columns to H-02 as one group (amendment before H-02 runs) | proposed; needs research-director and quant-reviewer sign-off | 0 (K_eff +1) |

**Tested universe (features, no trial).** These are opt-in `smc_replica` columns and reach a model only through
D-TVR or a pre-registered row: `bos/choch_{bull,bear}_{internal,swing}`, `trend_swing`, `trend_internal`, `eqh`,
`eql`, `fvg_bull`, `fvg_bear`, `mfi`, `mfi_os_cross`, `mfi_ob_cross`, `range_f1_dist_atr`, `range_f2_dist_atr`,
`sr_break_up`, `sr_break_dn`, `candle_class`, `ma_stack_sma_ema`.

**Not trialled, by decision:** money-flow cross rules (R-01 reversion, or no mechanism) and box-break rules (R-04
breakout). The order blocks cannot be replicated: they are off in the owner's settings (data request D-3).

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

## C. Multiple-testing ledger

| Quarter | Registry trials | Screens inside trials (K_eff) | DSR trial count to use for the next trial |
|---|---|---|---|
| Q4 2026 | #1–#20 | 0 | 20 |
| Q4 2026, owner-directed batch (2026-10-18; **dispatched**, B01-B04 = #21-#35 run; B01-B03 12/12 FAIL, R-02/R-05 retirement reconfirmed 2026-10-18; readout and registry defect D-1 in `batch-2026-10-18-readout.md`) | #21–#53 if all 33 run (2026Q4 budget override 53, settings) | 0 | **53** (gate-1 t ≈ 3.94). H-04 and H-01 move here from Q1, so the Q1 reservation becomes 6 and the end-of-Q1 projection is about 116 (t ≈ 4.2) |
| Q1 2027 (planned) | #21–#40 at most (8 reserved: 4 queued + 4 contingent H-02 survivor slots; 12 open under the E-5 cap, allocated 2026-10-17 as 9 conditional trial rows (O-1..O-10 without O-9, now a 0-trial diagnostic) + 3 free reserve; corrected 2026-10-17 to settings) | H-02: K_eff = features screened when survivors go forward as features, groups screened only for whole-group survivors (recorded as `k_eff` on the discovery row; `registry.n_trials_effective` adds it) | 20 + trials run so far + K_eff for any survivor of H-02. Projection, end of Q1: up to 40 registry rows + H-02 K_eff (about 10: **H-02 moved to `survivor_unit: group`, quant ruling Q-H02U 2026-10-17**; it was about 300 at the feature unit) + H-14 K_eff (about 12) ≈ 60; the gate-1 DSR then needs a model-filtered t of about 4.0 (was about 4.6). *Updated 2026-10-17:* H-14's K_eff is about 16 under the O-4 group-map amendment (14 groups verified on `f-079deacc61`, plus `liquidity` and `activity`), so the adopted end-of-Q1 case is about 66 (t ≈ 4.03, still 4.0). *Updated 2026-10-18 (research director):* N1 `tod_periodicity` adopted as one more O-4 group, so H-14 about 17 and the end-of-Q1 case about 67 (t still about 4.0); no registry trial |
| Owner-indicator replica (2026-10-18; `prereg-tv-replica-2026-10-18.md`; pre-registered, **not dispatched**, unfunded) | +2 (H-TVR1, H-TVR2) if funded; +1 if H-TVR3 triggers | D-TVR: K_eff +1 inside H-02 if adopted | Registry `n_trials_effective` at the verdict date, **never below 55** |
| Q2 2027 (draft) | #41–#60 at most | SW-0 if it runs: K_eff ≈ 16 (about 8 groups × 2 sides, `survivor_unit: group`); H-14 survivors and Q1 follow-ups add 1 each | **About 77 + the other Q2 trials** (the swing lane's projection: 60 + SW-0's row + its K_eff ≈ 16). On the updated Q1 base of about 66 the same sum is about 83 + the other Q2 trials. Either way the gate-1 bar is a t of about 4.1. Nothing in the lane lists adds to K until a row runs |
| Q4 2026, H-25 (pre-registered 2026-10-18, **not dispatched**; `prereg-h25-precision-stop-2026-10-18.md`) | +1 (#56 in budget order; needs the override 55 -> 56) | `k_eff: 19` on its row (tight-stop study's 19 entry variants) | **Registry `n_trials_effective` at the verdict, never below 75** (about 85 once H-02's K_eff is recorded); gate-1 t about 4.1 |

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
