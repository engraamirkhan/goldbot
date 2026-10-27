# 0049: Demo epoch roll, top-ups and the 200-per-account exploration budget

Status: proposed (CFO, 2026-10-27). It is decided under the owner's delegation of demo sizing and limit decisions to
the experts (2026-10-18), once trading-safety has reviewed it (blocking: items T1-T4 below).

**Tighten-only.** This ADR does not change any RiskGate limit, % ceiling, `daily_loss_limit_abs`, `budget_share`,
per-trade cap, `max_open_total`, drawdown stage or the 12% halt. Real-money accounts (`icm-live`, `vantage-live`) and
ADR 0004's £50 daily limit are untouched, and nothing here can reach them (T1).

What it amends:
- ADR 0043 section 2: the R1 rotation actor and the rotation budget, on demo `probe_only` hosts only;
- the volume targets of `docs/research/volume-500-plan-2026-10-26.md` sections 2.2 and 5, as corrected by the owner.

It is aligned with the frozen scalp-volume pre-registration H-81 (`docs/research/prereg-2026-10-27-scalp-volume.md`,
amendment 7, commit c7c9ac8e). Where this ADR's first draft differed (Vantage at $3-4.5 stops, icm-probe at 250),
**H-81 section 7 governs.**

## Context

**Directive.** The owner asked on 2026-10-26 and 2026-10-27 for 500+ demo fills a day. The program director relayed
a correction on 2026-10-27: that means **500+ in total, about 200 per account**, across icm-probe, icm-demo and
vantage-demo, not 500 on each.

**Inputs** (sources named; nothing re-measured here):

| Input | Value | Source, sample |
|---|---|---|
| ICM round trip per 0.01 lot | $0.156 (≈ £0.117) | daily review 2026-10-26, 137 fills, one day |
| Vantage round trip | $0.52 | 5 fills; no CI possible |
| Units | 0.01 lot = 1 oz; $1 = £0.75 | volume plan 2.1 |
| Exploration budget, measured as realised net loss + open risk at the stops | £198 icm-probe, £198 icm-demo, £180 vantage-demo (0.9 × £220 / £220 / £200) | `config/accounts.yaml` on main `a6762a2c` |
| −8% exploration pause (X4) | `probe_only` hosts, from the gate's mark; all three hosts are `probe_only` | `goldbot/engine/exploration.py` `paused` |
| Rotation command (ADR 0043 X5) | **not built.** Today nothing can lower a mark, so a host that reaches −8% stays paused | grep on main `a6762a2c` |
| Reference balances | icm-probe £11,000; icm-demo £10,960.55; vantage-demo £10,000 | ADR 0042; `accounts.yaml` comment |

**Arithmetic.** It uses the volume plan's first-passage model. The script is outside the repo; with the plan's
500-fill icm-probe mix it reproduces the plan's 6% / 17% to within 0.5 points.
- The mixes are the stated design mixes.
- D is the within-day variance inflation. It is unmeasured until research D4, so D = 1, 1.5 and 2 are all shown.
- Every drift figure assumes **zero gross edge**: it is the cost of learning, not a forecast. No profit is implied.

| Host and mix (fills/day) | Cost c | Drift £/day | P(budget binds) D1 / D1.5 / D2 | Weekly (cap 6%) | Trading days to −8% |
|---|---|---|---|---|---|
| icm-probe **250**: probes 50, 1m 125 ($3), 5m 75 ($4.5) | $0.156 | 29 | 0.1% / 0.7% / 1.8% | £146 (1.3%) | 30 |
| same | $0.25 | 47 | 0.3% / 1.4% / 3.3% | £234 | 19 |
| icm-demo **150**: 5m 140 ($4), intraday 10 (£12 R), 1 swing (£40) | $0.156 | 18 | 3.5% / 8.3% / 13.2% | £88 (0.8%) | 50 |
| icm-demo 200: 5m 190, intraday 10, 1 swing | $0.156 | 23 | 6.5% / 12.9% / 18.6% | £117 | 38 |
| icm-demo 200: 5m 195, intraday 5, 1 swing | $0.156 | 23 | 2.5% / 6.6% / 10.9% | £117 | 38 |
| vantage-demo **150 scalp**: probes 30, 1m 50, 5m 70 | $0.52 | 59 | 0.4% / 1.6% / 3.5% | £292 (2.9%) | 14 |
| vantage-demo **200 scalp**: probes 40, 1m 70, 5m 90 | $0.52 | 78 | 3.3% / 7.6% / 11.7% | £390 (3.9%) | 10 |
| same | $0.35 | 53 | 0.9% / 3.0% / 5.7% | £262 | 15 |
| vantage-demo 200 + 5 intraday | $0.52 | 78 | 10.6% / 17.3% / 22.6% | £390 | 10 |
| vantage-demo 200 + 10 intraday (today's mix) | $0.52 | 78 | 18.9% / 26.5% / 32.0% | £390 | 10 |

**Superseded rows.** The vantage-demo rows above assume $3-4.5 stops. At $0.52, those stops carry cost/R of
0.12-0.17, which fails H-81's single Vantage bar of cost/R ≤ 0.08. That bar needs **$7 stops** (£5.26 at risk per
trade). D1 and D2 use H-81 7.2-7.3's figures instead.

