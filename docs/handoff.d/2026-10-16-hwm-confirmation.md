- High-water-mark rises are confirmed (BACKLOG 54, ADR 0018, TRACEABILITY R40; needs trading-safety review). A
  balance read above `balance_closed_hwm` raises it only when the deal history since the mark was set explains it
  (closed deals, commission, swap, deposits; within `risk.hwm_rise_tolerance`, 0.1% of the mark), or when the next
  read agrees. Otherwise the mark stays where it is, the account is read again on the next tick, and the engine
  records `hwm_rise_unconfirmed`, then `hwm_raised` or `hwm_rise_refused`. One inflated balance read no longer
  causes a phantom size-down or kill switch later on. Deposits raise the mark, as before. The mark is never lowered
  (only /rearm resets it). The candidate and the deal-history anchor are stored in core.db (additive migration 2,
  table `hwm_confirmation`), not in a JSON file. A row taken for another mark is void. A restart keeps the candidate
  but never applies it unconfirmed. Health `risk:<account>` warns when a rise has waited more than 5 min in market
  hours, measured from the first of a run of disagreeing candidates. A rollback to an earlier release raises the mark
  on one read again. Review fixes (trading-safety review of faf824c):
  - a candidate that the deals after it carry to a later lower read was a real peak, and the mark rises to it;
  - candidates expire after 10 min;
  - core.db is opened once per engine, with a 500 ms write busy timeout;
  - the paper broker declares its balance reads exact, so one read raises the mark there.
  The ADR is renumbered 0018 (0017 is CostImputer).
