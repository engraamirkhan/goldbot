# Holiday list measurement (BACKLOG 45): draft, 2026-10-16

Draft by the performance analyst (feda07a). Quant-reviewer ruling 2026-10-16: **CHANGES; the cme-metals-v3 list is
SAFE TO ADOPT** (text fixes M1-M7; H-A, H-B and H-C rewritten before pre-registration). The research director
applied the ruling below on 2026-10-16. Final, with the re-run script, due 2026-10-30.

**Question.** Are the days in `config/holidays_cme_metals.yaml` (cme-metals-v2) thin compared with normal days? Is any day missing from the list thin?

**Scope.** This is a cost and liquidity measurement. It has no R and no model, so THIN does not mean "trades lose":
it means one spread costs more of the range. Whether blocked entries would have lost is H-B, H-C and BACKLOG 46.

**Data**
- Release `data-v1`, 1m XAUUSD bars, 17 yearly files downloaded 2026-10-16.
- sha256 prefixes: 2010 `26139f0b026b`, 2011 `a1497c69398a`, 2012 `89f4b77dbfe3`, 2013 `fe31b8442902`, 2014 `be2bf066f0c4`, 2015 `046ddd848fae`, 2016 `9c6073ccee32`, 2017 `e04d7da0d7e9`, 2018 `a8a93abfe5be`, 2019 `f5e43125c916`, 2020 `7a8faf3ddc34`, 2021 `975af8633f03`, 2022 `cfeb833d900e`, 2023 `5ba2c40b0380`, 2024 `0fd98a09f95d`, 2025 `c2a2492ed163`. The 2026 file (`b6d2e2078ab6`) is not read.
- Window: 2010-01-04 to 2025-09-30. Nothing at or after `research.holdout_from` = 2025-10-01 is read, so the 2026 file is out (holdout, and G2 with bid-imputed months).
- Liquidity measure: **Dukascopy volume_bid**. data-v1 has no tick count: its `tick_count` column is Dukascopy's `volume_bid` (`scripts/dukascopy_year.py:163`). Broker ticks are measured forward under H-A.

**Method**
- **The day** is the broker server date (Europe/Athens), the same date RiskGate uses.
- **Sessions** come from `DEFAULT_SESSIONS.session_label`: asia, london, newyork. Nominal open minutes come from the static session table, which has no holidays in it.
- **Per day and per session:**
  - `vol_ph`: Dukascopy volume_bid per nominal open hour. Closed minutes count as zero.
  - `vol_pq`: Dukascopy volume_bid per quoted minute, so open minutes only.
  - `spread`: median 1m close spread (Dukascopy).
  - `atr15`: mean 15m true range of the mid price.
  - `spr/ATR`: the cost of one spread as a share of a 15m range.
  - `quote_frac`: bars present divided by nominal minutes.
- **Controls:** the same weekday 7 and 14 days either side. A control is dropped if it is a holiday or candidate day of any type, a gap-affected day, or has fewer than 1,300 bars. An instance needs at least 2 clean controls.
- **Ratio:** each instance's value divided by the median of its controls. Ratios are pooled per day type as a geometric mean with a 95% t-interval on the logs.
- **Closed and excluded days:** an instance with fewer than 60 bars counts as closed and is left out of the ratios.
- **Unlisted types never count a listed day.** An unlisted-type instance that falls on a listed day is dropped from the unlisted type: CN Spring Festival × Presidents' Day on 2010-02-15, 2018-02-19 and 2021-02-15 (CN Spring Festival n 78 -> 75). No other unlisted instance falls on a listed day.
- **Normal-day baseline** (weekdays with at least 1,300 bars, n=3,904): spread 0.338, 15m ATR 1.80, spr/ATR 0.190. By session, spr/ATR is 0.268 in asia, 0.178 in london and 0.153 in newyork.

**Rule (written before the ratios were read; not independently timestamped)**
- **THIN:** whole-day `vol_ph` upper 95% bound < 0.75, or spread lower bound > 1.25, or spr/ATR lower bound > 1.25.
- **NORMAL:** `vol_ph` lower bound >= 0.75, spread upper bound <= 1.25 and spr/ATR upper bound <= 1.25.
- **UNRESOLVED:** anything else. On a listed day UNRESOLVED means keep; on an unlisted day it means do not add.

The rule was written in the analyst's working notes before the ratios were computed, but no commit or timestamp
proves the order, so Table 4 shows how each call moves under other thresholds and intervals. **Standard from
2026-10-16 (research director):** a measurement's rule is committed to the repo before the run that reads its data,
and the report cites that commit.

