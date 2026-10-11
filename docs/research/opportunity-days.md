# Opportunity days and runner exits (owner north-star)

Written 2026-10-10 by the strategy researcher, base `0e1fd94`. It answers the owner's north-star: "the whole purpose
of this trading system is to make £50 or more a day, but less if conditions are not suitable; usually there are days
in the week where the market can provide opportunities". The account is **£1,000**.

It builds on `state-of-the-art.md` ("SoA"), `indicator-survey.md` ("survey #n"), `xauusd-trader-playbook.md`
("playbook"), `hypotheses.md` (H-nn, R-nn) and ADR 0004. Code citations are `file:line` on `1c556e6`.

**Grades** (as in the playbook): **R** replicated · **S** single study or official data · **P** practitioner
consensus, untested · **F** folklore, no evidence found. "Net" means the source charges realistic costs.
*(unverified)* means we did not read the full text; the claim comes from an abstract or a secondary summary.

**No trial was run. No trial budget was spent or reserved. The holdout (2025-10-01..2026-09-30) and the Q1 2027
reserved queue were not touched. No market data was read: the only local bars are synthetic (`data_dryrun/`).**

---

## Owner summary

- **Predicting *how much* gold will move today is well supported. Predicting *which way* it will move is not.**
  Recent volatility, gold's implied volatility (GVZ) and scheduled US releases (NFP, CPI, FOMC) all forecast the
  day's range. No intraday direction signal for spot gold has survived our tests or the literature's.
- So the plan is: **(1)** a daily *opportunity score* that forecasts the day's range, **(2)** stand aside on
  low-score days and allow up to 1.5× normal size on high-score days, within the existing limits, and **(3)**
  *runner* exits that let a winner run on a trend day instead of banking a fixed 2–3 ATR target.
- **What £50 a day means at £1,000.** The rails allow exactly **one 0.01-lot position** at £1,000. That is 1 oz of
  gold, so £50 needs a **~$66 move in our favour**: about one full average day's range, or **6R on a 15-minute
  trade**. A £50 day is therefore possible **only on a trend day, caught early and held**. It is not an average.
  At the best edge we can hope for (0.2 R net a trade, about one trade a day), the *average* is **~£1–3 a day**.
  The rest of the time the score keeps us out, or a loss costs about £8 (at most ~£17 on a day).
- **At £1,000 only 15-minute trades fit the 1% cap**, and only while 15-minute ATR is below about $8.90. On the
  most volatile days the minimum lot risks more than 1%, so the gate refuses it. That is the gate working: the
  account is too small for the biggest days until it grows (ADR 0004 milestones).
- We will **measure this as a £/day distribution**: how often a day reaches £50, how many of the big days we
  caught, and the worst day and the drawdown. These are ADR 0004 section 6's north-star KPIs (BACKLOG 26), applied
  here to every research trial (BACKLOG 33).
- Five candidate hypotheses go to the **Q2 2027** pre-registration (3–4 trials). Six engineering items are added
  to the backlog, folded into ADR 0004's items: features and the allocator input under 30 (30a, 30b), runner
  mechanics under 31, projected daily caps under 29, and new items 32 (gate EV) and 33 (trial evaluation). A safety
  test is added to 25. None of them loosens RiskGate. The 29 addition tightens it.

---

## 0. What the north-star means under goldbot's rails

| Quantity at £1,000 (≈ $1,330 at GBPUSD 1.33, gold $4,150; ADR 0004 assumptions) | Value | Source |
|---|---|---|
| Combined-notional cap | 0.30 × 20 × equity = **$7,980** → at most 1.92 oz → **0.01 lot** (0.02 lot = $8,300 is refused) | `goldbot/risk/gate.py:49-52`, `:286-290` |
| Margin for 0.01 lot at 1:20 | $207.50 → margin level 641% (floor 300%) | `gate.py:175-198`, `:278-283` |
| £ per $1 gold move | 1 oz → $1 → **£0.75** | arithmetic |
| £50 | **$66.50** of gold move captured | arithmetic |
| 15m stop (1.5 × ATR(15m) ≈ $7.30, *estimate*) | $10.90 → **0.82%** of equity (£8.20) → £50 = **6.1 R** | ADR 0004 §1 |
| 1h stop (≈ $21.90) | 1.65% → refused by any rule (1% clamp) | `gate.py:266-276` |
| Today's min-lot rule | today the gate computes the min lot at 1.09% because USD and GBP are mixed (ADR 0004 A3), so it is **never allowed**. After BACKLOG 28: allowed iff 0.82% ≤ 1.2 × 0.5% × m, i.e. **m ≥ 1.37** | `gate.py:275-276` |
| With BACKLOG 25 and 28 (min-lot cap 1%) | allowed iff stop ≤ $13.30, i.e. **ATR(15m) ≤ $8.87** | ADR 0004 §2 |
| Daily limits | Effective **£15** (supervisor 1.5%; account 2% = £20; absolute £50, BACKLOG 29). The checks use the loss so far, so two full losses (~1.69%, ~£17) end the day **past** the 1.5% cap | `gate.py:231-234`, `goldbot/risk/supervisor.py:58-63`, ADR 0004 §3 |
| Average daily range, 1.0 × ATR(1d) | ≈ $70 (*estimate*, not measured) | ADR 0004 assumptions |

