# 0084: Cost of record v3: fill-to-research-bar cost by broker, session and exit (amends 0064)

Status: proposed (microstructure researcher, 2026-11-15; research-director plan line 2). Quant review of 89e69db3
returned CHANGES; this text answers it. Quant sign-off is due before the first pin.

## Context

ADR 0064 prices new pre-registrations with `cor-2026-10-31.2`. That table builds the round trip on the broker's own
quote: spread + max(0, entry slip) + max(0, exit slip) + commission, plus a stop-exit overshoot. Research P&L, however,
is measured on Dukascopy 1m **mid** bars.

The backtest-vs-demo parity study (`why-strategies-fail-2026-11-15.md` section 1) replayed 3,653 demo fills through the
research mechanics. ICM fills sit about $0.01 a side from the Dukascopy mid. On 993 ICM random-control fills whose
broker exit matched the replay's barrier, the cost actually taken (replay gross less broker net) is
$0.116/oz [0.093, 0.140]. The screens charge $0.30-0.32.

The quant review found that matched-only pricing selects on the outcome. Across all 1,048 control fills the cost is
**$0.199**, not $0.116.

## Decision

1. **A second table, `cor-2026-11-15.4`** (`config/cost_of_record_v3.yaml`, basis `fill_to_research_bar`). It is built
   by `python -m goldbot.research.cost_of_record fill-to-bar`. The definitions are in that module. `.3`
   (commit 89e69db3, matched fills only) was never pinned. It is superseded and is not rewritten.
   - **Population:** broker-reconciled demo fills, sent 2026-10-26 to 2026-11-06. These are closed gold exploration
     fills plus scheduled cost probes, the probes priced as time exits. Pyramid adds and `cost_cells` exclusions are
     dropped.
   - **Cost of one fill** = replay gross on the research bars less the broker's booked net. Swap is excluded because
     the screen charges it per rollover.
   - **Every replayed fill is priced** in the cell of its **research** barrier, whatever the broker did.
     `n_unmatched` is information only.
   - **The parity check is stated both ways** (`checks`): all fills, and matched only.
2. **What a v3 pin charges.**
   - For a v3 pin, the cell's charge **replaces** ADR 0064 point 2 (the 0.23 round-trip floor) and point 3 (the
     $0.12 ICM overshoot floor and the separate overshoot charge). The half-spread and the stop overshoot are inside the
     measurement, so they are not charged again.
   - The cell charge is the larger of three floors:
     - (a) the upper bound of mean +/- t(G-1, 0.975) x the **wider of the CR1 and CR2 (Bell-McCaffrey)** standard
       errors, clustered by opportunity day (22:00 UTC);
     - (b) the cell's commission;
     - (c) the session's **all-exit** cell charge.
   - **Why (c).** Pricing by research barrier moves each disagreement's whole swing into one exit's cell. A
     research-stop fill that the broker took to target lands in `stop` as a large negative cost; the reverse inflates
     `target`. Without (c), a stop-heavy arm would be charged about the commission, less than the sample's total cost.
   - A cell with fewer than 30 fills or 5 days is `sparse`. It also charges at least its broker's pooled cell for that
     exit.
3. **Target-exit slippage is floored at 0, per fill**, wherever the broker exited at target. It is measured against
   the order's own level: requested price +/- target distance. Demo price improvement is not bankable for real money.
   The first real-money fills must re-check the target cell.
4. **Stops, stress and geometry.**
   - **Built-in stress.** A broker whose demo stops fill at the stop price is `stops_trusted: false` (95 % or more
     within $0.005; Vantage). Its stop charge includes the 0.70 stress (ADR 0043, 0064 point 3).
   - **Stress line.** For a v3 pin, the screen's stress line (`net_r_cor_stress`) is the cell **without** its built-in
     stress + 0.70. The 0.70 is added once, never twice.
   - **Geometry flag.** `ArmCosts.cost_dominates(broker, tf, stop)` flags a decision timeframe of 15m or faster whose
     stop pays more than 0.05 R at the dearest session. The screen reports `cost_dominated_share`.
5. **New pre-registrations only.**
   - **v2 stays as it is.** `cor-2026-10-31.2` and `config/cost_of_record.yaml` are unchanged, and so is every pin on
     them (catalogue, sprint3000, running lines).
   - **`arm_costs.yaml` is not edited.** It is in the sprint3000 engine content hash. `load_costs` finds v3 as its
     sibling.
   - **A malformed v3 table is logged and left unloaded.** v2 screens keep running, and `check_pin` refuses a v3 pin.
   - **The v2 reader refuses a v3 pin.** `require_version` raises.
   - **Closed trials are not re-read.**
6. **Refresh.** A rebuild gets a new version (`.N+1`) and never rewrites one that exists.
   - **Trigger:** any cell whose realised mean over a later 10-session-day window exceeds its charge forces `.N+1`
     (`cost_of_record.refresh_due`).
   - **Routine rebuild:** at 20 or more session days, to narrow the bounds.

## Consequences

`cor-2026-11-15.4` charges these amounts in USD/oz. The all-exit mean is in brackets.

| broker, session | stop | target | time | all exits |
|---|---|---|---|---|
| ICM, all sessions | 0.281 | 0.669 | 0.482 | 0.281 (0.214) |
| ICM Asia / London / New York | 0.353 / 0.296 / 0.350 | 0.791 / 0.637 / 0.900 | 0.605 / 0.741 / 0.350 | 0.353 / 0.296 / 0.350 |
| Vantage, all sessions | 0.341 + 0.70 stress | 1.689 | 0.410 | 0.341 (0.175) |

- **The realised cost is lower than v2, but the charge is not.**
  - At ICM the realised cost across all fills is $0.214 [0.147, 0.281], against v2's $0.30-0.32 average charge: about
    1.5x lower, not 2.6x.
  - The charge falls only for stop exits (0.28-0.35 against 0.35-0.43).
  - Target and time cells are dearer than v2. They carry the disagreement swings, then floor (c) is added on top.
  - A typical 50 / 40 / 10 stop / target / time mix in London charges about $0.48, against v2's $0.29.
  - Until the quant reviewer rules on the alternative below, **v3.4 is more conservative than v2 for most arms.**
- **Alternative proposed to the quant reviewer.** Charge per exit = the matched exit cell (execution cost) + one
  exit-agnostic, day-clustered **disagreement surcharge** per session (all-fill mean less matched mean, about $0.055 at
  ICM).
  - It is unbiased for the sample's mix and does not inflate target cells.
  - It would be `.5`, only on that ruling.
- **Acceptance.** ICM controls give $0.116 matched (993 fills, as `parity.py` gives) and $0.199 over all fills (1,048).
- **Limits.**
  - The sample is one regime of 10 session days.
  - Vantage target cells are thin, which is why the charges run to 1.69-3.89.
  - Vantage stops stay at stress until backlog 141.
- **Code:**
  - `goldbot/research/cost_of_record.py`: `fill_to_bar_rows`, `build_fill_to_bar`, `day_clustered_ci` (CR1/CR2),
    `refresh_due`;
  - `goldbot/research/arms.py`: `FillToBarTable`, `check_pin`, `fill_to_bar_charge_usd(with_stress=)`,
    `cost_dominates`, `screen_charges(exits=)`;
  - `scripts/run_arm_backtest.py`.
