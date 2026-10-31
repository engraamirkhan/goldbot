# Cost of record, ICM and Vantage demo, week of 2026-10-26 (microstructure, 2026-10-31)

Research director bet 2, week of 2026-11-02. This is a measurement. No hypothesis is tested, no registry trial is
used, K does not change and the holdout is not touched. Source: the brain's `aux.db` (`exploration_trades`,
`cost_probes`, `evidence_exclusions`) and the store's `exit_fills`. All of it was read read-only on 2026-10-31. Only
aggregates are in this repo: no trade row, no login, no host.

The machine-readable table is `config/cost_of_record.yaml`, version **`cor-2026-10-31.2`** (it supersedes
`cor-2026-10-31`, which used an i.i.d. interval). `python -m goldbot.research.cost_of_record` generates it; the
definitions live in that module's docstring. Every research screen reads it through
`goldbot.research.arms.load_costs`. ADR 0064 governs how a new pre-registration pins a version and what it charges.

## 1. Definitions

- **Window.** Opportunity days 2026-10-26 to 2026-10-30, which is UTC 2026-10-25 22:00 to 2026-10-30 22:00.
  Sessions follow `DEFAULT_SESSIONS`, the same session table the cost table uses.
- **Population.** Exploration fills, all market orders.
  - The 95 fills voided by `evidence_exclusions` (scope `cost_cells`) are removed from every count: entries, closes,
    stop exits and exit legs.
  - All 95 fall on 10-26 and come from ICM. That leaves 1,278 ICM fills and 194 Vantage fills.
  - The daily reviews counted 1,373 and 195: they keep the voided fills, and one Vantage fill has no requested price.
- **Entry slippage** is `side x (fill - requested)`. Positive is adverse.
- **Spread** is ask minus bid at send.
- **Exit slippage** is `side x (requested - filled)` on market exit legs. ICM has 208 such legs after the voids.
  - Vantage records no exit legs at all (section 5, backlog 141).
  - For Vantage the exit leg is therefore measured from that broker's own scheduled probes:
    `side x (exit touch - exit price)`, n = 121.
- **Commission** is the broker's booked commission divided by (lots x 100 x the GBP-per-USD rate).
- **Round trip** is spread + max(0, entry slip) + max(0, exit slip) + commission. Each term is the pooled mean, and
  the floor is applied after pooling.
- **Overshoot** is `side x ((requested - side x stop_usd) - exit price)` on stop exits **at the initial stop**. It is
  charged on stop exits only.
  - A stop exit at a moved stop is counted apart, never in the mean. It is moved if it belongs to an arm that trails
    its stop (BXR runners: break-even at +1 R, then trailed), or if it exits more than $0.50 better than the initial
    stop.
  - ICM had 9 such exits: 5 in Asia, 2 in London and 2 in New York. Vantage had none.
  - Removing them raises the ICM mean from 0.093 to 0.110. Those exits had looked like negative overshoot.
- **ci90** is the 5th to 95th percentile of 4,000 bootstrap means (seed 20261031). The bootstrap resamples **whole
  minutes**, because fills on the same bar are correlated.
  - Entries cluster on their send minute. ICM's 1,278 fills fall in 880 minutes.
  - Closes, stop exits and exit legs cluster on their exit minute.
  - Spread and entry slip share one resample. The round-trip interval combines every term's draws.
- **Charged** is what a new pre-registration's screen pays (ADR 0064): `max(frozen 0.23, round-trip upper bound)`.
  - A trade outside every session pays the broker's dearest cell.
  - Demo slippage only ever raises a cost (prereg-cost-measurement-2026-10 decision 3).

## 2. The table of record ($/oz = per 0.01 lot)

"n fills / minutes" counts fills and the distinct minutes they fall in. The last column is the ADR 0064 overshoot
charge.

| broker | session | n fills / minutes | spread (ci90) | entry slip (ci90) | exit slip (n, ci90) | commission | **round trip (ci90)** | **charged RT** | stop exits | overshoot (ci90) | **charged overshoot** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ICM | Asia | 309 / 234 | 0.088 (0.085-0.092) | +0.015 (-0.049..+0.081) | +0.020 (68, -0.004..+0.045) | 0.080 | **0.204 (0.172-0.272)** | **0.272** | 126 (+5 moved) | 0.100 (0.067-0.136) | **0.136** |
| ICM | London | 376 / 240 | 0.084 (0.081-0.087) | -0.031 (-0.074..+0.012) | -0.011 (50, -0.045..+0.022) | 0.080 | **0.164 (0.161-0.191)** | **0.230** | 170 (+2 moved) | 0.085 (0.061-0.109) | **0.120** |
| ICM | New York | 593 / 406 | 0.086 (0.081-0.092) | -0.017 (-0.052..+0.018) | -0.044 (90, -0.151..+0.022) | 0.080 | **0.166 (0.162-0.194)** | **0.230** | 268 (+2 moved) | 0.130 (0.075-0.196) | **0.196** |
| ICM | all | 1,278 / 880 | 0.086 (0.083-0.089) | -0.013 (-0.039..+0.013) | -0.015 (208, -0.061..+0.018) | 0.080 | **0.166 (0.164-0.188)** | **0.230** | 564 (+9 moved) | 0.110 (0.081-0.145) | **0.145** |
| Vantage | Asia | 81 / 69 | 0.105 (0.104-0.106) | -0.025 (-0.101..+0.043) | -0.158 (83 probes, -0.319..-0.010) | 0.053 | **0.158 (0.157-0.202)** | **0.230** | 18 | 0.000, untrusted | **0.700** |
| Vantage | London | 44 / 40 | 0.104 (0.103-0.105) | **+0.135** (-0.005..+0.291) | -0.215 (18 probes, -0.623..+0.003) | 0.053 | **0.293 (0.158-0.450)** | **0.450** | 10 | 0.000, untrusted | **0.700** |
| Vantage | New York | 69 / 62 | 0.106 (0.104-0.107) | +0.097 (-0.006..+0.210) | +0.007 (20 probes, -0.002..+0.019) | 0.053 | **0.263 (0.169-0.378)** | **0.378** | 19 | 0.000, untrusted | **0.700** |
| Vantage | all | 194 / 171 | 0.105 (0.104-0.106) | +0.055 (-0.004..+0.117) | -0.139 (121 probes, -0.265..-0.035) | 0.053 | **0.213 (0.158-0.275)** | **0.275** | 47 | 0.000, untrusted | **0.700** |