Three consequences the design must respect:
1. **The size lever cannot add lots.** At £1,000 the multiplier does not change the position: it is always one
   minimum lot or nothing. Once the currency fix (BACKLOG 28) lands, and without the min-lot exception (BACKLOG
   25), the 1.2× rule makes the multiplier decide only *whether* a 15m trade is allowed: at m < 1.37 it is refused,
   at m = 1.5 it is allowed. So a 1.5× day weight acts as an on/off switch at this equity (section 3.2; a test is
   added to BACKLOG 25).
2. **Opportunity days raise ATR, and a higher ATR raises the minimum lot's risk.** On the days with the largest
   expected range, the 1.5 × ATR stop can exceed 1% and the trade is refused. A tighter structural stop (for
   example 1.0 ATR) is a strategy choice that changes R and cost-in-R. It is not a gate change, and it needs its own
   test.
3. **Fixed USD costs favour high-volatility days.** Round-trip cost is fixed in USD per lot, so its share of R falls
   as ATR rises. Cost in R was 2–3× higher in the low-volatility years 2012–19 (SoA A7). This is the strongest
   *economic* reason to stand aside on quiet days, and it holds even if no direction signal exists.

---

## 1. Evidence review: predicting high-opportunity days and sessions in advance

"PIT" = whether goldbot can know the input before the decision. "Predicts" separates **range** (volatility, size of
move) from **direction**.

