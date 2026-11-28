# Pre-registration amendment: scheduled FX cost probes for the measured FX10 cost of record (H-106 U2, P3)

Amends `docs/research/prereg-2026-11-28-u2-pooled-body.md` (H-106 U2, rev 2) section 3 (P3, fill cut-off, Stage 0
date) and section 7 (dates). Written by the research director on 2026-11-28, after the owner's approval of the same
day: "run scheduled FX cost probes on the demo accounts so FX10 can get a measured cost of record".

**What this fixes, and why it must be fixed first.** The card's P3 needs at least 30 measured fills per instrument at
BOTH brokers. Today no FX10 instrument can meet that. Re-derived by the research director from a copy of the brain
`aux.db` taken on 2026-11-15 (`exploration_trades` and `cost_probes`):

- 530 filled FX10 exploration trades, all sent between 2026-11-03 and 2026-11-13. None have been sent since (I0
  retirements, commit e62cca0a).
- 389 of them are at icm-probe (the 7 USD pairs only) and 141 at vantage-demo (the 3 JPY crosses only). No instrument
  has a fill at both brokers.
- 0 of the 530 recorded an exit quote. There are 0 FX rows in `cost_probes`.
- Vantage stops and targets fill at their level on demo (`realised_costs.DEMO_EXIT_AT_LEVEL`). So no vantage FX exit is
  measurable today.

So the probes this amendment fixes are the only route by which any FX instrument can become a member. That is exactly
why the protocol must not be able to choose members. The card (section 3) and the quant (U2 finding 5) both name this
selection-bias risk. Every count, time, side and stopping rule below is fixed now, before any probe runs, and none of
them reads an FX outcome, an FX cost value or any U2 result.

**K 0.** Probes are cost measurements (`prereg-cost-measurement-2026-10.md`; ADR 0037 Amendment 5), not trials. They
carry no signal and are never read for edge. Quant-reviewer signs (required). Trading-safety review is required on the
engine change before any probe runs (section 6).

## 1. Protocol (fixed now)