**Multiplicity.** About 28 day types are tested at 95% each. The thresholds (0.75 / 1.25), not the interval width,
carry the margin. Under Bonferroni across 28 types (Table 4) the only loosening this note proposes, 2 Jan NORMAL,
holds; only Memorial and Thanksgiving stay THIN, every other listed type becomes UNRESOLVED, and UNRESOLVED also means
keep. Multiplicity therefore changes no recommendation.

**Data gaps: excluded and listed, not read as thin**
- Source: the G1 coverage triage of 2026-10-16, `docs/handoff.d/2026-10-16-coverage-triage.md` at 7bc4ceb.
- G1 dates are excluded, together with the next server date (a UTC gap day also hits the following server day): 2020-05-26..28, 2021-11-05, 2022-01-31, 2023-11-08, 2024-01-10..12, 2024-06-07, 2024-07-01, 2024-07-16, 2025-02-26..28, 2025-03-26..28, 2025-05-12..14.
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
- **Re-run addendum after the G1 repair:** the measurement is re-run on the repaired data-v1 in the same run as the
  H-04 recount (the merged count-owed-events recount, after G1 closes on 2026-11-06), by **2026-11-13**, and the
  addendum records the repaired digests and any verdict that moves. Accountable: performance-analyst; dispatched with
  the recount by the research director.

**Closed on the feed (fewer than 60 bars), not in the ratios**
- New Year's Day (or its observed day): 13/13. 2012-01-02 had 1 bar, 2013-01-01 had 6, the rest 0.
- 25 Dec (or its observed day): 14/15. The open one is 2010-12-24, with 817 bars.
- Good Friday: 13/16 closed. The three open ones are 2010 (1,099 bars), 2011 (886) and 2012 (558).

**Instance screen: short open days with no explanation.** Early closes on US holidays (about 1,080-1,280 bars), 24 Dec
and the day after Thanksgiving are expected. Three instances are short with no known cause. They stay in the ratios
(no pre-stated rule removes them) and none changes a verdict:

| Instance | Bars | Quote frac | volume_bid/h | spr/ATR | Verdict without it |
|---|---|---|---|---|---|
| 26 Dec 2013 | 1,192 | 0.87 | 0.29 | 1.81 | 26 Dec still UNRESOLVED: volume_bid/h 0.59 [0.44,0.80], spr/ATR 1.23 [1.03,1.46], n=7 |
| Columbus 2013-10-14 | 1,130 | 0.82 | 1.48 | 0.85 | Columbus still NORMAL (both dropped): volume_bid/h 0.91 [0.79,1.04], spr/ATR 1.00 [0.93,1.07], n=13 |
| Columbus 2014-10-13 | 1,139 | 0.83 | 0.74 | 0.74 | (as above) |

The 2013 and 2014 Columbus Days look like a US-holiday early close on the feed on a day CME metals trade normally; the
G1 re-run checks whether they survive the repair.

**Table 1: whole day, holiday divided by matched controls, geometric mean [95% CI]**

