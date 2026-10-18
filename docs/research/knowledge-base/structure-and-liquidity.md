# Structure and liquidity

Part of the [knowledge base](README.md). Format, grades and markers are defined there.

**Scope.** Swing structure (higher highs and lows, lower highs and lows), breaks of structure (BOS) and changes of
character (CHoCH) on internal and swing scales, strong and weak highs/lows, equal highs/lows (EQH/EQL), fair value
gaps (FVG), order blocks, premium/discount zones and the 50-61.8% retracement band, liquidity sweeps and false
breakouts. Our implementation is the clean-room `smc_replica` family (`goldbot/features/smc_replica.py`) plus the
older `market_structure`, `fair_value_gaps` and `order_blocks` features (`goldbot/features/trader.py`) and swing
levels (`goldbot/features/structure.py`).

## Summary

- **Swing structure is a description of trend, not a separate signal.** HH+HL is what an up-trend looks like, so the
  evidence for "trade with structure" is the evidence for trend following, which for gold is strong only at slow
  horizons and weak or negative at intraday ones.
- **The liquidity ideas have one real mechanism behind them: stop and take-profit order clustering** (Osler). It is
  documented in FX dealer order books, not in gold, and it predicts *both* reversals (at take-profit clusters) and
  accelerations (through stop clusters). Which one occurs is the open question.
- **The smart-money vocabulary (FVG, order blocks, premium/discount) has no peer-reviewed test.** It is graded D.
- **Our own tests so far:** the owner-style multi-timeframe structure rules could not even reach a testable event
  count (H-34/H-35 withdrawn: 0-18 candidates in 15 years). Two single-timeframe structure rules are pre-registered
  (H-TVR1, H-TVR2) and not yet read.

## Claims

