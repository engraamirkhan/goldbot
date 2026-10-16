# Holiday list measurement (BACKLOG 45): draft, 2026-10-16

**Question.** Are the days in `config/holidays_cme_metals.yaml` (cme-metals-v2) thin compared with normal days? Is any day missing from the list thin?

**Data**
- Release `data-v1`, 1m XAUUSD bars, 17 yearly files downloaded 2026-10-16.
- sha256 prefixes: 2010 `26139f0b026b`, 2011 `a1497c69398a`, 2012 `89f4b77dbfe3`, 2013 `fe31b8442902`, 2014 `be2bf066f0c4`, 2015 `046ddd848fae`, 2016 `9c6073ccee32`, 2017 `e04d7da0d7e9`, 2018 `a8a93abfe5be`, 2019 `f5e43125c916`, 2020 `7a8faf3ddc34`, 2021 `975af8633f03`, 2022 `cfeb833d900e`, 2023 `5ba2c40b0380`, 2024 `0fd98a09f95d`, 2025 `c2a2492ed163`. The 2026 file (`b6d2e2078ab6`) is not read.
- Window: 2010-01-04 to 2025-09-30. Nothing at or after `research.holdout_from` = 2025-10-01 is read, so the 2026 file is out (holdout, and G2 with bid-imputed months).

**Method**
- **The day** is the broker server date (Europe/Athens), the same date RiskGate uses.
- **Sessions** come from `DEFAULT_SESSIONS.session_label`: asia, london, newyork. Nominal open minutes come from the static session table, which has no holidays in it.
- **Per day and per session:**
  - `vol_ph`: Dukascopy volume per nominal open hour. Closed minutes count as zero.
  - `vol_pq`: volume per quoted minute, so open hours only.
  - `spread`: median 1m close spread.
  - `atr15`: mean 15m true range of the mid price.
  - `spr/ATR`: the cost of one spread as a share of a 15m range.
  - `quote_frac`: bars present divided by nominal minutes.
- **Controls:** the same weekday 7 and 14 days either side. A control is dropped if it is a holiday or candidate day of any type, a gap-affected day, or has fewer than 1,300 bars. An instance needs at least 2 clean controls.
- **Ratio:** each instance's value divided by the median of its controls. Ratios are pooled per day type as a geometric mean with a 95% t-interval on the logs.
- **Closed and excluded days:** an instance with fewer than 60 bars counts as closed and is left out of the ratios.
- **Normal-day baseline** (weekdays with at least 1,300 bars, n=3,904): spread 0.338, 15m ATR 1.80, spr/ATR 0.190. By session, spr/ATR is 0.268 in asia, 0.178 in london and 0.153 in newyork.

**Rule, fixed before the ratios were read**
- **THIN:** whole-day `vol_ph` upper 95% bound < 0.75, or spread lower bound > 1.25, or spr/ATR lower bound > 1.25.
- **NORMAL:** `vol_ph` lower bound >= 0.75, spread upper bound <= 1.25 and spr/ATR upper bound <= 1.25.
- **UNRESOLVED:** anything else.

About 28 day types are tested at 95% each, so about 1-2 crossings of a boundary are expected by chance. Verdicts near a threshold are therefore called unresolved, not thin.

**Data gaps: excluded and listed, not read as thin**
- G1 dates from the 2026-10-16 coverage triage are excluded, together with the next server date (a UTC gap day also hits the following server day): 2020-05-26..28, 2021-11-05, 2022-01-31, 2023-11-08, 2024-01-10..12, 2024-06-07, 2024-07-01, 2024-07-16, 2025-02-26..28, 2025-03-26..28, 2025-05-12..14.
- The triage's small losses and Sunday-reopen artefacts are excluded on server dates 2012-10-01, 2013-07-01, 2014-08-25, 2015-07-01, 2017-05-01, 2018-10-01, 2019-03-11, 2019-07-01 and 2023-05-01.
- Holiday-type instances excluded this way (7):

| Day type | Dates | Reason |
|---|---|---|
| UK early May | 2017-05-01, 2023-05-01 | Reopen artefact |
| UK summer | 2014-08-25 | Small loss |
| CN spring festival | 2022-01-31, 2022-02-01 | G1 |
| CN national day | 2012-10-01, 2018-10-01 | Reopen artefact |