| Item | Fixed value |
|---|---|
| Instruments | All 10 of FX10: AUDJPY, AUDUSD, EURJPY, EURUSD, GBPJPY, GBPUSD, NZDUSD, USDCAD, USDCHF, USDJPY. None is added, dropped or paused during the window. |
| Brokers and accounts | ICM: account `icm-probe`. Vantage: account `vantage-demo`. Both are `mode: demo` in `config/accounts.yaml`. No other account sends these probes. ICM is one broker for the cost cells (`icm-demo` and `icm-probe` pool, as in `fx_cost_cells`). |
| Broker symbols | ICM: the 10 pair names with no suffix. The 3 crosses are listed by ICM with the same terms (see the `config/instruments.yaml` header). Vantage: `GBPJPY+`, `EURJPY+`, `AUDJPY+` as configured. The 7 USD pairs use their Vantage names, read by the read-only `symbol_check` before the first slot. |
| Never | On a `mode: live` or observe-only account, or on a terminal that does not report demo at the order. |
| Sessions | asia, london and newyork, by `goldbot.data.calendar.DEFAULT_SESSIONS`. A probe's session is `realised_costs.session_of(sent_utc)`. The whole window is in northern winter, so in UTC: asia runs 22:00-08:00, london 08:00-13:30 and newyork 13:30-22:00. |
| Slots (UTC, 15m bar closes) | asia: 00:15, 02:00, 03:45, 05:30. london: 08:30, 09:45, 11:00, 12:15. newyork: 14:15, 15:45, 17:15, 18:45. Monday to Friday. |
| Why these slots | Each slot plus its 30-minute window stays inside its session. All asia slots fall after the rollover blackout ends (16:45-18:15 New York = 21:45-23:15 UTC). The last newyork window ends at 19:15 UTC = 21:15 server, before the Friday cut (21:30 server). Re-derived with `DEFAULT_SESSIONS.session_label` on 2026-12-07, 12-18 and 12-22. |
| Count | Exactly one probe per instrument per broker per slot. That is **4 per instrument × broker × session per day**, and **120 per broker per weekday** (10 × 3 × 4). |
| Order within a slot | FX10 in alphabetical order, rotated by `(d × 12 + s) mod 10`. Here d is the number of weekdays since 2026-12-07 and s is the slot index 0-11 in the order above. Only the clock sets the order. |
| Slot window | Each probe is sent between its slot time and slot + 30 min. A probe not sent by then is **missed**. It is recorded with its reason and never replaced or made up later. |
| Side | A pre-committed seeded RNG with **seed 20261128**. `side = +1` if `int(sha256(f"20261128|{slot_utc:%Y-%m-%dT%H:%MZ}|{instrument}").hexdigest(), 16) % 2 == 0`, else `-1`. The broker is not in the key, so both brokers take the same side for a given slot and instrument. No rebalancing. |
| Lot | The broker's minimum (`symbol_info.volume_min`), read at the order and expected to be 0.01. If an instrument's minimum at a broker is above 0.01, it is not probed there. That is recorded, and the instrument keeps whatever fills it has. |
| Entry | A market order at the instrument's current quote, through `RiskGate.check` (purpose `cost_measurement`), then `Engine._execute`. |
| Hold and exit | The hold is fixed at **60 s** (1 minute), from the fill, on the host clock. The exit is a market close when the hold ends (`HOLD_EXIT`). It is never gated: halts, a probe switch-off and a restart all still close it. There is no target. The stop is the safety rail below, not a strategy stop. |
| Safety stop (risk rail) | A server stop **20 pips** from the fill (0.0020 on X/USD and USD/X pairs, 0.20 on JPY pairs). The loss at the stop is at most £2 at 0.01 lot (EURUSD about £1.6, USDCHF about £1.8, GBPJPY about £1.1). A probe closed by this stop is recorded. At vantage its exit is a level fill, so it is unmeasurable and is dropped from the cost cell and counted (`fx_cost_cells.DROP_EXIT`). |
| Exit quote | Recorded at both brokers: the **instrument's own** bid, ask and quote time at the moment the close is sent (prereg K11: `exit_bid`, `exit_ask`, `exit_quote_utc`). |
| Concurrency | At most **2 open FX probes per account** (on different instruments) and at most 4 across the program. No FX probe is sent while the account holds a position, or has an order in flight, on the same instrument. That slot waits inside its window, else it is missed with reason `busy`. Gold scheduled and mirror probes are separate and unchanged. |
| Hours | These slots replace `cost_probe.hours_utc` for this stream only. The [07:00, 21:00) UTC window of `prereg-amendment-2026-11-07-kill-only.md` section 4 stays in force for gold probes. Its restore condition is met: "Restore Asia probes only if a pre-registered card needs an Asia cost cell with fewer than 50 fills". U2 needs FX asia cells holding 0-16 fills (vantage AUDJPY asia: 0). |
| Caps | Every RiskGate check applies unchanged, including the instrument's spread cap (`max_spread_points`), margin, the daily loss limit and the kill switch. The stream has its own allowance of **£25 per account per risk day**: realised probe loss plus the open probes' loss at their stops. Below it, a probe is refused, fail-closed. Expected spend is about £10 per broker per day (section 3), so the allowance is not expected to bind. |
| Window | From the first slot after the engine change is deployed to the last slot before **2026-12-23 00:00 UTC**. No FX probe of this stream is sent at or after that instant. The end is hard-coded, and no config value can extend it. |
| Latest start | The first slot must be no later than **Wed 2026-12-09 00:15 UTC**, which leaves 10 weekdays (40 slots per cell). If it is later, this amendment lapses before any probe is sent, and a new amendment, written before any probe, sets new dates. |
| Identity | Its own magic offset, reserved in `goldbot/engine/magic.py`. A `cm-fx-` order id of at most 29 characters. Ledger `purpose: cost_measurement` and `basis: scheduled_fx_u2`, `agent_id: scheduled_fx`, in aux.db `cost_probes`. Never `closed_trades.jsonl`, the phase gate or attribution. |

**Independence (binding).** The probe count, the slot times, the order, the sides, the hold and the window do not
depend on any FX outcome, cost value, fill count or U2 read.

**No early stopping per pair.** The schedule runs to completion for every instrument at both brokers, whatever the
instrument's interim cost, P&L or fill count. That includes after it reaches 30. The only ways a probe is not sent:

- the RiskGate refuses it;
- the £25 allowance is reached;
- the slot is missed (busy, bridge or terminal down, market closed);
- the latest-start lapse applies.

Each of these is logged with its reason. None is ever chosen per instrument.

**Blind during the window.** Until the cut-off, the window's operational read reports **counts only**: scheduled,
sent, filled, refused by reason, missed by reason and dropped exits, per instrument × broker × session × day. No FX cost
value, slip, spread statistic or P&L of this stream is computed, reported or looked at for a decision before
2026-12-23 00:00 UTC. The single exception is a safety alert: an account's realised loss crossing a RiskGate limit,
which is reported as a limit event with no per-instrument cost.

