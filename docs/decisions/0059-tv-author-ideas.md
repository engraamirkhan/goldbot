# 0059: TradingView author ideas as a point-in-time data source

Status: proposed (data-engineer, 2026-10-28)

## Context
The owner trades gold with the daily and weekly chart ideas of three TradingView authors (KnightsofGold, Goldviewfx,
ArmanShabanTrading) and asked that the system follow them and use their ideas (directive 2026-10-28). The ToS risk of
unofficial TradingView reads was accepted on 2026-10-18 (ADR 0030: read-only, rate-limited). An idea is a claim made
at a time: it is only usable for research or trading if we know when it became knowable and whether it was changed
afterwards. TradingView has no published API for ideas. Measured on 2026-10-28:

- The profile page loads ideas from `GET /api/v1/ideas/?by=<handle>&sort=recent_extended&per_page=100&page=N`. It is
  public, so no cookie is needed. It serves at most 1000 items per query (page 11 at 100 answers 400
  `bad_pagination_request`), but its `count` is the whole catalogue: KnightsofGold 1957, Goldviewfx 2222,
  ArmanShabanTrading 2667. `exchange`, `symbol`, `type`, `is_video` and `q` narrow a query, and `sort=trending`
  changes its order. The listing cuts the text at 500 characters.
- `GET /api/v1/ideas/<uuid>/` returns the full text, `direction` (1 long, 2 short, 0 neutral), `interval`, and the
  author's `updates` ("Trade active", "Target reached"), each stamped with TradingView's server time.

## Decision
- One collector, `goldbot/data/tv_ideas.py`. It goes through the tv_wire read-only guard (GET only, allow-listed
  paths) and tv_pull's Pacer, sends no cookie, and has a daily request budget. The live poll runs every 12-15 min
  over the authors' newest-activity page, and reads the detail of every new or changed idea.
- Its own state file, `state/tv_ideas.db` (SQLite WAL, NORMAL). The Mac research copy is `raw/tv_ideas/`
  (gitignored). Third-party text and listing items never enter the repo; tests use synthetic fixtures only.
  Snapshot URLs are stored, but images are not downloaded.
- Point in time: every row has `first_seen_utc` and `available_utc`.
  - Live rows: `available_utc` is when we first held the full text.
  - Back-catalogue rows, and anything published before the author's first live poll: `backfilled=1`, with
    `available_utc = min(TradingView's time + 900 s, first held)`. That is the author's server-stamped claim, never
    earlier than one live poll would have held it.
  - Joins to bars use `available_utc` only (`asof_join`).
- First release is immutable. A changed title, text, direction or timeframe becomes a `tv_idea_versions` row with
  the time we saw it. An update becomes a `tv_idea_updates` row. `edited` is a convenience flag; the versions and
  updates tables carry the times.
- Levels (entry, stop, target, support, resistance) are parsed from the text by a keyword heuristic. They are
  derived data: `tv-ideas reparse` recomputes them and never touches the text.

Back catalogue read on the Mac, 2026-10-28 (raw/tv_ideas): 6,842 of 6,846 counted ideas stored (the DB count; KnightsofGold 1,955 of
1,957, from 2020-11-27; Goldviewfx 2,220 of 2,222, from 2021-10-06; ArmanShabanTrading 2,667 of 2,667, from
2021-08-08). The four missing are three that TradingView counts but never serves and one detail read that failed. 4,862 of
the ideas are XAUUSD. There are 2,933 updates; 905 of them are "Trade closed: target reached" and 24 are "stop
reached". The read took 6,843 detail requests plus about 1,500 listing requests, at 2 s apart.

The ideas path sends browser-like request headers (a Chrome User-Agent plus Origin and Referer, from
`goldbot/data/tv_wire.py`) so its requests look like the site's own page traffic rather than a script. The owner
accepted the terms-of-service risk of this unofficial read (this ADR); it stays read-only GETs, no cookie, 2 s apart.

## Consequences
- Finding: the authors almost never publish an entry, a stop and a target together (0-0.05% of ideas). About
  34-36% name a target and 17-54% name support or resistance. Only ArmanShabanTrading sets the long/short label
  (81%; Goldviewfx 1%, KnightsofGold 5%). A study should therefore read the ideas as levels and bias, not as
  complete trade tickets.
- Research can test "trade the author's idea" with honest timing: back-catalogue rows carry the author's claim and
  live rows carry our own clock. Using a backfilled row's text assumes it was not edited before we saw it.
  TradingView exposes no edit history for the original text, so studies should report results with and without
  backfilled and edited rows.
- The endpoint is unofficial and can change or be blocked without notice. The collector fails soft (health
  `tv_ideas` WARNs after 2 h without a successful poll on a weekday, and never FAILs), and nothing on the trading
  path depends on it.
- Cost: about 400 requests a day live, and about 7,000 once for the back catalogue (2 s apart, a few hours). It adds
  no Actions minutes. `tv_ideas.db` is about 54 MB for the three catalogues (the Parquet export is about 4 MB).
- What would change this: an official ideas API, a block or rate-limit answer from TradingView (raise
  `min_interval_s`, narrow to the newest page), or the owner adding or removing authors (config only).
