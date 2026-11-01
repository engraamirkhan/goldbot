# Pre-registration: measured-cost protocol (microstructure researcher, 2026-10-20)

**Status: written, not run. 0 registry trials, 0 to K, no holdout. Amendment 1 (2026-10-20, section 8): M1 as deployed. Amendment 2 (2026-10-20, section 9): TP exits, the New York blackout.** Every reading rule below was fixed at this
file's commit, before the author had read any probe fill, probe P&L or broker-tick aggregate. `aux.db` lives on the
brain VM. I did not query it, nor `state/ticks/`, nor `state/costs_*.json`. Inputs read: `costs-binding-2026-10-18.md`
("CB file"), `preregistration-costs-2026-10-18.md` (CB-0, CB-D1, CB-D2, CB-T1, CB-T2), `atr-measured-2026-10-18.md`,
ADR 0039, the O-4 row of `preregistration-2027Q1.md`, and the code at `origin/main` 9a44bdcb
(`goldbot/engine/cost_probe.py`, `goldbot/risk/gate.py`, `goldbot/execution/{costs,mt5_adapter}.py`,
`goldbot/ops/jobs.py`, `config/accounts.yaml`, `config/settings.yaml`).

This file is a measurement protocol, not a trial. Nothing in it selects a signal or reads a label. It cannot pass,
rescue or re-read any trial. Measured costs enter research only as the CB-0/CB-D1 rules already allow. The one
decision it may take is withdrawing O-4 before it runs (section 6), and that saves a trial.

## 0. Decisions in one place

1. **Only fills can measure what ticks cannot. Spread is not a fill question.** The recorded broker ticks already
   give the spread by hour, session and news state, millions of quotes a week, at zero risk. Probe fills are spent on
   only four things: (a) market-leg slippage against the quote at send, and latency; (b) the limit fill rule and
   adverse selection; (c) stop-exit slippage; (d) commission and swap as the broker books them in GBP.
2. **Two arms, run on a schedule.** Mirrored probes (ADR 0037) enter only when and where challengers signal. That
   mostly means 15m bar closes in busy hours, so quiet buckets may never reach 30. A **scheduled cost-measurement
   probe** fills every bucket evenly and cheaply: 0.01 lot, a 30 s hold, a $3 server stop, fixed clock times, side
   alternating by slot. Each schedule slot has a **market** arm and a **limit-at-the-touch** arm.
3. **Demo slippage only raises costs.** It is never used to lower anything. A demo fill is the broker's simulator,
   not its live liquidity, so measured demo slippage is a lower bound on live slippage. Research slippage is
   `max(0.15, measured)` per leg, as CB file K3 already says. Only tiny-live fills (owner gate) may lower it.
4. **The O-4/H-14 withdrawal rule moves to measured forward cost in R** (section 6). Withdraw O-4 at 0 trials if the
   median measured round trip at O-4's 1.5 x ATR15 stop is above **0.10 R** across its candidate hours.
5. **Code owed before the scheduled arms run:** K7-K10 (section 4). Until then, phase A reads only the mirrored
   probes that already run.

## 1. Arms

| Arm | What is sent | Entry | Exit | What it measures | Phase |
|---|---|---|---|---|---|
| M0 mirrored | ADR 0037 probe as coded: a challenger's shadow entry, 0.01 lot, its own stop (clamped) and target | market at the bar close | SL / TP server-side, time exit at market, `cost_probe_yield` | entry slippage at bar-close timing; **stop-exit slippage**; TP price improvement; commission; swap on held nights | A (running, no code) |
| M1 scheduled market | 0.01 lot at a fixed slot time; side = +1 on even slot index, -1 on odd | market, `deviation` as coded (30 points) | market close after 30 s; server SL at $3 from fill (safety only) | entry and exit leg slippage against the quote at send; latency; reject or requote rate; commission | B (code K8) |
| L1 scheduled limit | 0.01 lot, same side rule, at slot + 3 min | buy limit at the current **bid** (sell limit at the current **ask**), i.e. join the touch; expiry 120 s (`ORDER_TIME_SPECIFIED`) | if filled: market close after 30 s, server SL $3 | fill rate, time to fill, realised cost against M1, and calibration of CB-D2's fill rule | B (code K8) |

