# 0081: 1d/1w entry timing: the send after the reopen, the common filter at the entry close, the whole hold's swap

Status: accepted with amendments (quant-reviewer, 2026-11-13: truncation test, swap to the last weekday close, the
CONFIRM of record below); engine timing and swap approved by the trading-safety-reviewer on 6c90577c with fixes. 1d/1w
stay dark (`engine.timeframes_live`) until BACKLOG 144 blocker 7 lifts the settings guard.

## Context
WS-4 (PR #341) shipped 1d and 1w exploration arms dark. BACKLOG 144 lists why a live one would trade nothing or trade
at the worst hour: a 1d decision at the 21:00 UTC close was sent into the broker's rollover and daily break (the break
is 21:59-23:02 UTC in EET, 20:59-22:02 UTC in EEST); a 1w decision went out in the first minutes after the Sunday open,
when spreads are widest; a restart across the weekly open lost the week; and the rules' standing common filter
(rollover 20:45-22:15 UTC, Friday from 19:00 UTC; `arm_rules.common_filter_ok`, hash-pinned) judges a candidate at its
bar's close, so every 1d candidate (21:00) and every 1w candidate (Friday 21:00) was dropped before the engine saw it.
Separately, the RiskGate's daily projection charged only the swap of a rollover before 00:00 UTC, so a multi-day hold
entered the gate with one night's swap at most.

## Decision
1. **Send time.** A Monday-Thursday 1d decision and a 1w decision are decided at their entry close: the first 15m close
   at or after the broker's reopen (the engine's session table, `long_bars.reopen_after`) plus a settle (15 min for 1d,
   60 min for 1w) and, for 1d, after 22:15 UTC. So 22:30 UTC in EEST, 23:30 UTC in EET; the 1w decision at 23:15 UTC
   (EEST) or 00:15 UTC Monday (EET). The rule reads only the bars through its decision close; the signal is stamped
   with the entry close, so its stop and target go out from the send-time price through the RiskGate as every arm's.
   Minutes between the decision close and the send change nothing in the decision (truncation test, quant review).
   "Reopened" needs evidence: a minute after the reopen in the engine's bars, the whole last base bar open on the
   table, the common filter passing at the entry close; otherwise it waits.
2. **Lateness and catch-up.** A decision may still be sent up to 2 h (1d) or 24 h (1w) after its entry close, so a
   restart across the weekly open catches the week up once. Later, or with no reopen within 4 days (a 24/7 table, a
   long closure), it is given up and journalled `exploration_missed:<tf>`. The last decision close per account and arm
   is written to aux.db `long_tf_decisions` (migration 33) before its signal is queued: a restart never sends a day or
   week twice. Unreadable or unwritable: nothing is decided (journalled `history_gap:<tf>`).
   The mark comes before the send, deliberately: a crash just after the mark, or a refusal of the signal at the send
   (caps, `weekend_hold`, stale or missing data, the gate), loses that day's or week's decision for good; it is never
   retried. Safe by design (trading-safety review of 6c90577c): a missed entry is the failure mode, never a second
   order.
3. **The common filter (blocker 5, quant).** `common_filter_ok` stays byte-unchanged. The rules judge it at
   `RuleContext.filter_ts`: a bar's close for every intraday timeframe (unchanged), and for 1d/1w the bar's nominal
   entry (`long_bars.entry_after`: 22:30 UTC the same day for 1d, Sunday 23:15 UTC for 1w). It depends on timestamps
   only, so a research replay can apply the same rule. A Friday 1d bar's nominal entry is inside the Friday filter:
   a 1d decision never enters into the weekend (the engine also still records it blocked `weekend`).
4. **Swap of the hold.** A 1d/1w Intent carries the paid swap of every broker rollover from the send to the end of its
   hold: its `hold_bars`-th close after the send, weekday 21:00 UTC closes for 1d (so a hold across a weekend pays the
   weekend and the triple night), Friday 21:00 UTC for 1w (`long_bars.hold_end`; `Engine._swap_usd_per_oz(..., until)`,
   ADR 0017's rule, the triple night counted, a credit never). An unknown swap is NaN and the gate refuses it (`invalid:swap_usd_per_oz`).
   Intraday arms keep the risk day's projection.

Options weighed for 3: (a) lift the filter for 1d/1w entirely: rejected, it also stops Friday-evening entries and
tier-1 blackouts; (b) shift the decision bar's timestamps: rejected, it corrupts the weekly grouping and the
point-in-time join; (c) judge the filter at the entry (chosen): the filter's intent ("no new entry at the rollover or
into the weekend") applied where the entry happens. What would change it: a quant ruling that 1d/1w research must
judge entries at the close (then the 1d family is untradeable as specified and should be re-registered).

5. **The CONFIRM of record (quant amendment).** Research fills a 1d/1w signal at the next bar's open
   (`research/catalogue/sim.py`, `data/daily.py`): a 1d fill near 21:00 UTC in winter (the reopen in summer) where the
   engine sends at 23:30 (22:30) UTC; a 1w fill on Friday 21-22 UTC that holds the weekend gap where the engine enters
   Sunday/Monday an hour after the open; and Friday 1d trades exist in research but are never sent live. So the CONFIRM
   of record for any 1d/1w arm re-fills each trade at the first 1m open at or after its live entry close
   (`long_bars.entry_close`), with Friday 1d signals dropped. Until such a CONFIRM exists, no 1d/1w arm may qualify
   (BACKLOG 254, 253).

## Consequences
- Blockers 1, 2, 3, 5 (pending ratification) and 6 of BACKLOG 144 are closed in code; the settings guard
  (`GO_LIVE_BLOCKERS_1D_1W`) now lists only the open ones: the long-history refresh/preflight/alert (4), this ADR's
  ratification (5) and the CONFIRM pass with trading-safety sign-off (7).
- Research that replays 1d/1w arms through `arm_rules` must pass `filter_close=long_bars.entry_after(...)` to match the
  engine (BACKLOG 254); the WS-3 catalogue screen does not use `arm_rules` and is unaffected. The growth session rules
  (`arm_rules_growth`, intraday by design) keep judging at the close.
- The hold of a 1d/1w position runs from its send close (the order id, the barrier and `crosses_friday` all count
  from it), so it ends up to 2.5 h (1d) or a day (1w catch-up) later than a history run's from the decision close, and
  a cut-following rule whose frozen hold is capped at the week's end is refused `weekend_hold` live. Fail closed, never
  into the weekend; aligning the hold origin is BACKLOG 254 (with the research filter parity).
- The daily projection is more conservative for multi-day holds (it charges every night of the hold on the entry
  day); a 1w arm's lot fit is judged with it.
- Intraday behaviour is unchanged: `filter_ts` is the close, `is_bar_close` equals the clock boundary for every
  intraday timeframe, and the replay-identity golden still holds.