| # | Claim | Course baseline (paraphrased) | External evidence | Grade | Our data |
|---|---|---|---|---|---|
| S1 | Trend state can be read from swings: HH+HL = up, LH+LL = down, otherwise range. Trade only with the state | The first question before any trade is the trend; read it from swings, ideally on a close-only line chart, and buy pullbacks in up-trends, sell rallies in down-trends | Trend following is robust only as a slow, diversified premium: Moskowitz, Ooi, Pedersen 2012 (*JFE*, 58 futures incl. gold, 1965-2009, gross) and Hurst, Ooi, Pedersen 2017 (67 markets, 1880-2016, net of estimated costs), both in `state-of-the-art.md` A1. Single-asset predictability is weak (Huang, Li, Wang, Zhou 2020, *JFE*). Short-term trend dead on small-tick contracts since about 2009 (Kurth et al. 2026, arXiv 2607.01550) | **A** for slow trend; **B against** intraday trend on gold | **tested:** R-03 (1h trend pullback, no gross edge), R-06 (tsmom 1h/4h: gross +0.061 R, t 2.63, net −0.079 R). Swing trend as a filter: H-33 routed into H-02. Daily agreement: H-31 pre-registered |
| S2 | A close beyond the last swing high/low in the trend's direction (BOS) signals continuation | Swing and internal BOS mark continuation; the structure tool draws them in real time | No peer-reviewed test of BOS as defined by the structure tool. Closest: trading-range-break rules on the Dow (Brock, Lakonishok, LeBaron 1992, *JF* 47(5), https://ideas.repec.org/a/bla/jfinan/v47y1992i5p1731-64.html; 1897-1986, gross), whose significance did not survive data-snooping control out of sample (Sullivan, Timmermann, White 1999, *JF* 54(5), https://doi.org/10.1111/0022-1082.00163) | **A against** for simple break rules net of costs; **none found** for BOS itself | **pre-registered:** H-TVR2 (15m swing BOS, stop 1.0 / target 2.0 ATR15). **tested (related):** R-04 1h breakout gross +0.015 R; B03 15m breakout #29-#32 FAIL |
| S3 | The first break against the trend (CHoCH) warns of reversal, but most reversals fail; continuation is the default | Treat a change of character as a warning; the course states that most attempted reversals (it gives a figure of about 70%) fail, so only reverse at major levels with full confluence | No peer-reviewed estimate of how often first counter-trend breaks fail on any market. The figure is a course claim | **D** | **pending: pa-claims** (base rate of CHoCH failure on our swing data). **pre-registered:** H-TVR1 uses internal CHoCH *with* the swing trend |
| S4 | Strong vs weak highs/lows: an extreme that produced a BOS is "strong" (protected); one that did not is "weak" (likely to be taken) | The structure tool labels the recent chart extremes strong or weak | None found | **D** | **feature:** not built as its own column; derivable from `structure_breaks` |
| S5 | Equal highs/lows (two or more extremes within a small tolerance) are where stops gather, so price is drawn to them | The tool marks EQH/EQL with a tolerance of 0.1 × ATR; double tops and bottoms reuse the same idea | Stop-loss orders cluster just beyond salient levels and round numbers, and trends accelerate when those levels are hit (Osler 2005, *JIMF* 24(2); NY Fed SR150, https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr150.html; FX, 1-minute data, gross; effects last hours, not days). Extending "salient levels" to equal highs is our inference | **B** for the mechanism (FX); **none found** for EQH/EQL in gold | **feature:** `eqh`, `eql` (`smc_replica`). New hypothesis **KB-5** (magnet base rate) |
| S6 | A liquidity sweep (wick beyond an obvious high/low, close back inside) followed by a counter break of structure and a retest gives a reversal entry | Don't buy the breakout of an obvious level; wait for the failure, a close back inside, a break of the nearest swing, then a rejected retest; stop beyond the sweep extreme with room for gold's overshoot | Take-profit clustering produces reversals *at* levels; stop clustering produces cascades *through* them (Osler 2003, *JF* 58(5) *(volume/pages unverified)*, NY Fed SR125, https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr125.html; Osler 2005). No study tests "marginal break then close back inside reverses". Predatory trading around known stop levels is modelled by Brunnermeier & Pedersen 2005, *JF* 60(4) *(unverified)* | **B** for the mechanism; **none found** for the composite setup | **feature:** `market_structure` sweep columns (`goldbot/features/trader.py`). **proposed:** H-08 (inside H-14/O-4). **withdrawn:** H-35 (1h box sweep + 15m CHoCH: 0-18 candidates). New hypothesis **KB-2** (sweep depth decides cascade vs reversal) |
| S7 | A fair value gap (a three-bar imbalance where bar 1's high and bar 3's low do not overlap) tends to be revisited and acts as support/resistance | The structure tool shows FVGs with a significance filter, optionally from another timeframe | No peer-reviewed or working-paper test found (searches 2026-10-18). Vendor blog reports a 1,000-trade backtest with ~65% of 15m EUR/USD gaps partly filled within 48 h, method undisclosed (https://fxnx.com/en/blog/smart-money-concepts-work-backtest-evidence). Partial fill of a small gap is close to a random-walk base rate, so a fill rate alone shows nothing | **D** | **feature:** `fvg_bull`, `fvg_bear` (replica); `f_fair_value_gaps`. **pre-registered:** H-TVR1 (same-side FVG as a condition). R-09 retired FVG as a standalone rule |
| S8 | An order block (the last opposite candle before a displacement) marks where large orders sit and price will react on return | The tool shows internal and external blocks with an ATR filter; the owner keeps them switched off | No peer-reviewed test found. Same vendor blog reports ~43% "bounce" rate, undisclosed method (URL as S7) | **D** | **feature:** `f_order_blocks` (`trader.py`). Replica cannot reproduce them (owner's setting off; data request D-3). R-09 |
| S9 | Price in the upper half of the recent range is "premium" (sell), the lower half "discount" (buy); the 50-61.8% band is the "golden zone" of a pullback | Do not trade a box inside the golden zone (the 50-61.8% band of the 50-bar swing); premium/discount zones on by default | Fibonacci levels were not statistically different from non-Fibonacci levels in an automated test on three equity markets (*Expert Systems with Applications* 2021, https://doi.org/10.1016/j.eswa.2021.115893; authors not verified). An arXiv survey of retracement data finds a continuous distribution with no special levels (arXiv 1605.03559, https://arxiv.org/pdf/1605.03559) | **C against** | **feature:** `range_f1_dist_atr`, `range_f2_dist_atr` (replica). R-09 (Fibonacci, premium/discount retired as rules). **pending: pa-claims** if it checks the "no trade inside the band" rule |
| S10 | A 50% retracement of the last swing that coincides with the previous swing extreme is the best pullback entry | Price often retraces about half of the last swing before resuming; best when 50% meets old resistance-turned-support | As S9 for the ratio. Role reversal of broken levels is discussed in the S/R file (R5) | **C against** (ratio); see R5 | not checked |
| S11 | Internal (small) and swing (large) structure must be read together | The tool runs a 5-bar internal and a 50-bar swing scale | None found. Mixing horizons is a form of multi-horizon trend, see the multi-timeframe file | **D** | **study:** `owner-style-mtf-2026-10-18.md`; a structure stop at the 15m swing is a median **3.3 × ATR15**, so only 34 of 8,385 CHoCHs fit the 1.5 × ATR15 lot cap at £1,000 |

## What this means for goldbot

1. **Restate every liquidity idea as order clustering.** "Sweep", "stop hunt", "inducement" and "EQH" all reduce to:
   orders sit at known levels; where they are take-profits, price stalls; where they are stops, price accelerates.
   That gives testable, two-sided predictions (KB-2, KB-4, KB-5) instead of a one-sided fade.
2. **Structure stops do not fit the account.** The natural stop beyond a 15m structure point is about 3.3 × ATR15
   (about $27/oz today, about 2% of £1,000 at 0.01 lot). Structure rules at 15m are therefore not placeable under the
   rails; at 1h they need M2 equity. This is arithmetic, not research.
3. **Keep SMC terms as features, not rules.** They enter through O-4/H-02 groups (D-TVR). H-TVR1/2 are the only rule
   trials, run as an exception to R-09 at the owner's request; both failing closes the topic.

## Open checks

- CHoCH failure base rate (S3) and EQH magnet (S5): pa-claims and KB-5.
- Fetch Osler 2003 *JF* full record (volume/pages) and Brunnermeier & Pedersen 2005 to remove the markers.
