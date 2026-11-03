# 0069: C1 research cost model (bps point, measured swap, residual overshoot) and the random-side control

> **Implementation note (2026-11-03):** the decision stands; its code landed in a different place than the PR that
> proposed it (#289, closed as superseded). The C1 cost and random-side control run in
> `goldbot/research/sprint3000/engine.py`, `goldbot/research/sprint3000/control.py` and
> `goldbot/research/position/costs.py` (see `docs/research/sprint3000/prereg-amendment-c1.md`); the module names
> `cost_of_record.C1Costs` and `c1_gates` below are historical.

Status: accepted (amendment C1, adopted 2026-11-02 on the independent audit; principal software engineer, implementation)

## Context

The independent audit of 2026-11-02 (docs/research/rethink/audit.md) found that "0 of 3,330 qualified" is mostly a
process artefact. Two of its top flaws are in the shared research machinery:

1. **Costs in today's dollars against 2010-17 dollar stops.** Every screen charged the cost of record's dollar UPPER
   bound (round trip max($0.23, ci90 high), overshoot max($0.12, ci90 high)) and the shipped long swap (-$0.60/oz a
   night, shorts 0) against stops sized at $1,160-1,670 gold. 1h ATR in bps is stable (15-26 bps every year), so the
   same rule cost about twice the R in 2012 that it costs today. The measured short swap is a credit, not 0.
2. **The same-side "leak-free" twin plus a 0.05 R hurdle** rejects pure directional (TSMOM-type) edges by
   construction: a twin a few minutes later on the same side carries the same side edge.

## Decision

1. **One shared cost function** (`goldbot.research.cost_of_record.C1Costs.cost_r`, formula in `cost_r`):
   `cost_R = (RT_point_bps x P + max(0, OS_point_bps x P x w - simulated gap) - nights x swap_bps(side) x P) / stop`.
   - RT and OS are the cost of record's ICM point estimates per session (pooled `all` outside every session),
     converted to bps at the reference price $4,000.
   - The swap is the broker's measured terms (`swap_measured` in `config/cost_of_record.yaml`; ICM 2026-11-02: long
     -52.22, short +36.00 USD/lot/night, Wednesday triple), so a short held overnight is credited.
   - No double count: where a simulated stop already filled at a gapped minute open, only the measured residual
     overshoot is added.
   - The dollar upper bound stays a reported stress column, never a gate.
2. **The swap block is versioned on its own** (`swap-icm-2026-11-02`); the C1 model's id is
   `c1/<cells version>/<swap version>`. The cells and their version `cor-2026-10-31.2` are unchanged, byte for byte,
   so every screen pinned to them (ADR 0064, including the in-flight sprint-3000 screen) still loads. This amends ADR
   0064 point 1: "a refreshed table gets a new version" applies to each versioned block; adding a block never rewrites
   a pinned one.
3. **Control and gates** (`goldbot.research.c1_gates`): the null is the random-SIDE twin at the same minute with the
   same stop and exit, taken in expectation (both sides walked, the coin averaged exactly), or the drift-adjusted
   zero; pass needs excess >= 0. Same-side twins become an entry-timing diagnostic. Explore, ensemble, confirm
   (BH-FDR q 0.10 on the one-sided day-bootstrap p) and TEST-OLD rules are those of the amendment, in code.

## Consequences

- At 1h-4h the C1 cost is about a third to a half of the old charge on 2010-17 trades; rules whose gross was eaten by
  the dollar charge are re-read (`scripts/rethink/c1_rescore.py`), with no new trial (declared by the amendment).
- A long-only rule that collects the window's drift passes the random-side control but fails the drift-adjusted
  leg; both legs are required at explore.
- The engine's own cost settings (`costs.swap_*`: -60 / 0) are not changed here: that is the order path's estimate
  and goes through trading-safety review on its own.
- A broker without a measured swap has no C1 cost (KeyError): another broker's terms are never used (ADR 0043).
