# TradingView author-ideas direction parser audit 3 (direction-only with abstain), 2026-10-31

Line T-1 of `prereg-2026-10-28-tv-author-ideas.md`. Rule from the research director (bet 3): direction-only parse with an
abstain class, on a fresh random sample whose seed and n are declared before labelling; pass = precision >= 95 % AND
coverage >= 50 %; else the line stays parked to the Q1 refresh and no trial is spent. No idea text is stored here (copyright
and ToS): ids, labels and metrics only. Text and stored fields only; no price data opened; DB opened read-only.

## Verdict: FAIL. T-1 stays parked to the Q1 refresh. No trial spent. No parser shipped.

## Definitions (fixed before labelling)
- Prediction: `long`, `short` or `abstain`. Coverage = predictions that are not abstain / n. Precision = correct / non-abstain.
- My label from title + description only: `long` or `short` when the text states one expected direction or plan and no
  explicit counter-plan; `none` for neutral, two-sided / if-then scenario, results-only, question-only or unreadable posts.
  A prediction on a `none` idea is an error.
- Parser candidate: a new level-free classifier (cue lists adapted from the 2026-10-30 parser, plus vetoes for two-sided
  wording, past-tense results and a TradingView flag that disagrees). sha256 **c61894993f0bd9d289c52693d11ca779bb9f656932d71a3d125fd3e5c7d3cae0**,
  frozen before the sample was drawn. File not committed (failed).

## Sample (fresh)
- **Seed 20261101, n = 90**: `random.Random(20261101).sample` of 30 per author over ids ascending, `published_utc < 2025-10-01`,
  `edited = 0`, excluding the 120 ids already spent by audits 1-2 (seeds 20261030 and 20261031). I labelled all 90 before
  running the parser.
- Dev (tuning) set: the 120 spent ids with labels rebuilt from the two earlier audit tables: precision 29/32 = 90.6 %,
  coverage 26.7 %.
- KnightsofGold: 11454027, 12061003, 14631137, 10832582, 10380288, 10556924, 13296224, 13820180, 14180423, 12265109, 12500328, 16327618, 19191034, 17180384, 12451587, 9162562, 14375388, 9957612, 17277490, 14569264, 11714966, 17375040, 9508046, 12986014, 11925543, 14187622, 13627713, 12953286, 14000608, 14132534
- Goldviewfx: 18662193, 13006638, 13284443, 15748858, 12651982, 18261404, 12529121, 12602238, 17847447, 14180464, 14719396, 15208930, 13364064, 13590266, 16182384, 17105601, 19577115, 19640603, 16514778, 13537479, 17631647, 11850615, 15414211, 12303857, 16537405, 15698640, 13141082, 16572857, 12008570, 13954522
- ArmanShabanTrading: 18760246, 11622373, 11689016, 12630570, 11560076, 16972311, 11522549, 11539355, 15986580, 12018695, 12190080, 12366363, 11711725, 11761950, 12990855, 13764459, 13266025, 11753210, 14235682, 11349301, 12445423, 11457887, 13276281, 12617754, 11655140, 13295023, 11361486, 11890233, 16770702, 11662309

## Labels
- long (17): 10380288, 13006638, 13284443, 12651982, 18261404, 12529121, 17105601, 13537479, 12303857, 12630570, 11522549, 15986580, 12018695, 11349301, 12617754, 11361486, 16770702
- short (7): 17277490, 11714966, 11925543, 13590266, 11560076, 16972311, 13276281
- none (66): every other id above.

## Result
| Measure | Value | Bar |
|---|---|---|
| Non-abstain predictions | 20 / 90 | |
| Coverage | 22.2 % | >= 50 % |
| Correct among predictions | 7 / 20 | |
| Precision | 35.0 % | >= 95 % |
| Directional ideas recalled (17 of 24 missed) | 7 / 24 = 29 % | |

Wrong predictions (13): 12061003, 12265109, 14569264, 9508046, 14719396, 13364064, 19640603, 17631647, 12008570,
14235682, 11662309 (all labelled none, parser gave a side), 18760246 and 11689016 (labelled none; the parser said
long, and TradingView's flag also said long, but the text leads with a rejection or is a results post).

## Why this fails, and why a better regex will not rescue it
1. **Ceiling.** Only 24 of 90 ideas (27 %) state one unopposed direction by my strict labelling; 66 are two-sided,
   if-then, results-only, questions or empty. Even a perfect classifier covers about 27 %. A 50 % coverage bar cannot be met at
   95 % precision on these authors' text: the earlier 48 % coverage came from calling two-sided plans a side.
2. **Wording.** KnightsofGold writes scenario reports with a daily bias above and below a level; Goldviewfx's standing
   "buy dips" line is boilerplate on bullish, bearish and range posts alike; Arman's calls are often chart-only (title and
   caption carry no side). The side lives on the chart image, which the collector does not read.
3. **TradingView flag.** It is neutral on 72 % of the pool (3,267 of 4,558 ideas pre 2025-10 and unedited), so it can veto
   but not supply a side.
4. Label judgement: the pass bar is missed by 60 points of precision, so no re-reading of borderline labels (about 6 of 90)
   could flip it.

## Consequence for the research line
- Text-parsed author direction is not usable as a signal source. The only direction available at scale is TradingView's
  own flag (about 28 % of ideas), which is a different, unaudited and survivor-biased feed; using it would need its own
  pre-registration.
- Ideas spent as development: 120 earlier ids plus these 90. A Q1 refresh must draw a new seed.
- Owner-only: none. Trial count unchanged (no trial spent).
