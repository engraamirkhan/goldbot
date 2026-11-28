- 2026-11-28 U2 FX cost-probe amendment (research director, owner-approved probes, label-blind, K 0; quant signs before
  any probe): `docs/research/prereg-amendment-2026-11-28-u2-fx-cost-probes.md`. Scheduled FX10 cost probes at
  icm-probe and vantage-demo: 12 fixed UTC slots (4 per session), one probe per pair per broker per slot, seed-20261128
  sides, broker-min lot, 60 s hold then market close with the pair's own exit quote, 20-pip safety stop, at most 2
  open per account, GBP 25/account/day allowance, no early stopping per pair, counts-only reads until the cut-off.
  120 probes per broker per weekday; 48 slots per pair x broker x session by the new fill cut-off 2026-12-22 23:59 UTC
  (30 needed). P3 due 2026-12-30; Stage 0 on the first P1-P4 day, kill 2027-01-31 unchanged. Backlog 283 (engine
  stream), 284 (Dukascopy FX bid+ask), 285 (swap capture; quant ruling on history swap first).