| Day type | Weekdays | Closed | Excluded | n | Quote frac | volume_bid/h | volume_bid per quoted min | Spread | 15m ATR | Spr/ATR | Verdict | Verdict (vol_pq) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Listed** | | | | | | | | | | | | |
| New Year (observed) | 13 | 13 | 0 | 0 | - | - | - | - | - | - | CLOSED | - |
| 2 Jan | 8 | 0 | 0 | 8 | 1.00 | 0.98 [0.87,1.10] | 0.98 | 1.00 [0.98,1.02] | 1.17 [1.00,1.37] | 0.86 [0.74,1.00] | **NORMAL** | NORMAL |
| MLK | 16 | 0 | 0 | 16 | 0.84 | 0.61 [0.51,0.73] | 0.73 | 0.99 [0.97,1.00] | 0.73 [0.64,0.84] | 1.35 [1.18,1.53] | THIN | UNRESOLVED |
| Presidents | 16 | 0 | 0 | 16 | 0.86 | 0.56 [0.47,0.67] | 0.65 | 1.00 [0.98,1.03] | 0.69 [0.58,0.82] | 1.42 [1.24,1.64] | THIN | UNRESOLVED |
| Good Friday | 16 | 13 | 0 | 3 | 0.60 | 0.56 [0.12,2.49] | 0.93 | 1.12 [0.70,1.78] | 0.80 | 1.39 [0.56,3.44] | CLOSED (13/16) | - |
| Easter Monday | 16 | 0 | 0 | 16 | 1.00 | 0.88 [0.80,0.97] | 0.87 | 1.05 [1.00,1.09] | 0.93 [0.85,1.03] | 1.12 [0.99,1.26] | UNRESOLVED | UNRESOLVED |
| Memorial | 16 | 0 | 0 | 16 | 0.86 | 0.52 [0.44,0.62] | 0.60 | 1.03 [1.00,1.06] | 0.67 [0.57,0.78] | 1.57 [1.33,1.85] | THIN | THIN |
| Juneteenth (2022+) | 4 | 0 | 0 | 4 | 0.89 | 0.63 [0.46,0.87] | 0.71 | 0.99 [0.91,1.08] | 0.78 [0.56,1.08] | 1.29 [0.92,1.81] | UNRESOLVED (n=4) | UNRESOLVED |
| 4 July (observed) | 16 | 0 | 0 | 16 | 0.85 | 0.58 [0.50,0.68] | 0.68 | 1.08 [0.98,1.19] | 0.71 [0.61,0.82] | 1.52 [1.27,1.81] | THIN | THIN |
| Labor | 16 | 0 | 0 | 16 | 0.86 | 0.60 [0.50,0.72] | 0.70 | 1.02 [0.99,1.05] | 0.80 [0.70,0.90] | 1.27 [1.13,1.42] | THIN | UNRESOLVED |
| Thanksgiving | 15 | 0 | 0 | 15 | 0.84 | 0.44 [0.39,0.51] | 0.52 | 1.04 [0.99,1.10] | 0.63 [0.56,0.71] | 1.65 [1.42,1.91] | THIN | THIN |
| Day after Thanksgiving | 15 | 0 | 0 | 15 | 0.87 | 0.75 [0.70,0.80] | 0.86 | 1.02 [0.99,1.04] | 0.94 [0.83,1.07] | 1.07 [0.95,1.21] | UNRESOLVED | NORMAL |
| 24 Dec | 8 | 0 | 0 | 8 | 0.86 | 0.50 [0.35,0.72] | 0.59 | 1.05 [1.01,1.10] | 0.76 [0.57,1.01] | 1.39 [1.04,1.87] | THIN | UNRESOLVED |
| 25 Dec (observed) | 15 | 14 | 0 | 1 | - | - | - | - | - | - | CLOSED (14/15) | - |
| 26 Dec | 8 | 0 | 0 | 8 | 0.98 | 0.54 [0.39,0.75] | 0.56 | 1.03 [1.01,1.06] | 0.79 [0.65,0.97] | 1.29 [1.07,1.55] | UNRESOLVED | THIN |
| 31 Dec | 10 | 0 | 0 | 10 | 1.00 | 0.65 [0.57,0.74] | 0.65 | 1.09 [0.98,1.21] | 0.80 [0.66,0.98] | 1.34 [1.06,1.70] | THIN | THIN |
| **Not listed** | | | | | | | | | | | | |
| Columbus | 15 | 0 | 0 | 15 | 0.97 | 0.92 [0.80,1.06] | 0.94 | 1.02 [1.00,1.05] | 1.05 | 0.97 [0.90,1.04] | NORMAL | NORMAL |
| Veterans | 11 | 0 | 0 | 11 | 1.00 | 1.00 [0.86,1.15] | 0.99 | 1.01 [0.99,1.04] | 1.08 | 0.93 [0.77,1.12] | NORMAL | NORMAL |
| 3 Jul (not observed) | 9 | 0 | 0 | 9 | 1.00 | 1.11 [1.02,1.21] | 1.11 | 1.01 [0.99,1.03] | 1.05 | 0.95 [0.85,1.06] | NORMAL | NORMAL |
| Juneteenth date, 2010-2021 | 12 | 0 | 0 | 12 | 1.00 | 1.06 [0.93,1.22] | 1.06 | 0.98 [0.91,1.05] | 1.00 | 0.99 [0.86,1.15] | NORMAL | NORMAL |
| 27-30 Dec | 44 | 0 | 0 | 44 | 1.00 | 0.78 [0.71,0.85] | 0.78 | 1.08 [1.03,1.12] | 0.81 [0.76,0.85] | 1.33 [1.24,1.43] | UNRESOLVED | UNRESOLVED |
| JP 3 Jan | 12 | 0 | 0 | 12 | 1.00 | 1.06 [0.91,1.24] | 1.06 | 1.04 [0.97,1.11] | 1.12 | 0.92 [0.81,1.05] | NORMAL | NORMAL |
| JP Golden Week | 49 | 0 | 0 | 49 | 1.00 | 0.95 [0.88,1.04] | 0.95 | 1.01 [0.99,1.03] | 1.03 | 0.97 [0.92,1.03] | NORMAL | NORMAL |
| CN Spring Festival (eve to +5) | 80 | 0 | 2 G1 + 3 listed | 75 | 1.00 | 0.97 [0.92,1.01] | 0.97 | 1.01 [1.00,1.02] | 1.01 | 1.00 [0.95,1.04] | NORMAL | NORMAL |
| CN National Day (1-7 Oct) | 75 | 0 | 2 | 73 | 1.00 | 0.97 [0.92,1.01] | 0.97 | 1.01 [1.00,1.02] | 1.05 | 0.95 [0.92,1.00] | NORMAL | NORMAL |
| UK early May | 16 | 0 | 2 | 14 | 1.00 | 0.97 [0.85,1.10] | 0.97 | 1.04 [1.00,1.09] | 0.95 | 1.09 [0.97,1.23] | NORMAL | NORMAL |
| UK summer bank holiday | 16 | 0 | 1 | 15 | 1.00 | 0.85 [0.64,1.12] | 0.85 | 1.01 [0.98,1.05] | 0.95 | 1.05 [0.92,1.20] | UNRESOLVED | UNRESOLVED |
| UK one-off holidays (2011, 2012, 2022, 2023) | 7 | 0 | 0 | 7 | 1.00 | 0.96 [0.59,1.58] | 0.96 | 0.99 [0.95,1.04] | 0.85 [0.74,0.97] | 1.15 [0.96,1.37] | UNRESOLVED (n=7) | UNRESOLVED |

