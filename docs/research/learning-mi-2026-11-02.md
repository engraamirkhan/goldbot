# Multi-instrument learning arms, 2026-11-02 (demo only, unqualified)

Principal strategy researcher, 2026-11-02. Pre-registration of one learning arm per listed instrument and its matched
random control, written before any of these arms has a fill and before any outcome of them was read.

- **The directive.** Owner, 2026-11-02: all 26 listed instruments go live on DEMO, and demo strategies may run as a
  K-counted learning class: unqualified, each with a matched random control and a kill rule, never real money.
- **The rules this sits under.** ADR 0061 amendment 2026-11-02 (the learning class), ADR 0072 (its engine and limits),
  `docs/research/learning-2026-11-02.md` (the gold learning registration; every clause of its sections "What a
  learning arm is, and is not" and "Stops" applies here unchanged).
- **The data rule.** Each arm trades and is read on its own instrument only: its own 15m bars, its own ATR, its own
  costs, its own control. Nothing is pooled across instruments or with XAUUSD.
- **Holdout.** Not touched. These arms read no history: they are forward-only on demo, 2026-11-02 onwards.

## 1. What these arms can and cannot tell us (read this first)

- **They are not an edge search.** Section 7's power arithmetic: about 20-30 fills per arm by the end date, so the
  smallest excess over the control the read can detect is about 0.9 R. No published intraday effect is that large. The
  edge read is expected to be inconclusive, and that is the honest prior.
- **What they deliver.** Per instrument:
  - measured round-trip cost per R (spread, commission, slippage at entry and at stop, target and time exits) at a
    15m-4h hold;
  - fill quality and fill latency;
  - proof that the multi-instrument engine sizes, routes and closes each instrument correctly at the minimum lot.

  Those are the inputs the POS-1 multi-asset books need (`docs/research/rethink/strategy.md` section 3: "the CTA
  route" across "silver, platinum, FX majors, indices, oil" on the same ICM account), and today we have none of them.
- **Why not the literature's edge itself.** The slow trend premium (1 week to 12 months; section 3 below) cannot run
  inside the learning class:
  - demo hosts close every position at the Friday cut (`demo_weekend_flat`);
  - the class ends at 45 days;
  - the read is due at 300 fills.

  A 1d+ trend book belongs to the POS-1 position engine and its own pre-registration, not here.

## 2. The rule (frozen; `config/exploration_arms.yaml` LB-<key>)

**Opening-range breakout in the instrument's own session**, `rule: session_range_break` with `session_local`
(`goldbot/research/arm_rules_growth.py`; GS1's reviewed rule, generalised to an instrument's own clock; GS1 and GS1C are
unchanged, hashes pinned in `tests/test_learning_mi.py`).

| Item | Frozen value |
|---|---|
| Bars | the instrument's own 15m bars (mid), its own broker feed |
| Range | high and low of the 15m bars that open in [range_from, range_to) on the session's city clock (60 minutes, 4 bars; no range, no trade) |
| Entry | the first 15m close in (range_to, break_to] above the range high (long) or below the range low (short); at most one per session day |
| Side | the side of the break |
| Stop | 2.0 x ATR(14) of the instrument's own 15m bars at the decision close |
| Target | 1.5 R |
| Time exit | 16 bars (4 hours); 8 bars (2 hours) for the CBOT grains, whose day session closes at 13:20 Chicago |
| Window | the session's city clock, converted per date (each city's own DST); every exit lands before the 17:00 New York rollover and before the instrument's own session close, so no position pays swap |
| Common filters | as every arm: no entry 20:45-22:15 UTC, none on Friday from 19:00 UTC, the tier-1 blackout list |
| Size | the minimum lot, one unit, no adds (the learning class refuses anything else) |
| max_open | 1 |
| Per-trade cap | £8 at the stop, minimum lot, RiskGate-checked (section 4); a wider stop is a refused entry, never clamped |

One geometry for all 26 rows. Only the session clock differs, and it is the instrument's market, not a fitted value:
no parameter was chosen by looking at any of these instruments' prices.

## 3. Why this rule, from the rethink evidence

The trend lane, the academic memo and the strategy memo (all 2026-11-02) agree on three things that shaped it:

1. **Trend and breakout are where gross edge exists; cost and horizon decide whether it survives.**
   - Sprint-200 trend lane, XAUUSD, explore window 2010-2017, ICM upper-bound costs (`sprint200/trend-results.md`):
     11 of 34 trend rules had gross t >= 2; none survived confirm net of costs.
   - The strongest were channel breaks: 03 H1-DON48-D1 gross +0.316 R, t 4.08; 04 H1-DON24-ATRX +0.162 R, t 3.87.
2. **On gold, the session opening-range form is dead.**
   - Same lane: 07 ASIA-LON-1H gross +0.043 R (t 0.79), 08 LON-OR60-1H +0.071 R (t 1.54), 09 NY-OR60-D1 -0.006 R;
     net negative.
   - `scripts/sprint3000_catalogue.py` grades `session_range_break` and `opening_range_break` D ("retired").
   - **So XAUUSD is not one of these arms, and the two gold crosses (XAUEUR, XAUAUD) rank last (section 5).**
3. **Breadth is the CTA route.**
   - Single-market trend Sharpe is about 0.4, and diversified books are far higher (Hurst, Ooi, Pedersen 2017 JPM, 67
     markets, 1880-2016, net of estimated fees).
   - Trading more than gold is now the owner's scope (2026-11-02). Each instrument is a fresh hypothesis, not a retest
     of the retired gold one.

**Published evidence that the opening-range or intraday-momentum mechanism exists on these other markets.** No study
below is on our venue, our costs or after 2023. Several are single-study, and the strongest recent work says short-horizon
trend has decayed.

| Claim | Source | Sample, instrument | Net of costs? | Grade |
|---|---|---|---|---|
| The first half-hour return predicts the last half-hour (market intraday momentum) | Gao, Han, Li, Zhou 2018, JFE 129(2) | SPY, 1993-2013 | gross (cost discussion only) | S (replicated in parts by the next row) |
| Intraday momentum across futures asset classes, driven by gamma-hedging flows | Baltussen, Da, Lammers, Martens 2021, JFE 142(1) *(unverified: sample details)* | 60+ futures: equity, bond, commodity, FX; about 1974-2020 | gross | R for equity and bond futures |
| Opening-range breakouts were profitable in crude oil futures | Holmberg, Lönnbark, Lundström 2013, Finance Research Letters 10(1) *(unverified)* | CL futures, 1983-2011 | gross | S |
| A 5-minute ORB on QQQ was profitable net of commissions | Zarattini, Aziz 2023, SSRN working paper *(unverified)* | QQQ, 2016-2023 | net of commissions | S, practitioner |
| FX intraday momentum, partly informed trading | Elaut, Frömmel, Lampaert 2018, J. Financial Markets 37 *(unverified)* | RUB/USD, 2005-2014 | gross | S |
| Short-horizon trend has gone on small-tick contracts since about 2009 | Kurth et al. 2026, arXiv 2607.01550 (via `rethink/academic.md`) | about 100 futures, 1995-2025 | gross | S, strong authors: the main reason for a low prior |
| Technical trading rules on commodity futures fail after data-snooping control | Marshall, Cahan, Cahan 2008, JBF | 15 commodities, 1984-2005 | gross | conflicting (Szakmary et al. 2010) |

**Prior.** Gross excess over the control of about 0 to +0.05 R per trade on most instruments. Equity indices and
bond futures are the most likely to show any (Baltussen et al.), and FX the least. That is below what section 7 can
detect, hence section 1.

## 4. Size, costs and caps (estimates, before any fill)

The figures below are orders of magnitude. They use the brokers' public spread and contract pages (the FALLBACK terms
in `config/instruments.yaml`, still `verify: true`) and typical 15m ranges at today's price levels. **None is
measured.** Replacing them with measured values is the first deliverable of these arms.

