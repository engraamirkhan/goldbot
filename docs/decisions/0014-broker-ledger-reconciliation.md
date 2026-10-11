# 0014: Broker ledger: one read-only daily job, aux.db, folded into reconcile:<account>

Status: accepted (implementer, 2026-10-11; finance audit GAP-07 and GAP-08)

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
- **Deal cursor, not clocks.** Each snapshot records `deals_through_utc`, the newest deal in its read. The next
  tie-out covers deals after it. The brain's clock and the server's are never compared.
- **aux.db, additive migration 7.** This follows the robust-storage direction (no new JSON state files). The data is
  reporting, not entry gating, so it goes in aux.db rather than core.db. The migration is additive, so a rollback
  still opens the file.
- **Extend the existing check.** `reconcile:<account>` becomes the worse of the feed report and the ledger report.
  There is still one name per account for "goldbot against its broker", and no new alert name to learn.
- **Amounts stay local.** Amounts are kept in aux.db only. The job output and the health reason, which reach Telegram
  and the dashboard, give counts and verdicts only. The owner reads the amounts with `python -m goldbot.ops.ledger
  show`. Logins, servers and deal comments are never stored.
- **No bridge change.** `MT5Broker.deals_since` returns every deal type (`history_deals_get` with a time range),
  balance deals included. API_VERSION and MIN_COMPATIBLE are unchanged, and there is no deploy-order constraint.
- **Severity.** A balance change beyond the tolerance that the deals do not explain is a FAIL: money moved outside the
  record. A position difference is a WARN: the engine may be mid-write, and a fallback record (GAP-05) is expected to
  differ. A broker adjustment (charge, correction, interest) is explained but WARNs. None of these blocks entries:
  the check only reports.
- Rejected: a new `deal_history` bridge method. It would duplicate `deals_since` and force an MT5-box-first deploy
  for no gain.

## Consequences
- The first run per account is a baseline. Deposits before it are part of its opening balance and are not booked.
- A gap of more than 31 days between runs starts a new baseline with a WARN instead of a partial tie-out.
- The tolerances (`ledger.*` in settings.yaml) are health thresholds, not phase-gate thresholds. The owner may still
  want to sign off on `balance_tolerance`.
- What would change this: a broker that books balance changes without deals (then the tie-out needs the statement
  export), or a need to block entries on a ledger FAIL (an owner decision, routed through the trading-safety review).