**Why join the touch.** It is the canonical passive order. On a retail CFD book a buy limit fills when the **ask**
reaches it. So a buy limit at the bid fills only after the ask falls by a full spread, and adverse selection is
built in mechanically. Two sources bear on this, from different angles:
- Harris and Hasbrouck 1996 (*JFQA* 31(2): 213-231, NYSE SuperDOT, equities) is an execution-cost study. It finds
  that limit orders placed at or better than the quote outperform market orders. It does not frame the result as
  adverse selection. *(Partly verified: abstract read; the sample period, about 1990-91, is behind the paywall and
  unverified.)*
- Linnainmaa 2010 (*J. Finance* 65(4): 1473-1506, Finnish equities) is not an execution-cost study. It infers
  investor behaviour, and adverse selection of limit orders is its mechanism. *(Sample period, about 1995-2002,
  unverified.)*

So the saving at the quote is a single-paper equity finding, and adverse selection is a mechanism, not a measured
cost. Neither is gold-specific, and their size on IC Markets XAUUSD is unknown, which is why L1 exists. A mid-price limit (CB-T2's design) is a later variant. It is not run here, because one
variant answers whether the broker's fill rule matches the simulator at all.

**What counts as cost.** Each leg is measured against its own quote at send, so the 30 s market move is not cost:
- entry leg, $/oz paid = side x (fill - quote mid at send);
- exit leg = side x (quote mid at send - fill);
- the round trip = the two legs + commission per oz (in USD at the deal-time rate, K9).

The realised cost follows the realised-spread measure used by Huang and Stoll 1996 (*J. Financial Economics*
41(3): 313-357), a paired NASDAQ-versus-NYSE comparison of execution costs in equities *(sample year, about 1991,
unverified)*. It is side x (fill - mid 5 min after the fill). It is reported for both M1 and L1
so that a limit fill's adverse selection is visible.

## 2. Buckets and schedule

Buckets are anchored to **local exchange time** (Europe/London, America/New_York), as O-4's candidate window is, so
the 2026-10-25 to 11-01 US/EU clock gap does not smear them. ICM XAUUSD trading hours are read from the terminal
(`Broker.trading_sessions`) when the schedule is built. A slot that falls outside a session moves to the first
minute inside it, and the move is logged. *(The ICM daily break, about 16:57-18:01 New York, is unverified until
read from the terminal; IC Markets does not publish it. Verified from IC's Commodity Specification Sheet and DST
notice: 100 oz a lot, minimum 0.01 lot, stops level 0, pending orders GTC, server time GMT+2/+3 (Europe/Athens).)*

| Bucket | Window | M1 slot times | L1 slot times |
|---|---|---|---|
| B1 Asia | 23:00-06:59 London | 02:07, 05:07 London | +3 min each |
| B2 London open | 07:00-08:59 London | 07:07, 08:07 London | +3 min |
| B3 London morning | 09:00-12:59 London | 10:07, 11:37 London | +3 min |
| B4 NY morning / overlap | 08:00-11:59 New York, minus the data windows of N | 09:07, 11:07 New York | +3 min |
| B5 NY afternoon | 12:00-16:29 New York | 13:07, 15:37 New York | +3 min |
| B6 Rollover | 16:30 New York to the break, and the reopen to +30 min | 16:47 New York, reopen + 6 min | +3 min |
| N News | tier-1 US releases in `calendar-v1` (08:30 ET prints, 14:00 ET FOMC) | release + 60 s, one per event | release + 63 s, one per event |

Rules of the schedule (fixed now):
- **Fastest to 30 by count only.** Each arm-bucket cell stops scheduling once it has **30 fills**. The freed budget
  doubles the slots of the unfinished cells: a third and fourth slot at the bucket's listed times + 30 min. Counts
  only ever steer the schedule; a fill's cost never does. Stopping at a fixed n is not optional stopping on results.
- **Order of service when the day's budget is short:** the cell with the fewest fills first, ties in table order.
- **Weekdays only.** No slot within 5 min of a scheduled tier-1 release outside bucket N. No slot after the kill
  switch, in /mode propose, or in the re-arm window (`auto_entry.permitted`, as every probe).
- **One scheduled probe open at a time.** It is never open while a mirrored probe or real trade is being sent. It
  yields like any probe (`yield_to_live`).
- **N risk guard:** an N slot is sent only while at least £10 of the probe budget remains. A gap through the $3 stop
  at a print is outside the stop bound (as the ADR 0037 docstring already states).
- **Two held-overnight swap checks** (M0 or one deliberate M1 held over rollover): one normal night and one
  Wednesday, stop $4. They check the terminal's quoted swap against the booked one; they are not a bucket.

**Expected time to 30 fills per cell** (planning numbers, not outcomes):
- M1, B1-B6: 2 fills a bucket a day gives 30 in **15 trading days**.
- L1, B1-B6: at a prior fill rate of 30-50%, 30 fills in **15-25 trading days** once the cell doubling applies.
- N: about 8 tier-1 US events a month gives 30 in about **4 months**. It is read provisionally at 15 (section 5).

**Spend, demo GBP, planning:**
- One M1 round trip costs about $0.25-0.85/oz (spread 0.15-0.70 + commission 0.07 + slippage). At 1 oz and GBPUSD
  1.33 that is **£0.20-0.65**, with a 30 s noise SD of about £0.8.
- 12 M1 + about 6-10 L1 fills a day is an expected **£6-11 a day**. This fits inside icm-probe's £16 probe budget
  (0.8 x £20) and leaves room for the noise.
- The demo's dynamic limits (ADR 0031 ladder, ADR 0039 item 4 volatility step) can shrink the day's limit. The
  service order above then decides which cells wait. No cap is ever raised for this protocol.

**Host.** Phase B runs on **icm-probe** (`probe_only`, its own £20 day, outside the real accounts' combined limits),
not on icm-demo. On icm-demo, scheduled probes would compete with the 0.6 real-trade reserve and the mirrored
probes. Enabling icm-probe needs its credentials and terminal. That is an owner action (credentials), flagged in
section 7.

## 3. Only through the RiskGate

Every arm is an `Intent` checked by `RiskGate.check`, then `Engine._execute` (pending_orders row first). Nothing
calls `place_order` directly.

The gate's learning-probe fence requires a positive gross edge (p x target > (1-p) x stop). A scheduled probe has no
signal and no p, so the current fence refuses it, and it must stay refused until K8 below is merged. K8 adds a third
purpose, modelled on ADR 0021's operational order (`check_operational`): `cost_measurement`. It has:
- a demo lock (registry + terminal + phase file, as F1);
- the minimum lot only;
- a stop of at most $3 from the fill, and a loss at the stop ≤ the probe ceiling;
- a hold ≤ 60 s, closed by the engine;
- slot times only (a purpose a strategy cannot set);
- its own magic and a `cm-` comment.

It is refused on every non-demo account, at load and at the order. This is a change to an order path:
trading-safety-reviewer and an ADR 0037 amendment 5 (directors; demo only, within the delegated demo risk rules).

## 4. costs-v1 and costs-v2: build steps, including the GBP blockers

State of CB file blockers K1-K6 on `origin/main` 9a44bdcb (read from the code, commit 7d0816c5):
- **K1** swap converted on a GBP account: fixed.
- **K2** deal commission converted: fixed.
- **K3** publish without 50 fills, slippage labelled prior: fixed.
- **K4** hourly spread profile from recorded ticks: fixed (`SpreadProfile`, by UTC hour and session).
- **K5** labels still charge the Dukascopy spread: unchanged by design.
- **K6** the 12-11 date: depends now only on deploy, login and the token.

New blockers found for this protocol:

| # | Blocker | Evidence | Effect | Fix (owner of the fix) | Due |
|---|---|---|---|---|---|
| K7 | Probe fills never reach `fills`, so `nightly_costs` measures slippage from no probe at all | `cost_probe.py` docstring: "its fill never to the store's `fills`"; `jobs.nightly_costs` reads `fills` only | costs-v1 slippage stays the prior for ever on a demo with no real trades, whatever the probes record | A `costs-v2` reader: `cost_probe.probes()` -> per-leg slippage by bucket and arm, published beside costs-v1 as `probe_costs.json` (aggregates only, no ids). Never merged into the `fills` table (keeps the trade record clean) (data-engineer; quant-reviewer) | 2026-11-06 |
| K8 | No limit-order or scheduled path. The adapter sends only `TRADE_ACTION_DEAL` market orders, and the gate's probe fence needs a p | `mt5_adapter.place_order` (ORDER_TYPE_BUY/SELL only); `gate._learning_probe_reasons` | M1 and L1 cannot run | `cost_measurement` purpose (section 3); `TRADE_ACTION_PENDING` buy/sell limit with expiry and cancel in the adapter and bridge; the pending order reconciled like any order (unconfirmed handling) (implementer; trading-safety-reviewer, code-reviewer) | 2026-11-13 |
| K9 | **GBP: probe commission, swap and P&L are stored in account currency with no conversion rate** | `ProbeRecord` has `commission`, `swap`, `pnl`, but no fx field | Probe commission cannot be turned into USD/oz without a re-read of GBPUSD after the fact | Add `fx_acct_per_usd` at send and at close (the gate's `account_fx` of `loss_per_lot`, as K1/K2 use), nullable on old rows. A row with no fx is excluded from commission and swap, never converted at a later rate (implementer; quant-reviewer) | 2026-11-06 |
| K10 | **The dynamic probe count would loosen if scheduled probes joined its median** | `probe_limits` sets `max_open` from the median `native_stop_loss` of the last 20 probe rows | Scheduled probes' native loss (about £2.3) would pull the median down and raise `max_open` for the wide-stop mirrored probes: a loosening by accident | The `cost_measurement` rows are excluded from `PROBE_LOSS_WINDOW` and from `max_open`. They take at most 50% of the day's probe budget and count fully in `budget_refusal` (implementer; trading-safety-reviewer). Test: adding cost_measurement rows never raises `max_open` | with K8 |
| K12 | **The freeze level and the session hours are not captured.** IC Markets publishes neither for XAUUSD | The repo has no `symbol_info` capture of `trade_freeze_level`; `trading_sessions` is not yet used by any schedule | A limit at the touch inside the freeze level cannot be modified or cancelled, so L1's 120 s expiry and cancel could fail, and a slot could fall in the daily break | Read `trade_freeze_level` and `trading_sessions` from the terminal and record them in `broker_terms`. L1 places no order within the freeze level of the quote. A freeze level > 0 is logged, and L1 joins the touch at that distance instead (implementer; trading-safety-reviewer). **L1 does not start until this is merged** | with K8 |
| K11 | Quote-at-send is recorded for entries only | `ProbeRecord.requested_price`, `spread_usd` exist; the exit's quote at send does not | Exit-leg slippage (and stop slippage) cannot be separated from the market move | Record bid/ask at close send and the server stop/TP trigger price (from the deal) on the probe row (implementer) | with K8 |

Build sequence (who; where; when):
1. **Now, no code:** phase A on icm-demo as configured (M0). `nightly_costs` keeps building the spread table and the
   tick `SpreadProfile`. (sre: job health)
2. **Owner:** github-token in the brain keyring, and `costs.publish_release: true` (settings, sre). The first
   costs-v1 publishes spread and swap measured, and commission measured once one deal is seen. Target **2026-11-06**,
   after ≥ 10 trading days that include the clock-gap week and one tier-1 day (CB-0 step 4).
3. K7 + K9 merged: probe aggregates published as `probe_costs.json`. **2026-11-06.**
4. K8 + K10 + K11 + K12 merged and reviewed; icm-probe enabled (owner credentials); phase B starts. **2026-11-13 target.**
5. First full reading (section 5): M1 cells at 30 fills, about 15 trading days after phase B starts, so about
   **2026-12-04**. This is before O-4's pin (2026-12-11).
6. CB-0 step 7 basis check (broker against Dukascopy by hour) at ≥ 20 tick days. Reported only (K5).

## 5. Reading rules (fixed before any fill is read)

General rules:
- n is counted per arm-bucket cell.
- CIs are 95%, by day-block bootstrap (2,000 resamples, seed 20261020).
- Means are trimmed only by the pre-set rule: a leg with |slippage| > $5/oz is reported separately as a tail event
  and kept in the mean.
- Nothing is read for a cell below its n. A cell's first read is its only decision read. Later fills update the
  published table but never re-open a decision taken here.

| Quantity | n to read | Rule |
|---|---|---|
| Spread by hour and bucket | ≥ 20 tick days per hour, both DST regimes for the London and NY hours | From ticks, not fills. Published as is. It replaces the Dukascopy spread in **CB-D1 forward cost and section 6 only**, never in labels (K5) |
| Market-leg slippage (M1 by bucket; M0 entry, reported separately as "bar-close timing") | 30 fills | research slippage per leg = **max(0.15, mean)** (K3). If the mean's CI lower bound is > 0.15, the prior is raised for that bucket in costs-v1 from the next dispatch on |
| Stop-exit slippage (M0 SL exits) | 30 SL exits | Same max rule, charged to stop exits only |
| TP fills | 30 TP exits | Reported. A TP leg with price improvement is never credited in research |
| Commission | 3 deals agreeing within 1% in USD (K9 fx) | Measured, replaces 3.5 USD/lot/side; else the prior stays. Reported cross-check only (it decides nothing): the GBP amount booked against IC's published 5.50 GBP/lot round turn for GBP accounts (USD accounts: $7.00 round turn, $3.50 a side; both verified from IC's Commodity Specification Sheet). A gap > 5% is flagged to the data-engineer as a K9 conversion check |
| Swap | terminal value + both overnight checks within 5% | Measured; else flagged to the data-engineer, and the terminal value is used |
| L1 fill rate and realised cost | 30 L1 fills per bucket (attempts counted) | **Limit is a cost lever on this broker** if the fill rate's Wilson lower bound ≥ 30% AND (realised cost M1 - realised cost L1, 5-min mid) ≥ **$0.10/oz** with its CI above 0. **No saving** if that difference is ≤ 0. Otherwise undecided, reported at 60 fills, then closed as undecided. "Lever" makes scenario D a reported CB-D1 scenario; it does not adopt limit entries anywhere (CB-T2's own rule does that) |
| CB-D2 simulator calibration | 30 L1 attempts per bucket, all buckets pooled for the decision | For each attempt, CB-D2's rule (fill when ask ≤ limit - through, through = $0.05) is run on the same recorded ticks. **Validated** if it agrees with the real fill/no-fill on ≥ 90% of attempts. Else `through` is set to the smallest of {0, 0.02, 0.05, 0.10} that reaches 90%, written into CB-D2 before CB-D2 reads any candidate. None reaching 90%: CB-D2 is marked "simulator not valid on IC Markets", and its reading rule cannot say "build" |
| N bucket | provisional at 15, decision at 30 | Provisional figures are labelled so and decide nothing |

**Demo caveat on every read.** Demo fills bound live slippage from below and say nothing about last look or queue
position on the live book. Every published cell carries `venue: demo`.

## 6. Withdrawal rule for O-4 and H-14 on measured costs

This adds to O-4's existing D-1 withdrawal rule (median research-window round trip > 0.35 R at every admissible floor).
It does not replace it.

**Inputs, on the read date (2027-01-29, before O-4's condition date 2027-02-12):**
- For each clock hour h inside O-4's candidate window (08:00 London to 16:00 New York), C_h in $/oz is the sum of:
  - the broker median spread in hour h (ticks, ≥ 20 days; if not available, the Dukascopy hourly median of the last
    90 days, as in scenario A);
  - the commission (measured, else 0.07);
  - 2 x the research slippage of h's bucket (section 5: max(0.15, measured)).
- ATR_h is the last-90-day median ATR(14) on 15m mid bars, for bars opening in hour h, as of the read date. The
  method is that of `atr-measured-2026-10-18.md`.
- cost_R_h = C_h / (1.5 x ATR_h). The stop is O-4's 1.5 x ATR15.

**Rule.** Let m = the median of cost_R_h over the candidate hours.
- **m > 0.10 R: withdraw O-4 before it runs (0 trials, 0 to K)**, and with it O-5/O-6. H-14 is recorded as "15m
  uneconomic at measured cost, IC Markets, <date>". It may be re-proposed only if a later read of the same rule gives
  m ≤ 0.07 R.
- **m ≤ 0.05 R:** O-4 runs as planned. The research director is told that "15m cost-open" holds, so H-25 and C-8
  rank higher.
- In between, O-4 runs as planned, and m is reported with the verdict as a CB-D1 quantity.
- In no case does the rule change O-4's labels, cost-gate floor or features. Those stay as pinned on 2026-12-11.

**Why 0.10 R.** At O-4's adopted gate-1 case (K about 67, per-trade t about 4.03), with a 15m triple-barrier SD of
about 1.2 R and a clustered effective n of about 5,000, a pass needs a net mean of about 4.03 x 1.2 / sqrt(5000),
about **0.07 R**. At a 0.10 R cost that is a gross edge of about **0.17 R**. No 15m family in the registry
(#21-#35) showed any gross edge, and the 15m ML literature gives no gold precedent near it. Spending the trial would
buy almost no chance of a pass.

**Prior on the outcome:**
- Scenario A today gives 1.07 / 12.0 = 0.089 R, and scenario C 0.043 R.
- So the rule fires mainly if ATR15 falls back toward its 2024-25 levels (at ATR15 $5, scenario A gives 0.14 R).
- P(withdraw) is about 10-15% at today's volatility.

**Applies too** to O-5/O-6 survivors and any 15m shadow scalp, each at its own stop multiple. A survivor whose own
cost_R median is above 0.10 R is not given a demo agent.

## 7. Flags and honesty

**Owner-only (flagged, not assumed):**
- icm-probe credentials and terminal (needed for phase B).
- The github-token in the brain keyring for `costs.publish_release`.
- Lowering slippage on live evidence needs the paper -> tiny-live gate.
- The sizing fact stands: a 1.5 x ATR15 stop is about £9 at 0.01 lot. That is inside the demo 1% ceiling, but above
  the 0.5% live base. Live 15m stays unsizable at £1,000 whatever this protocol measures.

**Directors and reviewers:**
- The ADR 0037 amendment 5 (`cost_measurement` purpose).
- Trading-safety review of K8 and K10.
- Quant review of K7 and K9 and of section 5's statistics.

**Multiple testing.** This protocol is 0 trials. The section 6 rule is a go/no-go on spending one, so it lowers K or
leaves it unchanged. No configuration is chosen by a probe outcome.

**Known-failed or closed ideas here, not to be re-proposed without new evidence** (CB pre-registration "Not
proposed"):
- tight-spread-hours trials;
- cost-filtered research trials;
- the H-04 re-test;
- 15m wide-target variants (R-01, R-02, R-05);
- re-reading any scored trial at measured cost.

**What would change my mind:**
- Tiny-live fills (owner gate) that show slippage well below the prior. Then rule 5's max floor is revisited, by a
  new pre-registration.
- An L1 "lever" result together with a 15m cost_R near 0.05. Then a limit-entry 15m design is worth a slot.

Accountable: microstructure researcher. Next check: K7 and K9 on the board by Fri 2026-10-23. First M0 read when
cells reach n.

## 8. Amendment 1 (2026-10-20): the M1 arm as deployed for demo learning volume

**Dated 2026-10-20, written before any scheduled (M1) fill exists and before I read any probe fill, probe P&L or
tick aggregate.** Cause: owner directive (hundreds of demo fills by Fri 2026-10-23), shipped on the integration
branch `feat/demo-learning-volume`. This amendment records the parameters as deployed and what they change in the
schedule. **The reading rules of section 5 and the withdrawal rule of section 6 are unchanged**, word for word.

**M1 as deployed (replaces the M1 row of section 1 and the M1 slot times of section 2):**

| Parameter | Deployed value | Section 1-2 plan |
|---|---|---|
| Cadence | one probe at **every 15m bar close** | 2 fixed `:07`/`:37` slots per bucket a day |
| Side | alternating +1 / -1 by slot | same |
| Size | 0.01 lot (1 oz) | same |
| Stop | server SL **$3** from the fill (safety only) | same |
| Hold | **30 s on the host clock**, then market close | 30 s |
| Blackout | no slot **20:45-22:15 UTC**, none **after the Friday cut** | rollover bucket B6 scheduled |
| Account | **icm-probe only** (`probe_only`, `budget_share 0.9`, daily limit **£30**, so a £27 probe budget) | icm-probe, £20 day, £16 budget |
| L1 (limit) | **not deployed** | slot + 3 min |

**Mirrored probes on icm-probe (M0).** On a `probe_only` account a mirrored probe holds **min(agent's hold, 4 bars)**.
Its fills count in M0 exactly as section 1 defines (entry at bar-close timing, stop-exit and TP legs, commission).
The 4-bar cap means most M0 exits there are time exits at market; only SL exits count toward the stop-exit cell.

**Fills are cost and execution evidence only, never edge evidence.** A 30 s probe on an alternating side has no
signal. Its P&L is not a return of any strategy, family or timeframe. It is never read as a gross edge, never pooled
with shadow or real trades, never used to rank, select or rescue a hypothesis, and never cited as "demo performance".
M0 P&L is likewise not the challenger's performance (its hold is capped). Volume does not change this: a thousand
cost fills are still zero trials and add 0 to K.

**Per-cell 30-fill stop (decision read).** n is still counted per arm-bucket cell. A cell's **decision read happens
once, at its 30th fill** (N: provisional at 15, decision at 30). The owner's volume directive keeps the scheduler
sending after a cell reaches 30, so the section 2 rule "the cell stops scheduling at 30" is suspended for M1. Fills
after the 30th update the published table and its CIs, labelled `post-decision`, and never re-open the decision
(section 5, unchanged). Bucket counts steer nothing; a fill's cost steers nothing.

**Bucket assignment (fixed now, because 15m closes now fall in overlapping windows).** A fill belongs to exactly one
bucket, the first that matches in this order: N, B6, B2, B4, B3, B5, B1. Windows stay in local exchange time
(section 2).

**What the deployed parameters change, by bucket.** At 4 closes an hour, ignoring the budget:

| Bucket | M1 fills a weekday | ETA to 30 (decision read) | Note |
|---|---|---|---|
| B1 Asia | about 28-30 | 1-2 trading days | |
| B2 London open | 8 | 4 trading days | |
| B3 London morning | about 12-16 | 2-3 trading days | loses 12:00-12:59 UTC to B4 in the 10-26 to 10-31 gap week |
| B4 NY morning | about 14-16 | 2-3 trading days | |
| B5 NY afternoon | 18 | 2 trading days | first to be cut when the budget binds (below) |
| B6 Rollover | about 2 (16:30 NY close in EDT, reopen side only) | about **15 trading days (about 2026-11-10)** | the pre-break half is almost all inside the 20:45-22:15 UTC blackout, and all of it after 11-01. It stays unmeasured by fills; its spread comes from ticks only |
| N News | 0 if the RiskGate tier-1 blackout (-15/+30 min) applies to probes | none at this cadence | if probes are not blacked out, the 08:30 and 14:00 ET closes fall **at** the print, not at +60 s. Those fills are labelled `N-at-print` and are never pooled with the +60 s design. The 4-month N plan needs its own slot (later amendment) |
| L1, all buckets | 0 | none | L1 and the CB-D2 calibration wait for K8 and K12 as before |

**The budget binds before the clock does. This is the main risk to the schedule.** About 86-89 closes a weekday
remain after the blackout and the daily break. At the planning cost of £0.20-0.65 per round trip, that is an
expected £17-56 a day against a £27 budget. The risk day starts at 00:00 UTC, so budget runs out in time order:
B1, B2 and B3 are served first, and B4 and B5 lose fills on wide-spread days. Planning case: by Fri 2026-10-23,
B1-B3 reach 30 and B4/B5 do so only if the days are cheap; otherwise in the week of 10-26. No cap is raised for
this; the RiskGate stops probes at the budget as on every account. Recommendation to the research director (code,
not this file): serve the cells with the fewest fills first once the day's spend passes half the budget, as
section 2 already specified.

**Deviations that bias costs only upward, and are accepted.**
- 15m bar-close timing. M1 now sends when every bar-close algorithm sends, as M0 does, not at the quiet `:07` minute.
  Measured slippage is then an upper bound for an off-close entry. This is consistent with decision 3 (demo
  slippage only ever raises research cost) and with 15m strategies, which enter at the close anyway.
- The 30 s hold runs on the host clock. Each leg is still measured against its own quote at send, so clock drift
  changes the hold length, not the cost measure. The host-minus-server offset is logged per fill where the bridge
  reports it.

**What M1 fills still cannot give until code lands.** Without K11 (exit quote at send) only the entry leg is read;
the exit leg is reported as "includes market move". Without K9 (fx at send and close) no commission or swap is read
from these fills. Without K7 the fills do not reach `probe_costs.json`. K10's intent holds: cost-measurement rows
never enter the mirrored-probe `max_open` median. On icm-probe its 50% budget split is replaced by the account's own
£30 day (owner-set account limits, unchanged here).

**Owner decisions (flagged, not assumed):** the icm-probe credentials and terminal, and its £30 daily limit, are
the owner's. This amendment raises no limit and touches no live account.

Multiple testing: 0 trials, 0 to K, no holdout. Section 6 is still read on 2027-01-29.

## 9. Amendment 2 (2026-10-20, before the first scheduled fill)

**Dated 2026-10-20, written before any scheduled (M1) fill exists and before I read any probe fill, probe P&L or
tick aggregate.** Cause: the quant and code reviews of `feat/demo-learning-volume`. **The reading rules of section
5 and the withdrawal rule of section 6 are otherwise unchanged.** 0 trials, 0 to K, no holdout.

**Scheduled M1 probes carry a server take-profit at ±$3 from the fill, for safety only** (the symmetric target set
in `goldbot/engine/cost_probe.py` `_send_scheduled` and placed with the order by `Engine._execute` in
`goldbot/engine/runner.py`), beside the $3 server stop. Reading rule, fixed now:
- **A TP exit is EXCLUDED from the M1 exit-leg cost reading.** A take-profit fills like a resting limit (at the
  level or better), so counting it would bias the exit leg's slippage down, the unsafe direction. It is reported in
  the "TP fills" row of section 5 (never credited in research) and counts toward no M1 exit-leg n.
- **Its entry leg counts** like any M1 entry: it is a market order sent at the slot, filled before the target can act.
- A 30 s hold exit and an SL exit are read as before (section 8). Nothing else in section 5 changes.

**The mirror 4-bar cap biases the stop-exit cell high (accepted).** On icm-probe a mirrored (M0) probe is held at
most 4 of its agent's bars (section 8), so only stops hit within those 4 bars reach the "Stop-exit slippage" cell.
Those are fast stops, taken in fast markets, whose slippage runs higher than a stop's average. The cell therefore
reads stop-exit slippage biased high, the safe direction (decision 3: demo slippage only ever raises research
cost). It is labelled `fast-stops (M0 4-bar cap)` and is never read as the average stop's slippage.

**The schedule's blackout is fixed in New York time: no slot 16:45-18:15 America/New_York** (replaces "20:45-22:15
UTC" in section 8). The section 8 window was fixed in UTC and did not move with US DST: from 2026-11-01 the
22:15-22:45 UTC slots would fall in the daily break and 23:00 UTC on the reopen. In New York time the window
covers the rollover, the break (about 16:57-18:01) and the reopen minute in both regimes; it equals the old window
in EDT. Effect on section 8's table: B6 is served by the 16:30 New York close and the 18:15 and 18:30 closes (the
reopen to +30 min) in both regimes, about 3 fills a weekday, so its ETA to 30 is about 10 trading days, the same
before and after 11-01. Its pre-break half stays unmeasured by fills; its spread comes from ticks only. No other
bucket changes.

Multiple testing: 0 trials, 0 to K, no holdout. Section 6 is still read on 2027-01-29.

## Amendment 2026-11-01: M1 cadence trimmed on icm-probe (demo trim)

**Dated 2026-11-01.** Cause: cost of record 2026-10-31 section 6 and decision-2026-11-01-next-lines.md option A. ICM
market-leg and stop-exit slippage are settled from strategy fills, so the ~70 a day quiet-time M1 fills buy nothing
more. **icm-probe's M1 cadence goes from every 15m close to one per hour** (`cost_probe.scheduled.every_min: 60`, at
most 24 a day). Each hour's 15m slot rotates with the hour index plus the UTC day. The side rule (+1 on an even 15m slot
index) is unchanged, so sides still alternate hour by hour and each hour of day flips side daily. Bucket ETAs to 30 fills
are about 4 times longer on ICM. vantage-demo keeps every 15m close until its exit legs are recorded and it has 200
strategy fills per session, then 120 minutes. Sections 5 and 6 are unchanged. Multiple testing: 0 trials, 0 to K,
no holdout.