## 2. Which cost cells a probe fills (srv3_cost_record / fx_cost_cells fallback)

The ECR cells are instrument × broker × session × tf. U2's tfs are 15m, 30m and 1h (card section 1). A probe has no
timeframe, so:

- **A probe fill counts only toward its broker × session `all` cell**, with tf label `probe`. It never counts toward a
  15m, 30m or 1h cell.
- Each of U2's tf rows charges its own tf cell only if that cell is trusted (≥ 30 measured fills and ≥ 5 session days)
  from strategy fills. Otherwise it charges the session `all` cell (`ecr-session-all`), which the probes fill. Only if
  that cell is also untrusted does it charge the broker's worst trusted session (`ecr-broker-max`). This is the
  existing `fx_cost_cells.cost_rows` rule, unchanged.
- The schedule targets **≥ 30 in every session at both brokers** (48 slots per cell at the latest start, section 3).
  The card's membership rule is weaker: ≥ 30 in at least one session at each broker. So, barring outages, each session
  charges its own measured cell rather than the broker-max fallback.
- Membership stays exactly the card's (quant verbatim): "An instrument with fewer than 30 fills at either broker in
  every session stays `prior` and leaves the body; the measured broker is never charged alone." Fills mean measured
  fills, as `fx_cost_cells` counts them.

**Spread-cap refusals (bias guard, mechanical).** The RiskGate's spread cap refuses an entry when the quote is wide. So
an expensive pair could lose slots exactly when it is expensive, and that would bias its measured cost down. The rail is
never loosened. Instead:

- A probe refused for spread is recorded with its quoted spread.
- In the cost record, each such refusal enters its broker × session `all` cell's round-trip sample, for the mean and
  ub95 only. Its value is its quoted spread plus the cell's mean positive entry slip, mean positive exit slip and mean
  commission (bp).
- It does **not** count toward the 30 fills for membership.
- The record reports the refusal count per cell.

This can only raise a cell's charge. It needs no judgement.

## 3. Dates, counts and expected spend

| Date (UTC) | Step | Owner |
|---|---|---|
| before the engine deploy | This amendment merged; quant signs | research director; quant-reviewer |
| 2026-12-04 | Quant ruling on swap for history rows (section 5), before the first swap capture | quant-reviewer |
| 2026-12-04 21:00 | Engine change deployed after trading-safety and code review (section 6); swap capture starts with it | implementer; trading-safety-reviewer; sre |
| Mon 2026-12-07 00:15 | Planned first slot. The latest allowed is Wed 2026-12-09 00:15, else the amendment lapses | engine |
| 2026-12-18 | Dukascopy FX10 bid+ask 1m pull, 2003-01..2025-09 (era term), local only | data-engineer |
| **2026-12-22 23:59:59** | **New fill cut-off**: fills sent at or after 2026-12-23 00:00 UTC are not counted (replaces 2026-12-11 23:59 UTC), and the stream stops | engine; microstructure-researcher |
| 2026-12-30 | P3: measured FX10 cost of record, a new recorded version on an ADR 0098 R-H line (replaces 2026-12-11) | microstructure-researcher; quant countersigns |
| first day P1-P4 hold (expected 2026-12-30), else 2027-01-31 | **Stage 0**, recorded once and never re-run (card section 3, unchanged rule) | evaluator |
| 2027-01-06 23:59 / 2027-01-08 12:00 | U2 joins the N26 batch of 2027-01-08 if its Stage L CONTINUE is recorded by 2027-01-06 23:59. Otherwise it joins the next N26 batch, whose date the evaluator sets before U2's Stage L read | evaluator |
| **2027-01-31** | **Kill date, unchanged**: if P1-P4 do not hold, every cell is NOT STARTED and U2 closes UNTESTABLE (cost/data) | research director |

**Counts at the planned start.** There are 12 weekdays from 2026-12-07 to 2026-12-22, with no FX market holiday
between them.

- Per instrument × broker × session: 12 × 4 = **48 scheduled slots** against the 30 needed. That allows 18 lost slots,
  about 4.5 lost days, per cell.
- At the latest start (2026-12-09): 10 weekdays, 40 slots, still a margin of 10.
- Per broker: 120 probes per weekday, 1,440 over the window. Both brokers together: 240 per weekday, 2,880 over the
  window.
- Fills before 2026-12-07 (the 2026-11-03..11-13 exploration fills) still count under the existing builder rule.