- 241 control slots were dropped as not clean.
- G2 (2026) is entirely unread, along with the holdout. That means Thanksgiving 2025 and December 2025 are unread too.

**Closed on the feed (fewer than 60 bars), not in the ratios**
- New Year's Day (or its observed day): 13/13. 2012-01-02 had 1 bar, 2013-01-01 had 6, the rest 0.
- 25 Dec (or its observed day): 14/15. The open one is 2010-12-24, with 817 bars.
- Good Friday: 13/16 closed. The three open ones are 2010 (1,099 bars), 2011 (886) and 2012 (558).

**Table 1: whole day, holiday divided by matched controls, geometric mean [95% CI]**

| Day type | Weekdays | Closed | Excluded | n | Quote frac | Volume/h | Volume per quoted min | Spread | 15m ATR | Spr/ATR | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Listed** | | | | | | | | | | | |
| New Year (observed) | 13 | 13 | 0 | 0 | - | - | - | - | - | - | CLOSED |
| 2 Jan | 8 | 0 | 0 | 8 | 1.00 | 0.98 [0.87,1.10] | 0.98 | 1.00 [0.98,1.02] | 1.17 [1.00,1.37] | 0.86 [0.74,1.00] | **NORMAL** |
| MLK | 16 | 0 | 0 | 16 | 0.84 | 0.61 [0.51,0.73] | 0.73 | 0.99 [0.97,1.00] | 0.73 [0.64,0.84] | 1.35 [1.18,1.53] | THIN |
| Presidents | 16 | 0 | 0 | 16 | 0.86 | 0.56 [0.47,0.67] | 0.65 | 1.00 [0.98,1.03] | 0.69 [0.58,0.82] | 1.42 [1.24,1.64] | THIN |
| Good Friday | 16 | 13 | 0 | 3 | 0.60 | 0.56 [0.12,2.49] | 0.93 | 1.12 [0.70,1.78] | 0.80 | 1.39 [0.56,3.44] | CLOSED (13/16) |
| Easter Monday | 16 | 0 | 0 | 16 | 1.00 | 0.88 [0.80,0.97] | 0.87 | 1.05 [1.00,1.09] | 0.93 [0.85,1.03] | 1.12 [0.99,1.26] | UNRESOLVED |
| Memorial | 16 | 0 | 0 | 16 | 0.86 | 0.52 [0.44,0.62] | 0.60 | 1.03 [1.00,1.06] | 0.67 [0.57,0.78] | 1.57 [1.33,1.85] | THIN |
| Juneteenth (2022+) | 4 | 0 | 0 | 4 | 0.89 | 0.63 [0.46,0.87] | 0.71 | 0.99 [0.91,1.08] | 0.78 [0.56,1.08] | 1.29 [0.92,1.81] | UNRESOLVED (n=4) |
| 4 July (observed) | 16 | 0 | 0 | 16 | 0.85 | 0.58 [0.50,0.68] | 0.68 | 1.08 [0.98,1.19] | 0.71 [0.61,0.82] | 1.52 [1.27,1.81] | THIN |
| Labor | 16 | 0 | 0 | 16 | 0.86 | 0.60 [0.50,0.72] | 0.70 | 1.02 [0.99,1.05] | 0.80 [0.70,0.90] | 1.27 [1.13,1.42] | THIN |
| Thanksgiving | 15 | 0 | 0 | 15 | 0.84 | 0.44 [0.39,0.51] | 0.52 | 1.04 [0.99,1.10] | 0.63 [0.56,0.71] | 1.65 [1.42,1.91] | THIN |
| Day after Thanksgiving | 15 | 0 | 0 | 15 | 0.87 | 0.75 [0.70,0.80] | 0.86 | 1.02 [0.99,1.04] | 0.94 [0.83,1.07] | 1.07 [0.95,1.21] | UNRESOLVED |
| 24 Dec | 8 | 0 | 0 | 8 | 0.86 | 0.50 [0.35,0.72] | 0.59 | 1.05 [1.01,1.10] | 0.76 [0.57,1.01] | 1.39 [1.04,1.87] | THIN |
| 25 Dec (observed) | 15 | 14 | 0 | 1 | - | - | - | - | - | - | CLOSED (14/15) |
| 26 Dec | 8 | 0 | 0 | 8 | 0.98 | 0.54 [0.39,0.75] | 0.56 | 1.03 [1.01,1.06] | 0.79 [0.65,0.97] | 1.29 [1.07,1.55] | UNRESOLVED (at the threshold) |
| 31 Dec | 10 | 0 | 0 | 10 | 1.00 | 0.65 [0.57,0.74] | 0.65 | 1.09 [0.98,1.21] | 0.80 [0.66,0.98] | 1.34 [1.06,1.70] | THIN |
| **Not listed** | | | | | | | | | | | |
| Columbus | 15 | 0 | 0 | 15 | 0.97 | 0.92 [0.80,1.06] | 0.94 | 1.02 [1.00,1.05] | 1.05 | 0.97 [0.90,1.04] | NORMAL |
| Veterans | 11 | 0 | 0 | 11 | 1.00 | 1.00 [0.86,1.15] | 0.99 | 1.01 [0.99,1.04] | 1.08 | 0.93 [0.77,1.12] | NORMAL |
| 3 Jul (not observed) | 9 | 0 | 0 | 9 | 1.00 | 1.11 [1.02,1.21] | 1.11 | 1.01 [0.99,1.03] | 1.05 | 0.95 [0.85,1.06] | NORMAL |
| Juneteenth date, 2010-2021 | 12 | 0 | 0 | 12 | 1.00 | 1.06 [0.93,1.22] | 1.06 | 0.98 [0.91,1.05] | 1.00 | 0.99 [0.86,1.15] | NORMAL |
| 27-30 Dec | 44 | 0 | 0 | 44 | 1.00 | 0.78 [0.71,0.85] | 0.78 | 1.08 [1.03,1.12] | 0.81 [0.76,0.85] | 1.33 [1.24,1.43] | UNRESOLVED (lower bound 1.24 vs 1.25) |
| JP 3 Jan | 12 | 0 | 0 | 12 | 1.00 | 1.06 [0.91,1.24] | 1.06 | 1.04 [0.97,1.11] | 1.12 | 0.92 [0.81,1.05] | NORMAL |
| JP Golden Week | 49 | 0 | 0 | 49 | 1.00 | 0.95 [0.88,1.04] | 0.95 | 1.01 [0.99,1.03] | 1.03 | 0.97 [0.92,1.03] | NORMAL |
| CN Spring Festival (eve to +5) | 80 | 0 | 2 | 78 | 0.99 | 0.95 [0.90,1.00] | 0.96 | 1.01 [1.00,1.02] | 0.99 | 1.01 [0.97,1.06] | NORMAL |
| CN National Day (1-7 Oct) | 75 | 0 | 2 | 73 | 1.00 | 0.97 [0.92,1.01] | 0.97 | 1.01 [1.00,1.02] | 1.05 | 0.95 [0.92,1.00] | NORMAL |
| UK early May | 16 | 0 | 2 | 14 | 1.00 | 0.97 [0.85,1.10] | 0.97 | 1.04 [1.00,1.09] | 0.95 | 1.09 [0.97,1.23] | NORMAL |
| UK summer bank holiday | 16 | 0 | 1 | 15 | 1.00 | 0.85 [0.64,1.12] | 0.85 | 1.01 [0.98,1.05] | 0.95 | 1.05 [0.92,1.20] | UNRESOLVED |
| UK one-off holidays (2011, 2012, 2022, 2023) | 7 | 0 | 0 | 7 | 1.00 | 0.96 [0.59,1.58] | 0.96 | 0.99 [0.95,1.04] | 0.85 [0.74,0.97] | 1.15 [0.96,1.37] | UNRESOLVED (n=7) |