**Verdict (vol_pq)** reads the same rule with `vol_pq` in place of `vol_ph`, so the early close no longer counts as
thinness. MLK, Presidents, Labor and 24 Dec go from THIN to UNRESOLVED; the day after Thanksgiving goes from
UNRESOLVED to NORMAL (its low volume is the early close, finding 6); 26 Dec goes from UNRESOLVED to THIN. **The keeps
do not change:** THIN and UNRESOLVED both keep a listed day, 2 Jan is NORMAL either way, and no unlisted type becomes
THIN.

**Table 2: by session (volume_bid/h and spr/ATR ratios) for the types that matter**

| Day type | Asia volume_bid/h | London volume_bid/h | NY volume_bid/h | Asia spr/ATR | London spr/ATR | NY spr/ATR |
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
| CN Spring Festival (n=75) | 0.84 [0.78,0.90] | 1.00 [0.93,1.06] | 1.02 [0.96,1.08] | 1.12 [1.06,1.18] | 0.98 [0.93,1.04] | 0.93 [0.88,0.99] |
| CN National Day | 0.83 [0.78,0.89] | 0.95 | 1.04 | 1.03 [0.97,1.09] | 0.98 | 0.89 [0.84,0.94] |
| JP Golden Week | 0.80 [0.73,0.88] | 0.87 [0.79,0.96] | 1.05 | 1.06 [0.98,1.16] | 1.01 | 0.91 [0.86,0.97] |
| UK summer | 0.95 | 0.70 [0.49,0.99] | 0.88 | 0.95 | 1.14 [0.98,1.33] | 1.09 |

**Table 3: robustness by period (whole day; volume_bid/h, spr/ATR)**

| Group | 2010-2021 | 2022-2025-09 |
|---|---|---|
| US Monday-type holidays (MLK, Presidents, Memorial, Labor, Juneteenth) | 0.55 [0.50,0.61], 1.44 [1.32,1.56] (n=48) | 0.63 [0.56,0.71], 1.29 [1.19,1.39] (n=20) |
| Same group, New York session | 0.30 [0.26,0.35], 1.86 [1.69,2.05] | 0.34 [0.30,0.40], 1.71 [1.60,1.84] |
| 4 July | 0.57, 1.51 [1.19,1.92] (n=12) | 0.61, 1.52 [1.19,1.95] (n=4) |
| Thanksgiving | 0.42, 1.71 [1.43,2.04] (n=12) | 0.52, 1.44 [0.87,2.37] (n=3) |
| 27-30 Dec | 0.75 [0.68,0.82], 1.36 [1.25,1.49] (n=35) | 0.91 [0.74,1.12], 1.22 [1.09,1.36] (n=9) |

The US-holiday effect holds in both periods. It is somewhat smaller since 2022. Since 2022, US holiday days show about 1,227 bars against about 1,139 before, which looks like a shorter feed halt; this is noise-level for the verdicts. The 27-30 Dec effect is weaker after 2022 and is not stable.

**Table 4: sensitivity of the verdicts** (quant reviewer's re-run, `qr45/check.py`, on the analyst's `instances.csv`;
CN Spring Festival re-run on the 75 instances without the listed-day overlap. Base = the rule above. Bonferroni =
95% intervals widened for 28 types. Year clusters = one mean log ratio per calendar year, t-interval over years.)

