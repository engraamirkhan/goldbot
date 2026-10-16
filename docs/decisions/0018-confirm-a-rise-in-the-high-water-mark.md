# 0018: Confirm a rise in the balance high-water mark

Status: proposed (implementer, 2026-10-16; BACKLOG 54, from the trading-safety review of BACKLOG 44; revised the
same day after the trading-safety review of faf824c: M1 a real peak is kept, M2 the pending time, L1 one core.db
handle, L2 candidates expire; needs trading-safety re-review). Renumbered from 0017, which is CostImputer.

## Context
The drawdown stages measure equity against `balance_closed_hwm`. `_refresh_account` raised it on every account read
(`max(hwm, balance)`) and saved it. One inflated balance read (a glitch, a half-filled account struct) therefore raised
the mark for good. Every later real read then looked like a drawdown: size-down at 8%, and, once ADR 0016's two reads
agreed, a phantom 12% kill switch that closes everything at market and needs the 40-day re-arm. ADR 0016 confirms a
collapsed equity read, but it trusted any balance read that raised the mark.

## Decision
- **Rule.** A balance read above the mark raises it only when it is *confirmed*:
  1. **By the deal history.** The deals since the mark was set (the *anchor*) explain the read: at least one
     balance-changing deal, and mark + their profit, commission, swap and fee (credit excluded, as in the ledger's
     RC-9 tie-out) is within `risk.hwm_rise_tolerance` of the read. The mark rises to the read at once. Or:
  2. **By a second read that agrees.** The next balance read is within the same tolerance of the candidate. The mark
     rises to the lower of the two.
- Otherwise the read becomes the *candidate*:
  - the mark is unchanged, so the stage machine runs on the old mark;
  - the account is read again on the next tick (as ADR 0016);
  - the engine logs and records `hwm_rise_unconfirmed`.
- **A real peak is kept (review M1).** Take a read that disagrees with the candidate: at or below the mark, or above
  it but outside the tolerance. The engine first checks whether the deals since the candidate was read carry the
  candidate to this read (candidate + their sum within the tolerance, at least one deal).
  - If they do, the candidate was real. Example: mark 10,000, an unexplained 10,300, then a loss that reads 9,900.
    The mark rises to the candidate (`hwm_raised`, `by: candidate_deals`), anchored at the time the candidate was
    read. If this read is itself above the candidate and explained, the mark rises to it.
  - Otherwise the candidate is dropped, logged and recorded as `hwm_rise_refused`. A disagreeing read above the mark
    becomes the new candidate.
  - Without this check, the mark would have stayed at 10,000 until a new high, so the stages would have been 3% looser
    for an unbounded time.
- One disagreeing read never raises the mark; two agreeing reads always do.
- **Candidates expire (review L2).** A candidate older than `hwm.CANDIDATE_MAX_AGE` (10 min) is void. It is dropped on
  the next read, and on load on the live clock, and recorded as `hwm_rise_refused` with `reason: expired`. A read
  after an outage, or after a weekend, never confirms a value read before it.
- **Exact brokers.** A broker class may declare `balance_exact = True`. Only the paper broker does: its balance is its
  own ledger and cannot glitch. There, a read above the mark raises it at once (`by: exact`). The engine looks the flag
  up on the class, so the remote broker's attribute pass-through can never answer it. MT5 and the bridge keep the
  confirmation.
- **Tolerance.** `risk.hwm_rise_tolerance` defaults to 0.1% of the mark (£1 on £1,000). A deal history is exact to
  the cent, so the tolerance covers rounding, not money. A rise with no deal at all is never explained by the deal
  history, however small. Many small glitches therefore cannot ratchet the mark up.
- **Deposits raise the mark.** A deposit is a balance operation (MT5 `DEAL_BALANCE`, positive) in the deal history, so
  it explains the rise, and the mark rises with it, as before this ADR. The decision records the amount
  (`hwm_raised`, `deposits`). The drawdown is then measured on the capital the account now holds; not raising it would
  let a deposit hide a loss of the same size. Withdrawals are unchanged: the mark is never lowered by this rule.
- **Never lowered.** The mark only rises here. Only the owner's re-arm (`RiskGate.rearm`) resets it.
- **First read.** An engine with no mark yet (`balance_closed_hwm == 0`, a fresh account) sets it from its first read,
  as before, and anchors the deal history there.

## Accepted knowns (trading-safety review of faf824c)
- **The first read of a fresh account is unconfirmed.** It sets the mark, so an inflated first read would set it high.
  That happens once per account, before any trade. The owner sees the balance on the dashboard, and a re-arm resets it.
- **A withdrawal reads as drawdown.** The mark is never lowered by this rule. A withdrawal of 8% of the mark moves the
  account to size-down as if it were a loss, and 12% trips the kill switch. The owner withdraws only with the engine
  halted, then re-arms, which resets the mark.