**Table 2: by session (volume/h and spr/ATR ratios) for the types that matter**

| Day type | Asia volume/h | London volume/h | NY volume/h | Asia spr/ATR | London spr/ATR | NY spr/ATR |
|---|---|---|---|---|---|---|
| MLK | 0.91 [0.76,1.11] | 0.77 [0.65,0.92] | 0.31 [0.24,0.41] | 1.07 [0.92,1.24] | 1.36 [1.19,1.56] | 1.84 [1.59,2.13] |
| Presidents | 0.80 [0.68,0.94] | 0.71 [0.58,0.87] | 0.30 [0.23,0.40] | 1.11 [0.93,1.33] | 1.41 [1.23,1.62] | 1.81 [1.52,2.16] |
| Memorial | 0.80 [0.71,0.91] | 0.61 [0.49,0.75] | 0.29 [0.23,0.37] | 1.24 [1.07,1.45] | 1.62 [1.38,1.91] | 1.95 [1.62,2.36] |
| Juneteenth (2022+, n=4) | 0.96 [0.56,1.65] | 0.81 [0.65,1.01] | 0.40 [0.31,0.50] | 1.03 [0.66,1.59] | 1.17 [0.97,1.41] | 1.58 [1.04,2.42] |
| 4 July | 0.92 [0.79,1.08] | 0.74 [0.66,0.84] | 0.31 [0.24,0.39] | 1.15 [0.93,1.42] | 1.40 [1.18,1.67] | 2.11 [1.74,2.57] |
| Labor | 0.91 [0.75,1.11] | 0.78 [0.62,0.98] | 0.33 [0.25,0.42] | 1.02 [0.87,1.19] | 1.18 [1.07,1.31] | 1.72 [1.54,1.93] |
| Thanksgiving | 0.78 [0.71,0.86] | 0.60 [0.52,0.69] | 0.20 [0.16,0.25] | 1.14 [1.01,1.27] | 1.53 [1.32,1.77] | 2.47 [1.94,3.15] |
| Day after Thanksgiving | 0.89 [0.81,0.97] | 0.88 [0.76,1.02] | 0.61 [0.53,0.70] | 1.07 [0.93,1.24] | 1.00 [0.86,1.17] | 1.02 [0.86,1.22] |
| Easter Monday | 0.96 [0.83,1.10] | 0.67 [0.55,0.81] | 0.91 [0.81,1.02] | 0.99 [0.84,1.17] | 1.37 [1.20,1.57] | 1.05 [0.90,1.22] |
| 24 Dec | 0.72 [0.54,0.95] | 0.56 [0.37,0.85] | 0.35 [0.23,0.54] | 1.39 [1.11,1.74] | 1.35 [0.99,1.83] | 1.35 [0.92,1.96] |
| 26 Dec | 0.74 [0.52,1.07] | 0.44 [0.29,0.68] | 0.56 [0.39,0.80] | 0.92 [0.71,1.20] | 1.40 [1.00,1.96] | 1.52 [1.23,1.89] |
| 31 Dec | 0.79 [0.61,1.02] | 0.60 [0.51,0.69] | 0.62 [0.50,0.77] | 1.42 [1.23,1.63] | 1.39 [1.07,1.80] | 1.24 [0.92,1.67] |
| 27-30 Dec | 0.90 [0.82,1.00] | 0.79 [0.71,0.89] | 0.72 [0.64,0.80] | 1.26 [1.19,1.33] | 1.26 [1.15,1.39] | 1.40 [1.27,1.54] |
| CN Spring Festival | 0.83 [0.78,0.89] | 0.98 | 0.99 | 1.13 [1.07,1.19] | 1.00 | 0.96 |
| CN National Day | 0.83 [0.78,0.89] | 0.95 | 1.04 | 1.03 [0.97,1.09] | 0.98 | 0.89 [0.84,0.94] |
| JP Golden Week | 0.80 [0.73,0.88] | 0.87 [0.79,0.96] | 1.05 | 1.06 [0.98,1.16] | 1.01 | 0.91 [0.86,0.97] |
| UK summer | 0.95 | 0.70 [0.49,0.99] | 0.88 | 0.95 | 1.14 [0.98,1.33] | 1.09 |