**Expected spend, per probe.** The 11-03..11-13 fills give median quoted spreads (re-derived from the same 2026-11-15
copy) of:

| Instrument | Account | Median quoted spread (pips) |
|---|---|---|
| EURUSD | icm-probe | 0.05 |
| AUDUSD, NZDUSD, USDCHF, USDJPY | icm-probe | 0.1 |
| GBPUSD, USDCAD | icm-probe | 0.2 |
| EURJPY | vantage-demo | 0.0 (raw quote) |
| AUDJPY | vantage-demo | 0.4 |
| GBPJPY | vantage-demo | 0.95 |

- At 0.01 lot, a pip is worth about $0.10 on X/USD pairs and about ¥10 on JPY pairs. Raw-account commission is about
  $0.07 per 0.01 lot round trip.
- Expected cost per probe is therefore about £0.05-0.12.
- Per broker, 120 probes a day cost about **£6-14 per day**. A 60-second hold adds about ±£1 of P&L noise per day.
- This is demo money, spent on measurement. It is not a strategy and has no £/day target.

## 4. What changes in the card

- Section 3 (P3): the fill cut-off becomes "Fills dated after **2026-12-22 23:59 UTC** are not counted". The P3 due
  date becomes **2026-12-30**. The probe protocol is this amendment.
- Sections 3 and 7: Stage 0 still runs "on the first day P1-P4 hold or on 2027-01-31, whichever is first". The expected
  first P1-P4 day moves from 2026-12-11 to 2026-12-30. The kill date stays 2027-01-31.
- Code constant: `srv3_cost_record.FILL_CUTOFF_UTC` becomes `"2026-12-23T00:00:00+00:00"` on PR #527
  (`feat/cost-record-fx`), with the dry-run label text in `fx_cost_cells.build_record` to match.
- Unchanged: membership, the ub95 charge rule, the W2 floor, the worse broker charged, S0-2 binding, Holm m 3, the N26
  batch rule and every gate.

## 5. Work this amendment needs (each one owner; no two write the same file)

1. **Engine: scheduled FX probe stream** (implementer; trading-safety-reviewer and code-reviewer). See section 6.
2. **Dukascopy FX10 bid+ask pull** (data-engineer). Pull 1m bid and ask for the 10 pairs, 2003-01..2025-09, into
   `raw/dukascopy_fx/<inst>_1m_dukascopy_<year>.parquet` with `ts_utc`, `spread_mean` and `dq_flag`, the schema
   `fx_cost_cells.fx_spread_table` reads.
   - It runs via the `data-dukascopy` workflow pattern.
   - The data is local only and never published (data-local-only rule).
   - A manifest with sha256 per file. The holdout months and later are never pulled into the research tree. Due
     2026-12-18.
3. **FX swap-term capture at both brokers** (sre for the job, implementer for the reader). Once a day at 12:00 UTC on
   icm-probe and vantage-demo, read-only `symbol_info` for FX10, recording `swap_long`, `swap_short`, `swap_mode` and
   `swap_rollover3days`, each with `captured_utc`.
   - It is append-only in the ops DB (aggregates only: no account identifiers in anything published).
   - It starts with the engine deploy and runs through Stage 0.
4. **Quant ruling on swap for history rows** (quant-reviewer, before the first capture, due 2026-12-04). The card
   charges "FX/XAG swap per side per rollover from the broker's symbol swap spec captured point in time". But captures
   start in 2026-12, and the learn and confirm rows are 2010-2025-09. A 2026 swap spec applied to 2010-2021
   (near-zero-rate) rows is not point in time. The options:
   - (a) Charge the earliest capture to every history row. This is simple, and wrong in sign and size across rate
     regimes.
   - (b) Swap = the as-of policy-rate differential (point in time, `available_utc`) plus the broker's markup, measured
     as the capture minus the differential at capture time. This needs a policy-rate series per currency.
   - (c) Charge the worse side's captured swap on both sides as a conservative constant. This can only raise cost.

   **My recommendation: (b) as the charge, with (c) reported as a sensitivity.** If (b)'s rate series cannot be
   assembled by 2026-12-30, use (c), stated as such. Not (a). The ruling is recorded in the card before the first
   capture is read.

## 6. What the engine change must implement (implementer brief)

**1. A new stream in `goldbot/engine/cost_probe.py`**, beside the gold `scheduled_step`, not a reuse of its gold
constants.

- Config: a block `cost_probe.fx_scheduled` on icm-probe and vantage-demo only. The loader refuses it on any account
  that is not `mode: demo`, as `cost_probe` already is.
