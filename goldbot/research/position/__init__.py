"""Daily position-strategy research engine (docs/research/amendment-w3.md W3.2-W3.6; rethink strategy.md step 2).

A position strategy outputs a continuous target exposure per instrument per day; this package turns it into daily P&L
in units of starting equity, net of point costs (C1) and financing by date (W3.3), under three venue scenarios, and
reads it with the W3 statistics. Modules:

* `windows`   the W2 window table and the hard holdout guard (nothing at or after 2025-10-01 is read)
* `data`      daily mid bars closing at 21:00 UTC from the 1m Dukascopy files, other markets' daily files, DTB3
* `costs`     the C1 point round trip and overshoot in bps from the cost of record (swap for the shared C1 function)
* `financing` rollover nights, rates by date, markups and the venue scenarios A (gates), B and C (report only)
* `signals`   the signal interface (`PositionSpec`) and the point-in-time check every run makes
* `engine`    the path (vol scaling, no-trade band, disaster stop) and the booking of daily P&L
* `stats`     block-bootstrap t, years positive, legs, counts, the episode-sign and exposure-matched nulls, BH-FDR and
              the W3.1 power helpers
"""