| Instrument | Min lot | 2 x ATR15 at the min lot | Round trip / R (est.) |
|---|---|---|---|
| US500 | 0.1 | about $1.2 (£0.9) | about 0.05-0.08 |
| DE40 | 0.1 | about €3-6 (£2.6-5.2) | about 0.04-0.07 |
| XTIUSD | 0.01 (10 bbl) | about $2.4 (£1.8) | about 0.10-0.15 |
| EURUSD | 0.01 | about $1.0-1.4 (£0.8-1.1) | about 0.07-0.10 (commission included) |
| USDJPY | 0.01 | about ¥110-140 (£0.6-0.7) | about 0.08-0.12 (Tokyo ranges are narrow) |
| JP225 | 1 | about ¥120-160 (£0.6-0.8) | about 0.05-0.08 |
| UST10Y (Vantage) | 0.01 | about $1.2 (£0.9) | about 0.15-0.25: **the cost risk of this list** |
| BUND10Y (Vantage) | 0.01 | about €2.4 (£2.1) | about 0.15-0.25 |

- **Per-trade cap, £8.** The largest estimate is DE40 on a volatile day (about £5.2), plus about 50% headroom. Above
  the cap, the RiskGate refuses the entry and never clamps it. The arm and its control carry the same stop, so a
  refusal hits both alike.
