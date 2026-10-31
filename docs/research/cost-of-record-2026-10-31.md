# Cost of record, ICM and Vantage demo, week of 2026-10-26 (microstructure, 2026-10-31)

Research director bet 2, week of 2026-11-02. This is a measurement. No hypothesis is tested, no registry trial is
used, K does not change and the holdout is not touched. Source: the brain's `aux.db` (`exploration_trades`,
`cost_probes`, `evidence_exclusions`) and the store's `exit_fills`. All of it was read read-only on 2026-10-31. Only
aggregates are in this repo: no trade row, no login, no host. The machine-readable table is
`config/cost_of_record.yaml` (version `cor-2026-10-31`). Every research screen reads it through
`goldbot.research.arms.load_costs`.

## 1. Definitions

- **Window.** Opportunity days 2026-10-26 to 2026-10-30, which is UTC 2026-10-25 22:00 to 2026-10-30 22:00.
  Sessions follow `DEFAULT_SESSIONS`, the same session table the cost table uses.
- **Population.** Exploration fills, all market orders. The 95 fills voided by `evidence_exclusions` (scope
  `cost_cells`) are removed. All 95 fall on 10-26 and come from ICM, which leaves 1,278 ICM fills and 194 Vantage
  fills. The daily reviews counted 1,373 and 195: they keep the voided fills, and one Vantage fill has no requested price.
- **Entry slippage** is `side x (fill - requested)`. Positive is adverse.
- **Spread** is ask minus bid at send.
- **Exit slippage** is `side x (requested - filled)` on market exit legs. ICM has 215 such legs. Vantage records no
  exit legs at all (section 5). For Vantage the exit leg is therefore measured from that broker's own scheduled probes:
  `side x (exit touch - exit price)`, n = 119.
- **Commission** is the broker's booked commission divided by (lots x 100 x the GBP-per-USD rate).
- **Round trip** is spread + max(0, entry slip) + max(0, exit slip) + commission. Each term is the pooled mean, and
  the floor is applied after pooling.
- **Overshoot** is `side x ((requested - side x stop_usd) - exit price)` on stop exits. It is charged on stop exits
  only.
- **ci90** is the 5th to 95th percentile of 4,000 i.i.d. bootstrap means (seed 20261031). The round-trip interval
  resamples every term jointly.
- **Charged** is what a screen pays: `max(frozen 0.23, round-trip upper bound)`. The frozen 0.23 was itself the
  conservative case (p90 spread plus the upper slippage bound), so charging the measured upper bound compares like
  with like. Demo slippage only ever raises a cost (prereg-cost-measurement-2026-10 decision 3).

## 2. The table of record ($/oz = per 0.01 lot)

| broker | session | n fills | spread (ci90) | entry slip (ci90) | exit slip (n, ci90) | commission | **round trip (ci90)** | **charged** | stop exits | overshoot (ci90) |
|---|---|---|---|---|---|---|---|---|---|---|
| ICM | Asia | 309 | 0.088 (0.086-0.091) | +0.015 (-0.039..+0.070) | +0.020 (68, -0.005..+0.046) | 0.080 | **0.204 (0.172-0.264)** | **0.264** | 131 | 0.053 (0.004-0.100) |
| ICM | London | 376 | 0.084 (0.081-0.087) | -0.031 (-0.075..+0.013) | -0.013 (57, -0.045..+0.017) | 0.080 | **0.164 (0.162-0.187)** | **0.230** | 172 | 0.076 (0.051-0.100) |
| ICM | New York | 593 | 0.086 (0.083-0.089) | -0.017 (-0.051..+0.016) | -0.044 (90, -0.158..+0.022) | 0.080 | **0.166 (0.164-0.193)** | **0.230** | 270 | 0.124 (0.085-0.167) |
| ICM | all | 1,278 | 0.086 (0.084-0.088) | -0.013 (-0.037..+0.011) | -0.015 (215, -0.064..+0.017) | 0.080 | **0.166 (0.165-0.186)** | **0.230** | 573 | 0.093 (0.071-0.117) |
| Vantage | Asia | 81 | 0.105 (0.104-0.106) | -0.025 (-0.088..+0.035) | -0.160 (82 probes, -0.334..-0.008) | 0.053 | **0.158 (0.157-0.193)** | **0.230** | 18 | 0.000, untrusted |
| Vantage | London | 44 | 0.104 (0.103-0.105) | **+0.136** (-0.002..+0.296) | -0.232 (17 probes, -0.663..-0.003) | 0.053 | **0.293 (0.158-0.453)** | **0.453** | 10 | 0.000, untrusted |
| Vantage | New York | 69 | 0.105 (0.104-0.106) | +0.097 (-0.004..+0.209) | +0.007 (20 probes, -0.002..+0.018) | 0.053 | **0.263 (0.160-0.386)** | **0.386** | 19 | 0.000, untrusted |
| Vantage | all | 194 | 0.105 (0.104-0.105) | +0.055 (-0.002..+0.114) | -0.142 (119 probes, -0.271..-0.035) | 0.053 | **0.213 (0.159-0.273)** | **0.273** | 47 | 0.000, untrusted |

