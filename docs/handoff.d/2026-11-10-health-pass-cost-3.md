- Health pass cost, third step (R122, after #364; brain round 3: warm pass 7-8 s, service 13 % of a core). Every
  verdict and research result unchanged. phase_gates: `director._Ledger` matches trials to ad-hoc pre-registrations
  through `_AdhocIndex` (by `ts`, same pick as the old linear scan, which `tests/test_director_adhoc_index.py` keeps as
  the oracle), and `evaluate_gates` counts `n_trials_effective` once (it was built twice per evaluation). Synthetic
  5,930 + 5,930 rows: 2.68 s -> 0.05 s. order_failed reads the decisions journal through history_gap's part-file
  cache, both from the earlier of their windows (`_journal_from`), so one read per flush serves both; data_quality
  reads dq_events again only when the table's part set changes (`_cached_window` in checks/_base.py). pnl_basis: the
  trade ledger resolves the instruments registry once per build and re-selects aux.db's exploration records only
  when aux.db or aux.db-wal changed ((inode, mtime_ns, size) equal before and after the read and older than
  `AUX_RACY_S` = 2 s; `PRAGMA data_version` is per connection and so no use to a reader that opens its own). Still
  to measure on the brain after deploy: pass < 5 s across a :00/:15/:30/:45 flush, service < 5 % of a core over 1 h.
  Not addressed: Store.read still scans every part of the table after a flush (data-engineer item).