- **Loss cap, £160** (20 x £8, as the learning class sets it). At these sizes the £ cap is a backstop. The binding kill
  is in R (section 6).
- **Notional.** The 8 registered pairs at the minimum lot are each about $300 (JP225) to $2,700 (DE40).
  - At most 4 open on icm-probe, so under $11k, against the 6x-notional cap of about £60k on an £11k demo.
  - At most 2 open on vantage-demo.
  - The correlation-group caps (lane L2) bind first if anything does.

## 5. Ranking and registration: 8 registered, 18 queued

**Ranking criteria**, in order of weight:

- (a) published evidence for the mechanism on the asset class (section 3);
- (b) estimated cost per R at this geometry (section 4);
- (c) whether the instrument is one the POS-1 multi-asset books need costs for (XAG, XPT, FX majors, US500, GER40,
  WTI);
- (d) distinctness, so the first pick per correlation group ranks above a near-duplicate (US500 above USTEC, XTIUSD above
  XBRUSD).

Gold crosses rank last (section 3, point 2).

**Capacity rule (why 8, not 26).** These are the binding demo limits, none of them changed here:

| Limit | icm-probe | vantage-demo |
|---|---|---|
| `learning_max_open` | 6, already shared with 7 gold learning arms | 6, shared with Z6V and Z7V |
| Intraday pot slots | 4, a new pot carved inside the unchanged `max_open_total` 23 (scalp 19 + intraday 4) | 6, shared with VI0 (max_open 3, 24h holds) and VC1H, so about 2 are free |

- **Why refusals matter.** A learning entry refused for want of a slot is not a neutral loss of data. Slots run out
  on days when many markets break at once, which are the days a breakout rule is most likely to work, while the
  seeded control is refused at other times. That biases the arm-versus-control read.
- **The rule.** Register at most 2 pairs per session group on icm-probe and 1 on vantage-demo. The groups are Tokyo
  (01:00-08:00 UTC), Europe (07:00-15:00 UTC) and New York (12:30-21:00 UTC), and their open periods barely
  overlap.
- **The effect.** The pairs' open positions can never exceed the pot. At most 2 new learning positions are open at
  once beside the gold learning arms.
- **The exception.** In the US-EU DST gap weeks the Europe and New York groups overlap by up to an hour. None falls
  before 2026-12-17. A registration that runs into March needs this rule re-checked.

`tests/test_learning_mi.py` checks this rule.

**The 26 rows.** Session clocks are local. k_id is the K row's config hash: for a queued row it is what the row would
get if registered unchanged (it does not depend on `since`).

