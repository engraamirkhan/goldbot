# TradingView strategies plan: plots to features, the direct pull, trials (2026-10-18)

Goal (owner, 2026-10-18): with close to zero owner input, goldbot reads every plot of the owner's TradingView studies,
turns them into point-in-time features and searches strategies on them, inside the same evidence bar as everything else.
The direct pull (owner's session cookie in the keyring, read-only, rate-limited; memory "TradingView direct pull") is the
primary source; webhook alerts and the CSV export stay as the second live feed and the manual history route.
Sources: DESIGN "TradingView signals", "Aamir's indicator suite", promotion table ("filter features only" until proven),
"max 40 features per live model"; docs/plans/tradingview-integration.md (phases 1-3); TRACEABILITY F11, F12, F25,
R51-R53; ADR 0004.

## Readings taken
- **TradingView bars are never primary.** The chart feed is OANDA, not the broker: labels, costs, stops and sizing come
  from our own bars; TradingView values are joined onto them by `bar_time` / `available_utc` as features only.
- **Holdout.** TradingView history at 15m and 1h is short and falls inside the holdout window, so for those timeframes
  it is shadow-forward evidence only (recorded live, judged later), never searched. 4h and 1d history reaches back
  before the holdout and is searched pre-holdout only, under the quarter's pre-registered trial budget.
- **Live staleness fails closed.** At decision time a TradingView value is used only when its `bar_time` is the
  decision bar's TradingView bar and it arrived no more than 60 s after that bar's close; otherwise the value is NaN
  and nothing that needs it can trigger. In the feature (`tv_plots`) this is: the joined bar closed less than one
  TradingView bar + 60 s (`MAX_LAG`) before the row, else NaN.
- **Separate grid.** TradingView trials run under `TV_GRID_V1`, registered and counted apart from `GRID_V1`, so the
  deflated-Sharpe N of each search is its own and TradingView trials never dilute or inflate the core grid.
- **Unverified history never trains.** Pulled (`pull_history`) and exported (`csv`) history trains only for the plots
  the repaint check validated (>= 99 % of >= 200 overlapping bars vs live rows, `tv_repaint.validate`).
- **Unknown side never triggers.** A plot whose side is not readable from its title is a feature only.
- **Caps.** At most 8 TradingView columns per model, inside the 40-input cap; more is refused, not truncated.

## Lane A: plots -> point-in-time features (this change)
- `goldbot/data/tv_catalog.py`: `StudyMeta.from_metainfo(raw, tf)`, `catalog(meta, sample) -> list[PlotSpec]`,
  `feature_id(meta, plot_id)` = `tv_{study}_{meta_hash8}_{tf}_{plot_id}` (study, version and input values hashed, so
  any input or version change is a new id). Kind: shapes/chars/arrows = event; colourer = state; a line within 5 % of
  the close = level, else oscillator. Side from an event's title (buy/long/bull +1; sell/short/bear -1), else none.
  Origins: live = alert, `pull_live`; history = `csv`, `pull_history`.
- `goldbot/features/tv_plots.py`: `tv_plots` v1, opt-in (`enable()`), family `tradingview`. asof_join on
  `available_utc` only, history no earlier than bar close + 60 s (the live allowance: decision rows are stamped at
  the bar open, so training must not see a bar live cannot); missing bar -> NaN; history only when
  repaint-validated; only rows of the study's `meta_hash`; 1 % seeded per-bar blanking with ctx `tv_training` (a
  blanked bar reads NaN, never the bar before); <= 8 columns. `goldbot/research/model.py` `check_feature_cap`
  refuses a model over 8 `tv_` columns or 40 inputs.
- `goldbot/research/tv_repaint.py`: `history_origin` (`--origin pull_history`) and `meta_hash` (`--meta-hash`): each
  history and input set is compared with the live rows of the same input set only, and flagged on its own.
  `meta_hash` is part of the `tv_indicators` store key.

## Lane B: the direct pull (data-engineer, security-reviewer)
- Read-only websocket client, cookie from the keyring via `goldbot.ops.accounts`, polite rate limit, fails soft (the
  alerts keep working). Writes `tv_indicators` rows with `origin` `pull_live` (at each bar close, `available_utc` =
  receipt) or `pull_history` (`available_utc` = bar close, `repaint_validated=false`), and `meta_hash` from
  `StudyMeta`, plus the study's `metaInfo` so lane A can catalogue it. Never creates or edits anything on the account.
- Runs `tv-repaint-check --origin pull_history` once 200 live bars overlap.

## Lane C: strategies and trials (strategy-researcher, quant-reviewer, evaluator)
- `TV_GRID_V1`: pre-registered trials over 4h/1d pre-holdout with `tv_plots` columns as filters on the existing
  specialists; 15m/1h as shadow specialists per event plot with a known side, recorded forward for the
  performance-analyst. Promotion beyond filter only by the DESIGN rule (> 1 % log-loss gain in two consecutive
  retrains and >= 99 % on-time delivery over 90 days).
- Wiring: the research run passes `tv_catalog`, `tv_indicators` and `tv_training=True` (training folds only).

## Non-negotiables
Nothing here imports the engine, RiskGate or broker; exits untouched; demo only. Invite-only outputs stay in gitignored
`/data/` and `/state/`, never in a release. Cookies and secrets only in the keyring.

## Known gaps
- `tv_plots` reads only rows carrying its study's `meta_hash`, so webhook alerts and CSV exports (inputs unknown) feed
  the older `tradingview` feature only, until lane B pulls the same study with its metadata.
- Live staleness on the engine side (bar_time == decision bar, lag <= 60 s) is lane C's when a TradingView value first
  reaches a specialist.
