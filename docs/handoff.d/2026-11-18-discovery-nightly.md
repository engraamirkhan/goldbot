- 2026-11-18 (branch feat/discovery-nightly; R159, ADR 0090): `discovery_nightly`, the brain's own nightly strategy
  search (owner directive 2026-11-18, research director's build 4). Off until a host sets `research.discovery.enabled`
  and `bars_path` (RUNBOOK "Discovery nightly"); quant-reviewer and sre GO first.
  - One bounded child (`goldbot/ops/bounded.py`): nice 15, RLIMIT_DATA 4 GB, one BLAS thread, no new timeframe after
    `budget_s` (5400 s), killed at budget + 300 s, 01:00-03:10 UTC, clear of every other daily job. Enabled without
    bars fails the job instead of skipping (ADR 0083's lesson).
  - G2 on 1h/4h/1d, explore 2010-2017 only, bars only through `load_research_bars` (holdout sealed). Every spec, null
    spec and plateau neighbour gets the ADR 0085 power verdict from its signal counts BEFORE it is ledgered
    (`gen_gp.Admit`): refused, it costs no K. The night's planned K must pass `assert_budget`. Costs: cost of record v3,
    icm's dearest cell (0.90 $/oz) on top of the bar spread.
  - Grammar-node priors (`GPConfig.leaf_priors`, bounded 0.5-2.0) from demo labels by family and session, and from
    each discovered arm's forward labels on its spec's nodes; the G0 null uses the same priors.
  - Survivors that can run live -> `candidate_arms` in the discovery ledger's database
    (`goldbot/research/autodisc/handoff.py`, `pending()` for a hot-load path); registration stays
    `ops/autodisc_register.py`. Run record -> aux.db `discovery_runs` (migration 37) and the ledger's `nightly_runs`;
    report -> state/autodisc/discovery-<night>.md. No confirm read is opened (ADR 0085's 4 batches a year).
  - Outcome-blind reading on the real bars: power admits ~8 % of random G2 specs at 1h, ~5 % at 4h; no catalogue card
    with a stated count passes (31 refused, 0 started).
