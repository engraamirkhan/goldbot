- **Due books first in the multi-book pass (2026-11-03; live demo issue, icm-probe).** On 20 books the 1m S0
  controls fire on every book at the same minute close; the 5 s pass budget covered 4-15 books and the later books'
  signals passed their 10 s staleness before their pass ran (`exploration_stale`, ~80 an hour, `explore:S0-<SYMBOL>`).
  `books.account_pass` now runs the books with an exploration decision due (`exploration.due`: a signal queued, or a
  1m/5m arm's minute past its 1.5 s grace and not decided yet; read only, no broker call) right after gold, then the
  rest, each group in the fair rotation. The rotation's next book in turn runs even past the deadline, so due books
  never starve the others; the books left go first next pass as before. Exits (`_exits_first`, every book, every pass),
  the budget, the staleness limits, RiskGate and every cap are unchanged. Not done: fetching less per due book (each
  book's own pass still makes its round trips); if stale rows remain once deployed, that is the next lever. Tests:
  `tests/test_due_books_first.py`. Review: trading-safety (engine pass order).
