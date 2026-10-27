# Data sources survey for XAUUSD research (research-director-staff, 2026-10-26)

Status: survey and licence check only. Nothing was downloaded, no account was created, no credential was entered, no
trial was run. Prices and terms were read from the vendor pages on 2026-10-26; anything I could not read is marked
**UNVERIFIED** and must be confirmed (by the main session's download test, or by the owner for spending) before use.
Owner question: "do you have enough data from past? ... find and access real gold data that goes back as much as possible".

## 0. What we already have (not re-proposed)

| Have | Where | Span |
|---|---|---|
| Dukascopy XAUUSD 1m, per-year Parquet | release `data-v1` (assets 2010..2026) | 2010-01 .. 2026-10; 2003-2009 run crashed (issue #108, "download step crashed"), so nothing before 2010 is published |
| FRED macro: DTWEXBGS, DFII10, DGS10, DGS2, DFF, T10YIE, GVZCLS | `goldbot/data/macro.py`, release `macro-v1` | FRED fetch from 2003-01; GVZ from 2008-06-03 |
| CFTC disaggregated COT, futures only, COMEX gold 088691 (managed money, open interest, commercials) and GLD tonnes | `goldbot/data/positioning.py`, release `positioning-v1` | 2006-06 on (pre-2009-09 rows are a documented PIT caveat, B1 in macro-pit-audit) |
| US economic calendar, headline news collector (RSS + LLM scoring), TradingView pull, broker tick recorder, cross-feed reconcile | `goldbot/data/` | calendar from 2010; news and ticks forward-only |

Gaps relative to the owner's list: real volume, trade tape and depth, anything before 2010, options beyond the GVZ level,
silver, miners, CNH and other FX, central-bank buying, ETFs other than GLD, a second price source, credible news history.

## 1. Owner priority: the longest reliable gold price histories

Regime note first, because it decides how much "more history" is worth. Gold was fixed at 35 USD until 1968 and the
London market was two-tier until 1971; a free-floating market only exists from 1971, and the 1975-1999 market had
little in common with today's (US citizens could not hold gold until 1975, the 1980 spike and 20-year bear market, central
bank sales). Sources that go back to the 1800s are for level, regime and very-long-seasonal work only; they cannot
support intraday or even 30-day-horizon rules. Realistic long-history gain: ~1971-2009 daily (if a clean source is
found) and 2003-2009 intraday.

### 1a. Daily and lower frequency

| Source | Start | Frequency | Licence for personal research, repo is public | Method | Verdict |
|---|---|---|---|---|---|
| LBMA Gold Price AM/PM (London fix, USD), administered by ICE Benchmark Administration (IBA) | 1968 (AM/PM fix history, per FRED's old series description; LBMA page does not state a start) | twice daily (10:30 and 15:00 London) | **Licence from IBA required "to obtain, use or redistribute real-time or historical benchmark data"** (https://www.lbma.org.uk/prices-and-data/precious-metal-prices). History tables sit behind the MyLBMA portal. FRED deleted all IBA series (LBMA gold and silver included) after 2022-01-31 (https://fred.stlouisfed.org/series/GOLDPMGBD228NLBM). Price of a historical licence: not published, ask IBA/ICE (**UNVERIFIED**, CFO item) | portal download after licence; no free API | the reference series for 30/60/90-day work; paid or licensed only. Do not use copies scraped from third-party sites |
| World Bank Commodity Markets "Pink Sheet", monthly | 1960 (gold, per DataHub description) | monthly average | World Bank dataset terms; licence not stated on the page, links to "Terms of use for Datasets" and a CC BY anchor (**confirm CC BY 4.0 before committing derived values**; do not commit the xlsx) | `CMO-Historical-Data-Monthly.xlsx`, updated monthly (next 2026-11-03), https://www.worldbank.org/en/research/commodity-markets | free, usable now. 1960-1971 is the fixed-price era |
| DataHub `core/gold-prices` (World Bank 1960+ plus Timothy Green's table 1833-1959) | 1833 | monthly (1833-1959 is the annual average repeated across 12 months, so effectively annual) | ODC-PDDL-1.0 (public domain) as declared on the page (https://datahub.io/core/gold-prices); last refresh 2026-10-04 | CSV/JSON over HTTPS | free; best single file for a monthly 1833-now series. The pre-1960 PDF source now needs a login, so refresh may skip it |
| MeasuringWorth "Price of Gold, 1257 - Present" (Officer and Williamson) | London market price 1718, NY 1791, US official 1786, British official 1257-1945 | annual (market series are yearly averages) | "may be used for non-profit educational purposes if proper credit is given"; anything else needs permission (https://www.measuringworth.com/datasets/gold/). Personal research is plausible; keep it out of the repo | web download | free, regime history only |
| World Gold Council Goldhub gold price tables | not stated on the pages I could read | daily/monthly | priced off the LBMA Gold Price (IBA), so the IBA licence question applies; free registration for Goldhub; terms point to a proprietary-rights disclaimer (**UNVERIFIED** whether the daily history downloads) | Goldhub xlsx after login | check by the main session's test; assume IBA terms bind |
| Stooq XAUUSD daily | **UNVERIFIED** (page returned empty to my fetch) | daily | no licence statement found; treat as personal-use only | CSV link on stooq.com/q/d/?s=xauusd | main session is testing this; verify first date and gaps, compare with the Pink Sheet monthly mean |
| Yahoo Finance GC=F (COMEX front-month continuous) | **UNVERIFIED** (not retrievable here; recalled as ~2000) | daily OHLCV incl. real futures volume | Yahoo terms restrict redistribution and automated scraping; personal use only (**UNVERIFIED**) | `yfinance`-style unofficial pull | a free, single-source cross-check of volume and roll gaps; unadjusted rolls make it a futures series, not spot |
| Nasdaq Data Link LBMA/GOLD, Bundesbank, Bank of England millennium dataset | **UNVERIFIED** | | | | I could not confirm that LBMA/GOLD still exists (the IBA licence withdrawal suggests it does not or is paid); the BoE millennium dataset (1086-2016) was not confirmed to carry a gold price. Do not plan on them until the main session finds the file |

Honest gap: I found **no verified free, redistributable daily series for 1968-2000**. The legitimate routes are the IBA
licence (LBMA fix) or Stooq/Yahoo for personal use with the provenance caveat. Until one is verified, the free long
series are monthly (1960 or 1833) only.

### 1b. Intraday

| Source | Start for XAUUSD | Frequency | Licence | Method | Verdict |
|---|---|---|---|---|---|
| Dukascopy (what we use) | claimed **2003** by Tickstory support (https://support.tickstory.com/viewtopic.php?p=9441); our own 2003-2009 run failed (issue #108), so the real first bar is **unverified** until that run publishes | tick, 1m | Dukascopy data usage agreement (not read). See flag F1 | `data-dukascopy.yml` with `from_year=2003` | cheapest big win: 7 more years incl. the 2008 crisis and 2008-2011 run-up, zero new vendor. Wider spreads and thinner minutes pre-2010 (data-coverage-2026-10-18.md caveats) |
| HistData.com | **XAU/USD from 2009-03** (listed on the free download page); also XAU/EUR, XAU/GBP, XAU/AUD, XAU/CHF, XAG/USD, UDX/USD, BCO/USD, WTI/USD, 66 FX crosses (https://www.histdata.com/download-free-forex-data/) | 1m bars and tick (ASCII), by month | terms not shown on the page I read (**UNVERIFIED**, read the linked privacy/terms before use); paid formats 7 USD/month for MetaTrader, NinjaTrader etc. | manual per-month zip download, no API (third-party downloaders exist) | a second independent 2009+ minute source for cross-checking Dukascopy (gap days, spikes). Not for pre-2009 |
| TrueFX | not stated; XAU/USD availability **UNVERIFIED** (page lists no pairs) | tick, ms | free with registration; terms not shown | web download after login | skip unless the main session sees XAU listed |
| CME DataMine (COMEX GC) | exchange history | tick / L1 / L2 / L3 | CME licence agreements; **price not published**, quote via CME Data Sales (**UNVERIFIED**, CFO item) | self-service portal, paid | dominated by Databento for our scale (section 3) |
| FirstRate Data, GC futures | continuous from 2008-01-31, individual contracts from GCZ08 (https://firstratedata.com/i/futures/GC) | 1m, 5m, 30m, 1h, 1d; no tick listed | licence page not read; one-off price not shown, then 99.95 USD/year for updates after the first free month; whether volume is included is not stated | download | a cheap futures-volume candidate, but trading hours and roll method must be checked; **UNVERIFIED** on volume |

## 2. Ranked sources by research value per cost and effort

Free first. "Unlocks / kills" names hypotheses already in docs/research/hypotheses.md or the playbooks.

| # | Source | Cost | Effort | Unlocks or kills |
|---|---|---|---|---|
| 1 | **Dukascopy 2003-2009 gold 1m** (fix issue #108 crash) | free | one workflow dispatch plus loader floors (listed in data-coverage-2026-10-18.md) | about 7 more independent years (2008 crash, 2009-2011 run). Doubles regime count for walk-forward folds; kills or confirms any rule that only worked in 2010-2025 |
| 2 | **Dukascopy sibling instruments** (XAG/USD, EUR/USD, USD/JPY, USD/CNH if listed, indices, Brent, same pipeline) | free | new pull configs, same loader; CNH listing **UNVERIFIED** | indicator-survey rows 61-62 (gold/silver ratio z, intraday FX lead); gives 15m-1h intermarket features at the same timestamps with no new licence. HistData also lists XAG/USD, UDX/USD |
| 3 | **CFTC COT extensions** (legacy futures-only from 1986, futures-and-options combined from 1995 (legacy) / 2009 (disaggregated), Commodity Index Trader supplement from 2006) and the Public Reporting API | free, US government | extend `positioning.py` | positioning z-scores over 1986-2009 (the 4h/1d swing lane's SW-0); combined reports add option positions. Release lag is unchanged (Friday 15:30 ET for Tuesday). API dataset ids: disaggregated futures `72hh-3qpy`, combined `kh3c-gbw2` (https://publicreporting.cftc.gov). TFF covers financial futures, not gold (**UNVERIFIED**; the audit already uses disaggregated) |
| 4 | **World Bank Pink Sheet and DataHub monthly gold, 1960 / 1833** | free | tiny | 12-month seasonality, long trend and regime classification; cannot test 30/60/90-day rules (too few independent monthly points after 1971: about 660 months) |
| 5 | **WGC Goldhub: central-bank reserves (IMF IFS based), ETF flows** | free with registration | monthly xlsx loader | reserves: quarterly from 2000, monthly from 2002, **two months behind** (revised); ETF flows: more than 100 funds, weekly (Monday) and monthly (within a week of month-end), history start not stated (https://www.gold.org/goldhub/data/gold-reserves-by-country, https://www.gold.org/goldhub/data/gold-etfs-holdings-and-flows). Useful as slow macro state for macro-researcher; useless intraday |
| 6 | **Databento GLBX.MDP3, COMEX GC: real volume, trades, depth** | pay as you go from 0.50 USD/GB; **125 USD free credit** for new users (6 months); Standard plan 199 USD/month (grandfathered 179 for 12 months) but it includes only 1 year of L1 and 1 month of L2/L3 history, so it is the wrong plan for backfill; Plus 1,750 and Unlimited 4,500 USD/month on annual contracts (https://databento.com/pricing, https://databento.com/blog/updates-to-subscription-pricing) | account, API key (owner), loader | closes gaps 1 and 2: 1m OHLCV with real volume since 2010-06 (the vendor says full coverage of CME futures and options "beginning in June 2010"), trades for delta, MBP-10 and MBO for depth/footprint. Size estimate (**mine, unverified**): GC 1m bars for 16 years are small (tens of MB), trades are a few GB, MBO tens of GB per year, so 1m + trades likely fit the free credit and depth is a measured purchase; get an exact quote from the batch tool (`metadata.get_cost`) before buying. See section 3 |
| 7 | **HistData 2009+ XAU/USD** | free (terms to read) | manual monthly zips | independent cross-check of Dukascopy (gap days, wrong prints); decides option A/B/C in data-coverage doc |
| 8 | **GPR index (Caldara-Iacoviello), GDELT** | free | small | GPR recent index from 1985 (10 newspapers), long index from 1900 (3 newspapers); there is also a daily AI-GPR from 1960 (https://www.matteoiacoviello.com/gpr.htm); site asks for citation, no licence text found. GDELT: unlimited and unrestricted use, redistribution allowed with citation and link (terms quoted by a secondary source, **verify at gdeltproject.org/about.html**), BigQuery free tier 1 TB/month. Both are published with revisions or after the fact, so any use must be forward-stamped with the file date, not the article date. News/sentiment: research value for gold intraday is unproven; treat as low |
| 9 | **FirstRate Data GC futures** | 99.95 USD/year update plus one-off (not shown) | download | real futures volume 2008+ if volume is included (**UNVERIFIED**); inferior to Databento on provenance and tick detail, cheaper only if Databento exceeds the credit |
| 10 | **Cboe DataShop GLD options EOD with IV/Greeks** | quote only (the order form computes it; not shown on the page) | owner purchase | IV surface and skew from 2012-01. Only 2012+ and GLD (not COMEX options). GVZ level already in `macro-v1`. Defer until a vol hypothesis needs skew |
| 11 | **IBA/LBMA historical licence** | not published, ask ICE | contract | the proper 1968 daily fix; only worth it if the 1971-2009 daily gap blocks a pre-registered 30/60/90-day trial |
| 12 | **CME DataMine** | quote only | | skip while Databento covers CME GC |
| 13 | **ETF holdings beyond GLD (IAU, SGOL, WGC aggregate)** | free | issuer Excel files; IAU page links an Excel download dated to the trade date, history start not stated (https://www.ishares.com/us/products/239561/ishares-gold-trust-fund) | GLD alone has the ~70% share for the question "do ETF flows lead gold"; add IAU only if GLD shows signal |

Not recommended: scraped Macrotrends/Kitco/TradingEconomics tables (terms forbid, provenance unclear), FRED copies of
IBA series (removed), Polygon/Massive and Alpha Vantage for XAU (I could not read their plan and licence pages in this
pass: **UNVERIFIED**; Polygon now redirects to massive.com).

## 3. Volume, order flow and the SMC slice: what they would actually settle

Volume (gap 1): H-40's volume flags (volume veto, break-with-volume) were retired on 2010..2025-09 using tick volume
(AUC 0.503). Tick volume in a decentralised spot market is a proxy for quote activity, not traded size, so that FAIL does
not prove real volume is useless. Real COMEX volume could (a) re-test break-with-volume on the real series, (b) feed a
volume-at-time-of-day profile for the opportunity-day work (H-54 style), (c) give open interest and roll. Re-entry
needs the evaluator's re-entry record (hypotheses.md says "a flag returns only with new evidence"). Cheapest path:
Databento ohlcv-1m for GC, or Yahoo GC=F daily as a free first look at daily volume vs range.

Order flow and SMC (gap 2): the SMC and sweep rules (H-34, H-35, H-47, H-48) died on **candidate counts, stop caps and a
costed gross t** (H-48 gross t -1.17), not on missing data, so footprint data would not have rescued them. It would let
us test the *mechanism* claims directly (delta/absorption at the swept level, imbalance at an FVG) on COMEX trades and
MBP-10; those claims are sold as flow facts, so only flow data can confirm or kill them. Value: moderate, mostly as a
"kill the mechanism cleanly" instrument; cost is the highest on this list because depth is large. Caveat: COMEX flow
is a proxy for spot XAUUSD (CFD) execution, and our broker's spread and fills are what we trade, so any positive result
is then tested on the broker tick record, not assumed.

## 4. Point-in-time rules for any new source

- Stamp every row `available_utc` conservatively and join only with `store.asof_join`. COT: Friday 15:30 ET for Tuesday
  (CFTC 2026 schedule is tentative, https://cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm).
  WGC reserves: two months behind. ETF flows: the following Monday or within a week of month-end. Pink Sheet: monthly
  average known only after month-end (next update 2026-11-03). GPR/AI-GPR: stamp at the download date, never the
  article date (index is rebuilt). IFS and Pink Sheet values are revised; keep `vintage`.
- Futures volume and open interest: the exchange prints final volume the next day; use the trading-day close, not the bar
  time, for daily volume features.
- Pre-2010 data enters walk-forward as its own regime (wider spreads; cost model not shared with 2010+).

## 5. Fetch route and licensing

- Our Claude sandboxes cannot reach several of these hosts. Free public sources (CFTC, World Bank, DataHub, GDELT,
  Dukascopy) go the usual way: a GitHub Actions workflow pulls, builds Parquet, publishes a release.
- **Licensed or restricted data (Databento/CME, FirstRate, IBA, Cboe, Yahoo/Stooq, MeasuringWorth, HistData) must not
  go to a release or the repo, because the repo and its releases are public.** Run the pull on the Mac or the brain VM
  and keep the Parquet on the machine or in private storage; commit only code, the schema, row counts and sha256
  digests (the pattern the broker tick recorder already uses). A vendor key belongs in the OS keyring via
  `goldbot.ops.accounts`; the owner enters it, no agent does.
- Spending (Databento, FirstRate, Cboe, IBA licence) is owner-only and goes to the CFO with a measured quote:
  Databento first (free credit), then, if needed, a single purchase of GC trades. Standing order: no sign-ups from research.

## 6. Flags

- **F1 (licence, existing practice):** `data-v1` publishes Dukascopy Parquet to a public GitHub release. I read neither
  Dukascopy's data usage agreement nor its redistribution terms. The data-engineer or security-reviewer should check that
  agreement; if it forbids redistribution, move the assets to private storage. Out of scope here, nothing changed.
- **F2:** issue #108 (data coverage 2003) shows a crashed download step; a new `data-dukascopy` run was queued on
  2026-10-26. The first-bar date for pre-2010 gold is unverified until it publishes.
- **F3:** unverified items are collected here for the main session's download test: Stooq XAUUSD and Yahoo GC=F start
  dates and gaps; WGC daily price download; TrueFX XAU listing; HistData terms; FirstRate volume; Polygon/Massive and
  Alpha Vantage coverage; Dukascopy CNH listing.

## Sources

https://databento.com/pricing, https://databento.com/blog/updates-to-subscription-pricing, https://databento.com/docs/datasets/glbx-mdp3,
https://www.lbma.org.uk/prices-and-data/precious-metal-prices, https://fred.stlouisfed.org/series/GOLDPMGBD228NLBM,
https://www.worldbank.org/en/research/commodity-markets, https://datahub.io/core/gold-prices,
https://www.measuringworth.com/datasets/gold/, https://www.histdata.com/download-free-forex-data/,
https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm, https://publicreporting.cftc.gov/stories/s/r4w3-av2u,
https://www.gold.org/goldhub/data/gold-etfs-holdings-and-flows, https://www.gold.org/goldhub/data/gold-reserves-by-country,
https://firstratedata.com/i/futures/GC, https://datashop.cboe.com/option-quotes-end-of-day-with-calcs,
https://www.matteoiacoviello.com/gpr.htm, https://www.gdeltproject.org/data.html, https://support.tickstory.com/viewtopic.php?p=9441,
https://www.ishares.com/us/products/239561/ishares-gold-trust-fund, https://fred.stlouisfed.org/series/GVZCLS