| Day type | Base (0.75/1.25) | 0.70/1.30 | 0.80/1.20 | vol_pq | Bonferroni | Year clusters |
|---|---|---|---|---|---|---|
| **Listed** | | | | | | |
| 2 Jan | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL |
| MLK | THIN | UNRESOLVED | THIN | UNRESOLVED | UNRESOLVED | THIN |
| Presidents | THIN | THIN | THIN | UNRESOLVED | UNRESOLVED | THIN |
| Good Friday (3 open) | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED |
| Easter Monday | UNRESOLVED | NORMAL | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED |
| Memorial | THIN | THIN | THIN | THIN | THIN | THIN |
| Juneteenth (n=4) | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED |
| 4 July | THIN | THIN | THIN | THIN | UNRESOLVED | THIN |
| Labor | THIN | UNRESOLVED | THIN | UNRESOLVED | UNRESOLVED | THIN |
| Thanksgiving | THIN | THIN | THIN | THIN | THIN | THIN |
| Day after Thanksgiving | UNRESOLVED | NORMAL | THIN | NORMAL | UNRESOLVED | UNRESOLVED |
| 24 Dec | THIN | UNRESOLVED | THIN | UNRESOLVED | UNRESOLVED | THIN |
| 26 Dec | UNRESOLVED | UNRESOLVED | THIN | THIN | UNRESOLVED | UNRESOLVED |
| 31 Dec | THIN | UNRESOLVED | THIN | THIN | UNRESOLVED | THIN |
| **Not listed** | | | | | | |
| Columbus | NORMAL | NORMAL | NORMAL | NORMAL | UNRESOLVED | NORMAL |
| Veterans | NORMAL | NORMAL | NORMAL | NORMAL | UNRESOLVED | NORMAL |
| 3 Jul (not observed) | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL |
| Juneteenth date, 2010-2021 | NORMAL | NORMAL | NORMAL | NORMAL | UNRESOLVED | NORMAL |
| **27-30 Dec** | UNRESOLVED | UNRESOLVED | **THIN** | UNRESOLVED | UNRESOLVED | UNRESOLVED |
| JP 3 Jan | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL |
| JP Golden Week | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL |
| CN Spring Festival (n=75) | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL |
| CN National Day | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL | NORMAL |
| UK early May | NORMAL | NORMAL | UNRESOLVED | NORMAL | UNRESOLVED | NORMAL |
| UK summer | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED |
| UK one-offs | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED | UNRESOLVED |

Reading Table 4 against the list: 2 Jan, the only drop, is NORMAL on every reading. Every other listed day is THIN or
UNRESOLVED on every reading except Easter Monday at 0.70/1.30 and the day after Thanksgiving at 0.70/1.30 and on
vol_pq (NORMAL there); both stay listed, because a drop rests on the pre-stated rule, not on a threshold chosen after the fact, and H-A reads
both forward. Among unlisted days, **27-30 Dec is the one call that depends on the threshold**: THIN at 0.80/1.20,
UNRESOLVED on every other reading. It is not added (see finding 8).

**Year-clustered intervals** (whole day, one value per year; volume_bid/h, spread, spr/ATR):

| Day type | Year clusters | volume_bid/h | Spread | spr/ATR |
|---|---|---|---|---|
| 27-30 Dec | 15 (44 instances) | 0.79 [0.69,0.91] | 1.06 [0.99,1.13] | 1.31 [1.17,1.46] |
| CN Spring Festival (n=75) | 16 | 0.96 [0.89,1.04] | 1.01 [1.00,1.02] | 1.00 [0.94,1.07] |
| CN National Day | 15 | 0.97 [0.91,1.04] | 1.01 [0.99,1.02] | 0.95 [0.89,1.02] |
| JP Golden Week | 16 | 0.94 [0.83,1.05] | 1.01 [0.99,1.03] | 0.98 [0.92,1.05] |
| JP 3 Jan | 12 (one per year) | 1.06 [0.91,1.24] | 1.04 [0.97,1.11] | 0.92 [0.81,1.05] |
| UK one-offs | 4 (7 instances) | 1.08 [0.36,3.27] | 0.99 [0.89,1.09] | 1.16 [0.88,1.52] |

Clustering by year widens the multi-day types; it moves no verdict except to make 27-30 Dec less certain, and the UK
one-offs have 4 clusters, too few to read.

