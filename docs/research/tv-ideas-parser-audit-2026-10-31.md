# TradingView author-ideas parser audit 2 (outcome-blind), 2026-10-31

Pre-registration `prereg-2026-10-28-tv-author-ideas.md` 1.1 "Parser freeze": pass needs >= 95 % direction and >= 90 % levels agreement. Not committed. Text and stored fields only; no price data opened; DB opened read-only (`mode=ro`).

## Definitions (fixed before reading any idea)
- Direction compared = parser `direction_decision` (long/short/neutral/conditional) vs my hand read of title + description.
- T1 = first OPEN target (not marked done/hit/reached), unconditional, on the idea's side. Entry and stop as stated; "if it breaks X" in a one-sided idea is its entry. Levels agree when entry, stop and T1 each match within 0.5 USD or are both absent (present vs absent = disagree). Parser T1 = `levels.target[0]`.

## Method
- Parser: `goldbot/data/tv_ideas_parse.py`, worktree agent-aeebb246c66606d77, sha256 **8403acf9f2a68943c94b7d4a9017b5a9f93b04f6512b4784225bc9404b839c86** (verified by shasum; equals the frozen hash named in the brief). Stored fields in the Mac DB (already reparsed) were compared; no re-parse.
- Seed 20261031: `random.Random(20261031).sample` over ids ascending with `published_utc < 2025-10-01`, `edited = 0`, excluding the 60 first-audit ids (reproduced exactly from seed 20261030 on the same pools 1205 / 1760 / 1593; 11 + 17 + 11 of them appear in the 10-30 file). Pools after exclusion 1185 / 1740 / 1573. 20 per author.
- KnightsofGold: 13954201, 13509669, 14464234, 11107800, 17511107, 10325812, 13153005, 13570568, 16754215, 13275693, 12625134, 11822104, 19434196, 10076087, 13765283, 13345664, 11197099, 18752971, 16718736, 12879370
- Goldviewfx: 14950665, 14422186, 18855717, 18569874, 15544737, 12755485, 16663234, 12494399, 14090780, 14446451, 16364194, 14172388, 17624957, 13708580, 13178809, 18989628, 17154419, 18803925, 17267069, 12409572
- ArmanShabanTrading: 12290473, 12095804, 19444992, 17701889, 12521568, 11587178, 13357098, 11509556, 11975867, 12106985, 13119223, 12018531, 14230564, 11812173, 11655458, 13815171, 19257557, 13904503, 11483577, 12172202
- I read each text and wrote my read to a file BEFORE querying the parser fields (reads written, then compared by script).

## Result: FAIL
| author | direction agree | levels agree (entry+stop+T1) |
|---|---|---|
| KnightsofGold | 16/20 = 80 % | 16/20 = 80 % |
| Goldviewfx | 17/20 = 85 % | 13/20 = 65 % |
| ArmanShabanTrading | 17/20 = 85 % | 17/20 = 85 % |
| **All 60** | **50/60 = 83.3 %** (needs 95 %) | **46/60 = 76.7 %** (needs 90 %) |

Both bars missed.

## Direction disagreements (10)
| id | author | my read | parser | cause |
|---|---|---|---|---|
| 10325812 | KOG | conditional | short | "out of trades, watch support; lower target 1720 below it": a break-conditional lower list taken as a short expectation (judgement case) |
| 13275693 | KOG | long | neutral | one conditional plan ("comes down to 1850-55 and supports, take the long to 1875, 1880"): no cue phrase; targets sit as conditional |
| 19434196 | KOG | conditional | neutral | event preview with "Break above X for ... / Break below Y for ..." red-box lines: not a two-sided pattern |
| 13765283 | KOG | conditional | long | both a long and a short plan; "upside" twice = tone long, the two-sided read missed |
| 12755485 | Goldviewfx | long | neutral | past-tense "remained Bullish ... called the targets UP, ALL HIT"; one tone word only |
| 14090780 | Goldviewfx | conditional | neutral | "break below half-line -> 1624; failure -> 1668": if/failure scenario wording not recognised |
| 13178809 | Goldviewfx | long | conditional | mid-term bullish and short-term bearish target lists both present => conditional though the text expects the move up |
| 17701889 | Arman | short | long | "slight upward move followed by further declines": `_DOWN` matches "decline" not "declines" (plural), so only the up cue fired |
| 13357098 | Arman | conditional | short | "BUY and SELL positions": `(buy|long) position` needs adjacency, so only "SELL positions" fired |
| 13815171 | Arman | neutral | long | results post ("+220 pips"); text is neutral but the fallback uses TradingView's flag because a level (1745, a done target) exists |