| # | Predictor | Predicts | Evidence | Grade | PIT for a pre-London decision | goldbot today |
|---|---|---|---|---|---|---|
| 1.1 | **Tier-1 US releases (NFP, CPI, FOMC, PCE)** | **Range: yes.** Direction: no documented drift, except gold keeps adjusting for more than 5 min after FOMC shocks, and more after dovish surprises | COMEX gold intraday volatility is driven by US releases: employment, GDP, CPI and personal income lead among 23 announcements (Cai, Cheung, Wong 2001, *J. Futures Markets* 21(3) 257–278, https://ideas.repec.org/a/wly/jfutmk/v21y2001i3p257-278.html; COMEX 1994–97, gross). The 08:30 ET set, NFP above all, has the largest effect, and strong news is negative for gold (Elder, Miao, Ramchander 2012, *JBF* 36(1); 2002–08 *(unverified)*). FOMC: adjustment continues past 5 min (Awartani, Hussain, Virk 2024, *Int. Rev. Financial Analysis* 95, https://pure.kfupm.edu.sa/en/publications/how-do-the-gold-intra-day-returns-and-volatility-react-to-monetar/ *(unverified)*). US surprises carry post-announcement effects in Indian gold futures at 5 min (Sobti, IIMA conference paper, https://iima.ac.in/sites/default/files/2022-11/Paper-Neharika%20Sobti.pdf, S). In bonds, announcement-day volatility does not persist into later days (Jones, Lamont, Lumsdaine 1998, *JFE* 47(3) 315–337, https://ideas.repec.org/a/eee/jfinec/v47y1998i3p315-337.html) | **R** (range) · **S** (direction, FOMC only) | **Yes.** The schedule is known days ahead | Calendar features `min_to_next_tier1` / `min_since_last_tier1` (`goldbot/features/session.py:56-81`). Blackout −15/+30 min in the gate (`gate.py:215-216`). Allocator zeroes ±30 min (`goldbot/allocator/rules.py:43-49`). **No "event today" day flag, no event count per day** |
| 1.2 | **Volatility regime: recent realised volatility (HAR)** | **Range: yes, strongly.** Direction: no | Volatility is persistent. HAR-RV (daily, weekly and monthly RV) reproduces the persistence and forecasts well (Corsi 2009, *J. Fin. Econometrics* 7(2) 174–196, https://academic.oup.com/jfec/article/7/2/174/856522). ORB returns differ by about 200 bp/day between the highest and lowest volatility states in crude oil and about 150 bp in the S&P 500 (Lundström 2013, Umeå Economic Studies 861, https://ideas.repec.org/p/hhs/umnees/0861.html, WP, gross *(unverified)*) | **R** (range); **S** (strategy return depends on volatility state) | **Yes** | `atr_ratio_14_100` (`goldbot/features/technical.py:91-97`); `rv_20`, `rv_ratio`, `vol_tercile` (`technical.py:99-110`); GK/RS/YZ and the YZ term structure (`goldbot/features/survey.py:96-116`); expected move in costs (`survey.py:221-238`). All on the decision bars. **No daily HAR forecast of today's range** (grep finds none) |
| 1.3 | **Expansion after compression (NR4/NR7, inside day, low ATR percentile)** | Claimed: range expansion. Direction: none, by the practitioners' own account | Crabel 1990 (book) popularised NR4/NR7. Practitioner backtests across 42 futures exist (Oxford Strat blog, https://oxfordstrat.com/?p=16086). We found **no peer-reviewed test**. Note the tension with 1.2: persistence says a quiet day predicts another quiet day. "Compression then expansion" is volatility mean reversion over longer horizons, which HAR already captures | **P/F** | **Yes** (from completed daily bars) | `compression_8_96` on the decision bars (`technical.py:244-256`); retired breakout family (R-04). **No daily NR4/NR7/inside-day flag** |
| 1.4 | **Day of week** | Range: indirectly (Friday NFP, Wednesday FOMC). Direction: fragile | Friday positive and Tuesday negative across the Tokyo/London/NY gold markets (Lee et al. 2016, *Banks and Bank Systems*, https://businessperspectives.org/journals/banks-and-bank-systems/issue-190/weekday-effects-on-gold-tokyo-london-and-new-york-markets). Thursday rather than Monday/Friday (Yu & Shih 2011, *IMFI*, https://businessperspectives.org/images/pdf/applications/publishing/templates/article/assets/4109/imfi_en_2011_02_Yu.pdf). Monday day-session returns lower (Blose, https://scholarworks.gvsu.edu/fsdg/200). Monday/Friday patterns in GVZ, not in gold itself (Qadan & Idilbi-Bayaa 2021, *Resources Policy*, https://cris.haifa.ac.il/en/publications/the-day-of-the-week-effect-on-the-volatility-of-commodities-2/ *(unverified)*) | **S, conflicting** | Yes | `dow` (`session.py:26`). Weekday direction effects retired (R-08). **Recommendation:** use the event calendar (1.1), not the weekday. Most of any weekday range effect is the release schedule (our inference, untested) |
| 1.5 | **London–NY overlap / intraday periodicity** | Range *within* the day (when the move happens). Direction: no | Intraday periodicity strongly shapes gold volatility (Cai et al. 2001). Gold-specific periodicity work is thin (Batten, Lucey, McGroarty, Peat, Urquhart 2017, https://reading-clone.eprints-hosting.org/79175/1/BattenLuceyMcGroartyPeatUrquhart2017.pdf). Liquidity follows London OTC and COMEX hours (WGC volumes, playbook 2.1). Lee et al. find Tokyo the most volatile by daily-session returns, which conflicts with practitioner belief | **R** (periodicity) · **P** (the overlap itself) | Yes (clock) | Fixed-UTC sessions (`goldbot/data/calendar.py:104-105`). DST-aware local sessions exist only for `intraday_session` (`calendar.py:295-296`, `goldbot/features/momentum.py:60-71`). D-1 / BACKLOG 24 makes the rest DST-aware |
| 1.6 | **Opening-range size / early-session realised range** | **Range for the rest of the session: yes** (persistence). Direction: retired for us (R-02) | In China gold futures, realised volatility in earlier intervals forecasts the last half-hour's (2023 study, *Finance Research Letters* 58, https://ideas.repec.org/a/eee/finlet/v58y2023ipas154461232300394x.html, S; volatility only). ORB profitability in futures: Holmberg, Lönnbark, Lundström 2013, *FRL* 10(1) 27–33, https://ideas.repec.org/a/eee/finlet/v10y2013i1p27-33.html (gross *(unverified)*). In stocks, ORB works on "stocks in play" with high relative volume (Zarattini, Barbon, Aziz 2024, SSRN; summary https://www.cxoadvisory.com/individual-investing/intraday-trading-of-overactive-stocks-via-opening-range-breakout/ *(unverified)*). An independent replication of the related QQQ rules on index futures and CFDs found the gross edge reproduced and **net ≈ 0** (blog, https://www.mql5.com/en/blogs/post/776235, P) | **S** (range) · **R-against** (direction for gold: our 4 trials) | Yes, intraday (after the first n minutes) | `im_ldn_*`, `im_ny_*` (`momentum.py:60-71`); session high/low (`goldbot/features/trader.py:100-127`). **No "early range ÷ typical range at this time of day" feature** |
| 1.7 | **Overnight / weekend gap** | Range: via persistence (a big gap is a big return). Direction: overnight-intraday reversal in equities, weak in gold | Tug of war between overnight and intraday returns (Lou, Polk, Skouras 2019, *JFE*, https://eprints.lse.ac.uk/87481/, equities, M). Gold: Blose; Donati & Jung thesis (SoA B2, W). Weekend gap episodes (playbook 2.5) | **W** | Yes (at the open) | `gap_atr`, `last_gap_level_dist_atr` on the decision bars (`goldbot/features/structure.py:146-162`). **No daily weekend-gap feature in ATR(1d)** |
| 1.8 | **Implied vs realised volatility (GVZ)** | **Range: yes, beyond HAR.** Direction: the variance risk premium predicts monthly returns, not daily | GVZ improves next-day volatility forecasts for Shanghai gold futures and beats jump measures (Luo, Qin, Ye 2016, *FRL*, https://scholars.ln.edu.hk/en/publications/the-information-content-of-implied-volatility-and-jumps-in-foreca/). GVZ and the silver index improve HAR forecasts in and out of sample (Wei et al. 2020, *FRL* 35, https://ideas.repec.org/a/eee/finlet/v35y2020ics1544612319305793.html). VRP and returns at 1–24 months (Nguyen et al. 2017, survey #72) | **R** (two studies, Chinese futures; not yet tested on spot XAUUSD) | **Partly.** GVZCLS for day D is available the next business day at 16:30 ET (`goldbot/data/macro.py:55-60`, `:74`), so at the London open of day D the latest GVZ is **D−2's close** | `macro_gvz`, `macro_gvz_chg20` (`goldbot/data/macro.py:171-172`), joined by `asof_join` (`goldbot/features/macro.py:43-58`). **No GVZ ÷ realised-volatility ratio** |
| 1.9 | **Prior-day range and trend-day signatures** | Range: yes (persistence). "Trend day" (open near one extreme, close near the other): folklore as a forecast. An *intraday* trend-day detector has net support in SPY | Persistence: Corsi 2009. Zarattini, Aziz, Barbon 2024 ("Beat the Market", SFI RP 24-97, https://ideas.repec.org/p/chf/rpseri/rp2497.html): a move outside the "noise area" (the 14-day average move from the open at the same time of day) flags a trend day. Trailing exits at the noise boundary and VWAP, flat at the close. SPY 2007–2024, **net of costs**, Sharpe 1.33 (backtest, single study, equities) | **R** (range) · **S** (intraday detector, equities) · **F** (trend-day forecast) | Prior day: yes. Noise area: intraday | Previous-day high/low distances (`trader.py:122-123`, `:130-162`). **No prior-day range ÷ ATR(1d), no noise-area feature** |
| 1.10 | **Intraday momentum (first half-hour → last half-hour)** | Direction for the last 30 min where a close auction and gamma hedging exist | SPY (Gao, Han, Li, Zhou 2018, *JFE* 129(2), SoA A4, R). 60+ futures, mechanism gamma hedging, reverses over later days (Baltussen, Da, Lammers, Martens 2021, *JFE* 142(1), https://www3.nd.edu/~zda/intramom.pdf, R). Chinese commodity futures (2020, *JFM* 40(4), https://ideas.repec.org/a/wly/jfutmk/v40y2020i4p632-650.html, S). **No direct test on gold returns found.** Ours: London −0.028 R, NY −0.006 R (R-05) | **R** elsewhere · **N** for spot gold | Yes | Retired (R-05). Keep only as a trend-day *feature* (1.9), not a rule |
| 1.11 | **COT / positioning extremes** | Claimed: crowded positioning unwinds (direction). For volatility: nothing found | Speculative pressure across commodities (Fan et al., survey #67, gross). Gold mixed: Rouwenhorst & Tang 2012 find no prediction (SoA B3). On extremes as a *volatility* predictor we found vendor and blog claims only (for example https://metalcharts.org/cot/gold) | **S, mixed** (direction) · **F** (volatility) | Weekly; Tuesday data released Friday 15:30 ET | `pos_cot_*`, opt-in (`goldbot/features/positioning.py:1-27`); H-09. **Too slow for day selection** |
| 1.12 | **DXY / real-yield shocks** | Range: plausible spillover from a large overnight USD or yield move (our inference). Direction: contemporaneous, not predictive; the real-yield link broke in 2022 | Contemporaneous USD and real-yield relations (playbook 1.1, 1.2; SoA B1). Gold responds to monetary shocks (IMF WEO Oct 2023 annex, https://imf.org/-/media/Files/Publications/WEO/2023/October/English/ch1onlineannex.ashx, monthly horizon). We found **no intraday study of USD or yield shocks predicting gold's later range** | **R** (contemporaneous) · **W** (predictive) | **No intraday USD data** (survey #62, D+). FRED series are daily and lagged | DFII10, DTWEXBGS daily (`goldbot/data/macro.py:21-29`, `:66`); `macro_real_yield_chg20`, `macro_dollar_chg20` (`goldbot/data/macro.py:166-170`) |

### What the evidence supports

- **Ex-ante range predictors, best first:** recent realised volatility, i.e. HAR (R); GVZ on top of HAR (R, but
  only on Chinese futures and stale by 1.5 days for us); tier-1 release days (R); intraday periodicity, i.e. *when*
  in the day (R); early-session range for the rest of the session (S); prior-day range (persistence, R). NR7 and
  inside days (P/F), weekday (S, conflicting) and COT for volatility (F) come last.
- **Ex-ante direction predictors for spot gold intraday:** none robust. Our 4 session-open, 2 intraday-momentum, 1
  mean-reversion and 3 breakout trials agree (R-01 to R-05).
- **Implication.** A range forecast creates value only if (a) quiet days are skipped, because costs in R are larger
  there, and (b) on loud days the exit lets a correctly-signed trade run far enough to be paid for the range.
  Without an entry edge, (b) only widens the outcome distribution. The mean stays below zero by the cost. **Section
  2 cannot rescue an entry with no gross edge.** That is the single most important caveat in this document.

---

## 2. Exit research: runners vs fixed targets

| # | Finding | Source | Grade | For goldbot |
|---|---|---|---|---|
| 2.1 | Stops (including trailing) add value **only when returns are serially correlated** (momentum). Under a random walk they cut expected return | Kaminski & Lo 2014, *J. Financial Markets* 18, https://dspace.mit.edu/handle/1721.1/114876 (theory and futures/equity tests) | S | A runner exit pays only on days with intraday persistence, which is exactly the trend day. So runner exits belong *conditional on* the opportunity score, not everywhere |
| 2.2 | Tight stops trail buy-and-hold **because of trading costs**. Outperformance needs high serial correlation. Downside reduction is modest | Lo & Remorov 2017, *J. Financial Markets* 34, 1–15, https://dspace.mit.edu/handle/1721.1/107017 (US stocks 1964–2014, net) | S | Trailing too tight raises the exit count and cost. Trail distances must be several times the round trip |
| 2.3 | Trailing-stop gain distribution and the optimal stop distance for a drifting random walk | Glynn & Iglehart 1995, *Management Science* 41(6) 1096–1106, https://pubsonline.informs.org/doi/10.1287/mnsc.41.6.1096 (theory) | S (theory) | The optimal trail widens as drift falls relative to volatility. Set trail distance from the forecast range, not as a constant |
| 2.4 | Stop-loss rules do not add value to a 200-day MA rule on the S&P 500 (1952–2011, 0.2% switching cost) | Clare, Seaton, Smith, Thomas 2013, *J. Asset Management* 14(3), https://ideas.repec.org/a/pal/assmgt/v14y2013i3d10.1057_jam.2013.11.html | S | Slow-trend exits: keep simple (relevant to H-01, not to intraday runners) |
| 2.5 | Across simulations, stops change average returns little; trailing stops mainly **reduce volatility** | CXO Advisory summary of academic simulations, https://www.cxoadvisory.com/technical-trading/using-trailing-stop-losses-to-reduce-risk | S (secondary) | Expect a runner to change the *shape* (fewer, larger winners), not the mean, unless 2.1 holds |
| 2.6 | Intraday trend-day strategy with **trailing exits at the noise boundary or VWAP and a flat at the close**: net Sharpe 1.33 | Zarattini, Aziz, Barbon 2024 (row 1.9) | S (net, single study, SPY) | The best-documented template for "enter late on a confirmed trend day, trail, flat by a clock time". Untested in gold |
| 2.7 | Chandelier exit (LeBeau): trail k × ATR from the highest high since entry | Descriptive only (for example https://wallstreetmojo.com/chandelier-exit); no out-of-sample study found | P | Already what `ExitPolicy.trail_stop` does, with ATR frozen at the signal bar (`goldbot/labels/exit_policy.py:60-64`) |
| 2.8 | Scale-out (bank part at +1R, trail the rest), breakeven at +1R | No study found. Breakeven is **F** (playbook 4.14) | P / F | A scale-out is a blend of a fixed target and a runner, and its mean is the blend's. Use it to cut variance, not to add edge |
| 2.9 | Fixed targets cap the upside on runner days; trailing stops shrink the average winner on normal days | Retail backtest (https://forexop.com/strategy/trailing-stops/, P); practitioner commentary | P | This is exactly why the choice should depend on the day type |
| 2.10 | **Gold intraday specifically:** we found **no study** comparing trailing and fixed-target exits | — | — | An open question that our own trials must answer |

**Our own evidence.** The trend (trail 1.5 ATR armed at 1.25 ATR, `goldbot/specialists/trend.py:48-49`) and
breakout (half at 1.0 ATR, trail 1.0 ATR, `goldbot/specialists/breakout.py:51-53`) families failed, but they had
no gross edge to begin with (R-03, R-04). Exits were never the binding constraint there.

### What the code supports today (`goldbot/labels/exit_policy.py`)

- One policy runs in all three places, labels, shadow and live, through `policy_step` (`exit_policy.py:1-31`,
  `:102-120`; `goldbot/labels/triple_barrier.py:96-108`; `goldbot/engine/shadow.py:148-169`;
  `goldbot/engine/runner.py:735-760`). Any new runner mechanic must go through the same step, or labels and live
  diverge.
- **Trail from the best price at k × (signal-bar ATR)**, armed after `trail_after_atr` (`exit_policy.py:46-47`,
  `:60-64`). This is a chandelier with frozen ATR.
- **One scale-out** (`:48-49`, `:108-110`).
- **Hard flat before the next session open** (`:50-51`, `:82-87`). There is no flat at a fixed local clock time
  such as "16:30 New York".
- **The target always ends the trade** (`:111-112`), so a runner needs a far `target_atr` as a cap.
- **Missing:** a structure trail (behind the last confirmed swing low or high, lag-shifted), a clock-time exit, and
  a trail distance that refreshes ATR.
- **Gate interaction (a risk):** the RiskGate's EV check is `p × target_atr − (1 − p) × stop_atr − cost`
  (`gate.py:242`), and the target floor is `target_atr ≥ 2.5 × cost` (`gate.py:245-246`). Under a policy, the label
  is +1 at the target, −1 at the initial stop, and otherwise the sign of the return (`exit_policy.py:131-133`). So
  `p` is "P(win)", while the EV assumes every win reaches the target. For a runner with a far cap this is wrong in
  both directions. The EV must use the measured mean winner of the agent's labels, capped at the target (BACKLOG
  32). That never makes the gate looser than today.

---

## 3. Design proposal

### 3.1 The opportunity score (allocator and regime input, never a gate input)

**Output, computed per trading day (feature day, 22:00 UTC rollover):**
- `opp_range_hat`: the forecast of today's high−low range ÷ ATR20(1d).
- `opp_score`: its rank against the trailing 250 days (0–1, history only, like `vol_tercile`).
- `opp_event`: number of tier-1 releases today.

The score is refreshed three times a day: before London (06:30 UTC, the existing `agents_presession` slot,
`config/settings.yaml:87`), one hour after the London open, and one hour after the NY open. The later refreshes
add the early-session range.

**Inputs, all point in time:**

| Input | Built from | PIT rule |
|---|---|---|
| HAR forecast of daily range | stub-free daily bars (as `time_series_momentum.drop_stub_days`): 1-, 5- and 22-day mean range, coefficients fitted inside each training fold | completed daily bars only |
| GVZ ÷ realised volatility | `macro_gvz` ÷ YZ20 annualised | `asof_join` on `available_utc` (D−2 at the London open) |
| Tier-1 count today, minutes to the first | calendar archive (the schedule is known in advance) | scheduled time, never the outcome |
| Prior-day range ÷ ATR20(1d), NR4/NR7/inside-day flags | completed daily bars | — |
| Weekend or overnight gap ÷ ATR(1d) | first bar after the reopen vs the last close | at the reopen |
| Early-session range ÷ its 14-day average at the same time of day (noise ratio, row 1.9) | DST-aware local session clock (`calendar.py:295-296`) | at the refresh time, completed bars only |

**Model.** A single in-fold linear or HAR-style regression on log range. There are no direction terms, and it is
not a classifier on returns. The score never contains `side`.

### 3.2 How the score maps onto sizing (inside the existing rails)

- `RuleAllocator.weights` (`goldbot/allocator/rules.py:47-59`) multiplies every family weight by a day factor:
  - **0.0 below the low cut**: stand aside. The weight 0 gives multiplier 0, and `runner.py:578` records
    `below_threshold` before any intent is built.
  - **1.0 in the middle band.**
  - **1.5 above the high cut.**
  - The cuts are fixed in config before any evaluation (proposal: the 33rd and 80th percentiles). `Regime` gains
    `day_score` (`rules.py:29-34`), filled by `runner._regime` (`runner.py:1641-1652`).
- **Why this cannot loosen anything:** `size_multiplier` = allocator weight × clip((p − p0) / 0.2, 0, 1)
  (`goldbot/research/metrics.py:52-56`), so the multiplier is at most 1.5. RiskGate then:
  - clamps the multiplier to `multiplier_bounds` [0.25, 1.5] (`gate.py:259-260`, `config/settings.yaml:44`);
  - caps risk × multiplier at 1% (`gate.py:266-267`);
  - halves risk and caps the multiplier at 0.5 in `SIZE_DOWN` (`gate.py:263-264`);
  - keeps every other check: daily and weekly caps, blackouts, spread, margin, notional.
  - At the design 0.5% the most a high-score day can target is **0.75%**.
- **Exits never consult the score.** A position opened on a high-score day keeps its policy if the score falls.
- **Interplay at £1,000 (must be reviewed by trading safety).** After BACKLOG 28, the 1.2× tolerance allows a 15m
  minimum lot (0.82%) only when m ≥ 1.37 (section 0). So without BACKLOG 25 the day factor 1.5 becomes the *only*
  way a £1,000 account trades. Realised risk is then 0.82% against a 0.75% target: within the gate's rule, but it
  should be shown on the approval card. With BACKLOG 25 the factor stops acting as an on/off switch.
- **Owner's contract is unchanged:** one-click confirmation of every entry (90 s), automatic exits.

### 3.3 Runner exits as policy variants

Defined per family in `exit_spec` (the `goldbot/specialists/base.py:122-129` pattern). All distances are in
signal-bar ATR, and every variant runs identically in labels, shadow and live.

| Variant | Mechanics | Code today? |
|---|---|---|
| **V0 fixed** (baseline) | Target 3.0 ATR, stop 1.5 ATR, time barrier | yes |
| **V1 scale-and-run** | 50% off at 2.0 ATR. Chandelier trail 2.5 ATR from the best price, armed at 1.5 ATR. Cap target 10 ATR. **Flat at 16:30 New York** | trail and scale-out yes; clock flat **no** (BACKLOG 31) |
| **V2 pure runner** | No scale-out. Trail 3.0 ATR armed at 2.0 ATR, cap 12 ATR, flat at 16:30 New York | clock flat **no** |
| **V3 structure trail** | Stop moves behind the last *confirmed* 15m swing low or high (fractal lag, as `goldbot/features/structure.py:126`), never loosening, flat at 16:30 New York | **no** (BACKLOG 31) |

The variants are *chosen by the day type*: V0 on mid-band days and V1 on high-score days. This is a pre-registered
pairing, not a per-trade optimisation. Only V0 and V1 enter the Q2 trial (H-17). V2 and V3 are diagnostics,
reported but not used for any decision. Using them later costs a trial each.

### 3.4 How it is evaluated

The **opportunity-day report** (BACKLOG 33, extending item 26's KPIs) is computed per research trial and monthly on the shadow book. Every
trade is re-sized as the £1,000 account would size it: 0.01 lot or refused, under the gate's rules.

| Metric | Definition (fixed now) |
|---|---|
| £/day distribution | Per trading day: P(day ≥ £50), P(day ≥ £20), median, mean, 5th percentile, worst day, days traded / days available |
| Opportunity days captured | Primary: ADR 0004 section 6's definition (range ≥ 1.5 × ATR(1d), ≥ 1 R made). Stricter variant: an *opportunity day* is defined ex post as a day whose realised range is in the top 20% of the trailing 250 days, a definition fixed before any data is seen. **Captured** = in a trade in the direction of the day's open-to-close move that took ≥ 50% of that move. Report the % captured and the % missed through stand-aside, gate refusal (by reason) or a wrong direction |
| Stand-aside accuracy | Mean realised range ÷ ATR on stand-aside days vs traded days. Net R the family *would* have made on stand-aside days (shadow) |
| Drawdown | Max drawdown %, longest drawdown, P(8%), P(12%), P(weekly cap) in 6 and 12 months (G-5 / BACKLOG 20 tool) |
| Edge | Net R per trade with a 90% interval, by score band and by exit variant; cost share of gross |
| Benchmark | The same entries with day factor 1.0 every day and exit V0 |

---

## 4. Ranked candidate hypotheses

Full rows are appended to `hypotheses.md` (section A2). They are feature-first: the score's inputs are opt-in bulk
features (BACKLOG 30a). They stay **out of the frozen Q1 H-02 universe**, so its configuration and K_eff are
unchanged. Trading-day count in the research window (2010-01 .. 2025-09): about **3,940** (≈ 250 × 15.75).

| Rank | ID | Hypothesis (short) | Evidence grade | Events (estimate) | Cost to beat | Trials | Q2 2027? |
|---|---|---|---|---|---|---|---|
| 1 | H-16 | **Opportunity score forecasts the day's range** out of sample beyond a HAR-only baseline (adds GVZ, events, prior-day, compression, gap) | R (HAR, events), R for GVZ in Chinese futures | ~3,940 days | none (no trades) | 1 | **yes** |
| 2 | H-19 | **Stand-aside overlay:** net R by score band for every Q2 trial, plus the stand-aside rule's effect | R (costs fixed in USD; SoA A7) | as each trial | — | 0 (overlay, as H-13) | **yes** |
| 3 | H-17 | **Trend-day continuation with a runner:** on high-score days, a 15m entry after a noise-area breach between London+60 min and NY+120 min, one trade a day. V1 vs V0 exits | S (Zarattini et al. 2024 net, equities; Lundström volatility-state WP; Kaminski–Lo) · N for gold | ~1,000–1,500 (to be counted from events only before freezing) | 0.15–0.25 R historically on 15m; lower on high-ATR days | 2 (V0, V1) | **yes**, quant review of the retired-family overlap first (R-02/R-05) |
| 4 | H-18 | **Post-blackout event continuation:** at +30 min after NFP, CPI, FOMC or PCE, enter in the direction of the release move if it exceeds k ATR, runner V1, flat at 16:30 New York | R (event volatility), S (FOMC adjustment > 5 min) | ~690 (44 events/yr) — **below the 1,000 floor** | as H-17 | 1 | **yes, only if** the owner rules an event-family floor, or tier-2 data (BACKLOG 18, D-4) lifts events above 1,000 |
| 5 | H-20 | **Compression → expansion** (NR4/NR7, inside day, ATR percentile) adds to the range forecast | P/F | inside H-16 | — | 0 (features in H-16) | inside H-16 |
| 6 | H-21 | **Early-session noise ratio** predicts the rest of the day's range and the trend day | S (range); S (detector, SPY) | inside H-16 refreshes and H-17 | — | 0 | inside H-16/H-17 |
| 7 | H-23 | **Overnight USD / real-yield shock** predicts the day's range | W | — | — | 0 until intraday USD data (D+, owner) | no |
| 8 | H-22 | **COT extremes predict volatility** | F | weekly, < 1,000 | — | 0 (feature only) | no |

Q2 2027 cost: **3 trials** (H-16, H-17 × 2), **4** with H-18. That is well inside a 20-trial quarter, and nothing
comes out of Q1.

**Reading rules (decided now, before any data is seen; full text in hypotheses.md A2):**
- **H-16.** Continue if, out of sample and fitted in fold, Spearman ρ(score, realised range ÷ ATR20) exceeds the
  HAR-only baseline by ≥ 0.05, AND top-quintile days average ≥ 1.5× the realised range of bottom-quintile days.
  - If it fails, the day factor ships with HAR alone (BACKLOG 30b still applies), and GVZ, events and compression
    are retired as range inputs.
- **H-17.** Continue only if all of these hold:
  - the screen passes (gross t ≥ 2 on ≥ 1,000 events) and net mean R > 0 for V1;
  - ≥ 3 positive net years, including 2021 or 2022;
  - V1 net ≥ V0 net;
  - the low-score half (shadow, not traded) has a lower gross R than the high-score half.
  - If the conditioning makes no difference, it is R-05/R-02 again and stays retired.
  - The £/day metrics are reported but are not part of the rule.

---

## 5. Raised risks (unasked)

1. **Retired-family overlap.** H-17 conditions retired ideas (session-open breakout, intraday momentum) on a new
   filter and a new exit. The "never re-test without new evidence" rule is met only if the quant reviewer accepts
   the new evidence: volatility-state dependence and the net SPY detector. Otherwise H-17 must wait for the
   director's retired-family exploration slot (`goldbot/research/director.py:61-68`), which it cannot choose.
2. **Daily-cap overshoot.** The percentage caps check the loss so far (`gate.py:231-234`,
   `goldbot/risk/supervisor.py:58-63`). At £1,000 the second full loss ends the day at ~1.69%, past the
   supervisor's 1.5%, and above £2,500 the same happens to the 2% cap. ADR 0004 projects only the £50 absolute
   limit (BACKLOG 29). The addition to item 29 projects the percentage caps too. It is a tightening, and at £1,000
   it means at most one full loss a day, so it is the owner's call.
3. **EV mis-specification for policy exits** (section 2, BACKLOG 32). It exists today for trend and breakout and
   would grow with far-capped runners.
4. **GVZ staleness.** At the London open GVZ is 1.5 days old. If H-16 credits GVZ, the credit is for stale
   information, which is still honest PIT but weaker than the literature's same-day use.
5. **Base rates unknown.** We have not measured how often gold gives a $66+ directional day at today's prices.
   BACKLOG 30's descriptive report measures the *unconditional* distribution of daily range (no predictor), to set
   expectations before any trial.
6. **Chasing.** A £50 target invites raising size after losses. The design forbids it: the day factor depends only
   on the pre-day forecast, never on P&L (ADR 0004 §3.6).

## 6. Needs the owner

1. ~~Report £/day as a distribution~~: already decided by ADR 0004 section 6 (north-star KPIs, BACKLOG 26).
   BACKLOG 33 applies the same KPIs to research trials.
2. **Event-family floor for H-18** (~690 events vs 1,000). Options: a floor of 500 for scheduled-event families
   (retire-only, as proposed for daily families in ruling A), pool with tier-2 events (needs D-4 data), or drop
   H-18. Recommendation: pool when the tier-2 archive exists; until then H-18 waits.
3. **Projected percentage daily caps (the addition to BACKLOG 29)**: fewer trades on bad days; at £1,000, one full
   loss a day. Recommendation: adopt.
4. **Realistic expectation.** At £1,000 the rails allow one 0.01 lot. £50 days can happen only on trend days with a
   6R runner. The average, even with an edge we do not yet have, is a few pounds a day. Deposits towards the ADR
   0004 milestones, not size, are what raise the £ figure.

## 7. Not verified / left out

- Several sources were read as abstracts or secondary summaries: Elder et al. 2012, Awartani et al. 2024,
  Lundström 2013, Holmberg et al. 2013, Zarattini et al. 2024 (both papers), Qadan & Idilbi-Bayaa 2021, and Glynn &
  Iglehart beyond the abstract.
- No gold-specific study was found on trailing vs fixed exits, NR7, the London–NY overlap as a range predictor, COT
  and volatility, or intraday USD/yield shocks. Those rows are graded P/F/W accordingly.
- ATR, price and GBPUSD are ADR 0004's estimates, not measured. Every £ and threshold above scales with them.
- Event counts for H-17 and H-18 are estimates. They must be counted from events only, with no outcomes, before the
  Q2 freeze.
- No code, settings, data, trials, budget, holdout or the Q1 queue were touched. The multiple-testing ledger is
  unchanged.
