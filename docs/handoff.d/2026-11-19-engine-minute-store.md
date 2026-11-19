- The engine's stored minutes say what each engine held and since when (2026-11-19). Brain 11-18 17:00..11-19 02:45:
  both gold engines of one broker write `bars_1m_live` under one key (`books.live_key`: broker, symbol) and the store
  keeps the last copy of a minute, so the other engine's plain copy, written seconds after a `broker_bar_replaced`,
  erased the `replaced` flag on 47 of the 61 replaced minutes (every minute both engines replaced kept it; 0 of 16
  replaced by one engine only and 6 of 37 by the other did), and the nightly arm read voided most decision points as
  `replaced_minute`. Now `Store.append(sticky=...)` keeps a flagged row against a later unflagged copy of its key
  (decided under the partition lock, so two writers racing cannot lose it; a newer flagged copy still wins) and
  `history.BarWriter` writes `bars_1m_live` with `sticky="replaced"`. Minutes a history fill or a cut-over rebase
  joins are stored flagged `filled` (history.FILLED_COL) with their write time, and a fill stores only the minutes it
  joined: one the engine already held keeps its stored copy and write time (before, the fill rewrote it). Nothing
  changes what the engine holds or decides. The read side (arm integrity, `account_bars.minute_versions`) needs no
  change for the flag; passing `filled` through is optional. Tests: tests/test_engine_minute_store.py.