**Table 3: robustness by period (whole day; volume/h, spr/ATR)**

| Group | 2010-2021 | 2022-2025-09 |
|---|---|---|
| US Monday-type holidays (MLK, Presidents, Memorial, Labor, Juneteenth) | 0.55 [0.50,0.61], 1.44 [1.32,1.56] (n=48) | 0.63 [0.56,0.71], 1.29 [1.19,1.39] (n=20) |
| Same group, New York session | 0.30 [0.26,0.35], 1.86 [1.69,2.05] | 0.34 [0.30,0.40], 1.71 [1.60,1.84] |
| 4 July | 0.57, 1.51 [1.19,1.92] (n=12) | 0.61, 1.52 [1.19,1.95] (n=4) |
| Thanksgiving | 0.42, 1.71 [1.43,2.04] (n=12) | 0.52, 1.44 [0.87,2.37] (n=3) |
| 27-30 Dec | 0.75 [0.68,0.82], 1.36 [1.25,1.49] (n=35) | 0.91 [0.74,1.12], 1.22 [1.09,1.36] (n=9) |

The US-holiday effect holds in both periods. It is somewhat smaller since 2022. Since 2022, US holiday days show about 1,227 bars against about 1,139 before, which looks like a shorter feed halt; this is noise-level for the verdicts. The 27-30 Dec effect is weaker after 2022 and is not stable.