**What the table says:**
1. **At about 200 per account, the daily caps are not the problem.** Every scalp-dominated mix stays inside the
   budget on about 90% or more of days, even at Vantage's $0.52.
2. **The 15m-1h fills set the bind probability, not the scalp count.** Ten intraday fills at £12 R add more variance
   and open risk than about 150 scalps.
3. **The cumulative drawdown is the problem.** At $0.52, Vantage at its V2 level (≈ 130 at $7 stops, £53 a day) reaches
   its −8% (£800) in about 15 trading days, and 200 at $3-4.5 stops would reach it in about 10. Under ADR 0043 that means
   2-4 rotations in what is left of Q4, against a budget of 2 a quarter, each
   needing an owner TOTP the code does not yet have. Volume would stall on Vantage within about two weeks of
   reaching 200.

## Decision

### D1. Volume and the daily exploration £ budget per account (question 3)

The budget per account is **unchanged**: £198 / £198 / £180, the same `budget_share` 0.9. The bind target is
**P(budget binds) ≤ 10% at the measured D** (D = 1.5 until research D4 reports). It is met by choosing the mix, not
by raising any £.

| Host | Target fills/day (step 1, 11-02) | Mix limit that holds the target | P(bind) at D1.5 | Drift/day (zero edge) |
|---|---|---|---|---|
| icm-probe | **≈ 323**: scalp 269 + probes 54 (17%, under 20%) | scalp + probes only, with no 15m-1d arm. Scalp pot £168, `max_open` 14, all inside the unchanged £198, `max_open_total` 17 and `max_positions` 18 (H-81 7.1) | 1.0% (whole £198, $0.156) to 3.5% (scalp pot, $0.23) | £38 |
| icm-demo | **≈ 138**: scalp 128 + intraday ≈ 9 + swing ≈ 1 | **Option A** (H-81 3.4): the style pots become shares of one £198 host budget. That is an ADR 0047 amendment and an engine change, and needs trading-safety review. Under option B, separate pots, the target is ≈ 120 | 8.0% | £18 |
| vantage-demo | **≈ 90** (V1); **≈ 130** once its 15m-1d arms move to `vantage-swing` (V2); **never above ≈ 165 at the $0.52 cost** | **one stop bar**: cost/R ≤ 0.08 at the measured round trip (the 95% upper bound once there are ≥ 200 own fills), so $7 stops at $0.52. V1: scalp pot £100 / 8 open, intraday £40, swing £40. V2: scalp £150 / 12, probes £30 | 8.5% (V1) / 5.2% (V2) | £23-35 (V1) / £53 (V2) |
| **Total** | **≈ 551 (V1) to 591 (V2)**, at least 520 either way | | | **≈ £80-110 a day, demo £; real cost £0** |

- **The plan has headroom on purpose.** 551-591 planned against 500 required gives a 10-18% margin. The margin is
  for refusals and the 20% probe-count rule. These are filled fills; admissible fills still need step 0's void repair
  (45% void on 10-26).
- **No new paid host is needed for the corrected target.** The ICM depth host (about £3.6 a month, owner-only spend)
  in the volume plan 2.3 C is withdrawn as a requirement. icm-demo stays a depth host at about 138.
- **The CFO chooses option A for icm-demo.** It adds about 18 fills a day at £0 of real cost, and the £198 total is
  unchanged. It is conditional on trading-safety accepting the ADR 0047 amendment; until then option B (≈ 120)
  applies.
- **Weekly check** (research director, in the daily review): if a host's 10-day realised bind rate is above 10%, its
  target steps down one rung at the next 00:00 UTC roll. A cap never moves. The rungs are H-81's:
  - icm-probe: if its bind rate is above 5%, or its cost is above $0.23, Z7P and Z4P stop first;
  - icm-demo: option A, then option B;
  - vantage-demo: V2, then V1.

### D2. Vantage: scalp waits for its own measured cost (question 2)

**Chosen: wait.** Vantage runs at the cost-safe level for the $7 bar and steps up only on a cost read from its own
fills: at least 200 admissible fills, the 95% upper bound of the round trip, and at least 3 session days. At V1's
about 90 a day that read takes about 3 trading days. It is the cheapest lever in the program.

