# 0084: Cost of record v3: fill-to-research-bar cost by broker, session and exit (amends 0064)

Status: proposed (microstructure researcher, 2026-11-15; research-director plan line 2; quant-reviewer sign-off due
before the first pin)

## Context

ADR 0064 prices new pre-registrations with `cor-2026-10-31.2`. That table builds the round trip on the broker's own
quote: spread + max(0, entry slip) + max(0, exit slip) + commission, plus a stop-exit overshoot. Research P&L, however,
is measured on Dukascopy 1m **mid** bars.

The backtest-vs-demo parity study (`why-strategies-fail-2026-11-15.md` section 1) replayed 3,653 demo fills through the
research mechanics:
- ICM fills sit about $0.01 a side from the Dukascopy mid, not the broker's $0.043 half-spread;
- the zero floors throw away favourable slippage;
- the cost actually taken, replay gross less broker net, is **$0.116/oz [0.093, 0.140]** on 993 ICM random-control
  fills, where the screens charge $0.30-0.32.

The bias is conservative, so it passed no false positive. It decides 1m-5m economics, though: 0.10 R is charged on a
$3 stop where 0.04 R is paid.

## Decision

1. **A second table, `cor-2026-11-15.3`** (`config/cost_of_record_v3.yaml`, basis `fill_to_research_bar`). It is built
   by `python -m goldbot.research.cost_of_record fill-to-bar`. The definitions are in that module.
   - **Population:** broker-reconciled demo fills, sent 2026-10-26 to 2026-11-06 (the research bars end there). These
     are closed gold exploration fills plus scheduled cost probes, the probes priced as time exits. Pyramid adds and
     `cost_cells` exclusions are dropped.
   - **Cost of one fill** = replay gross on the research bars less the broker's booked net. Swap is excluded because
     the screen charges it per rollover.
   - **Cells:** broker x session (asia, london, newyork, all) x exit (stop, target, time, all).
   - **Which cell a fill goes in:** the research barrier's cell, and only when the broker's exit agrees. Disagreements
     are counted (`n_unmatched`, about 5 %) and never priced.
2. **The charge is the upper bound.** The interval is mean +/- t(G-1, 0.975) x the standard error clustered by
   opportunity day, with the G/(G-1) correction.
   - The charge is that upper bound, and never less than the cell's commission.
   - A cell with fewer than 30 fills or 5 days is `sparse`. It charges at least its broker's pooled cell for that exit.
3. **Target-exit slippage is floored at 0, per fill.** It is measured against the order's own level: requested price
   +/- target distance.
   - Demo price improvement on targets (about $0.16 a fill at ICM) is not bankable for real money.
   - A target exit is therefore charged commission + entry slippage against the bracket's quote + any adverse fill at
     the level.
   - The first real-money fills must re-check this cell before a screen relies on it.
4. **Stops and geometry.**
   - A broker whose demo stops fill at the stop price (95 % or more within $0.005; Vantage) is `stops_trusted: false`.
     Its stop exits keep the 0.70 stress on top of the cell, as ADR 0064 point 3 and ADR 0043 require.
   - The table carries the window's median ATR14 at 1m, 5m and 15m.
   - `ArmCosts.cost_dominates(broker, tf, stop)` flags a decision timeframe of 15m or faster whose stop pays more than
     **0.05 R** per stop exit at the dearest session. Our own history gross at 1m-15m is within about +/-0.03 R.
   - The screen reports `cost_dominated_share`. A pre-registration that trips the flag must say, before any data, why
     its gross covers the cost.
5. **New pre-registrations only.**
   - **v2 stays as it is.** `cor-2026-10-31.2` stays in `config/cost_of_record.yaml`, unchanged. The catalogue
     (`catalogue_costs.yaml`) and sprint3000 (`sprint3000_catalogue.yaml`) pin it, and so does every running line.
   - **`arm_costs.yaml` is not edited.** It is in the sprint3000 engine content hash. `load_costs` finds v3 as its
     sibling (`FILL_TO_BAR_FILE`).
   - **Closed trials are not re-read**, and no registry row changes.
   - **How a screen uses v3:** it pins `cor-2026-11-15.3` (`--cost-version`). `screen_charges` / `screen_net_r` then
     charge each trade the cell of its own research barrier.
   - **Screens without exit labels** charge the dearer of target and time as the round trip, with a top-up to the stop
     cell on stop exits.
   - **The v2 reader refuses a v3 pin.** `require_version` raises, so a v3 table is never read as v2 cells.
6. **Refresh.** A rebuild on more session days gets a new version, `.4` and up. It never rewrites `.3`.

## Consequences

The table, `cor-2026-11-15.3`, charges these amounts in USD/oz (mean in brackets):

| broker, session | stop | target | time |
|---|---|---|---|
| ICM, all sessions | 0.165 (0.113) | 0.236 (0.191) | 0.305 (0.205) |
| ICM Asia / London / New York | 0.177 / 0.247 / 0.278 | 0.310 / 0.214 / 0.354 | 0.430 / 0.344 / 0.251 |
| Vantage, all sessions | 0.071 + 0.70 stress | 0.336 | 0.307 |

- **Stop exits get cheaper.** An ICM stop exit costs 0.18-0.28 against 0.35-0.43 under v2, roughly 40 % less.
- **Target and time exits are about level with v2.** Two things cause this:
  - the target floor charges the entry slippage that demo price improvement had hidden;
  - only 10 session days are available, so each session x exit upper bound sits 0.1-0.2 above its mean.
- **The charge will fall as days accrue.** The standard error falls with the square root of the day count. A refresh
  at 20 or more days is the cheapest next gain.
- **The pooled-session charges** are the ones a broker-agnostic power calculation should use. Asia time exits (0.43)
  are the dearest cell, and that is mostly the probes' 30-second noise.
- **Acceptance check.** The builder's ICM random-control cell, on the parity definition with swap included, reproduces
  **$0.116 on 993 fills**. That matches `parity.py` on the same pull.
- **The 0.05 R flag in this regime.** ATR14 is 1m $1.70, 5m $4.03 and 15m $7.45. At those values the flag trips for a
  1-ATR stop at 1m (0.16 R) and 5m (0.069 R), but not at 15m (0.037 R). On 2010-25 history, where the 5m ATR was
  about $1, a 1-ATR 5m stop pays about 0.28 R.
- **Limits.**
  - The sample is one regime of 10 days.
  - Fill probability at the target, where the broker needs the bid or ask to reach the level, sits in the unmatched
    5 % and is not priced. It is symmetric here: 70 against 63 at ICM.
  - Vantage cells are thin. Vantage stays at stress for stops until backlog 141 records its exit legs.
- **Code:**
  - `goldbot/research/cost_of_record.py`: the v3 builder (`fill_to_bar_rows`, `build_fill_to_bar`,
    `day_clustered_ci`, `render_fill_to_bar_yaml`);
  - `goldbot/research/arms.py`: `FillToBarTable`, `check_pin`, `fill_to_bar_charge_usd`, `cost_dominates`, and
    `screen_charges(exits=)`;
  - `scripts/run_arm_backtest.py`: per-exit charges and `cost_dominated_share`.
