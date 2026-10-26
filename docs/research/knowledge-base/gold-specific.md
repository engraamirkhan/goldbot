# Gold-specific (XAUUSD)

Part of the [knowledge base](README.md). Format, grades and markers are defined there. This file is the gold layer
under the other topic files: where gold trades, when it moves, what it costs, and what drives it. The macro and
anomaly evidence is reviewed in full in `../state-of-the-art.md` (section B) and the practitioner checklist in
`../xauusd-trader-playbook.md`; this file summarises them and adds the microstructure facts the owner's notes rely on.

## 1. Where the price comes from

| Fact | Source | Grade | Implication |
|---|---|---|---|
| About $361bn a day traded in 2025: OTC (mostly London spot) about $180bn, futures about $174bn (COMEX about $114bn, Shanghai about $51bn), ETFs about $7bn | World Gold Council, https://www.gold.org/goldhub/data/gold-trading-volumes (via playbook 2.1) | **A** (industry data) | A spot CFD is a derived price; liquidity follows London and New York hours |
| London OTC was 78% of global turnover in 2000-2015; COMEX 7.7% | Batten, Lucey, McGroarty, Peat, Urquhart 2017, *PLoS ONE* 12(4) e0174232, https://pmc.ncbi.nlm.nih.gov/articles/PMC5407636/ | **B** | — |
| Neither London nor New York dominates price discovery; leadership switches over time | Lucey, Larkin, O'Connor 2013, *Applied Economics Letters*, https://research.ucc.ie/en/publications/london-or-new-york-where-and-when-does-the-gold-price-originate/ | **B** | Do not hard-code "London leads" or "COMEX leads" |
| A working paper argues New York futures lead in price discovery | Hauptfleisch, Putniņš, Lucey 2015, "Who sets the price of gold? London or New York", Auckland Centre for Financial Research WP (cited at https://journals.kantiana.ru/eng/vestnik/4452/13079/; published version *(unverified)*) | **C** | Consistent with the New York data slot being the day's biggest move |

## 2. Sessions, opens and auctions (DST-aware clocks)

Our session windows (`goldbot/data/calendar.py` `DEFAULT_SESSION_WINDOWS`): **Asia** 08:00 Tokyo (23:00 UTC the day
before) to 08:00 London; **London** 08:00 London to 08:30 New York; **New York** 08:30 to 17:00 New York. They
follow each city's own daylight-saving clock.

| Event | Local time | UTC (summer / winter) | Source | Notes |
|---|---|---|---|---|
| Shanghai Gold Exchange benchmark auctions | 10:15 and 14:15 Beijing | 02:15 and 06:15 (no DST in China) | CME Group, https://www.cmegroup.com/education/articles-and-reports/the-global-footprint-of-the-chinese-gold-market.html; Kitco, https://www.kitco.com/price/fixes/shanghai-benchmark | No study of their effect on the international price was found |
| London open (our window) | 08:00 London | 07:00 / 08:00 | calendar.py | Course notes treat London and New York opens as the key liquidity events |
| LBMA Gold Price auctions (the "fixes") | 10:30 and 15:00 London | 09:30 and 14:00 / 10:30 and 15:00 | ICE Benchmark Administration, https://www.ice.com/iba/lbma-gold-silver-price (playbook 2.9) | Electronic since 2015. The pre-2015 front-running edge is gone (Caminschi & Heaney 2014, *J. Futures Markets* 34(11), https://ideas.repec.org/a/wly/jfutmk/v34y2014i11p1003-1039.html; R-08) |
| US data slot (CPI, NFP, PCE) | 08:30 New York | 12:30 / 13:30 | playbook 2.10 | The biggest scheduled volatility point of the day |
| FOMC statement | 14:00 New York | 18:00 / 19:00 | — | FOMC dominates US-news-driven gold jumps (playbook 1.4 *(venue unverified)*) |
| COMEX Globex | Sun-Fri 18:00-17:00 New York, 60-minute daily break | 22:00-21:00 / 23:00-22:00 | CME fact card (playbook 2.10) | — |
| Broker daily break and swap | server 23:59-01:02 (`server_tz` Europe/Athens, UTC+3 summer / UTC+2 winter) | 20:59-22:02 / 21:59-23:02 | `goldbot/data/calendar.py`, `config/settings.yaml`; playbook 2.3-2.4 | Spreads spike into the close and after the reopen; Wednesday rollover charges three nights. **Playbook 2.4 gives 21:59-23:02 UTC for summer, one hour late on an Athens server clock**: fix it there |

## 3. When gold moves and what it costs

**Measured ATR(14) on our data** (`../atr-measured-2026-10-18.md`, Dukascopy mid, $/oz). Last 90 days to 2026-10-05:
15m **$8.0**, 1h $17.7, 4h $36.6, 1d $87.6. Volatility is about four times its 2021-2023 level (15m median $1.9-2.2
then).

15m ATR median by session, last 90 days: **Asia $7.7, London $7.6, New York $9.1** (2020-2026: 2.1 / 2.4 / 3.3).

**By UTC hour, 2026 to 10-05** (`../costs-binding-2026-10-18.md` section 2.3; Dukascopy spread, $/oz):

| UTC hour | Spread | ATR15 | Round trip in R at 1.0 × ATR15 (cost prior A) |
|---|---|---|---|
| 00-03 (Asia) | 0.75-0.77 | 9.3-10.3 | 0.11-0.12 |
| 05-06 (late Asia) | 0.67-0.75 | 8.7-8.9 | 0.12-0.13 |
| 07-11 (London) | 0.60 | 8.5-8.9 | 0.11 |
| 13-16 (London/NY overlap) | 0.64-0.66 | 10.1-12.1 | **0.085-0.10** |
| 20 | 0.70 | 9.0 | 0.12 |
| 21-23 (rollover) | 0.76-0.82 | 7.5-11.7 | 0.10-0.15 |

| Fact | Source | Grade | Our data |
|---|---|---|---|
| Gold's 5-minute volume is n-shaped through the day, peaking in the European-US overlap (about 11:00-17:00 GMT); volatility flat to about 12:00 GMT, a small rise to 14:00 GMT, then lower; spreads fairly flat with a rise around the 22:00 GMT break | Batten et al. 2017 (above), Thomson Reuters ticks May 2000-Apr 2015, gross | **B** | Matches our hourly table: the overlap hours are the cheapest in R |
| Intraday gold volatility is dominated by US macro releases | Cai, Cheung, Wong 2001, *J. Futures Markets* 21(3) 257-278, https://ideas.repec.org/a/wly/jfutmk/v21y2001i3p257-278.html; COMEX 1994-97 | **A** (with later work, `state-of-the-art.md` B2) | tier-1 blackout implemented |
| Tight-spread hours save about 0.01-0.05 R; a filter, not a strategy | `../costs-binding-2026-10-18.md` | our data | H-13 cost filter |
| Holidays and thin sessions widen spread relative to ATR | `../holiday-list-measurement-2026-10.md` | our data | H-A diagnostic (forward) |
| A trade needs gross R ≥ 0.71 $/oz ÷ stop $ to break even at the cost prior | `../tight-stop-entry-study-2026-10-18.md` | our data | About 0.09 R at 1.0 × ATR15 today |

## 4. Claims from the owner's notes that are specific to gold

| # | Claim | Course baseline (paraphrased) | External evidence | Grade | Our data |
|---|---|---|---|---|---|
| G1 | Gold sweeps obvious levels and recovers, so wick breaks do not count and stops need room beyond the level | Gold-specific emphasis in the sweep and triangle notes: only a close beyond a level counts; stop beyond the sweep extreme, not on the level | Stop clustering and cascades (Osler 2005, FX). Round-number barriers in gold (Aggarwal & Lucey 2007, *Rev. Financial Economics* 16(2), https://ideas.repec.org/a/wly/revfec/v16y2007i2p217-230.html; Lucey & O'Connor 2016, *FRL*). No study of sweep frequency in gold | **B** mechanism (FX); **B** barriers (gold) | **feature:** sweep columns. **pending: pa-claims** (sweep-and-reclaim base rates on XAUUSD). KB-2 |
| G2 | London and New York opens are where the day's direction is set | Implied by session focus; MTF trigger on the hourly close | Volume and volatility peak around market opens and the overlap (Batten et al. 2017). Directional effects at opens: none found net of costs. Our tests of session-open breakouts and London/NY intraday momentum found no gross edge | **B** for activity; **A against** (our data) for direction | **tested:** R-02 (#1, #7, #11, #13; reconfirmed #21-#25), R-05 (#18, #19, #26-#28). KB-6 (extreme timing as context) |
| G3 | Gold rises in Asian hours and falls in Western hours | Not in the notes; included because it is the best-known gold session claim | Hat-shaped intraday pattern (Donati & Jung, CBS MSc thesis, https://research.cbs.dk/en/studentProjects/gold-price-dynamics-around-the-clock/; grade C); in-holdout press (contaminated) | **C** | **tested:** H-04 #44: gross +0.043 R, t 3.09 on 4,025 events, **net −0.093 R: stop** (costs decide it). Do not re-test without a cheaper execution path |
| G4 | Intraday technical rules work on gold | The whole course assumes chart reading works on gold | Some long-window simple moving-average rules on intraday gold are significant in and out of sample, gross; none for silver (Urquhart, Batten, Lucey, McGroarty, Peat 2018, *JIFMIM* 52, 102-113, https://eprints.soton.ac.uk/411993; summary https://www.cxoadvisory.com/?p=27613). Short-term trend dead on small-tick contracts since about 2009 (Kurth et al. 2026, arXiv 2607.01550) | **B** gross; **B against** net | **tested:** R-03, R-04, R-05, R-06 (tsmom 1h: gross +0.061 R, net −0.079 R) |
| G5 | Avoid FOMC, NFP and CPI | Stay out close to the big three | Cai et al. 2001; Elder, Miao, Ramchander 2012, *JBF* 36(1) *(unverified)*: prices adjust within minutes | **A** (volatility) | implemented (blackout) |
| G6 | $100 and $50 levels matter more than arbitrary prices | Not in the notes | Aggarwal & Lucey 2007 (gold less likely to continue at $100 levels); Lucey & O'Connor 2016 (barriers at 0 and 00 endings) | **B** | **feature:** round numbers without the $100 step (D-5). H-05. KB-4 |

## 5. Macro drivers (context, daily frequency)

Summarised from `../state-of-the-art.md` B1 and B3; no new claims here.

- **Real yields:** strong inverse relation 2005-2021, broke in 2022-23 (correlation about 0.84 → about 0.03). Do not
  hard-code the sign. Grade R before 2022, practitioner after.
- **US dollar:** gold is priced in USD; broad-dollar moves are a daily conditioning input (H-06, H-32).
- **Central-bank buying:** more than 1,000 t a year in 2022-24, about 850 t in 2025; price-insensitive and reported
  months late. Context only.
- **Safe-haven demand:** gold hedges equity shocks for about 15 trading days (Baur & Lucey 2010) and reacts to
  geopolitical threats rather than acts (Baur & Smales 2020). H-07, H-15.
- **Positioning and flows:** COT speculative pressure (mixed for gold, weekly), ETF flows (daily). Features only
  (H-09, H-12).
- **Seasonality:** Indian festivals and Chinese New Year demand; the old "autumn effect" turned into a "winter effect"
  (survey R12). Features only.

## 6. What this means for goldbot

1. **Cost in R is lowest in the London-New York overlap** because ATR is highest there, not because the spread is
   lowest. Any 15m idea should be judged in those hours first.
2. **The documented gold-specific microstructure facts are about activity and barriers, not direction.** They belong
   in the cost filter (H-13), the range forecast (H-16) and the level features (H-05, KB-2, KB-4), not in a
   standalone direction rule.
3. **Missing and cheap:** the $100 step, the LBMA and SGE auction windows as time features (D-5), and a tier-2
   release calendar.

## Open checks

- Hourly spread and tick profile on the broker's own ticks (CB-0 step 6, after 20 trading days).
- Verify Elder et al. 2012 and the published version of Hauptfleisch et al.
- pa-claims: sweep-and-reclaim frequencies by session on XAUUSD.

## 7. Time structure, levels and indicators measured (2026-10-26)

From `../time-structure-2026-10-26.md` (discovery half 2010-2017, randomised controls, two cost cases). Claims rows for
the topic files are summarised here; the slice files hold the tables.

| Claim | Our data | Grade |
|---|---|---|
| Activity follows each exchange's own clock (NY data/COMEX 08:20-08:30 NY is the day's activity event; DST gap weeks follow New York) | measured: sessions.md 4, README 6.9 | A (our data) |
| The LBMA fixes have no activity spike of their own after 2015 | measured: the 15:00 London spike is 10:00 New York | B (our data) |
| Session opens set direction | not in the form tested: 24 ORB variants ≈ random-start ORB, net −0.12..+0.03 R | A against (our data) |
| Fibonacci levels act as support/resistance | not better than distance-matched random levels (0/26 cells after BH) | A against (our data) |
| Key levels (PDH/PDL, PWH/PWL) break more often than random levels | +3 to +8 points break share; no magnet; retests worse than random | B (our data) |
| Order blocks / FVGs | OB adds +0.04..+0.08 R over random brackets (CI includes 0); FVG revisit +2 pts | C against (our data) |
| 30/60/90-day windows predict direction | no (abs(t) < 1.2); they predict volatility | A against for direction (our data) |
| FOMC and NFP days carry more range than RV20 predicts | +0.40 / +0.37 log points (t 3.0 / 2.7) | B (our data) |
| Tick volume measures traded size | no: about 40-60% clock shape, units drift 4x across years; COMEX volume needed | B against (our data) |
