# 0015: Broker ledger: one read-only daily job, aux.db, folded into reconcile:<account>

Status: accepted (implementer, 2026-10-11; finance audit GAP-07 and GAP-08; revised after the SRE and
trading-safety review of e185bea)

## Context
The finance audit (docs/finance/audit-framework.md) found two gaps. Nothing compared goldbot's position records with
the broker (GAP-08, check RC-8). There was no balance history, and deposits and withdrawals were not captured
(GAP-07, check RC-9). Without them the monthly ledger cannot tie out. The D12 feed check already owned the health
name `reconcile:<account>`, and the bridge already served `deals_since`.

## Decision
- **One job, not two.** `broker_ledger` takes the snapshot and runs the reconciliation together. The balance tie-out
  needs the balance and the deal history from the same moment, so the job reads the deals before and after the
  account and retries until both reads are equal (`STABLE_READS`). Two separate jobs would leave a gap between them in
  which a deal could land.
- **Deal cursor, not clocks.** Each snapshot records `deals_through_utc` = max(newest deal read, read time - 1 day).
  The next tie-out covers deals after it.
  - Review fix: the first version used the read's start when there were no deals. That moved the cursor back an hour
    on every idle run until a 31-day baseline dropped deposits.
  - The one-day floor keeps the cursor within a day of the read. It also stays far enough back that skew between the
    brain's clock and the server's cannot skip a deal.
- **Real time, slot day.** Snapshots and reports are stamped with the real read time. The scheduler slot's UTC date
  only keys the day's row, so a late or repeated run of a slot replaces that day's row.
- **Read again before a FAIL.** A balance can change a moment before its deal shows in the history. An unexplained
  balance is therefore read again once, `RECHECK_S` (5 s) later, before the FAIL is committed. The report records
  `rechecked`.
- **Baselines book too.** The first snapshot, or a new baseline after a currency change or a gap of more than 31 days,
  books every balance deal it read, flagged `baseline`. Those deals are already in its opening balance or in a gap
  that was not tied out, so a monthly ledger must not add them again.
- **aux.db, additive migration 7.** This follows the robust-storage direction (no new JSON state files). The data is
  reporting, not entry gating, so it goes in aux.db rather than core.db. The migration is additive, so a rollback
  still opens the file.
- **Extend the existing check.** `reconcile:<account>` becomes the worse of the feed report and the ledger report.
  - Only a missing `ledger_reports` table (aux.db from before migration 7) counts as "no report".
  - Any other database error WARNs as "ledger report unreadable: <type>".
- **Amounts stay local.** Amounts are kept in aux.db only. The job output and the health reason give counts and
  verdicts only.
  - The owner reads the amounts with `python -m goldbot.ops.ledger show`. It opens aux.db read-only (`mode=ro`), so
    it is safe to run as the service user next to the scheduler.
  - Logins, servers and deal comments are never stored.
- **No bridge change.** `MT5Broker.deals_since` returns every deal type (`history_deals_get` with a time range),
  balance deals included. API_VERSION and MIN_COMPATIBLE are unchanged, and there is no deploy-order constraint.
- **Severity.**
  - A balance change beyond the tolerance that the deals do not explain is a FAIL: money moved outside the record.
  - A position difference is a WARN: the engine may be mid-write, and a fallback record (GAP-05) is expected to
    differ.
  - A broker adjustment or a cancelled deal is explained but WARNs.
  - None of these blocks entries: the check only reports.
- Rejected: a new `deal_history` bridge method. It would duplicate `deals_since` and force an MT5-box-first deploy
  for no gain.

## Known limitations
- **Cancelled deals (MT5 13 BUY_CANCELED, 14 SELL_CANCELED).** MT5 changes the original trade deal's type, zeroes its
  profit, and books the P&L reversal as a separate balance operation (type 2).
  - That operation is not the owner's deposit or withdrawal. The ledger cannot pair it with the cancelled deal for
    certain.
  - Every balance operation at or after the first cancelled deal in the read is tagged `follows_cancel`, and the
    report WARNs. A monthly ledger should exclude tagged rows from deposits and withdrawals after checking the
    statement.
  - The cancelled position also drops out of the trade reconciliation (its deal is no longer type 0 or 1). Its closed
    record then shows as `extra`, which is the right prompt to look.
- **BONUS (type 6).** It is counted as a balance adjustment, as MT5 documents it. A broker that books a bonus into
  credit instead would leave the balance unchanged while the ledger counts the bonus as explained. The tie-out would
  then FAIL by the bonus amount. If that happens, check the statement; the rule changes only with evidence from the
  broker.

## Consequences
- The first run per account is a baseline. Deposits read on that day are booked, flagged `baseline`.
- A gap of more than 31 days between runs starts a new baseline with a WARN instead of a partial tie-out. An idle
  account with daily runs never does.
- The tolerances (`ledger.*` in settings.yaml) are health thresholds, not phase-gate thresholds. The owner may still
  want to sign off on `balance_tolerance`.
- What would change this: a broker that books balance changes without deals (then the tie-out needs the statement
  export), or a need to block entries on a ledger FAIL (an owner decision, routed through the trading-safety review).