**Findings**
1. **The spread is not the channel; the range is.** On Dukascopy the median spread moves only x0.96-1.12 on any day type. On US early-close holidays the 15m ATR falls to x0.63-0.80, so the cost of a spread in range units rises x1.27-1.65 for the whole day. In the New York session it rises x1.7-2.5, where volume is x0.20-0.40. For ATR-scaled stops this is the cost in R. On a normal day spr/ATR is 0.19; on Thanksgiving it is about 0.31.
2. **The thinness sits in London and New York.** The Asia session on US holidays has volume x0.78-0.92 and spr/ATR x1.02-1.24. Five of the seven Asia intervals include 1.00 or touch 1.07; Memorial and Thanksgiving are the exceptions. A whole-day block mostly throws away usable Asia hours (hypothesis H-C).
3. **The effect follows the CME holiday, not the date.** The 19 June date was normal before CME observed Juneteenth: 2010-2021 volume 1.06 [0.93,1.22], spr/ATR 0.99 [0.86,1.15], n=12. From 2022 its New York volume is 0.40 [0.31,0.50]. This is a natural experiment supporting the US-holiday proxy. Its n=4 is too small to stand alone.
4. **2 January is a normal day** (n=8): volume 0.98 [0.87,1.10], spr/ATR 0.86 [0.74,1.00]. Blocking it costs signals for nothing.
5. **Easter Monday is a London-only effect**: London volume 0.67 [0.55,0.81] and spr/ATR 1.37 [1.20,1.57]; Asia and New York are normal. The current list format cannot scope a block to a session.
6. **Day after Thanksgiving:** the open hours cost normally (spr/ATR 1.07 [0.95,1.21], New York 1.02). The lower volume (0.75) is mostly the early close (quote fraction 0.87), which the broker's short session already blocks as `market_closed`.
7. **Asian and UK holidays are normal over the whole day.** CN and JP holidays thin the Asia session (volume 0.80-0.83) without a matching cost rise (Asia spr/ATR 1.03-1.13). UK early May is normal. UK summer is unresolved: London volume 0.70 [0.49,0.99], spr/ATR 1.14 [0.98,1.33].
8. **27-30 December** (not listed; the playbook's "24 Dec-2 Jan" window): spr/ATR 1.33 [1.24,1.43], n=44. It misses the pre-stated THIN bar by 0.01 and is weaker since 2022. On the rule it is not added.

**Recommendation for cme-metals-v3** (owner adopts; implementer edits the list and the hand-reviewed set in `tests/test_entry_windows.py`; quant review)

| Day | Recommendation | Basis |
|---|---|---|
| 2 Jan | Drop | NORMAL (finding 4) |
| MLK, Presidents, Memorial, Juneteenth, 4 July, Labor, Thanksgiving, 24 Dec, 31 Dec | Keep | THIN (Juneteenth via finding 3 and Table 3) |
| New Year, Good Friday, 25 Dec | Keep | Closed on the feed: no signal cost, and the block is fail-safe if the broker's session list is wrong |
| Easter Monday, day after Thanksgiving, 26 Dec | Keep in v3, marked unresolved | Test under H-A and H-C. Do not drop before then. |
| 27-30 Dec, UK, JP and CN holidays | Do not add | Not THIN by the rule |

**Limits**
- The liquidity measure is Dukascopy's volume, not ticks or broker volume.
- The spread is Dukascopy's, not IC Markets'. Broker spreads on holidays may widen where Dukascopy's do not.
- The CN Spring Festival window is approximated as the eve to day +5.
- In the DST-gap weeks the feed loses about 59 bars a day against the Athens table. Controls within 14 days can cross that boundary, which affects quote fraction by 4% or less. It matters most for Good Friday and Easter Monday.
- The CIs are not corrected for multiple tests (about 28 types).

**Commands** (repo `.venv` Python, run with `-I` from a clean directory)
1. `scripts/fetch_data_release.py --download-only` (cwd = an empty directory) gives 17 files in `raw/`. Hash them with `shasum -a 256 raw/*.parquet`.
2. `daystats.py raw daystats.parquet` gives the per-day and per-session statistics and the baseline. The baseline comes from the inline groupby over days with at least 1,300 bars.
3. `holidays45.py daystats.parquet .` gives Tables 1-2 and the closed and excluded lists (`summary.csv`, `instances.csv`, `closed.csv`, `excluded.csv`).
4. `robust45.py instances.csv` gives Table 3 and the open Good Friday instances.
5. Bar-count evidence (early closes, the 1,139 to 1,227 shift, closures): an inline listing of weekdays with fewer than 700 bars, and with between 700 and nominal-minus-30, from `daystats.parquet`.

The scripts and outputs are kept outside the repo, in the analyst's temp folder (`backlog45/`: `daystats.py`, `holidays45.py`, `robust45.py`, `*.csv`, `*.out`). For the final version (2026-10-30), commit them as `scripts/measure_holidays.py` with a test, so the numbers can be re-run after the G1 repair.

**Open for quant ruling**
1. **Volume for ticks.** data-v1 has no tick count: its `tick_count` column is Dukascopy's `volume_bid` (`scripts/dukascopy_year.py:163`), and no tick store exists until the deploy. "Ticks per hour" in the BACKLOG 45 criteria is replaced here by Dukascopy volume per nominal open hour (`vol_ph`) and per quoted minute (`vol_pq`). Ruling needed: is this an acceptable proxy for the final, or must the final wait for broker ticks?
2. **The Dukascopy spread for the broker's.** Every spread figure is Dukascopy's 1m close spread, not IC Markets'. The broker's holiday spreads stay unmeasured until the engine runs (forward check: H-A). Ruling needed: can the list be changed on the Dukascopy evidence, with H-A as confirmation, or only after H-A?
3. **G1/G2 as the only gap days.** Of the coverage-triage items G1-G5, only G1 (2020-2025) and G2 (2026) are gaps in days of data. G3-G5 are process items (recount, digests, workflow minutes) with no gap dates. This draft excludes the G1 dates plus the following server date, and the triage's small losses and reopen artefacts; G2 is unread together with the holdout. Ruling needed: confirm this exclusion set, and whether the final is re-run after the G1 repair (due 2026-11-06).

**Hypotheses for pre-registration** (at most three; they read data not used here)
- **H-A (broker check of the proxy, forward).**
  - Data: the IC Markets demo feed, 2026-10-18 to 2027-04-30. Listed THIN days in that window: Thanksgiving 2026-11-26, 24 and 31 Dec 2026, MLK 2027-01-18, Presidents 2027-02-15.
  - Measure: broker median spread divided by the 15m ATR, against same-weekday controls 1 and 2 weeks either side.
  - Rule: the v3 THIN set is confirmed if the pooled geometric-mean ratio is at least 1.25 with a lower 95% bound above 1.0. It is refuted if the upper bound is at most 1.10. Otherwise inconclusive. With fewer than 4 clean days the result is inconclusive.
  - Secondary, read the same way: the day after Thanksgiving, 26-30 Dec 2026 and Easter Monday 2027-03-29.
- **H-B (cost in R, research window).**
  - Data: the frozen Q1 candidate sets (H-01, H-04, C4H; costs as each row pins).
  - Measure: candidates entered on a v3 THIN day against candidates on their matched control days (same weekday, 7 and 14 days either side), net mean R, with a block-bootstrap 95% interval.
  - Rule: the whole-day block is kept if THIN-day net mean R is lower by at least 0.05 R and the interval excludes 0. Otherwise the block is reported as unproven for that family. Below 30 THIN-day candidates the family's reading is inconclusive.
- **H-C (session-scoped block).**
  - Claim: on US early-close holidays, Asia-session entries are not worse.
  - Measure: net mean R of Asia-session candidates (H-04 `asia_drift`, and H-01 where entry falls in Asia) on those days against matched controls.
  - Rule: non-inferior if the lower 95% bound of the difference is above -0.03 R. If so, the holiday rule is re-specified to block London and New York only, which needs a list format change and a trading-safety review. Otherwise the whole-day block stays.
