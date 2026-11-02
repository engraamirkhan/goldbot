- Blocked non-gold books are quiet (live demo issue 2026-11-02, icm-probe passes up to 35.8 s on a gold-only bridge):
  a book with `BookSpec.blocked` set that holds nothing in its magic range makes no broker call per pass (no quote,
  exits step, refresh, terms or positions read) and publishes `disabled` from config (`books.quiet_books`, rechecked
  from the pass's shared `account_positions()` every `QUIET_RECHECK_S` = 10 s). A blocked book holding positions runs
  its full pass (exits) but never reads instruments.db terms. Every non-gold book now reads its positions from the
  shared `account_positions()` (no `positions(symbol=)` call per book) and shares one `symbol_info` read for 30 min
  (`books.symbol_info`, failures included); decision-cycle reads and gold are unchanged. Tests:
  tests/test_blocked_books_quiet.py.
