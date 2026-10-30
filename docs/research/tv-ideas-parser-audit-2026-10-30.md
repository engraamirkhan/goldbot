# TradingView author-ideas parser audit (outcome-blind), 2026-10-30

Pre-registration: `prereg-2026-10-28-tv-author-ideas.md` section 1.1 "Parser freeze". Not committed. Text and stored fields only; no price data was opened or queried; the database was opened read-only.

## Method
- Parser: `goldbot/data/tv_ideas.py` at fe045043, `parse_levels` (levels), `Idea.direction` (TradingView's idea flag mapped by `DIRECTIONS`; the parser does not read direction from text), `symbol`, `tf` (TradingView metadata).
- **Frozen hash (sha256 of the file at fe045043): `4883770776cc1bfeb2feb21bb6d52baab9455eb35983457762ad86d5003a1730`** (working tree is identical, `git diff fe045043` empty).
- Sample: `random.Random(20261030).sample(...)` per author, from ideas with `published_utc < 2025-10-01` and `edited = 0` (pools: KnightsofGold 1205, Goldviewfx 1760, ArmanShabanTrading 1593). 20 per author, 60 total. Sample ids are reproducible from the seed (ids sorted ascending, `idea_id` order).
- I read title and description first, wrote direction/entry/stop/T1 by hand, then looked at the parser's values. Parser levels were re-computed with the frozen `parse_levels` from the stored text. Stored `levels` column equals the re-computed one for 53 of 60 rows (7 rows are stale, all KOG; the store needs `IdeasDb.reparse()` before scoring). Agreement is the same, 40/60, on stored and on re-computed levels.
- Direction agreement: my read long/short must equal the parser's; my read neutral or conditional must equal the parser's `neutral` (the parser has no conditional value). T1 = first parsed target in document order. Levels agree if entry, stop and T1 each match within 0.5 USD or are both absent.

## Result
| Measure | Value | Target | Verdict |
|---|---|---|---|
| Direction agreement, all 60 | 32/60 = 53.3% | >= 95% | FAIL |
| Direction agreement, spot only (42) | 22/42 = 52.4% | >= 95% | FAIL |
| Direction, my read long/short (32) | 11/32 = 34.4% | | |
| Direction by author | Knights 17/20, Goldviewfx 3/20, Arman 12/20 | | |
| Levels agreement, all 60 | 40/60 = 66.7% | >= 90% | FAIL |
| Levels agreement, spot only (42) | 24/42 = 57.1% | >= 90% | FAIL |
| Levels, spot and my read long/short (21) | 11/21 = 52.4% | | |

Symbol and timeframe: the record fields are TradingView's, so no parser error is possible; where the text states a timeframe (for example "1H", "12H") it matches `tf`. Note `tf` "1M" means one month and "1" one minute; the bucketing code must keep that distinction.

## Type counts (by hand read)
- Spot (XAUUSD from any broker): 42 of 60. Excluded symbols (not spot gold, not futures): 18 (BTC 5, DXY 2, ETH 2, GBPUSD 2, USOIL, DOGE, MBOX, XRP, EURUSD, CHZ, BNB 1 each). Futures (GC1!, GC, MGC): 0.
- Of the 42 spot: long 18, short 3, neutral 12, conditional (type C) 9.
- Type L (entry + stop + T1): **0 of 60** by hand and by parser. No author states an entry and a stop in this sample.
- Type D (direction plus at most levels, spot): 21 by hand (18 long, 3 short; all 21 have a T1, none an entry-and-stop pair).
- Parser view: spot ideas admitted as long/short by the stored direction: 5 (of which 2 are not trade ideas by text: a greeting and a text-less post). The 18 long-biased Goldviewfx ideas would be dropped as "neutral".

## Disagreements: direction (28)
| idea_id | author | read | parser | likely cause |
|---|---|---|---|---|
| 12506210 | KnightsofGold | short | neutral | TradingView flag neutral; text names a short/lower bias (flag unset) |
| 11269963 | KnightsofGold | short | neutral | TradingView flag neutral; text names a short/lower bias (flag unset) |
| 9952569 | KnightsofGold | short | neutral | TradingView flag neutral; text names a short/lower bias (flag unset) |
| 16900813 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 17010467 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 15804759 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 12458562 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 18482961 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 19058786 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 16645449 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 17248287 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 19530272 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 14550298 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 17590600 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 14619622 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 13320778 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 15220994 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 14779115 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 15810139 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 17779199 | Goldviewfx | long | neutral | Direction is TradingView's idea flag only (0=neutral); text is long-biased (buy-dips plan) but flag unset |
| 13652016 | ArmanShabanTrading | neutral | short | TradingView flag disagrees with the text (flag set on a greeting or a text-less or two-sided post, or unset on a trade post) |
| 12342599 | ArmanShabanTrading | neutral | long | TradingView flag disagrees with the text (flag set on a greeting or a text-less or two-sided post, or unset on a trade post) |
| 11495304 | ArmanShabanTrading | long | neutral | TradingView flag disagrees with the text (flag set on a greeting or a text-less or two-sided post, or unset on a trade post) |
| 13877644 | ArmanShabanTrading | conditional | long | TradingView flag disagrees with the text (flag set on a greeting or a text-less or two-sided post, or unset on a trade post) |
| 12134718 | ArmanShabanTrading | neutral | long | TradingView flag disagrees with the text (flag set on a greeting or a text-less or two-sided post, or unset on a trade post) |
| 14163785 | ArmanShabanTrading | neutral | short | TradingView flag disagrees with the text (flag set on a greeting or a text-less or two-sided post, or unset on a trade post) |
| 12655237 | ArmanShabanTrading | conditional | short | TradingView flag disagrees with the text (flag set on a greeting or a text-less or two-sided post, or unset on a trade post) |
| 11762329 | ArmanShabanTrading | neutral | short | TradingView flag disagrees with the text (flag set on a greeting or a text-less or two-sided post, or unset on a trade post) |

## Disagreements: levels (20), entry / stop / T1 as read vs parsed
| idea_id | author | read | parsed | likely cause |
|---|---|---|---|---|
| 17940729 | KnightsofGold | -/-/- | -/-/16 | bare count `16 targets` taken as a price (_values keeps any integer >= 10) |
| 18361468 | KnightsofGold | -/-/- | -/-/2929 | conditional bias text read as targets; T1 is the first number met, not the first target of a direction |
| 14083846 | KnightsofGold | -/-/- | -/-/1665 | same: conditional bias levels taken as targets |
| 14719654 | KnightsofGold | -/-/- | -/-/1880 | target list in a conditional/neutral post taken as T1 |
| 13669470 | KnightsofGold | -/-/- | -/-/1770 | conditional targets in a neutral post taken as T1 |
| 14126116 | KnightsofGold | -/-/- | -/-/1665 | conditional bias levels taken as targets |
| 12616343 | KnightsofGold | -/-/- | -/-/1812 | conditional bias levels taken as targets |
| 13811011 | KnightsofGold | -/-/- | -/-/1750 | conditional bias levels taken as targets |
| 17010467 | Goldviewfx | -/-/2429 | -/-/- | list under a `BULLISH TARGETS` header on the next line: _BREAK splits at newline so the keyword and its numbers fall in different segments |
| 19530272 | Goldviewfx | -/-/3683 | -/-/3564 | T1 is a target already marked done (3564) not the open one; parser has no done/open state and takes document order |
| 14550298 | Goldviewfx | -/-/1923 | -/-/- | header-then-newline target list missed (_BREAK) |
| 14619622 | Goldviewfx | -/-/1931 | -/-/- | header-then-newline target list missed (_BREAK) |
| 13320778 | Goldviewfx | -/-/1879 | -/-/- | header-then-newline target list missed (_BREAK) |
| 15220994 | Goldviewfx | -/-/1990 | -/-/- | header-then-newline target list missed (_BREAK) |
| 14779115 | Goldviewfx | -/-/1824 | -/-/- | header-then-newline target list missed (_BREAK) |
| 17779199 | Goldviewfx | -/-/2760 | -/-/- | header-then-newline target list missed (_BREAK) |
| 13560121 | ArmanShabanTrading | 1730/-/1721 | -/-/- | conditional trigger `if it breaks X` not an entry keyword; `drop below Y` not a target keyword |
| 13877644 | ArmanShabanTrading | -/-/- | -/-/19650 | conditional scenario target taken as T1 on a two-sided post (excluded symbol) |
| 12163412 | ArmanShabanTrading | 1770/-/1760 | -/-/1760 | conditional trigger `if the level breaks` not an entry keyword (entry 1770 missed); T1 ok |
| 14745367 | ArmanShabanTrading | -/-/25930 | -/-/- | `targets ... still valid` sits in an earlier sentence than the prices ('!' break), parenthesised list missed (excluded symbol) |

## Top causes
1. Direction comes only from TradingView's idea flag. 2193 of 2220 Goldviewfx ideas (and 1866/1955 KOG) carry flag 0 (neutral) although the text is a stated buy-dips plan, so a flag-only direction drops almost all of that author. Needs a text direction rule, or a pre-registered decision that flag-only is the frozen definition.
2. `_BREAK` splits at newlines, so `BULLISH TARGETS` followed by a price on the next line is missed (7 Goldviewfx ideas here; this is that author's standard format).
3. No conditional or done/open awareness: two-sided KOG/Arman bias text is parsed as targets, done targets are taken as T1, and "if it breaks X" triggers are not entries.
4. Bare integers >= 10 that are counts are taken as prices (`16 targets`).

Conclusion: parser does not meet the freeze bar (direction 53% vs 95%, levels 67% vs 90%). It must be fixed and re-audited with a fresh seed (this sample is now read and cannot be reused outcome-blind for tuning without bias). Do not freeze the hash above.