- The values are pinned by a test to section 1: slots, hold 60 s, stop 20 pips, ≤ 2 concurrent, £25 allowance, seed
  20261128, end 2026-12-23 00:00 UTC.
- It is independent of `cost_probe.enabled`, `hours_utc` and the gold `scheduled` toggle.

**2. A probe-only symbol at the second broker.** Each FX10 instrument gets the other broker's symbol for this stream
only: the 3 crosses at ICM and the 7 USD pairs at Vantage, with names and terms from a read-only `symbol_check`.

- No strategy or exploration book or arm trades them.
- `tests/test_instrument_accounts.py` ("one broker per instrument") is relaxed only for a `probe_only` listing.
- A tick read per symbol at send and at close is enough. Do not add full books: icm-probe has no CPU for more books
  (incident 2026-11-02).

**3. Schedule.**

- Slot times, the rotation and the 30-minute window, all from the clock (section 1).
- The same-instrument busy rule, ≤ 2 open FX probes per account, and one record per missed slot with its reason.
- The existing `schedule_skip` weekend, rollover-blackout and Friday-cut checks still apply. None is expected to fire at
  these times.

**4. Side.** The seeded sha256 function of section 1, a pure function with a test vector.

**5. Order.** Through `_place`, so `RiskGate.check` runs, then `Engine._execute`. The ADR 0037 Amendment 5
`cost_measurement` fence is extended to FX:

- the minimum lot;
- a demo terminal, read fresh;
- hold ≤ 60 s;
- a stop ≤ 20 pips, with loss at the stop ≤ £2 at the minimum lot.

Every other gate check is unchanged, including the instrument's spread cap. The stream gets its own reserved magic
offset in `magic.py`, a `cm-fx-` id (≤ 29 characters) and `basis: scheduled_fx_u2`.

**6. Exit.** An FX variant of `hold_exits` closes at fill + 60 s and is never gated.

- **The exit quote must be the probe's own symbol's tick.** `hold_exits` today stores `eng._last_tick`, which is gold's
  quote. That would be a silent wrong exit quote on every FX probe. Failing test first.
- Record `exit_bid`, `exit_ask` and `exit_quote_utc` at both brokers.
- A market close at vantage must fill at market, not at a level. Verify this on the first probes; it is reported as a
  count.

**7. Record.** One `ProbeRecord` per order in `cost_probes`, with:

- `symbol` set;
- `spread_usd` in the symbol's price units;
- `fx_acct_per_usd` = account currency per unit of the QUOTE currency (as `fx_cost_cells` reads it);
- commission and swap from the deals;
- `signal_utc` = the slot time.

A refused or missed slot gets a skip row with its reason, and its quoted spread for a spread refusal.

**8. Allowance.** £25 per account per risk day, counting realised loss plus open loss at the stops, fail-closed.

**9. Hard end.** No send at or after 2026-12-23 00:00 UTC.

**10. Counts-only report.** Scheduled, sent, filled, refused by reason, missed by reason and dropped exits, per
instrument × broker × session × day. It shows no cost values before the cut-off.

**11. Builder** (`fx_cost_cells` / `srv3_cost_record` on PR #527). The microstructure-researcher's lane does this, not
the engine lane:

- read the stream's rows from `cost_probes` (`purpose: cost_measurement`, `basis: scheduled_fx_u2`, FX10 symbol) with
  tf `probe`, so they feed the session `all` cells only, and an exit source `quote`;
- apply the spread-refusal imputation of section 2;
- `FILL_CUTOFF_UTC` = 2026-12-23 00:00 UTC.

## 7. Integrity

- Pre-registered before any FX probe runs and before any FX cost value of this window exists. The only inputs are the
  2026-11-15 `aux.db` copy's counts and median quoted spreads, which are fixed and pre-window, plus the code paths
  cited.
- If any row with `basis: scheduled_fx_u2` is dated before this amendment's merge commit, the window is void, and a new
  amendment is written before more probes.
- K 0: no trial is booked by probes. U2's K stays 0 now, with a worst case of 6.
- Holdout sealed: the Dukascopy pull stops at 2025-09.
- Public repo: account ids only. No login, host or raw fill is committed. The cost record publishes aggregates only.
- Real money, sizing and live unlock are owner-only and untouched. Probes never run on a real-money account.

## Sign-off

- Research director: 2026-11-28.
- Quant-reviewer: pending (required before any probe).
- Trading-safety-reviewer: pending, on the engine change (section 6), before deploy.