**Costs in R at the typical stop.** The median stop is $3 on ICM (scalp) and $7 on Vantage. On ICM the charged round
trip is 0.077 R in London and New York and 0.088 R in Asia (Asia's median stop is $4.5, so 0.059 R there). Add the
overshoot: its 0.117 upper bound times the 45% stop-exit share is about 0.018 R. The all-in figure on ICM is about
0.10 R. On Vantage, London costs 0.065 R and New York 0.055 R. A scalp still needs a gross edge of at least 0.3 R
(three times cost) to be worth running.

**What changes for screens.**
1. The frozen 0.23 is **not** conservative in three cells: ICM Asia (upper bound 0.264), Vantage London (0.453) and
   Vantage New York (0.386). A screen that trades those cells has to charge `charged_round_trip_usd(broker,
   session)`.
2. The 0.70 overshoot scenario is 6 to 13 times the measured ICM overshoot (0.053 to 0.124 by session; upper bound
   0.167 in New York). For new pre-registrations I recommend charging `overshoot_of_record_usd` (the upper bound) in
   place of the 0.70 stress. The frozen pair `[0, 0.70]` stays as registered so earlier registry rows stay
   comparable. *Research-director decision; not changed here.*

## 3. Corrections to figures in circulation

- **Commission is $0.080/oz, not $0.06.** The 0.06 GBP the broker books per 0.01 lot is GBP, and at about 0.747
  GBP/USD that is $0.080. `cost-model-1m-2026-10-25.md` read it as USD, so its "central $0.144" understated cost by
  $0.020/oz. The frozen 0.23 still covers it.
- **The week's 0.208 for ICM (Asia 0.237, London 0.200, New York 0.198) is biased upward by about $0.04.** I
  reproduced those figures exactly as the fill-weighted mean of the daily review's per-day, per-host, per-session
  cells. Each of those cells floors its own small-n slippage mean at zero, and slippage has sd 0.5 with n of 20 to
  100 per cell, so the floor charges noise as cost. Pooled first and floored after, the figures are 0.166 overall,
  0.204 Asia, 0.164 London and 0.166 New York. The daily review's per-cell figure suits an alarm. It is the wrong
  figure for a cost of record.

## 4. Why the round trip "rose about 50%" from 10-26 to 10-30

The single-day comparison does not hold up:
- After exclusions, ICM 10-26 has 43 fills, all in New York. 95 of that day's 138 fills are void.
- Vantage 10-26 has 5 fills.

So I decompose the first half of the week (10-27 and 10-28) against the second half (10-29 and 10-30). Both halves
have all three sessions. Mix is Σ(Δweight x old cost) and rate is Σ(new weight x Δcost), over sessions.

| broker | RT 27-28 → 29-30 | spread Δ (mix / within-session) | entry slip mean | exit slip mean | commission | what drove it |
|---|---|---|---|---|---|---|
| ICM (531 → 704 fills) | 0.164 → 0.195 (**+19%**, +0.031) | +0.003 (+0.001 / +0.003) | -0.034 → +0.004 (floored contribution +0.004) | -0.037 → +0.023 (n 130 → 68; contribution +0.023) | 0.080 flat | **exit slippage, about 75%**, plus entry slip about 13% and spread about 11%. Session mix about 0. |
| Vantage (96 → 93) | 0.158 → 0.281 (**+77%**, +0.122) | -0.001 (0.000 / 0.000) | -0.027 → +0.123 (mix -0.023, within-session **+0.172**; London -0.030 → +0.316, New York +0.037 → +0.143) | not recorded | 0.053 flat | **entry-slippage drift, all of it.** Spread is flat. Mix *lowered* cost, because Asia's share rose from 34% to 52%. |

**Reading.**

**Spread did not widen.** ICM +4%, Vantage 0%. The one wide pocket was ICM New York on 10-30 (0.101 against the week's
0.086). By hour, ICM 13 UTC averages 0.116 (n 97) and 01 UTC 0.098, against 0.069 to 0.070 at 09 and 15 UTC.

**Session mix explains nothing.** It is about 0 on ICM and negative on Vantage.

**The ICM rise is mostly noise passing through the zero floor.**
- The exit-slip mean rests on 68 legs with sd about 0.37, so its standard error is about $0.045.
- The entry-slip change is +0.038 with a standard error of about 0.030 (z ≈ 1.3).
- Neither is distinguishable from zero. The pooled weekly ICM figure (0.166) is the one to use.

**The Vantage rise is real slippage drift, at borderline significance.**
- The entry-slip change is +0.149 with a standard error of about 0.071 (z ≈ 2.1).
- One +$3.48 fill at 20 UTC contributes $0.037 of it. Without that fill, z ≈ 1.6.
- It sits in London and New York scalps (London scalp entry slip +0.186, ci90 +0.039..+0.353, n 39), which is
  consistent with signal-time fills on a thinner demo book.

**Action.** Vantage London and New York carry their own charged round trip (0.453 and 0.386) until four more weeks
of fills say otherwise.

## 5. Gaps found (flagged, not fixed here)

1. **Vantage records no exit legs.** `exit_fills` holds 0 rows for that host this week. Its daily-review round trip
   therefore omits exit slippage. The cost of record substitutes Vantage's own scheduled-probe exits. Owner of the
   fix: data-engineer and engine, backlog item to be raised by the research director.
2. **Vantage overshoot reads 0.000 (n 47, sd 0.001).** The demo server fills stops at the stop price. That is a demo
   artefact that will not survive live, so the cell is marked `overshoot_trusted: false` and screens keep the frozen
   scenarios for Vantage.
3. **The intervals are i.i.d. across fills.** Five days give too few blocks for a day-block bootstrap. Day-to-day
   dispersion (ICM pooled 0.152 to 0.196) is wider than the interval, so treat these intervals as lower bounds on
   the uncertainty.
4. **These are demo fills.** Live fills only raise these numbers, never lower them. Only tiny-live fills (an owner
   gate) may lower a cost.

## 6. How many cost probes a day the protocol still needs (recommendation; config not changed)

The protocol (prereg-cost-measurement-2026-10, decision 1) spends probe fills on four things only: market-leg
slippage, the limit fill rule, stop-exit slippage, and booked commission and swap. Against what this week's strategy
fills already measure:

- **Commission is settled.** It is a constant per broker, with sd $0.0003.
- **Stop-exit slippage on ICM is settled** from 573 strategy stop exits. Probes add nothing.
- **ICM market-leg slippage is settled** from strategy fills, which are the population that actually pays it.
  - To see a $0.05/oz drift (about 0.017 R at a $3 stop) at one-sided α 0.10 with power 0.8, with sd 0.50, takes
    ((1.28 + 0.84) x 0.50 / 0.05)² ≈ 450 fills per group.
  - ICM strategy fills deliver about 1,280 a week (300 to 600 per session).
  - The ~70 a day ICM scheduled M1 probes (350 this week) measure quiet-time fills. They cannot answer the signal-time
    question, and they cost a host slot and probe budget.
- **The limit fill rule is unmeasured.** The L1 limit-at-the-touch arm was never built (`cost_probe.py` has only M1).
  At about half a spread ($0.04) plus slippage per entry, it is the largest open cost lever. It is also the only probe
  use left with real information.

**Recommendation:**

| broker | now (actual / plan) | recommend | why |
|---|---|---|---|
| ICM | ~70 a day M1 / 90 | **24 a day M1** (one per open hour) **+ 24 a day L1 limit** once its code lands. Until then, **24 M1 only.** | 24 a day, about 120 a week, keeps a fixed-time control series. That series can detect a $0.10 drift in a week (n ≈ 100 needed) and says whether a rise is the broker's or the signals'. The other ~46 a day buy nothing. |
| Vantage | ~24 a day M1 / 40 | **keep 40 a day** until Vantage exit legs are recorded **and** strategy fills reach 200 per session (about 3 to 4 weeks at about 13 per session per day), **then 12 a day** | Probes are Vantage's only exit-slip measurement, and its entry slip is drifting (section 4). |
| mirror probes | about 1 a day | no change needed | negligible volume, no measurement role left |

Re-read this recommendation at the next cost-of-record refresh (weekly: re-run the aggregation and bump `version`).
Changing `probe_plan`, account `cost_probe` settings or arms is for the research director and program director. I
changed none of them. Probe budgets are demo-only and inside the existing sleeves, so no owner money decision is
involved.

## 7. Discipline

- Not to re-test:
  - an hour-of-day spread edge on ICM. The 10-25 doc found none within noise. This week the hour spread ranges from
    0.069 to 0.116, which is a cost to avoid (13 UTC, 01 UTC), not an edge.
  - flooring slippage per day cell for cost (section 3).
- Multiple testing: 0 trials, K unchanged. This is descriptive, and no gate or arm reads it as a verdict.
- Owner-only items: none raised. Going live, sizing and spend are untouched. Demo slippage never lowers a cost.
