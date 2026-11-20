- 2026-11-20 arm-integrity false voids (SRE brain simulation: 0/206 1h and 0/56 4h discovered-arm decisions ok on
  every account). (1) icm 4h: the 10-26 timezone-mislabel quarantine also removes those minutes from the broker's
  M1 evidence, so the read hit the M1 coverage check first and reported `no_broker_coverage` (arm unverified, owner
  told). The quarantine check now runs before the M1 check, so the candidate is a plain `quarantined_minute` void
  that ages out of the window and does not mark the arm unverified. Quant condition: the nightly arm_lifecycle summary reports
  `quarantine_void_share` per discovered arm (quarantined_minute voids / candidates the read saw,
  `ArmRead.n_integrity_cands`). The broker's coverage is still checked apart from any arm: feed_reconcile grades
  coverage gaps and short broker returns as warnings and writes `reconcile_broker_short` (goldbot/data/crossfeed.py
  grade and reconcile_account), and health `reconcile:<account>` reports them (`_feed_status`).
  (2) `sampled_minute` is NOT relaxed for an engine alone on its key (quant review of 6fec4099: two processes on one
  account are two writers, and nothing stored names the writer, so the void stays fail closed; vantage keeps
  these voids). Sharers now come from evidence as well as tonight's config: every configured account on the key,
  enabled or not, plus every account with dq_events on the key's symbol that the config puts on no other key
  (`evidence_sharers`; jobs passes `keys`). This removes the survivorship gap where disabling an account dropped its
  late minutes from the read. A test pins the restart case: a sampled minute refilled after the decision is never read as held.
  Follow-up, not done: proving a single writer (which would let a solo engine's own late minute verify, and the
  non-sampling icm account skip the other engine's void) needs a writer id on each bars_1m_live row or a gold live
  key per account (books.live_key). Kill rules, caps, the prereg and `LIFECYCLE_READY` are unchanged.
