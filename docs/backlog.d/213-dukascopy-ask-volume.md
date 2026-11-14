---
number: 213
rank: 74
title: Keep Dukascopy's ask-side tick volume in the 1m bars
stage: 'data (follow-up of R139, 2026-11-13)'
size: M
status: 'done 2026-11-14 (branch fix/data-backlog-1114), re-pull not run · accountable: data-engineer · reviews: quant-reviewer'
---
- **213. Keep Dukascopy's ask-side tick volume in the 1m bars.** Value: `scripts/dukascopy_year.py` stores only the bid side's volume as `tick_count` and drops the ask side's, so R139's imbalance is a bulk-volume classification from price changes; the quoted-volume imbalance (bid vs ask volume) is a second, independent proxy. Acceptance, tests first: a nullable `tick_count_ask` column in the bar schema (absent = NaN, never 0; resample sums it like `tick_count`); `orderflow` gains `of_quote_imb_15m/60m` only where both sides exist; the re-pull's size and Actions minutes are estimated before any run, and the data stays local (no release).
  **Done 2026-11-14 (code).** Nullable `tick_count_ask` (resample.OPTIONAL_BAR_COLUMNS): NaN when a side was imputed or a month predates it, never 0; a kept published day gains it from a re-download minute by minute without any published value changing; resample sums it (a bar with an unknown minute reads NaN); `of_quote_imb_15m/60m` only where both sides exist in the whole window; feature `orderflow` version 3 (tests/test_dukascopy_ask_volume.py). Re-pull estimate: no Actions minutes (gold is local-only, ADR 0048); `dukascopy_year.py <year> --months <all 12> --existing ...` per year, about 12,000 bi5 requests for 2003-2026 (both sides are already downloaded by every build), 1-2 h on the Mac with throttling retries, +1 float column (about +10% per yearly file, about +30 MB in raw/). Not run: the quant reviews the proxy first. The brain's store keeps BAR_COLUMNS (release.load selects them): live bars never carry the column.