**Findings**
1. **On the Dukascopy feed the spread barely moves (x0.96-1.12); the range is the channel.** On US early-close holidays the 15m ATR falls to x0.63-0.80, so the cost of a spread in range units rises x1.27-1.65 for the whole day. In the New York session it rises x1.7-2.5, where Dukascopy volume_bid is x0.20-0.40. For ATR-scaled stops this is the cost in R. On a normal day spr/ATR is 0.19; on Thanksgiving it is about 0.31. The broker's spread may widen on holidays where Dukascopy's does not, so for IC Markets these THIN ratios are probably lower bounds (H-A measures them).
2. **The thinness sits in London and New York.** The Asia session on US holidays has volume_bid x0.78-0.92 and spr/ATR x1.02-1.24. Five of the seven Asia intervals include 1.00 or touch 1.07; Memorial and Thanksgiving are the exceptions. A whole-day block mostly throws away usable Asia hours (hypothesis H-C).
3. **The effect follows the CME holiday, not the date.** The 19 June date was normal before CME observed Juneteenth: 2010-2021 volume_bid 1.06 [0.93,1.22], spr/ATR 0.99 [0.86,1.15], n=12. From 2022 its New York volume_bid is 0.40 [0.31,0.50]. This is a natural experiment supporting the US-holiday proxy. Its n=4 is too small to stand alone.
4. **2 January is a normal day** (n=8): volume_bid 0.98 [0.87,1.10], spr/ATR 0.86 [0.74,1.00]; NORMAL on every reading in Table 4. Blocking it costs signals for nothing.
5. **Easter Monday is a London-only effect**: London volume_bid 0.67 [0.55,0.81] and spr/ATR 1.37 [1.20,1.57]; Asia and New York are normal. The current list format cannot scope a block to a session.
6. **Day after Thanksgiving:** the open hours cost normally (spr/ATR 1.07 [0.95,1.21], New York 1.02). The lower volume_bid per nominal hour (0.75) is mostly the early close (quote fraction 0.87; per quoted minute it is 0.86 and the vol_pq verdict is NORMAL). After the close there are no quotes, so no entries. The engine's `market_closed` does not cover it: that flag comes from the static session table, which is not a holiday calendar.
7. **Asian and UK holidays are normal over the whole day.** CN and JP holidays thin the Asia session (volume_bid 0.80-0.84) without a matching cost rise (Asia spr/ATR 1.03-1.12). UK early May is normal. UK summer is unresolved: London volume_bid 0.70 [0.49,0.99], spr/ATR 1.14 [0.98,1.33].
8. **27-30 December** (not listed; the playbook's "24 Dec-2 Jan" window): spr/ATR 1.33 [1.24,1.43] over 44 instances, but those are four days in each of 15 years; clustered by year it is 1.31 [1.17,1.46]. It is UNRESOLVED on the rule and on every reading in Table 4 except 0.80/1.20, and weaker since 2022 (Table 3). On the rule it is not added; H-A reads 28-30 Dec 2026 as a secondary.

**Recommendation for cme-metals-v3** (implementer edits the list and the hand-reviewed set in `tests/test_entry_windows.py`; quant review)

| Day | Recommendation | Basis |
|---|---|---|
| 2 Jan | Drop | NORMAL on every reading (finding 4, Table 4). **No effect before 2029-01-02:** 2 Jan 2027 is a Saturday and 2 Jan 2028 a Sunday, so the next weekday 2 Jan is 2029-01-02, beyond v2's `valid_through` (2028-12-31) |
| MLK, Presidents, Memorial, 4 July, Labor, Thanksgiving, 24 Dec, 31 Dec | Keep | THIN |
| Juneteenth | Keep, UNRESOLVED (n=4); supported by finding 3 | Not THIN on its own data, so it is not in H-A's THIN set |
| New Year, Good Friday, 25 Dec | Keep | Closed on the feed: no signal cost, and the block is fail-safe if the broker's session list is wrong |
| Easter Monday, day after Thanksgiving, 26 Dec | Keep, marked unresolved | UNRESOLVED means keep. Read forward under H-A; H-C for the session question. Not dropped before then |
| 27-30 Dec, UK, JP and CN holidays | Do not add | Not THIN by the rule (27-30 Dec: Table 4, finding 8) |

- **YAML `source:` line of v3:** `"measured (BACKLOG 45, Dukascopy proxy, feda07a + final sha); unresolved days marked"`,
  with `<final sha>` filled in from the 2026-10-30 final and Juneteenth, Easter Monday, the day after Thanksgiving and
  26 Dec marked unresolved in their labels.
- **Adopting the list does not switch the window on.** `risk.holidays.enabled` stays off. Switching it on stays with
  the owner, after BACKLOG 46 (the replay of what each window filters).

**Limits**
- The liquidity measure is Dukascopy volume_bid, not ticks or broker volume. Broker ticks per quoted minute are measured forward (H-A).
- The spread is Dukascopy's, not IC Markets'. Broker spreads on holidays may widen where Dukascopy's do not (finding 1).
- The CN Spring Festival window is approximated as the eve to day +5.
- In the DST-gap weeks the feed loses about 59 bars a day against the Athens table. Controls within 14 days can cross that boundary, which affects quote fraction by 4% or less. It matters most for Good Friday and Easter Monday.
- The 95% intervals are per type; Table 4 gives the Bonferroni and year-clustered readings.
- The rule's order relative to the data is attested by the analyst, not by a commit (M1; standard above).

**Commands** (repo `.venv` Python, run with `-I` from a clean directory)
1. `scripts/fetch_data_release.py --download-only` (cwd = an empty directory) gives 17 files in `raw/`. Hash them with `shasum -a 256 raw/*.parquet`.
2. `daystats.py raw daystats.parquet` gives the per-day and per-session statistics and the baseline. The baseline comes from the inline groupby over days with at least 1,300 bars.
3. `holidays45.py daystats.parquet .` gives Tables 1-2 and the closed and excluded lists (`summary.csv`, `instances.csv`, `closed.csv`, `excluded.csv`).
4. `robust45.py instances.csv` gives Table 3 and the open Good Friday instances.
5. Bar-count evidence (early closes, the 1,139 to 1,227 shift, closures): an inline listing of weekdays with fewer than 700 bars, and with between 700 and nominal-minus-30, from `daystats.parquet`.
6. `check.py instances.csv` (quant reviewer) gives Table 4 and the 2 Jan leave-one-out.
7. `screen.py instances.csv instances_no_overlap.csv` (research director) gives the instance screen and drops the CN × Presidents overlap; `check.py instances_no_overlap.csv` and `clu.py instances_no_overlap.csv` give the n=75 CN rows and the year-clustered table.

The scripts and outputs are kept outside the repo, in the temp folders of the analyst (`backlog45/`), the quant reviewer (`qr45/`) and the research director (`rd45/`). For the final version (2026-10-30), commit them as `scripts/measure_holidays.py` with a test, including the Table 4 variants and the listed-day overlap rule, so the numbers can be re-run after the G1 repair.

**Quant ruling on the open questions (2026-10-16)**
1. **Volume for ticks.** Dukascopy volume_bid per nominal hour and per quoted minute is accepted as the proxy for v3, named as such everywhere. Broker ticks per quoted minute are added to H-A.
2. **Dukascopy spread for the broker's.** The v3 list may change on the Dukascopy evidence (it only drops 2 Jan, NORMAL on every reading); H-A is the forward confirmation, not a precondition.
3. **G1/G2 as the only gap days.** The exclusion set is confirmed and cited to the triage artifact. The measurement is re-run after the G1 repair, in the same run as the H-04 recount, by 2026-11-13 (addendum).

**Hypotheses for pre-registration** (at most three; rewritten 2026-10-16 per the quant ruling, before any
pre-registration row). H-A reads forward broker data not used here. H-B and H-C read the frozen Q1 candidate sets,
whose research window overlaps the data measured here. **Trial cost:** H-A and H-B are 0-cost diagnostics (no model
or parameter is chosen, nothing is promoted); H-C counts as **1 registry trial**.

- **H-A (broker check of the proxy, forward).**
  - Data source: the IC Markets demo terminal (ICMarketsSC-Demo; account type recorded at the start: Raw Spread
    expected. If it is Standard, H-A runs and reports, but is labelled Standard and not pooled with a later Raw run).
  - Store: MT5 ticks (bid, ask, time) pulled with `copy_ticks` (`goldbot/execution/bridge.py`) after each server day
    closes, one Parquet file per server date on the brain VM, outside git; their sha256 digests are listed in the
    verdict.
  - Uptime rule: a day (THIN or control) is clean only if the tick recorder was up for at least 90% of the server
    day's nominal open minutes (static session table). A THIN day with fewer than 2 clean controls is not clean.
  - Window 2026-10-18 to 2027-04-30. **THIN set, by date:** Thanksgiving 2026-11-26, 24 Dec 2026-12-24, 31 Dec
    2026-12-31, MLK 2027-01-18, Presidents 2027-02-15 (Juneteenth is excluded, and is outside the window anyway).
  - Secondary set, read the same way and reported separately: day after Thanksgiving 2026-11-27, 28-30 Dec 2026
    (27-30 Dec without the Sunday), Easter Monday 2027-03-29.
  - Measures: primary, broker median spread divided by the broker 15m ATR (mid); secondary, broker ticks per quoted
    minute and the broker median spread alone; each against same-weekday controls 7 and 14 days either side.
  - Rule: **CONFIRMED** if the pooled geometric-mean spr/ATR ratio over the clean THIN days is at least 1.25 with a
    lower 95% bound above 1.0. **REFUTED** if the pooled point ratio is at most 1.05 with at least 4 clean THIN days.
    Otherwise **INCONCLUSIVE**; fewer than 4 clean THIN days is INCONCLUSIVE.
  - Actions: CONFIRMED keeps the v3 THIN days. REFUTED sends the list back to the program director and product owner
    with the evidence for a v4 review (quant review); nothing is dropped automatically. INCONCLUSIVE leaves v3 as it is.
- **H-B (cost in R, research window).**
  - Data: the frozen Q1 candidate sets (H-01, H-04, C4H; costs as each row pins). H-01's R is in ATR(1d) units, so
    the 15m spr/ATR finding does not carry over to it directly.
  - Timing: no H-01, H-04 or C4H outcome is read before that family's registered run and its evaluator verdict
    (H-01 not before 2027-01-01). H-B runs family by family after each verdict.
  - The holiday filter is never applied to a frozen candidate set: H-B reads the sets, it does not change them or
    their counts (H-01 stands at 152 against a floor of 150).
  - Measure: candidates entered on a v3 THIN day against candidates on their matched control days (same weekday, 7
    and 14 days either side). **Primary: gross mean R difference** (THIN minus control) with a block-bootstrap 95%
    interval. Net mean R and the mechanical cost (net minus gross) are reported separately.
  - Rule and actions: **SUPPORTED** if THIN-day gross mean R is lower by at least 0.05 R and the interval excludes 0:
    the block is kept and the result goes to BACKLOG 46. **UNPROVEN** if there are at least 30 THIN-day candidates and
    the test fails: the block stays (it only removes entries) and is recorded as unproven for that family.
    **INCONCLUSIVE** below 30 THIN-day candidates: nothing changes.
  - Expected counts, stated now: H-01 about 6 THIN-day candidates (quant reviewer's estimate), so INCONCLUSIVE by
    construction; H-04 about 110 if its 4,025 entries are spread evenly over the 4,107 weekdays (113 of them THIN); C4H is
    stated by the strategy researcher from its frozen count before its row is written.
- **H-C (session-scoped block).**
  - Claim: on US early-close holidays (MLK, Presidents, Memorial, Juneteenth, 4 July, Labor, Thanksgiving),
    Asia-session entries are not worse than on control days.
  - Runs only after H-04's verdict, and only if H-04 is not retired.
  - Measure: gross mean R of Asia-session candidates (H-04 `asia_drift`, and H-01 where entry falls in Asia) on those
    days against matched controls; net and the mechanical cost reported separately, as in H-B.
  - n and power, stated now: about 97 holiday Asia entries from H-04 if spread evenly (99 such weekdays in the
    window), with about 4 controls each. At a per-trade SD of about 1 R the difference has a standard error of about
    0.12 R, so the power to show non-inferiority at the margin below, if the true difference is 0, is about 15%.
    INCONCLUSIVE is the likely result.
  - Margin: **-0.10 R.** Justification: the largest Asia cost rise measured here is x1.24 (Memorial) on a normal Asia
    spr/ATR of 0.268, so +0.064 of a 15m ATR per spread, under 0.10 R for any stop of at least 0.65 ATR. A loss larger
    than 0.10 R per entry is more than the cost channel can explain.
  - Rule and actions: **NON-INFERIOR** if the lower 95% bound of the gross difference is above -0.10 R with at least
    60 holiday Asia entries: the holiday rule is re-specified to block London and New York only, which needs a list
    format change, a trading-safety review and a PD+PO decision. **WORSE** if the upper bound is below 0: the whole-day
    block stays. Otherwise, or below 60 entries, **INCONCLUSIVE**: the whole-day block stays.

**Decision (2026-10-16, program director + product owner, under the owner's delegation; owner-only items untouched).**
**ADOPT cme-metals-v3** as recommended above (drop 2 Jan; keep every other listed day, unresolved days marked; add
none), with this note's final on 2026-10-30. The implementer ships the list as a new version with the hand-reviewed
set in its test (BACKLOG 66, due 2026-10-30). The window stays off; switching it on is the owner's, after BACKLOG 46.
Quant record (E1): quant-reviewer · CHANGES (v3 list SAFE TO ADOPT; text fixes M1-M7, H-A/H-B/H-C to rewrite before
pre-registration) · draft feda07a · 2026-10-16.