**What clustering changed.** The intervals widened modestly (for example ICM New York spread 0.083-0.089 became
0.081-0.092). The ICM Asia upper round trip went from 0.264 to 0.272, Vantage London from 0.453 to 0.450 and Vantage
New York from 0.386 to 0.378. The pooled point estimates did not move.

**Costs in R at the typical stop.** The median stop is $3 on ICM (scalp) and $7 on Vantage. A scalp needs a gross
edge of at least 0.3 R (three times cost) to be worth running.
- **ICM charged round trip:** 0.077 R in London and New York, 0.091 R in Asia at a $3 stop. Asia's median stop is
  $4.5, which makes it 0.060 R there.
- **ICM overshoot:** the charge times the 45% stop-exit share adds 0.018 R (London) to 0.029 R (New York).
- **ICM all-in:** about 0.10 to 0.11 R, against about 0.18 R under 0.23 plus 0.70.
- **Vantage** pays the 0.70 stress on stops (ADR 0064), about 0.025 R at 24% stop exits and a $7 stop, on top of
  0.033 R (Asia) to 0.064 R (London) of round trip.

**What changes for screens (ADR 0064, accepted).**
1. **Round trip.** The frozen 0.23 is **not** conservative in three cells: ICM Asia, Vantage London and Vantage New
   York. A new pre-registration charges `charged_round_trip_usd(broker, session)`. It pins the table version, and
   `run_arm_backtest.py --screen-broker --cost-version` refuses any other version.
2. **Overshoot.** New ICM pre-registrations charge the session's clustered upper bound, floored at $0.12. Vantage pays
   0.70 until its exit legs are recorded. The 0.70 is reported as a stress line (`net_r_cor_stress`), not a gate.
3. **Registry rows.** They keep 0.23 and [0, 0.70].

## 3. Corrections to figures in circulation

- **Commission is $0.080/oz, not $0.06.** The 0.06 the broker books per 0.01 lot is GBP. At about 0.747 GBP/USD that
  is $0.080.
  - `cost-model-1m-2026-10-25.md` read it as USD, so its "central $0.144" understated cost by $0.020/oz.
  - Its conservative case (p90 spread both legs 0.12 + slippage upper bound 0.05 + commission) is **0.25**, not 0.23.
  - The frozen 0.23 therefore understates that case by $0.02. That is why ADR 0064 charges `max(0.23, measured upper
    bound)`, and why the measured bound carries the real commission.
  - The frozen 0.23 stays only on registry rows, so they remain comparable.
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
   therefore omits exit slippage. The cost of record substitutes Vantage's own scheduled-probe exits. The fix is
   backlog 141 (data-engineer, then implementer).
2. **Vantage overshoot reads 0.000 (n 47, sd 0.001).** The demo server fills stops at the stop price. That is a demo
   artefact that will not survive live.
   - The builder decides trust on the broker's pooled stops: 95% or more within $0.005 of the stop, with at least 20
     stops. It then marks every Vantage cell `overshoot_trusted: false`.
   - Vantage screens charge 0.70 per stop exit (ADR 0064).
3. **The scheduled-probe exit-slip sign is correct, and its mean is a tail.** The sign is right by construction: a
   long exits at the bid, so a fill below the bid is adverse and positive. Vantage's -0.139 (and ICM's -0.069 on its
   own probes) does not come from a typical fill:
   - The median is 0.000 and the 10% trimmed mean is -0.001.
   - The mean comes from a favourable tail. 4.1% of Vantage probe exits fill more than $0.20 better than the touch,
     and none fill more than $0.20 worse. On ICM the figures are 6.2% better and 4.8% worse.
   - The exit quote is 0.26 s old at the median on Vantage and 0.64 s on ICM. A fill against a quote that is a second
     old in a moving market explains tails on both sides. Only demo price improvement explains Vantage's one-sided
     tail.
   - Because the round trip floors exit slip at zero, this never lowers a cost.
4. **The intervals cluster by minute, not by day.** Five days give too few blocks for a day-block bootstrap. Day-to-day
   dispersion (ICM pooled 0.152 to 0.196) is still wider than the interval, so treat these intervals as lower bounds
   on the uncertainty.
5. **These are demo fills.** Live fills only raise these numbers, never lower them. Only tiny-live fills (an owner
   gate) may lower a cost.

## 6. How many cost probes a day the protocol still needs (recommendation; config not changed)

The protocol (prereg-cost-measurement-2026-10, decision 1) spends probe fills on four things only: market-leg
slippage, the limit fill rule, stop-exit slippage, and booked commission and swap. Against what this week's strategy
fills already measure:

- **Commission is settled.** It is a constant per broker, with sd $0.0003.
- **Stop-exit slippage on ICM is settled** from 564 strategy stop exits at the initial stop. Probes add nothing.
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

Re-read this recommendation at the next cost-of-record refresh (weekly: run `python -m goldbot.research.cost_of_record` with a new `--version`; ADR 0064: a running
pre-registration keeps the version it pinned).
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
