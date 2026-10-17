- **E-6 day-summed DSR coded and wired into the survivor verdict (BACKLOG 76, implementer, 2026-10-17; code review
  APPROVE, quant review round 2 pending; not pushed).** The discovery survivors' reading rule (pre-registration
  2027Q1 O-5/O-6: "the day-summed DSR is also >= 0.95") is now a coded check.
  - **The record.** `metrics.day_summed_dsr` sums net R per UTC entry day over days with a trade only (G days), with
    var(SR) = 1/G and the skew and kurtosis of the daily sums. The entry day is `pipeline.entry_utc`: `ts_utc` plus
    one bar, the real entry. Every walk-forward (not a discovery trial) stores it with the trial as
    `results.model_filtered.day_summed`. Beside it are the per-trade DSR of the same R series and both effective ts
    (`dsr_per_trade_r`, `t_per_trade_r`). Those two are **R basis, not the gate DSR**, which is on `ret`.
  - **The reading.** `gates.day_summed_check(row, rows)` reads, on `registry.n_trials_effective(rows)` (the phase
    gate's K, both discoveries' K_eff included), two things: the gate's DSR from `gates.current_dsr` (moved there from
    `ops/gates_phase.py`, which imports it) and the day-summed DSR. It passes only if both are >= 0.95, and fails
    closed without stored inputs or below 200 trading days.
  - **The verdict path.** A pre-registration with `rule_id` "O-5"/"O-6" (`registry.DAY_SUMMED_RULE_IDS`) puts its
    evaluated trial under `director.survivor_decisions` and `survivor_refusal`:
    - `record_verdict`: "continue" needs the gates and the reading passed; otherwise "stop", or "inconclusive" when
      the inputs are missing.
    - A holdout slot goes live, and `promotion_refusal` / `passed_gates` count the survivor's pass, only with the
      reading passed.
    - H-02's survivors, pre-registered under their own rule, are not read by it.
  - **Rule amendment.** The pre-registration's O-5/O-6 text records the stricter amendments dated 2026-10-17, before
    any H-14 outcome: the 200-day floor, the entry day and the gate DSR on `ret`. The program director notes them for
    the owner.
  - **Not changed.** `research_gates`, the phase gate's numbers, stored Q4 results and any frozen reading. Rows
    written before this have no `day_summed`.
  - **Tests.** `tests/test_day_summed_dsr.py` (22) and a compat case in `tests/test_persistence_compat.py`.
