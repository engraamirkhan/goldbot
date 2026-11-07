- Long daily history (branch `feat/gold-history-1978`, WS-2 of the 2026-11-07 plan): `goldbot/data/long_history.py`
  (row contract: date, OHLC, close finite and > 0, `source` per row, `available_utc` point in time; ranked stitch,
  one cut-over per source, overlap gap reported), `goldbot/data/long_sources.py` (free sources: Bundesbank/ECB
  fixings, EIA spot, Yahoo indices/futures, WGC Goldhub when downloaded), `scripts/gold_history.py`,
  `scripts/long_daily_history.py`. 33 Parquet files (32 instruments + XAUUSD 1968-06..2025-09) live only in the
  Mac's git-ignored `raw/daily/` and on the brain in `/opt/goldbot/state/daily_history/` (goldbot:goldbot, 0750/0640),
  with `manifest.json` (sha256, rows, first/last, source segments = cut-over dates) and `manifest.sha256`.
  Copy: `scripts/ship_long_history.py --host <brain alias>` (BatchMode scp, `sha256sum -c` before files move into
  place, then the Mac and brain sha256 compared; idempotent). Never in git or a release (ADR 0048); nothing at or
  after the research cutoff. Cache downloads are validated before they are cached; ECB end date derives from CUTOFF.
  Gold has a hole 1999-01..2000-08 until a Goldhub workbook is supplied.