- **A re-arm clears the anchor.** `RiskGate.rearm` sets the mark to the equity, so the row (taken for the old mark) is
  void. Until the next confirmed rise sets an anchor, a rise needs a second read; the deal history cannot explain it.

## Not sticky, unlike ADR 0016
ADR 0016 makes a pending 12% read sticky, because there the strict side is to trip. Here the strict side is to keep
the mark. A sticky candidate would let a glitch that recurs (the same inflated value an hour apart) raise the mark on
its second appearance. So a disagreeing read drops the candidate.

Alternating readings cannot hold off a real rise either:
- a real rise comes from a closed deal or a deposit, and the deal history confirms it on its first read, whatever
  the reads around it say;
- only a rise the deal history cannot see (an unreadable history) relies on two reads in a row;
- a real peak followed by a loss is kept by the candidate's own deals (review M1).

## Persistence (S12)
- **Where.** The candidate and the anchor are one row per account in core.db: table `hwm_confirmation`, migration 2,
  synchronous FULL. This follows the state-store direction: no new JSON state file. The row is written only when it
  changes (a candidate set or dropped, a rise applied).
- **Fields.** `pending_since_utc` is the first of an unbroken run of candidates (review M2): a disagreeing candidate
  that replaces another keeps it, and only a raise or a read at or below the mark clears it. That is the health
  warning's age. `candidate_utc` is when the current candidate was read: its age (L2), and the anchor for the deals
  that may corroborate it (M1). A row from before `candidate_utc` existed uses `pending_since_utc`, which then meant
  the same.
- **One handle (review L1).** The engine opens and migrates core.db once at start (`hwm.HwmStore`). Each save is one
  write on the tick thread, with a busy timeout of `hwm.WRITE_BUSY_TIMEOUT_MS` (500 ms) instead of 5 s. A locked or
  unopenable core.db is logged and takes the strict side: the rise is confirmed in-process only, and never on one
  read.
- **The mark stays in the risk state.** The mark itself is still in `risk_<account>.json`, and a confirmed rise saves
  it at once.
- **Void row.** A row whose `mark` differs from the risk state's mark is void (no candidate, no anchor). That covers a
  re-arm, and an older release that raised the mark after a rollback. The row's candidate or anchor is never used
  against a mark it was not taken for.
- **Strict defaults.** A missing or unreadable row, or a failed write, means no candidate and no anchor. A rise then
  needs a fresh confirmation; it is never applied on one read. The engine logs the failure and continues: exits and
  every other check are untouched.
- **Restart.** A restart between the two reads keeps the candidate: an agreeing read after the restart confirms it, and
  a disagreeing one refuses it. The unconfirmed value is never written to the risk state.
- **Rollback.** The migration is additive (`compat_back_to` 1), so an older release opens core.db. That release never
  reads the table and raises the mark on one read again: the old rule.
- `HwmConfirmation` is read tolerantly (`RecordRepo(..., tolerant=True)`); compat case
  `tests/test_persistence_compat.py::case_hwm_confirmation`.

## Why the delay is safe
- **Holding a real rise for one tick.** It can only understate the drawdown by that rise, for one tick. The stops
  rest at the broker, and exits are not gated.
- **Without a deal history.** An unreadable history, or a terminal without MT5 deal columns: a real rise waits for
  the next read.
- **What it removes.** A phantom size-down, and a phantom flatten-at-market with the 40-day re-arm, on one bad number.

## Options considered
- *Always wait for a second read.* Simpler, but every closed trade would wait a read. The deal history makes the common
  case instant.
- *Deal history only.* A broker that books a balance change without a deal would never raise the mark. The second read
  covers it.
- *Sticky candidate (as ADR 0016).* Declined, see above.
- *Keep the candidate in risk_<account>.json.* Declined: the state store replaces JSON state files, and the mark's
  confirmation does not need to change the risk file's format.

## Consequences
- After a single inflated read the mark stays put. Entries are not refused for it: the old mark is the safe one.
- A terminal that keeps reporting the same inflated balance raises the mark on its second read, as two agreeing reads
  must. This rule filters single glitches.
- The supervisor's combined check reads the published `balance_closed_hwm`, so it inherits the confirmed mark.
- Health `risk:<account>` warns when a run of candidates has waited longer than `HWM_PENDING_WARN_S` (5 min) in market
  hours: the terminal's balance reads keep disagreeing, and the mark may be understated. Jittering reads cannot hide
  it, because the age runs from the first candidate of the run (review M2).
- **What would change this.** A measured glitch that repeats the same value on two consecutive reads would call for a
  deal-history-only rule with a time-based fallback.
