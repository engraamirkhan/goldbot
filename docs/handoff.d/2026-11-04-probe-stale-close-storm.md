- **icm-probe stale entries and the vantage-demo close-retry storm (2026-11-04).** Both measured on the brain
  (read-only: py-spy 0.4.3 run from /tmp, /proc schedstat, the journal and the decision parquet; nothing written on
  the server, the temp files removed).
  1. **Brain CPU starvation.** The brain has 2 cores. The api and telegram services each used a full core: 96-97%
     of their py-spy samples were in `instruments_view._registry`, which parsed config/instruments.yaml once per
     ledger row (`instrument_key`). CPU pressure (PSI "some") was 46%. icm-probe's main thread waited 1.83 s on
     the run queue for every 1 s it ran (345 s run against 631 s waiting since its 11:00 start). This, not the
     engine, is the main reason "the brain is 10x slower than the Mac", and it explains why db07bff7 did not help
     there. Fix: the registry is parsed once per version of the file (mtime and size). An edit is read on the next
     call, and an unreadable file is logged once per version.
  2. **Engine pass (150 s py-spy of icm-probe across the 11:59 and 12:00 closes).** 147 passes: median 0.34 s, p90
     1.66 s, max 10.5 s. Within passes: bridge round trips 62.9 s, everything else 59.9 s, of which about 2/3 was
     waiting for CPU. Main bridge costs: `ticks_many` about 166 ms a pass, `account` and `account_positions` about
     50 ms each, and `loss_per_lot` 8.9 s in total. Fixes: `PassBroker` asks `loss_per_lot` once a pass for each
     exact question. Every book's exploration pass used to ask again for the same open positions' loss at their
     stops. The deferred-pass warning now gives the exits time and the three slowest books with their seconds, so
     the next measurement needs no profiler.
  3. **Why entries go stale.** It is not tick age. `exploration._enter` measures a signal's age from its bar close.
     453 of today's stale refusals were S0-* (the 1m random control on all 19 non-gold books, firing in the same
     minute) plus LCS5A/B, with a median age of 18.6 s against a 10 s limit. A 1m decision waits for the broker's
     M1 bar, which arrives 3.7-5 s after the close. The first order of a minute goes out a median 7.3 s after the
     close, and later orders follow every 2.7 s (median). Each order runs serially through place_order, last_tick,
     a full `_refresh_account` (account, positions, deals_since, exposure) and two orders-file writes. A fresh tick
     would not help, and the limit is unchanged.
     Model on the measured split: removing the run-queue wait cuts pass time by about a third (in-pass 123 s to
     about 82 s per 150 s). At 20 books, p90 goes from about 1.7 s to about 1.1 s; at 26 books (the owner's list),
     about 1.4 s. In a minute's S0 sweep, roughly 4-5 orders fit inside 10 s instead of about 2. Stale refusals
     should fall (LCS5A/B on gold, which runs first, should mostly clear) but will not reach zero: 19 S0 signals
     in the same minute cannot be sent serially within 10 s at about 1.5-2.7 s an order. Next levers (not done,
     order path, need trading-safety review): a lighter account re-read after an exploration fill, and orders-file
     writes once a pass.
  4. **vantage-demo close storm.** The probes had closed on their own stop or target. `_retry_closes` dropped the
     due close of a position the broker no longer listed, and the 30 s hold timer (`cost_probe.hold_exits`) sent
     it again on the next pass, until the bar close's sweep 14.5 min later. New `engine/close_retry.py`:
     - A gone position is recorded at once from its exit deal (`broker_close`, journalled `close_due_gone`). With no
       deal yet it is kept due, without sending a close, for the sweep.
     - A close refused while the position is still open keeps being sent: every 1 s for 30 refusals, then backed
       off, doubling, with one warning per try. The cap is 5 s, except for a scheduled exit (probe or arm hold,
       time exit) whose price is still short of the engine's stop, which gets 30 s. So kill, Friday cut, hard
       flat and engine-stop closes are never more than 5 s apart (trading-safety review MEDIUM).
     - The refusal count is kept apart from the market-closed backoff count, and a market-closed refusal resets
       it. `gone()` keeps its own look count.
     - A missing instruments.yaml is cached as missing, so it is not re-read or logged on every call.
     A paper replay of the incident sent 560 closes before and 1 after. Exploration hold exits skip due closes, so
     they are covered too.
  Not done: the effect on the brain is modelled, not observed (needs a deploy). The serial order path still caps a
  minute's sends. The S0 sweep's design (19 books in the same minute) is a quant question.
  Review: trading-safety (close retry backoff on the exit path, `loss_per_lot` pass memo on the risk figures).
