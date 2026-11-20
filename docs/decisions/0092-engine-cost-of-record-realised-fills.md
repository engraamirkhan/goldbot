# 0092: Engine cost of record from realised fills (P5/P6)

Status: proposed (principal data engineer, 2026-11-20); quant review and trading-safety review required before merge

## Context
Finance audit 2026-11-20, finding N10 (recommendations P5 and P6, open since October): the engine's per-trade `cost`
was 2.81x the realised entry cost (n 673 since 9 Nov), and the exit quote was recorded on 0 of 811 exploration exits,
so exit slippage was unmeasured.

Root cause, from the code and the brain's `state/costs_<account>.json` (built 2026-11-19 23:10):
* the engine's round trip is `CostTable.round_trip_usd_per_oz` = spread median + 2 x slippage + 2 x commission;
* its slippage cells read only MAIN-engine fills (`slippage_table`). Every engine is `probe_only`, so `n_fills` is 0
  in every cell and each cell charges the $0.15/side prior; the probe sleeve may only RAISE a cell
  (`apply_probe_slippage`), and the scheduled probes measured entry slip of -$0.02 to -$0.05 (favourable);
* so the engine charged about 0.08 + 0.30 + 0.08 = $0.46-0.47 per oz on ICM ($0.46 on Vantage), of which $0.30 is
  the unmeasured prior. The exploration ledger's 4,302 gold fills (2026-10-26..11-19) were never fed back, by an
  earlier rule ("demo fills never lower a cost") that was right while nothing was measured and now keeps a known
  overcharge in force.

Measured (`goldbot.research.realised_costs`, read-only on aux.db): the realised round trip (spread + signed entry slip +
signed exit slip + commission; swap reported apart) is $0.19-0.26 per oz mean per account x session, about half the
engine's charge. The audit's 2.81x is overstated because it excluded exit slippage; the right ratio is about 2x. But
not every cell is cheaper: ICM 15m cells pay large stop-exit slippage (+$0.33 to +$0.58 mean exit slip in London) and
are DEARER than the engine assumed.

Research is separate: `cost_of_record_v3.yaml` (ADR 0084) prices the fill against the research BAR and is pinned by
pre-registrations; it is not changed here.

## Decision
1. **A versioned engine cost of record** (`config/engine_costs/<version>.yaml`, `EngineCostRecord`), built by
   `python -m goldbot.research.realised_costs` from closed gold exploration fills: account x session x timeframe cells
   plus an account x session pooled cell, each with n, session days, the mean with a day-clustered (22:00 UTC) CR1
   95 % t interval, p50/p75/p90 and the component means. The first version is `ecr-2026-11-20.1` (window
   2026-10-26..2026-11-19, 4,302 fills).
2. **Exit slip, by the best evidence available**: the recorded exit quote (P6, below), else a stop at its initial
   level or a target at its level (target price improvement floored at 0 per fill, v3's rule), else the same broker's
   scheduled-probe market-exit slip p75 in that session, else the $0.15 prior. Brokers whose demo exits fill at the
   level by construction (`DEMO_EXIT_AT_LEVEL`, quant ruling 2026-11-09) never use their level slip.
3. **Charge = max(point, 95 % upper bound of the mean)** (`ub95`), where point = spread + max(0, entry slip mean) +
   max(0, exit slip mean) + commission. A cell under 30 fills or 5 days charges max(its point, its pooled cell's
   charge). Favourable slippage is floored on the cell mean, never credited.
   *Why not p75 of per-trade costs (the brief's example):* the engine uses this number as an expectation (the gate's
   cost share of R, the EV hurdle, sizing, the H-81 cost stop). p75 adds about 0.67 sd of per-fill market noise (the
   send latency's move) to every cell and lands at $0.39-0.63, i.e. it would largely reinstate the overcharge this
   ADR removes. Conservatism belongs on the estimate's uncertainty. `--charge p75` remains available; a quant ruling
   can switch it with a new version.
4. **The engine reads it first** (`Engine._cost_atr` -> `engine/cost_record.py`): the cell of its account, session and
   timeframe (exploration arms pass their own tf, the main engine its decision tf), gold's book only; else the legacy
   nightly table; else `cost_atr`. The live spread + commission floor (ADR 0075) still applies. An unreadable record
   falls back to the dearer legacy table, logged. Accounts without cells (live accounts, non-gold books) are unchanged.
5. **Versions are immutable and stamped.** `EngineCostRecord.save` refuses an existing file; a new measurement is a new
   version file and a reviewed change of `costs.engine_record`. Each exploration row stamps `cost_version`
   (`ecr-...`, `table:<day>` or `fallback`), so every past read keeps the basis it was charged on.
6. **P6: the exit quote on every exploration exit.** `OpenTrade.exit_bid/exit_ask/exit_quote_utc` are set when the
   engine sends a close (the quote it was decided on) and when a tick first reaches the broker-held stop or target
   (the trigger quote); `exploration.record_close` copies them to the ledger row. Persisted with the open trade, so a
   restart between the trigger and the sweep keeps it (the engine-store golden gains three null fields, 192 cases, no
   other change).

## Consequences
* Most cells charge 25-50 % less (e.g. icm-probe London 1m $0.234 vs $0.459; vantage-demo London 15m $0.231 vs
  $0.454): entries the gate refused on the prior's $0.30 now pass when their real cost allows. ICM 15m cells charge
  more (icm-probe London 15m $1.996, icm-demo London 15m $1.445, icm-demo NY 15m $0.774, icm-probe Asia 15m $0.725):
  those arms' entries now meet a truer, higher cost. Both directions are the point of the change.
* The H-81 cost stop (`arm_stops.cost_state`) reads the recorded `cost` per R across rows; after the switch a stratum's
  30-fill window mixes bases. `cost_version` makes the split readable; whether the stop reads one version only is a
  quant decision (flagged).
* Exit quotes make the next version's exit legs `quote`-measured instead of imputed; rebuild once >= 95 % of a month's
  exits carry one (audit P6 target) or when cost drift says so. Rebuilding is a reviewed new version, never an edit.
* Real-money accounts keep the legacy (dearer) table until their own fills measure them; nothing here borrows demo
  cells for live.
* Rollback: `costs.engine_record: null` (the legacy table charges every cell again).