| Vantage state | Stop bar (cost/R ≤ 0.08) | Target |
|---|---|---|
| Cost unmeasured; 15m-1d arms still on the host (V1) | $7 | **≈ 90** |
| `vantage-swing` live, so the whole £180 serves scalp + probes (V2) | $7 | **≈ 130** (5.2% bind at D1.5) |
| 95% upper bound $0.35-0.56 | $7 (stops below UB/0.08 are skipped as `cost_per_r` and logged, never resized) | at most **≈ 165**. At $0.52, 195 fills bind on 16.1% of days and are refused |
| 95% upper bound ≤ $0.35 on ≥ 200 own fills (V3) | the 1m strata at $7 (H-81 amendment) | ≈ 175-190, up to **200** |
| 95% upper bound ≤ $0.24 | $3 allowed; the VS S-twins unblock | **200+**, subject to the volume plan's 300/500 bars |
| Upper bound above $0.56, or entry slip above $0.25 on ≥ 100 fills | — | hold at V1, with a microstructure review within 2 working days |

**Rejected:**
- **$3-4.5 stops at $0.52** (this ADR's first draft): cost is 0.12-0.17 R, so a trade needs 0.35-0.5 R gross by the 3×
  rule. That buys cost-dominated labels.
- **A reduced share of 500 now:** it would be sized on a 5-fill cost figure.

### D3. Demo epoch roll: top-up and mark reset at −8% (question 1)

**Trigger.** Unchanged. ADR 0043 −6% warning, then the −8% X4 pause; P0 continues and exits are never gated.

**The roll.** It runs at the first 00:00 UTC risk-day roll after the pause, once the host is flat except P0 (open
arms exit by their own rules first). In this order:

| Step | Act | Rule | Records |
|---|---|---|---|
| 1 Top-up (optional) | demo deposit **back to the reference balance, never above it** (£11,000 / £10,960.55 / £10,000) | Never above the reference: a larger balance would widen the £ weekly and stage room while the %s stay the same. That is a loosening, and it is refused. ADR 0042 D3/D4 apply (mark + flow; day and week starts + flow) | a `deposit` capital flow, real cost £0 |
| 2 Mark reset | mark = equity, stage NORMAL, **automated** on the fenced path (T1) | It never touches `day_start_equity` or `week_start_equity`, and never touches another engine | journal `rotation`: epoch n → n+1, old and new mark, equity, the epoch's demo £ by sleeve |
| 3 Resume | exploration unpauses at the same targets | the arms' H-61 stops, £ ledgers, Thompson posteriors and two-look counts **carry over unchanged** | |

**Without a top-up the roll is still safe.** The next epoch's £ caps shrink pro rata, because 2% of a lower day start
is less. That is a tightening.
- **Top-ups.** A top-up is an optional broker client-area act for the owner. It is reported in the digest as
  available, never as an ask.
- **If the start balance falls below 85% of the reference** (about two rolls without a top-up), the SRE opens a fresh
  demo account for that slot at the reference balance. This is ADR 0043 R3: no mark is lowered and no owner input is
  needed. The login goes only to the VPS keyring.

**Why the roll is automated (amends ADR 0043 R1).**
- An owner TOTP every ~15 trading days on Vantage goes against the owner's standing "no owner input" rule.
- Lowering a mark on a fenced demo account puts no real money at risk.
- The trading-safety constraint is that **the path must never reach a live account**. T1 enforces that by
  construction.
- **If trading-safety does not accept T1,** the roll falls back to R3 (fresh demo accounts) only. No mark is lowered
  without the owner.

**The rotation budget (replaces ADR 0043's "2 a quarter, the 3rd not done" on demo `probe_only` hosts).**

| Tripwire | Action |
|---|---|
| An epoch shorter than 5 trading days | the host's target steps down one rung; CFO + research director review within 2 working days. Drift is then about twice the plan, so cost or edge is worse than measured |
| 3 or more rolls on a host in a quarter with no arm at C1 | the research director's readout (arms stopped, demo £ per admissible label) goes to the monthly review |
| The quarter's cumulative demo exploration cost per host is above £2,500 (icm-probe), £1,200 (icm-demo) or £3,500 (vantage-demo). That is about 1.5× the zero-edge plan for the 43 trading days left in Q4 | CFO review within 2 working days; the host drops one rung until it is reviewed |
| −12% / HALTED | **no roll.** The ADR 0043 K2 re-arm path stays as it is. A roll is never available at or after a halt |

### D4. Recording, so P&L reads stay honest

| Item | Rule |
|---|---|
| Top-up | a `deposit` capital flow (ADR 0042 D1/D2). Never in P&L, R, the phase gate, adaptive or dynamic-limit evidence, or the north-star count. Monthly return is time-weighted with flows excluded |
| Epoch | a `rotation` journal row and a ledger epoch row per host: epoch number, start and end dates, the demo £ consumed by sleeve, the top-up amount |
| Drawdown | always reported **twice**: current epoch, and cumulative "as if no roll" (the sum of every epoch's loss since the reference balance), on the dashboard, in the daily review and in the monthly review. A roll never makes a demo drawdown look smaller |
| Account-level metrics | drawdown, Sharpe and the equity curve never splice across a roll; each epoch is a separate segment. Arm evidence in R is scale-free and continuous |
| Cost of learning | "exploration cost (demo £)" by host and quarter, real cost £0, kept apart from the strategy book (ADR 0043). ROI is read as demo £ per admissible label and as graduates per quarter |
| Capital plan | no demo epoch result is evidence for a real-money sizing or capital recommendation. Live readiness reads cumulative figures only, and through its own gates |

### Break-even (E1)

- **Real cash: £0.** Demo deposits are free, and no hosting is added.
- **Demo £:** about £80-110 a day at zero gross edge.
- **Break-even per fill:** a gross edge of $0.156 per oz on ICM (about 0.05 R on a $3 stop) and $0.52 per oz on Vantage
  (0.074 R on the $7 stop). An arm below that bleeds demo £ and is stopped by its H-61 rule, not by this ADR.

## What would reverse or change this

| Signal | Change |
|---|---|
| D4 measures D > 2 | each target steps down one rung |
| D4 measures D ≤ 1.2 | icm-demo may go to about 150 |
| Vantage's 95% upper bound ≤ $0.24 on ≥ 200 fills | the S-twins unblock, and Vantage is eligible for 200+ and the volume plan's 300/500 rungs |
| H-81 is amended or retired | D1 and D2 follow it; the caps and the roll do not change |
| A broker stops demo top-ups or expires a demo | R3 fresh account for that slot |
| Trading-safety rejects T1 | R3 only; ADR 0043 R1 (owner TOTP) stays the only mark-lowering path |
| Any proposal to roll a real-money or non-`probe_only` account | refused. That needs a new ADR, and it is owner-only |

## Consequences

**Expected rolls in the rest of Q4 (about 43 trading days, zero edge):**

| Host | Expected rolls |
|---|---|
| vantage-demo at V2 (≈ 130) | about 2-3 |
| icm-probe at ≈ 323 | about 1-2 |
| icm-demo | 0 |

- **Volume** stays at about 551-591 fills a day across the three hosts and never stalls on the −8% pause, provided
  the roll code is in before the first expected −8%.
- **The first expected −8% is Vantage.**
  - At V2 (£53 a day), it comes about 15 trading days after V2 starts, around 2026-11-20 or later, from a flat mark.
  - At V1 (£23-35 a day), it takes about 23-35 trading days.
  - icm-probe at £38 a day takes about 23 trading days, around 2026-12-03.
  - The roll code is due **Fri 2026-11-13**.
- **If the roll code is late,** the host pauses (as today) and the program director hears it the same day.

**Implementation** (implementer, failing tests first; trading-safety full review):

| # | Change | Review |
|---|---|---|
| T1 | The automated roll, refused unless **all** of these hold: config `mode: demo` **and** `probe_only` **and** an `approval_mode` that is not confirm; the broker-reported trade mode is DEMO when the roll is called; the drawdown is ≥ 8% and < 12%; the host is flat except P0; it is a 00:00 UTC roll. It is refused at load on any account that could be live. It never touches day or week starts or another engine | **trading-safety** (full), security, code |
| T2 | The top-up guard: a confirmed deposit that would lift the balance above the reference journals `topup_above_reference`, and the host stays paused until it is reviewed | trading-safety |
| T3 | Arm state carries over the roll unchanged: stops, £ ledgers, posteriors, look counters. Test: a roll leaves every arm's H-61 counters byte-identical | quant, code |
| T4 | Tripwires D3 and the step-down rungs D1, applied at the 00:00 roll and journalled | trading-safety |
| T5 | Ledger epoch rows, cumulative-across-epoch drawdown and cost, deposits as capital flows | finance-auditor |
| T6 | Dashboard and digest: the two drawdowns, the epoch number, "top-up available" as information | ui-ux, code |

**Accountable:**
- implementation: the program director (T1-T6), due 11-13;
- the volume targets in D1 and D2: the research director, from step 1 on 11-02;
- the icm-demo option A engine change (ADR 0047 amendment): the program director, with trading-safety review;
- the tripwire reviews: the CFO;
- the November finance review checks D4 against the ledger.