| Rank | Instrument | Account | Session (zone, range, break until) | Hold | Status | Arm / control | k_id arm / control |
|---|---|---|---|---|---|---|---|
| 1 | US500 | icm-probe | New York 09:30-10:30, 12:30 | 16 | **registered** (NY group) | LB-US500 / LBC-US500 | `7824af5337d6` / `0b48159e6744` |
| 2 | DE40 | icm-probe | Berlin 09:00-10:00, 12:00 | 16 | **registered** (Europe) | LB-DE40 / LBC-DE40 | `0cc84bd9b32d` / `fd514988ac7f` |
| 3 | XTIUSD | icm-probe | New York 09:00-10:00, 12:00 | 16 | **registered** (NY) | LB-XTIUSD / LBC-XTIUSD | `3c4f380493a9` / `95662ebf0c11` |
| 4 | UST10Y | vantage-demo | New York 08:30-09:30, 11:30 | 16 | **registered** (NY) | LB-UST10Y / LBC-UST10Y | `934a5d28e98b` / `43a691518ebe` |
| 5 | EURUSD | icm-probe | London 08:00-09:00, 11:00 | 16 | **registered** (Europe) | LB-EURUSD / LBC-EURUSD | `15cb722f0a67` / `e43dd09885bd` |
| 6 | USDJPY | icm-probe | Tokyo 09:00-10:00, 12:00 | 16 | **registered** (Tokyo) | LB-USDJPY / LBC-USDJPY | `24fb1d1b9c86` / `2b291a35aef4` |
| 7 | BUND10Y | vantage-demo | Berlin 08:00-09:00, 11:00 | 16 | **registered** (Europe) | LB-BUND10Y / LBC-BUND10Y | `cbf941bf9300` / `13a7a667a577` |
| 8 | JP225 | icm-probe | Tokyo 09:00-10:00, 12:00 | 16 | **registered** (Tokyo) | LB-JP225 / LBC-JP225 | `c7f73f8a84da` / `cddd20718048` |
| 9 | XAGUSD | icm-probe | New York 08:30-09:30, 11:30 | 16 | queued (NY group full) | LB-XAGUSD / LBC-XAGUSD | `5d6d7b150e90` / `afa358f94caa` |
| 10 | USTEC | icm-probe | New York 09:30-10:30, 12:30 | 16 | queued (US500's near-duplicate) | LB-USTEC / LBC-USTEC | `be4886b0e38b` / `d42af4dc795e` |
| 11 | GBPUSD | icm-probe | London 08:00-09:00, 11:00 | 16 | queued (Europe full) | LB-GBPUSD / LBC-GBPUSD | `70665fc91289` / `78a7d6e40779` |
| 12 | COPPER | vantage-demo | New York 08:30-09:30, 11:30 | 16 | queued (Vantage NY full) | LB-COPPER / LBC-COPPER | `53ad7dfe0793` / `b7e5c7ded5b4` |
| 13 | UK100 | icm-probe | London 08:00-09:00, 11:00 | 16 | queued | LB-UK100 / LBC-UK100 | `0a1e8565e58d` / `e5d58f3c524e` |
| 14 | XPTUSD | icm-probe | New York 08:30-09:30, 11:30 | 16 | queued | LB-XPTUSD / LBC-XPTUSD | `ecde87228882` / `e5cf0f52dcef` |
| 15 | AUDUSD | icm-probe | Tokyo 09:00-10:00, 12:00 | 16 | queued | LB-AUDUSD / LBC-AUDUSD | `a2ddabbad3b7` / `348d01e9fa68` |
| 16 | USDCAD | icm-probe | New York 08:00-09:00, 11:00 | 16 | queued | LB-USDCAD / LBC-USDCAD | `e7edb44163bf` / `f81005cd4b16` |
| 17 | XBRUSD | icm-probe | London 08:00-09:00, 11:00 | 16 | queued (XTIUSD's near-duplicate) | LB-XBRUSD / LBC-XBRUSD | `35006879a48b` / `8380b8dbbcb6` |
| 18 | USDCHF | icm-probe | London 08:00-09:00, 11:00 | 16 | queued | LB-USDCHF / LBC-USDCHF | `5928cb942f16` / `0f45accc184e` |
| 19 | NZDUSD | icm-probe | Tokyo 09:00-10:00, 12:00 | 16 | queued | LB-NZDUSD / LBC-NZDUSD | `9ef62e4ea9e3` / `4c24c0084b8f` |
| 20 | XNGUSD | icm-probe | New York 09:00-10:00, 12:00 | 16 | queued (cost and gap risk) | LB-XNGUSD / LBC-XNGUSD | `faf3c514280b` / `b80ba9755d38` |
| 21 | WHEAT | vantage-demo | Chicago 08:30-09:30, 11:00 | 8 | queued (cost; session hours unverified) | LB-WHEAT / LBC-WHEAT | `ca8e9909da51` / `42845fe11e71` |
| 22 | SOYBEAN | vantage-demo | Chicago 08:30-09:30, 11:00 | 8 | queued (as WHEAT) | LB-SOYBEAN / LBC-SOYBEAN | `67efc9c3242f` / `000f85f8c7b2` |
| 23 | COFFEE | vantage-demo | New York 04:30-05:30, 07:30 | 16 | queued (cost; session hours unverified) | LB-COFFEE / LBC-COFFEE | `7993d968376a` / `884ba48bbb9a` |
| 24 | SUGAR | vantage-demo | New York 04:30-05:30, 07:30 | 16 | queued (as COFFEE) | LB-SUGAR / LBC-SUGAR | `b8a6f41dffe2` / `cd31c35923db` |
| 25 | XAUEUR | icm-probe | London 08:00-09:00, 11:00 | 16 | queued (gold component retired on XAUUSD) | LB-XAUEUR / LBC-XAUEUR | `aec0750bde08` / `fefdb9196f39` |
| 26 | XAUAUD | icm-probe | Tokyo 09:00-10:00, 12:00 | 16 | queued (as XAUEUR) | LB-XAUAUD / LBC-XAUAUD | `80041997a8d4` / `e13da7f72451` |

**A queued row** is frozen here (its definition does not change) but is not in the spec: no K row, no fill.

- **When it may be registered.** When a registered pair in its session group retires, or when the slots above are
  raised (an owner decision, section 9).
- **How.** It joins at the next rank, by appending its two spec rows exactly as above, with `since` = its registration
  day and `until` at most 45 days later. Its two K rows are written before its first fill.
- **No re-ranking from fills.** A queued row is never re-ranked by any registered pair's fills: rank order is fixed
  here.

## 6. The control (frozen; LBC-<key>) and the stops

**The control.** `rule: session_range_control` (GS1C's reviewed rule), with the same `instrument`, `session_local`,
stop, target and hold as its arm, and `params.control: "learning:LB-<key>"`.

- One entry on every eligible session day (a range formed), at a 15m close drawn uniformly over the break window from
  the sha256 seed of (control id, day, session).
- A seeded coin-flip side.
- The same common filters, cap and minimum lot as its arm.
- **Cadence.** At most one entry per session day, like its arm. The control enters on every eligible day; the arm only
  on days that break. The read pairs them by session day (section 7).
- **What it is the null for.** Timing and direction together, inside the same window and with the same exits. This is
  the sign-randomised null the strategy memo asks for (W3 (c)), not a same-side twin.
- **Its stops.** It never stops on its own. It stops with its arm (engine `exploration_learning`) and is listed under
  `none` in `config/arm_stops.yaml`.

**Kill rules, whichever comes first.** The engine enforces 1-3, configuration enforces 4, and the weekly
learning-ops review enforces 5-7 by a dated retirement (`retired_day` / `retired_rule` on both rows).

1. **Read due.** At 300 admissible fills since `since`, the arm stops entering (engine). It will not reach 300 in 45
   days.
2. **Loss cap.** Cumulative net P&L since `since` reaching minus £160 retires the arm (engine; each entry is also
   refused if realised loss plus open risk plus the new trade's loss would reach it).
3. **End.** Past `until` 2026-12-17, the arm stops entering (engine).
4. **H-61 intraday stop.** Running net R of -15 R within the first 100 fills retires the arm (`config/arm_stops.yaml`
   `h61`).
5. **Cost kill.** At 30 or more fills of the pair (arm plus control), if the measured mean round-trip cost per R is
   above 0.20 R, retire the pair. At that cost no published intraday effect leaves a net edge, and the cost cell is
   recorded.
6. **Execution kill.** Any of the following stops every LB/LBC pair at once (both rows back to `blocked`), opens an
   incident to the trading-safety reviewer, and the fill is excluded as evidence:
   - a fill on a symbol other than the row's instrument;
   - a magic number outside the instrument's block;
   - a size other than the minimum lot;
   - a position held across its session close or the 17:00 New York rollover.
7. **Selection check (not a kill).** If over 10% of an arm's signals in a week are refused by `learning_slots` or the
   pot's slots, the read reports it as selection-affected. It goes to the research-director within 24 h.

## 7. Reading rule (decided before any fill)

**When.** Per instrument, never pooled, at the earliest of the engine's stops or 2026-12-17.

**Fills.** Only admissible fills sent on or after `since` count. In practice that means after lane L3 unblocks the
pair; nothing can fill before then.

**Quantities:**

- **Cost per R.** The pair's measured round trip (spread plus commission at entry, slippage at entry and at each exit
  kind) divided by the stop distance in price, with its mean and day-clustered SE.
- **Excess.** Arm net R minus control net R, on session days where both entered. Its SE is day-clustered (the pair
  enters at most once a day, so this is the per-day difference SE). Also reported: the arm's net R, the control's,
  and gross R for both.

**Power, before any data.**

- **Expected fills.** If L3 unblocks around 2026-11-09, about 28 weekdays remain, so about 22 arm fills (assuming
  about 80% of sessions break) and about 28 control fills.
- **What the read can detect.** With a per-trade R sd of about 1.1 for both, the paired SE is about 0.33 R, so the
  minimum detectable excess at t >= 2 with 80% power is about 0.9 R.
- **What to expect.** Section 3's prior is 0 to +0.05 R. **The expected outcome is NO-LEAD on every instrument, and
  that is not evidence against the hypothesis.**

**Verdicts** (the evaluator gives one per instrument):

| Verdict | Condition | Action |
|---|---|---|
| COST-FAIL | n >= 30 pair fills and mean cost per R > 0.20 | The pair retires (kill 5). The instrument is recorded as "15m intraday too costly at the minimum lot" in hypotheses.md A29 and in the cost of record. No 15m learning line on it without a new cost measurement |
| LEAD | Excess >= +0.15 R, paired t >= 2.0, arm net R > 0, and >= 15 paired days | The pair retires at its end. The researcher may write ONE history qualification pre-registration for that instrument's own-session ORB (screen 2010-2017, confirm 2018-2025-09, on that instrument's history, with costs from this pair's measured cells), at K +1 when it runs. A LEAD is never a qualification and never sizes anything |
| NO-LEAD | Anything else | The pair retires at its end. Recorded as "no lead from the learning class (underpowered)", **not** as a failed hypothesis. It is not re-registered for learning without new evidence (a history screen or a cost change) |

The cost cells of every pair go into the cost of record per instrument (`research.cost_of_record`, keyed by symbol)
whatever the verdict.

## 8. K, the budget and the registry

- **Rows written.** 16 K rows: 8 arms and their 8 dedicated controls, family `learning_class`, status `learning`, each
  config = (arm id, its frozen config hash, this doc).
  - `scripts/record_learning_k_rows.py` writes them in the same run as the gold learning rows (30). The script now
    holds 46 rows in all.
  - Run `--budget` on the brain first: it prints the quarter's count, the 3,500 cap (ADR 0073) and the missing rows.
    The write checks the budget under the registry lock and writes nothing if the rows do not fit.
- **Fit.** ADR 0073 sized the 3,500 for sprint-3000 (3,000), the sprint-200 lanes, the C1 composites, POS-1, mlprob
  and "the demo learning-class rows, with headroom".
  - The registry is on the brain and was not readable from this lane, so the exact count used is not known here.
  - The 16 rows are inside the headroom ADR 0073 describes, but that is not verified.
  - If they do not fit, nothing is written, the arms stay refused (fail closed), and raising the budget is the
    owner's decision.
- **Queued rows** cost nothing until registered, then 2 each.
- **Fail closed.** Until the K rows exist, every LB and LBC row is refused by the learning gate as well as held by its
  `blocked` status.

## 9. Deviations from the brief, and decisions that are not ours

**1. `status: blocked`, not `run`.** The brief said "status run".

- Today's engine evaluates every arm's rule on the account's gold bars: nothing in `goldbot/engine` reads
  `params.instrument` yet. A `run` row would trade a gold session breakout under the name LB-US500.
- The pairs are therefore blocked with the same note as S0-/I0-<key>, and registered on their accounts. The engine
  drops blocked arms (logged).
- **The unblock.** When lane L3 runs an arm on its own instrument, set `status: run` on both rows of each pair together
  with S0-/I0-<key>. Status is not hashed, so this changes no K row and no k_id.
- `since` stays 2026-11-02 and `until` 2026-12-17. No fill can exist before the unblock, so the 45-day window only
  shortens.

**2. 8 of 26 registered.** The brief's own rule applies: rank and register the top ones; queue the rest (section 5).

**Raising the slots to register more** is a demo-limits change under ADR 0072. It is not taken here. The numbers, for
the research-director, PD and PO:

- **Registering all 26.** That needs `learning_max_open` 6 -> about 14 and an intraday pot of about 12 on icm-probe
  (scalp 19 -> 11 inside the unchanged 23, or a host-cap change, which is the owner's), and vantage-demo's intraday pot
  6 -> 10.
- **Recommendation:** none until the first two weeks of the 8 pairs show the slot-refusal rate and the measured costs
  (kills 5 and 7). Then register the next ranks group by group.

**3. Budget.** Owner-only if the 16 rows do not fit (section 8).

**4. Real money.** Never. Every pair is on a demo probe_only account, and the loader refuses a non-XAUUSD instrument
on a live or observe-only account (`tests/test_learning_mi.py` and `tests/test_instrument_accounts.py`).

## 10. Known-failed, not re-tested here

- **Session and opening-range breakouts on XAUUSD** at 15m-1h: TREND-07/08/09, R-02, GS-C2; sprint3000 grade D.
- **15m entry patterns on gold generally.** The strategy memo freezes new trials of this kind. These arms are on other
  instruments and are a learning line, not a qualification trial.
- **Intraday momentum on gold** (first and last half-hour): academic memo, section 7.