## Levels disagreements (14) (entry / stop / T1; read vs parser)
| id | author | read | parser | cause |
|---|---|---|---|---|
| 10325812 | KOG | T1 none | T1 1720 | conditional lower list taken as target (direction wrong) |
| 13275693 | KOG | entry 1850-55, T1 1875 | none | direction neutral => all conditional; "supports" not an entry keyword |
| 11822104 | KOG | entry 1775, T1 1735 | entry none (1775 conditional), T1 1735 | "Resistance at that level could be a possible shorting opportunity": shorting-opportunity level not an entry |
| 13765283 | KOG | none | T1 1750 | direction wrong (long) |
| 18569874 | Goldviewfx | T1 3049 | none (3049 conditional) | text says the lock above 3029 was confirmed ("opening 3049 yesterday"); header "will open" always marks conditional |
| 15544737 | Goldviewfx | T1 none | T1 1979 | prose "We got our 1979 target today" not read as done (only list marks count) |
| 12755485 | Goldviewfx | T1 1974 | none | direction neutral => conditional; 1974 in prose "1974 open" |
| 16364194 | Goldviewfx | T1 2051 | T1 2036 | bearish retracement target 2036 kept as T1 in a long idea (listed first in a lists with a "bearish" word far away) |
| 17624957 | Goldviewfx | T1 2729 | none | "gap to 2729 long range AXIS TARGET": no trade keyword adjacent |
| 13178809 | Goldviewfx | T1 1814 | none | direction conditional => 1814 conditional |
| 18989628 | Goldviewfx | T1 3389 | none | same as 18569874 (lock confirmed in prose, header "will open") |
| 19444992 | Arman | T1 3318 | T1 3334 | "hitting both targets at 3334 and 3324" in prose not read as done |
| 14230564 | Arman | entry 1.171, T1 1.165 | entry none, T1 1.148 | "important condition is the close below 1.171" not an entry; range 1.148-1.165 first value is the far end |
| 13815171 | Arman | none | T1 1745 | direction wrong (flag fallback), and 1745 was a hit target |

## Notes and judgement calls (for the principal)
- The 0.5 USD tolerance is meaningless on sub-dollar prices (ADA, DOGE, GBPUSD, EURUSD): on those the match is effectively presence-only. 19257557 (DOGE) agrees by the rule though the parser puts 0.7 before 0.287 in target[0] (a list ordering issue: "up to $0.70" in the title is read first).
- Reads I would call judgement (could flip the count by 1-3 ideas): 10325812, 13357098, 13178809, 14090780, 18569874 and 18989628 (lock-confirmed-in-prose T1). Even if all six flipped in the parser's favour, direction would be 53/60 = 88 % at best and levels ~ 50/60 = 83 %: still FAIL.
- Top causes by count: (1) prose done / lock-confirmed states not understood (5 level misses); (2) two-sided or scenario phrasing not recognised, or one-sided lists over-read as two-sided (5 direction misses); (3) plural / adjacency regex gaps (2); (4) TradingView flag fallback on a results post (1); (5) levels without a trade keyword ("gap to X target", "shorting opportunity", "close below X").
- Ideas now SPENT as development set: these 60 (seed 20261031) plus the first 60.
